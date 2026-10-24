"""Open-Meteo hourly forecast client.

Docs: https://open-meteo.com/en/docs
- Many coordinates per request (comma-separated); the response is then a JSON list.
- rain, showers and snowfall are sums over the preceding hour, so they read as in/hr.
- Free tier: under 10,000 calls/day, 5,000/hour, 600/minute. Cache and batch.

Units are read from `hourly_units` and converted, so the code is correct
whether or not the API honours precipitation_unit=inch for snowfall.
"""

from __future__ import annotations

from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import time
from typing import Protocol

import httpx

from .types import LatLon, WeatherSample

BASE_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall")
CHUNK_SIZE = 50
COORD_DECIMALS = 1  # 0.1 deg, about 11 km
MAX_WORKERS = 4
CACHE_TTL_S = 30 * 60
RETRY_WAIT_S = 2.0
RATE_LIMITED_MESSAGE = "The weather service is rate-limiting requests. Try again in a minute."

SPEED_TO_MPH = {"mp/h": 1.0, "mph": 1.0, "km/h": 0.621371, "m/s": 2.236936, "kn": 1.150779}
DEPTH_TO_IN = {"inch": 1.0, "in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54}


class WeatherError(Exception):
    pass


class Cache(Protocol):
    def get(self, key: str, default=None): ...
    def set(self, key: str, value, timeout: int | None = None): ...


@dataclass
class HourlySeries:
    position: LatLon  # as the caller passed it, before rounding (Open-Meteo also snaps to its grid)
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
    """Hourly series from floor(start) to ceil(end) for each position, in input order.

    Positions are rounded to COORD_DECIMALS and de-duplicated before the request,
    so nearby checkpoints cost one location. Each result keeps its original position.
    """
    if not positions:
        return []
    start_h, end_h = floor_hour(start), ceil_hour(end)
    cells = [_snap(p) for p in positions]
    unique = list(dict.fromkeys(cells))
    chunks = [unique[i : i + CHUNK_SIZE] for i in range(0, len(unique), CHUNK_SIZE)]
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(lambda c: _fetch_chunk(client, c, start_h, end_h, cache), chunks))
    finally:
        if own_client:
            client.close()
    by_cell = {series.position: series for chunk in results for series in chunk}
    return [replace(by_cell[cell], position=p) for cell, p in zip(cells, positions)]


def _snap(p: LatLon) -> LatLon:
    return LatLon(round(p.lat, COORD_DECIMALS), round(p.lon, COORD_DECIMALS))


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
    payload = cache.get(key) if cache is not None else None
    if payload is None:
        payload = _get_json(client, params)
        if cache is not None:
            cache.set(key, payload, CACHE_TTL_S)

    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(chunk):
        raise WeatherError(f"Open-Meteo returned {len(items)} locations for {len(chunk)} requested")
    return [parse_location(item, pos) for item, pos in zip(items, chunk)]


def _get_json(client: httpx.Client, params: dict) -> dict | list:
    resp = _send(client, params)
    if resp.status_code == 429:
        # Retry once only: the driver is waiting on this request.
        time.sleep(RETRY_WAIT_S)
        resp = _send(client, params)
    if resp.status_code == 429:
        raise WeatherError(RATE_LIMITED_MESSAGE)
    if resp.status_code != 200:
        raise WeatherError(f"Open-Meteo {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _send(client: httpx.Client, params: dict) -> httpx.Response:
    try:
        return client.get(BASE_URL, params=params)
    except httpx.HTTPError as exc:
        raise WeatherError(f"Open-Meteo request failed: {exc}") from exc


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
    """Weather the truck meets at `when` (timezone-aware, any zone).

    - Wind is instantaneous: use the nearest forecast hour. Exactly half past
      goes to the later hour, the same hour rain and snow use, so at :30 every
      value comes from one forecast row.
    - Gusts are shown, not scored, so they come from the wind's hour and the
      two read together.
    - Rain and snow are preceding-hour sums: use the first forecast hour >= when
      (the hour whose window contains `when`).
    - Clamp to the first/last hour if `when` is outside the series.
    forecast_time is the hour used for wind.
    """
    if when.tzinfo is None:
        raise ValueError("sample_at needs a timezone-aware datetime")
    if not series.times:
        raise WeatherError("No forecast hours to sample")
    # Convert first: adding 30 minutes to a local time is wall-clock maths and
    # goes wrong across a DST change.
    t = when.astimezone(timezone.utc)
    wind_i = _hour_index(series.times, floor_hour(t + timedelta(minutes=30)))
    precip_i = _hour_index(series.times, ceil_hour(t))
    return WeatherSample(
        wind_mph=series.wind_mph[wind_i],
        gust_mph=series.gust_mph[wind_i],
        rain_in_hr=series.rain_in_hr[precip_i],
        snow_in_hr=series.snow_in_hr[precip_i],
        forecast_time=series.times[wind_i],
    )


def _hour_index(times: list[datetime], hour: datetime) -> int:
    """Index of `hour` in the series, clamped to the first/last hour."""
    return min(bisect_left(times, hour), len(times) - 1)
