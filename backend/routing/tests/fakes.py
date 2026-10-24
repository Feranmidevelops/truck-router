"""Stand-ins for TomTom and Open-Meteo, so planner tests never touch the network.

install_service_fakes replaces the routing and weather functions the planner calls, so only
the planner's own glue runs.
"""

from datetime import datetime, timedelta, timezone
import math

from routing.services import corridor, planner, tomtom, weather
from routing.services.geometry import METERS_PER_MILE, cumulative_m
from routing.services.types import LatLon, RouteOption, TimingPoint, WeatherSample

# In the future whenever the tests run, since a past departure is moved up to now
T0 = (datetime.now(timezone.utc) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
DALLAS = {"label": "Dallas, TX", "lat": 32.0, "lon": -97.0}
NORTH = {"label": "Up the road", "lat": 33.5, "lon": -97.0}
M_PER_DEG_LAT = 111_195
# Sustained wind on each fake route, picked by its longitude: LOW, HIGH, MODERATE
WIND_BY_ROUTE = [5.0, 36.0, 26.0]


def wiggly_route(k: int, miles: float = 110, step_m: float = 70, wiggle_m: float = 3) -> RouteOption:
    """A north-south route with a point every `step_m`, zig-zagging `wiggle_m` either side."""
    lon0 = -97.0 - 0.5 * k
    kx = M_PER_DEG_LAT * math.cos(math.radians(32))
    n = int(miles * METERS_PER_MILE / step_m) + 1
    pts = [
        LatLon(32.0 + i * step_m / M_PER_DEG_LAT, lon0 + (wiggle_m if i % 2 else -wiggle_m) / kx)
        for i in range(n)
    ]
    length = cumulative_m(pts)[-1]
    duration = length / METERS_PER_MILE / 60 * 3600  # 60 mph
    return RouteOption(
        id=f"r{k}",
        source="tomtom",
        distance_m=length,
        duration_s=duration,
        departure_time=T0,
        arrival_time=T0 + timedelta(seconds=duration),
        points=pts,
        timing=[TimingPoint(0, 0), TimingPoint(length, duration)],
    )


def wind_at(p: LatLon) -> float:
    return WIND_BY_ROUTE[min(2, max(0, round((-97.0 - p.lon) / 0.5)))]


def fake_fetch_hourly(positions, start, end, **kwargs):
    first = weather.floor_hour(start)
    hours = int((weather.ceil_hour(end) - first) / timedelta(hours=1)) + 1
    times = [first + timedelta(hours=h) for h in range(hours)]
    return [
        weather.HourlySeries(
            position=p,
            times=times,
            wind_mph=[wind_at(p)] * hours,
            gust_mph=[wind_at(p) + 10] * hours,
            rain_in_hr=[0.0] * hours,
            snow_in_hr=[0.0] * hours,
        )
        for p in positions
    ]


def fake_sample_at(series, when):
    return WeatherSample(
        wind_mph=series.wind_mph[0],
        gust_mph=series.gust_mph[0],
        rain_in_hr=series.rain_in_hr[0],
        snow_in_hr=series.snow_in_hr[0],
        forecast_time=weather.floor_hour(when),
    )


def install_service_fakes(monkeypatch, routes: list[RouteOption]) -> None:
    monkeypatch.setattr(tomtom, "calculate_routes", lambda *a, **k: list(routes))
    monkeypatch.setattr(tomtom, "ensure_three_routes", lambda found, *a, **k: found)
    monkeypatch.setattr(weather, "fetch_hourly", fake_fetch_hourly)
    monkeypatch.setattr(weather, "sample_at", fake_sample_at)
    monkeypatch.setattr(corridor, "build_grid", lambda lines: [line[0] for line in lines])


def plan(**overrides) -> dict:
    kwargs = {
        "origin": DALLAS,
        "destination": NORTH,
        "departure_time": T0,
        "timezone": "America/Chicago",
        "load_weight_lb": 20_000,
        "interval_miles": 25,
    }
    kwargs.update(overrides)
    return planner.build_plan(**kwargs)
