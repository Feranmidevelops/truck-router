"""Open-Meteo hourly forecast client.

Docs: https://open-meteo.com/en/docs
- Many coordinates per request (comma-separated); the response is then a JSON list.
- rain, showers and snowfall are sums over the preceding hour, so they read as in/hr.
- Free tier: under 10,000 calls/day, 5,000/hour, 600/minute. Cache and batch.

Units are read from `hourly_units` and converted, so the code is correct
whether or not the API honours precipitation_unit=inch for snowfall.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Protocol

import httpx

from .types import LatLon, WeatherSample

BASE_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall")
CHUNK_SIZE = 50
MAX_WORKERS = 4
CACHE_TTL_S = 30 * 60

SPEED_TO_MPH = {"mp/h": 1.0, "mph": 1.0, "km/h": 0.621371, "m/s": 2.236936, "kn": 1.150779}
DEPTH_TO_IN = {"inch": 1.0, "in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54}


class WeatherError(Exception):
    pass


class Cache(Protocol):
    def get(self, key: str, default=None): ...
    def set(self, key: str, value, timeout: int | None = None): ...


@dataclass
class HourlySeries:
    position: LatLon  # the position we asked for (Open-Meteo snaps to its grid)
    times: list[datetime]  # UTC, one per hour
    wind_mph: list[float]
    gust_mph: list[float]
    rain_in_hr: list[float]  # rain + showers
    snow_in_hr: list[float]


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def ceil_hour(dt: datetime) -> datetime:
    floored = floor_hour(dt)
    return floored if floored == dt.astimezone(timezone.utc) else floored + timedelta(hours=1)


def fetch_hourly(
    positions: list[LatLon],
    start: datetime,
    end: datetime,
    *,
    client: httpx.Client | None = None,
    cache: Cache | None = None,
) -> list[HourlySeries]:
    """Hourly series from floor(start) to ceil(end) for each position, in input order."""
    if not positions:
        return []
    start_h, end_h = floor_hour(start), ceil_hour(end)
    chunks = [positions[i : i + CHUNK_SIZE] for i in range(0, len(positions), CHUNK_SIZE)]
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(lambda c: _fetch_chunk(client, c, start_h, end_h, cache), chunks))
    finally:
        if own_client:
            client.close()
    return [series for chunk in results for series in chunk]


def _fetch_chunk(
    client: httpx.Client, chunk: list[LatLon], start: datetime, end: datetime, cache: Cache | None
) -> list[HourlySeries]:
    params = {
        "latitude": ",".join(f"{p.lat:.4f}" for p in chunk),
        "longitude": ",".join(f"{p.lon:.4f}" for p in chunk),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "GMT",
        "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": end.strftime("%Y-%m-%dT%H:%M"),
    }
    key = "wx:" + hashlib.sha1(repr(sorted(params.items())).encode()).hexdigest()
    payload = cache.get(key) if cache else None
    if payload is None:
        try:
            resp = client.get(BASE_URL, params=params)
        except httpx.HTTPError as exc:
            raise WeatherError(f"Open-Meteo request failed: {exc}") from exc
        if resp.status_code != 200:
            raise WeatherError(f"Open-Meteo {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if cache:
            cache.set(key, payload, CACHE_TTL_S)

    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(chunk):
        raise WeatherError(f"Open-Meteo returned {len(items)} locations for {len(chunk)} requested")
    return [parse_location(item, pos) for item, pos in zip(items, chunk)]


def parse_location(item: dict, position: LatLon) -> HourlySeries:
    hourly = item["hourly"]
    units = item.get("hourly_units", {})
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
    rain = _convert(hourly["rain"], units.get("rain"), DEPTH_TO_IN)
    showers = _convert(hourly["showers"], units.get("showers"), DEPTH_TO_IN)
    return HourlySeries(
        position=position,
        times=times,
        wind_mph=_convert(hourly["wind_speed_10m"], units.get("wind_speed_10m"), SPEED_TO_MPH),
        gust_mph=_convert(hourly["wind_gusts_10m"], units.get("wind_gusts_10m"), SPEED_TO_MPH),
        rain_in_hr=[r + s for r, s in zip(rain, showers)],
        snow_in_hr=_convert(hourly["snowfall"], units.get("snowfall"), DEPTH_TO_IN),
    )


def _convert(values: list, unit: str | None, table: dict[str, float]) -> list[float]:
    if unit not in table:
        raise WeatherError(f"Unexpected unit {unit!r}")
    factor = table[unit]
    # Missing values (null) are treated as 0. Revisit if gaps show up in real responses.
    return [(v or 0.0) * factor for v in values]


def sample_at(series: HourlySeries, when: datetime) -> WeatherSample:
    """Weather the truck meets at `when`.

    - Wind and gusts are instantaneous: use the nearest forecast hour.
    - Rain and snow are preceding-hour sums: use the first forecast hour >= when
      (the hour whose window contains `when`).
    - Clamp to the first/last hour if `when` is outside the series.
    forecast_time is the hour used for wind.
    """
    raise NotImplementedError("sample_at")
