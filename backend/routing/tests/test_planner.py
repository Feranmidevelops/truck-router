from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re

import pytest

from routing.services import corridor, geometry, planner, tomtom, weather
from routing.services.types import LatLon
from routing.tests.fakes import T0, install_service_fakes, plan, wiggly_route, wind_at


@pytest.fixture
def faked(monkeypatch):
    """Every collaborator of build_plan replaced, so only the planner's own glue runs."""
    routes = [wiggly_route(k) for k in range(3)]
    install_service_fakes(monkeypatch, routes)
    return routes


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
