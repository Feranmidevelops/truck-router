from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math

import pytest

from routing.services import corridor, geometry, planner, tomtom, weather
from routing.services.geometry import METERS_PER_MILE, cumulative_m
from routing.services.types import LatLon, RouteOption, TimingPoint, WeatherSample

T0 = datetime(2026, 10, 26, 13, tzinfo=timezone.utc)
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
        LatLon(32.0 + i * step_m / M_PER_DEG_LAT, lon0 + (wiggle_m if i % 2 else -wiggle_m) / kx) for i in range(n)
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


@pytest.fixture
def routes():
    return [wiggly_route(k) for k in range(3)]


@pytest.fixture
def faked(monkeypatch, routes):
    """Every collaborator of build_plan replaced, so only the planner's own glue runs."""
    monkeypatch.setattr(tomtom, "calculate_routes", lambda *a, **k: list(routes))
    monkeypatch.setattr(tomtom, "ensure_three_routes", lambda found, *a, **k: found)
    monkeypatch.setattr(weather, "fetch_hourly", fake_fetch_hourly)
    monkeypatch.setattr(weather, "sample_at", fake_sample_at)
    return routes


def plan(**overrides):
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


def test_routes_get_unique_ids_whatever_the_routing_client_returns(faked, monkeypatch):
    clashing = [replace(r, id=i) for r, i in zip(faked, ["r0", "r0", "via15L-0"])]
    monkeypatch.setattr(tomtom, "ensure_three_routes", lambda *a, **k: clashing)
    data = plan()
    ids = [r["id"] for r in data["routes"]]
    assert sorted(ids) == ["r0", "r1", "r2"]
    assert data["recommended_route_id"] == ids[0]
    for route in data["routes"]:
        # Each route still has its own checkpoints: the fake wind follows the line's longitude
        lon = route["geometry"][0][0]
        assert {cp["weather"]["wind_mph"] for cp in route["checkpoints"]} == {wind_at(LatLon(0, lon))}


def test_response_lines_are_simplified(faked):
    data = plan()
    by_id = {r.id: r for r in faked}
    for route in data["routes"]:
        full = by_id[route["id"]].points
        assert len(full) > 2_000
        assert len(route["geometry"]) < 10
        assert route["geometry"][0] == [round(full[0].lon, 5), round(full[0].lat, 5)]
        assert route["geometry"][-1] == [round(full[-1].lon, 5), round(full[-1].lat, 5)]


def test_checkpoints_are_sampled_on_the_full_line(faked):
    # The zig-zag makes the full line ~0.4% longer than the simplified one, so sampling the
    # simplified line would move the destination checkpoint by ~0.4 mile.
    data = plan()
    by_id = {r.id: r for r in faked}
    for route in data["routes"]:
        r = by_id[route["id"]]
        expected = geometry.sample_checkpoints(r.points, r.timing, r.departure_time, 25)
        assert [cp["mile"] for cp in route["checkpoints"]] == [round(c.mile, 1) for c in expected]
        assert route["checkpoints"][-1]["mile"] > 110.3


def test_heatmap_grid_is_built_from_the_simplified_lines(faked, monkeypatch):
    data = plan()
    seen = []

    def fake_grid(lines):
        seen.extend(lines)
        return [lines[0][0]]

    monkeypatch.setattr(corridor, "build_grid", fake_grid)
    planner.build_heatmap(data["plan_id"])
    assert seen == [geometry.simplify(r.points, planner.SIMPLIFY_TOLERANCE_M) for r in faked]
