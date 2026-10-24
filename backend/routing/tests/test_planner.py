from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import re

import pytest

from routing.services import corridor, geometry, planner, tomtom, weather
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
    monkeypatch.setattr(corridor, "build_grid", lambda lines: [line[0] for line in lines])
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


def test_routes_are_lettered_in_ranked_order(faked):
    # The fastest-first input order is LOW, HIGH, MODERATE; ranking puts HIGH last.
    data = plan()
    routes = data["routes"]
    assert [r["label"] for r in routes] == ["Route A", "Route B", "Route C"]
    assert [r["rank"] for r in routes] == [1, 2, 3]
    assert [r["summary"]["max_level"] for r in routes] == ["LOW", "MODERATE", "HIGH"]
    assert data["recommended_route_id"] == routes[0]["id"]


def test_a_past_departure_starts_the_whole_plan_now(faked, monkeypatch):
    cdt = timezone(timedelta(hours=-5))
    asked = (datetime.now(timezone.utc) - timedelta(minutes=40)).astimezone(cdt)
    sent = []

    def calculate_routes(origin, destination, departure, *args, **kwargs):
        sent.append(departure)
        return list(faked)

    monkeypatch.setattr(tomtom, "calculate_routes", calculate_routes)
    before = datetime.now(timezone.utc).replace(microsecond=0)
    data = plan(departure_time=asked)
    start = datetime.fromisoformat(data["input"]["departure_time"])
    assert before <= start <= datetime.now(timezone.utc)
    assert start.utcoffset() == timedelta(hours=-5)  # still shown in the origin's offset
    assert sent == [start]
    for route in data["routes"]:
        assert datetime.fromisoformat(route["departure_time"]) == start
        assert datetime.fromisoformat(route["checkpoints"][0]["eta"]) == start
        assert route["arrival_time"] == route["checkpoints"][-1]["eta"]
    heatmap = planner.build_heatmap(data["plan_id"])
    assert datetime.fromisoformat(heatmap["start_time"]) == weather.floor_hour(start)


def test_a_future_departure_is_used_as_given(faked, monkeypatch):
    # TomTom's own departure for a fallback can be a few seconds off; the plan's wins.
    late = [replace(r, departure_time=T0 + timedelta(seconds=7)) for r in faked]
    monkeypatch.setattr(tomtom, "ensure_three_routes", lambda *a, **k: late)
    asked = T0.astimezone(timezone(timedelta(hours=-6)))
    data = plan(departure_time=asked)
    assert data["input"]["departure_time"] == asked.isoformat()
    for route in data["routes"]:
        assert route["departure_time"] == asked.isoformat()
        assert route["checkpoints"][0]["eta"] == asked.isoformat()
        arrival = datetime.fromisoformat(route["arrival_time"])
        assert abs(arrival - (asked + timedelta(seconds=route["duration_s"]))) < timedelta(seconds=1)
    heatmap = planner.build_heatmap(data["plan_id"])
    assert datetime.fromisoformat(heatmap["start_time"]) == T0


def test_plan_and_heatmap_log_how_long_each_step_took(faked, caplog):
    data = plan()
    planner.build_heatmap(data["plan_id"])
    lines = [r.getMessage() for r in caplog.records if r.name == "routing.services.planner"]
    assert len(lines) == 2
    plan_line, heatmap_line = lines
    assert plan_line.startswith(f"Plan {data['plan_id']}: 3 routes, ")
    steps = ("routing", "fallback routes", "checkpoints", "weather", "risk + ranking", "simplify")
    timings = ", ".join(rf"{re.escape(step)} \d+\.\d\d s" for step in steps)
    assert re.search(rf" s total \({timings}\)$", plan_line)
    assert heatmap_line.startswith(f"Heatmap {data['plan_id']}: 3 points, ")
    assert re.search(r" s total \(grid \d+\.\d\d s, weather \d+\.\d\d s, risk \d+\.\d\d s\)$", heatmap_line)


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
