"""Open-Meteo hourly forecasts: the requests to make, and reading the replies.

The browser makes the requests (see planner.py for why) and sends the replies back, so
this module builds the URLs and checks and reads what comes back, but never calls out.

Docs: https://open-meteo.com/en/docs
- Many coordinates per request (comma-separated); the response is then a JSON
  list in request order.
- start_hour and end_hour are both inclusive, in the requested timezone (GMT).
- wind_speed_10m, temperature_2m and weather_code are instantaneous.
  wind_gusts_10m is the preceding hour's max. rain, showers and snowfall are sums
  over the preceding hour, so they read as in/hr.
- Free tier: under 10,000 calls/day, 5,000/hour, 600/minute
  (https://open-meteo.com/en/pricing). Up to 10 variables over up to 2 weeks is
  one call; a request for several locations is weighted by how many it asks for
  (maintainer, https://github.com/open-meteo/open-meteo/issues/438). The limit is
  per IP address. Round, de-duplicate and batch, and cap the locations one plan asks for.

Units are read from `hourly_units` and converted. With the params below the API
returns "mp/h" for wind, "inch" for rain, showers and snowfall, and "°F" for
temperature. weather_code is a WMO code ("wmo code"), described in conditions.py.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import urlencode

from .types import LatLon, WeatherSample

BASE_URL = "https://api.open-meteo.com/v1/forecast"
# Temperature and weather_code are shown, not scored. Seven variables still count as one call.
HOURLY_VARS = (
    "wind_speed_10m",
    "wind_gusts_10m",
    "rain",
    "showers",
    "snowfall",
    "temperature_2m",
    "weather_code",
)
CHUNK_SIZE = 50
# Positions are snapped to a grid this many degrees apart (0.1 deg is about 7 miles), and each
# grid point is asked for once. When that is more locations than the caller allows, the grid gets
# coarser in these steps, then keeps doubling, until they fit.
CELL_STEPS_DEG = (0.1, 0.2, 0.25, 0.5)
GRID_DECIMALS = 6  # hides float noise such as 32.300000000000004, which would be another cell

SPEED_TO_MPH = {"mp/h": 1.0, "mph": 1.0, "km/h": 0.621371, "m/s": 2.236936, "kn": 1.150779}
DEPTH_TO_IN = {"inch": 1.0, "in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54}
TEMP_TO_F = {"°F": lambda f: f, "°C": lambda c: c * 9 / 5 + 32}


log = logging.getLogger(__name__)


class WeatherError(Exception):
    pass


@dataclass
class HourlySeries:
    position: LatLon  # as the caller passed it, before rounding (Open-Meteo also snaps to its grid)
    times: list[datetime]  # UTC, one per hour
    wind_mph: list[float]
    gust_mph: list[float]
    rain_in_hr: list[float]  # rain + showers
    snow_in_hr: list[float]
    temp_f: list[float | None]  # None where Open-Meteo has no value: 0 °F is a real temperature
    weather_code: list[int | None]  # WMO code


@dataclass
class ForecastRequests:
    """The Open-Meteo requests that cover some positions, and how to read the replies back."""

    positions: list[LatLon]  # as the caller passed them
    cells: list[LatLon]  # each position's grid point, in the same order
    chunks: list[list[LatLon]]  # the distinct grid points, sorted, CHUNK_SIZE to a request
    start: datetime  # first and last hour asked for, both included, UTC
    end: datetime

    def urls(self) -> list[str]:
        return [forecast_url(chunk, self.start, self.end) for chunk in self.chunks]

    @property
    def hours(self) -> int:
        return int((self.end - self.start) / timedelta(hours=1)) + 1


def forecast_requests(
    positions: list[LatLon], start: datetime, end: datetime, *, max_cells: int | None = None
) -> ForecastRequests:
    """What to ask Open-Meteo for to cover each position hourly from floor(start) to ceil(end).

    Positions are snapped to a 0.1 degree grid and de-duplicated, so nearby checkpoints cost one
    location. With `max_cells`, at most that many locations are asked for (see snap_cells). The
    grid points are sorted before chunking, so the same points in any order make the same URLs.
    """
    cells = snap_cells(positions, max_cells) if positions else []
    unique = sorted(set(cells), key=lambda c: (c.lat, c.lon))
    chunks = [unique[i : i + CHUNK_SIZE] for i in range(0, len(unique), CHUNK_SIZE)]
    return ForecastRequests(list(positions), cells, chunks, floor_hour(start), ceil_hour(end))


def forecast_url(chunk: list[LatLon], start: datetime, end: datetime) -> str:
    return f"{BASE_URL}?{urlencode(_params(chunk, start, end), safe=',:')}"


def read_forecasts(requests: ForecastRequests, replies: list) -> list[HourlySeries]:
    """Each position's series from Open-Meteo's replies to requests.urls(), in the same order.

    The replies come from the browser, so anything that isn't the forecast that was asked for
    (another number of replies or locations, other hours, missing values) is a WeatherError.
    """
    if not isinstance(replies, list) or len(replies) != len(requests.chunks):
        count = len(replies) if isinstance(replies, list) else "no list of"
        raise WeatherError(f"Expected {len(requests.chunks)} forecast replies, got {count}")
    by_cell = {}
    for chunk, reply in zip(requests.chunks, replies):
        items = reply if isinstance(reply, list) else [reply]
        if len(items) != len(chunk):
            raise WeatherError(f"A forecast reply has {len(items)} locations for {len(chunk)} requested")
        for cell, item in zip(chunk, items):
            try:
                series = parse_location(item, cell)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise WeatherError(f"A forecast is in an unexpected shape: {exc!r}") from exc
            _check_hours(series, requests)
            by_cell[cell] = series
    return [replace(by_cell[cell], position=p) for cell, p in zip(requests.cells, requests.positions)]


def _check_hours(series: HourlySeries, requests: ForecastRequests) -> None:
    values = (series.wind_mph, series.gust_mph, series.rain_in_hr, series.snow_in_hr, series.temp_f)
    if not series.times or series.times[0] != requests.start or len(series.times) != requests.hours:
        raise WeatherError("A forecast covers other hours than the ones asked for")
    if any(len(v) != requests.hours for v in (*values, series.weather_code)):
        raise WeatherError("A forecast is missing values for some hours")


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def ceil_hour(dt: datetime) -> datetime:
    floored = floor_hour(dt)
    return floored if floored == dt.astimezone(timezone.utc) else floored + timedelta(hours=1)


def snap_cells(positions: list[LatLon], max_cells: int | None = None) -> list[LatLon]:
    """Each position's nearest point on the finest grid with at most `max_cells` distinct points.

    The grid is 0.1 degree, then 0.2, 0.25 and 0.5, then 1, 2, 4... until the points fit,
    which they always do in the end. A coarser grid is logged.
    """
    if max_cells is not None and max_cells < 1:
        raise ValueError("max_cells must be at least 1")
    counts = []  # distinct cells at each grid tried
    for step in _cell_steps():
        cells = [_snap(p, step) for p in positions]
        counts.append(len(set(cells)))
        if max_cells is None or counts[-1] <= max_cells:
            break
    if len(counts) > 1:
        log.info(
            "%d weather cells at %g deg is over %d, so using %g deg cells (%d)",
            counts[0], CELL_STEPS_DEG[0], max_cells, step, counts[-1],
        )
    return cells


def _cell_steps():
    yield from CELL_STEPS_DEG
    step = CELL_STEPS_DEG[-1]
    while True:
        step *= 2
        yield step


def _snap(p: LatLon, step: float) -> LatLon:
    return LatLon(_to_grid(p.lat, step), _to_grid(p.lon, step))


def _to_grid(value: float, step: float) -> float:
    return round(round(value / step) * step, GRID_DECIMALS)


def _params(chunk: list[LatLon], start: datetime, end: datetime) -> dict[str, str]:
    return {
        "latitude": ",".join(f"{p.lat:.4f}" for p in chunk),
        "longitude": ",".join(f"{p.lon:.4f}" for p in chunk),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
        "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": end.strftime("%Y-%m-%dT%H:%M"),
    }


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
        temp_f=_temperature_f(hourly["temperature_2m"], units.get("temperature_2m")),
        weather_code=[None if c is None else int(c) for c in hourly["weather_code"]],
    )


def _convert(values: list, unit: str | None, table: dict[str, float]) -> list[float]:
    if unit not in table:
        raise WeatherError(f"Unexpected unit {unit!r}")
    factor = table[unit]
    # Missing values (null) are treated as 0. Revisit if gaps show up in real responses.
    return [(v or 0.0) * factor for v in values]


def _temperature_f(values: list, unit: str | None) -> list[float | None]:
    if unit not in TEMP_TO_F:
        raise WeatherError(f"Unexpected unit {unit!r}")
    to_f = TEMP_TO_F[unit]
    # Unlike wind or rain, a missing temperature can't be read as 0, so it stays missing
    return [None if v is None else to_f(v) for v in values]


def sample_at(series: HourlySeries, when: datetime) -> WeatherSample:
    """Weather the truck meets at `when` (timezone-aware, any zone).

    - Wind is instantaneous: use the nearest forecast hour. Exactly half past
      goes to the later hour, the same hour rain and snow use, so at :30 every
      value comes from one forecast row.
    - Gusts are shown, not scored, so they come from the wind's hour and the
      two read together.
    - Temperature and the weather code are instantaneous too ("Instant" in
      Open-Meteo's docs), so they also use the nearest hour, the wind's.
    - Rain and snow are preceding-hour sums: use the first forecast hour >= when
      (the hour whose window contains `when`).
    - Clamp to the first/last hour if `when` is outside the series.
    forecast_time is the hour used for wind, gusts, temperature and the code.
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
        temp_f=series.temp_f[wind_i],
        weather_code=series.weather_code[wind_i],
    )


def _hour_index(times: list[datetime], hour: datetime) -> int:
    """Index of `hour` in the series, clamped to the first/last hour."""
    return min(bisect_left(times, hour), len(times) - 1)
