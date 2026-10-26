from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re

import httpx
import pytest

from routing.services import corridor, geometry, planner, tomtom, weather
from routing.services.types import LatLon
from routing.tests import fakes
from routing.tests.fakes import T0, heatmap, install_service_fakes, plan, wiggly_route, wind_at


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
    grid = heatmap(data["plan_id"])
    assert datetime.fromisoformat(grid["start_time"]) == weather.floor_hour(start)


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
    assert datetime.fromisoformat(heatmap(data["plan_id"])["start_time"]) == T0


def test_the_first_and_last_checkpoints_are_the_two_places_when_the_truck_is_there(faked):
    # The frontend reads what the origin and destination are like from these two checkpoints
    for route in plan()["routes"]:
        first, last = route["checkpoints"][0], route["checkpoints"][-1]
        assert [first["lon"], first["lat"]] == route["geometry"][0]
        assert [last["lon"], last["lat"]] == route["geometry"][-1]
        assert first["eta"] == route["departure_time"]
        assert last["eta"] == route["arrival_time"]
        for cp in (first, last):
            weather_shown = {k: cp["weather"][k] for k in ("temp_f", "conditions", "weather_code")}
            assert weather_shown == {"temp_f": 60.0, "conditions": "Partly cloudy", "weather_code": 2}


def every_hour_shows(monkeypatch, temp_f, weather_code):
    """The faked forecast, with this temperature and weather code every hour."""
    read = weather.read_forecasts

    def read_forecasts(*args, **kwargs):
        series = read(*args, **kwargs)
        hours = len(series[0].times)
        return [replace(s, temp_f=[temp_f] * hours, weather_code=[weather_code] * hours) for s in series]

    monkeypatch.setattr(weather, "read_forecasts", read_forecasts)


def test_a_missing_temperature_and_weather_code_are_sent_as_null_and_unknown(faked, monkeypatch):
    every_hour_shows(monkeypatch, None, None)
    for cp in plan()["routes"][0]["checkpoints"]:
        assert (cp["weather"]["temp_f"], cp["weather"]["weather_code"]) == (None, None)
        assert cp["weather"]["conditions"] == "Unknown"


def test_temperature_and_conditions_do_not_change_the_risk(faked, monkeypatch):
    calm = plan()
    every_hour_shows(monkeypatch, -20.0, 75)
    cold = plan()
    for a, b in zip(calm["routes"], cold["routes"]):
        assert [cp["risk"] for cp in a["checkpoints"]] == [cp["risk"] for cp in b["checkpoints"]]
        assert a["summary"] == b["summary"]
        assert {cp["weather"]["conditions"] for cp in b["checkpoints"]} == {"Heavy snow"}


def test_every_route_has_a_report_from_departure_to_arrival(faked):
    data = plan()
    for route in data["routes"]:
        cps, journey = route["checkpoints"], route["report"]
        assert journey["departure"] == {
            "place": "Dallas, TX",
            "time": route["departure_time"],
            "level": cps[0]["risk"]["level"],
            "weather": cps[0]["weather"],
        }
        assert journey["arrival"] == {
            "place": "Up the road",
            "time": route["arrival_time"],
            "level": cps[-1]["risk"]["level"],
            "weather": cps[-1]["weather"],
        }


def test_report_stretches_follow_the_checkpoints(faked):
    # The fake wind is the same along each whole route: LOW on one, MODERATE and HIGH on the others
    low, moderate, high = plan()["routes"]
    assert low["report"]["stretches"] == []
    for route, level in ((moderate, "MODERATE"), (high, "HIGH")):
        cps = route["checkpoints"]
        [stretch] = route["report"]["stretches"]
        assert (stretch["first_index"], stretch["last_index"]) == (0, len(cps) - 1)
        assert (stretch["start_mile"], stretch["end_mile"]) == (0.0, cps[-1]["mile"])
        assert (stretch["start_eta"], stretch["end_eta"]) == (cps[0]["eta"], cps[-1]["eta"])
        assert stretch["miles"] == pytest.approx(route["distance_miles"], abs=0.1)
        assert stretch["miles"] == route["summary"]["miles_by_level"][level]
        assert (stretch["level"], stretch["drivers"]) == (level, ["wind"])
        assert stretch["max_wind_mph"] == cps[0]["weather"]["wind_mph"]
        assert stretch["max_gust_mph"] == cps[0]["weather"]["gust_mph"]
        assert (stretch["max_rain_in_hr"], stretch["max_snow_in_hr"]) == (0.0, 0.0)
        assert (stretch["min_temp_f"], stretch["max_temp_f"]) == (60.0, 60.0)


def test_stretches_are_named_after_the_town_near_their_start(faked):
    for route in plan()["routes"]:
        for s in route["report"]["stretches"]:
            start = route["checkpoints"][s["first_index"]]
            assert s["where"] == f"near Town {start['lat']:.1f} {start['lon']:.1f}"


def test_place_names_are_asked_for_once_per_plan_in_rank_order(faked, monkeypatch):
    asked = []

    def place_names(positions, api_key, **kwargs):
        asked.append(positions)
        return [None] * len(positions)  # e.g. every lookup failed

    monkeypatch.setattr(tomtom, "place_names", place_names)
    data = plan()
    [positions] = asked
    starts = [
        LatLon(route["checkpoints"][s["first_index"]]["lat"], route["checkpoints"][s["first_index"]]["lon"])
        for route in data["routes"]
        for s in route["report"]["stretches"]
    ]
    assert [LatLon(round(p.lat, 5), round(p.lon, 5)) for p in positions] == starts
    for route in data["routes"]:
        assert [s["where"] for s in route["report"]["stretches"]] == [
            f"at mile {round(s['start_mile'])}" for s in route["report"]["stretches"]
        ]


def timings(*steps: str) -> str:
    return ", ".join(rf"{re.escape(step)} \d+\.\d\d s" for step in steps)


def test_plan_and_heatmap_log_how_long_each_step_took(faked, caplog):
    data = plan()
    heatmap(data["plan_id"])
    lines = [r.getMessage() for r in caplog.records if r.name == "routing.services.planner"]
    assert len(lines) == 3
    started_line, plan_line, heatmap_line = lines
    assert re.fullmatch(
        rf"Plan {data['plan_id']} started: 3 routes, \d+ checkpoints, \d+ forecast locations, "
        rf"\d+\.\d\d s total \({timings('routing', 'fallback routes', 'checkpoints', 'simplify')}\)",
        started_line,
    )
    assert plan_line.startswith(f"Plan {data['plan_id']}: 3 routes, ")
    assert re.search(rf" s total \({timings('weather', 'risk + ranking', 'place names')}\)$", plan_line)
    assert heatmap_line.startswith(f"Heatmap {data['plan_id']}: 3 points, ")
    assert re.search(rf" s total \({timings('grid', 'weather', 'risk')}\)$", heatmap_line)


def test_a_started_plan_lists_one_open_meteo_url_per_chunk_of_its_checkpoint_cells(faked):
    started = planner.start_plan(
        origin=fakes.DALLAS,
        destination=fakes.NORTH,
        departure_time=T0,
        timezone="America/Chicago",
        load_weight_lb=20_000,
        interval_miles=10,
    )
    assert set(started) == {"plan_id", "forecast_urls"}
    asked = [
        (lat, lon)
        for url in started["forecast_urls"]
        for lat, lon in zip(
            httpx.URL(url).params["latitude"].split(","), httpx.URL(url).params["longitude"].split(",")
        )
    ]
    assert all(url.startswith(weather.BASE_URL + "?") for url in started["forecast_urls"])
    assert len(started["forecast_urls"]) == -(-len(asked) // weather.CHUNK_SIZE)
    assert len(asked) == len(set(asked))
    # Every checkpoint of the finished plan is within half a cell of a location asked for
    data = planner.finish_plan(started["plan_id"], [{}] * len(started["forecast_urls"]))
    for route in data["routes"]:
        for cp in route["checkpoints"]:
            assert any(
                abs(float(lat) - cp["lat"]) <= 0.05 + 1e-6 and abs(float(lon) - cp["lon"]) <= 0.05 + 1e-6
                for lat, lon in asked
            )


def test_finishing_or_mapping_a_plan_nobody_started_is_plan_not_found(faked):
    with pytest.raises(planner.PlanNotFound):
        planner.finish_plan("0" * 32, [])
    with pytest.raises(planner.PlanNotFound):
        planner.heatmap_request("0" * 32)
    with pytest.raises(planner.PlanNotFound):
        planner.build_heatmap("0" * 32, [])
    # A started plan has no heatmap until it's finished
    started = planner.start_plan(
        origin=fakes.DALLAS,
        destination=fakes.NORTH,
        departure_time=T0,
        timezone="America/Chicago",
        load_weight_lb=20_000,
        interval_miles=25,
    )
    with pytest.raises(planner.PlanNotFound):
        planner.heatmap_request(started["plan_id"])


def test_a_plan_can_be_finished_again_with_the_same_answer(faked):
    # e.g. the browser's first post was cut off and it tries again
    started = planner.start_plan(
        origin=fakes.DALLAS,
        destination=fakes.NORTH,
        departure_time=T0,
        timezone="America/Chicago",
        load_weight_lb=20_000,
        interval_miles=25,
    )
    replies = [{}] * len(started["forecast_urls"])
    first = planner.finish_plan(started["plan_id"], replies)
    assert planner.finish_plan(started["plan_id"], replies) == first


def test_a_finished_plan_is_not_worked_out_again_from_other_forecasts(faked, monkeypatch):
    # Made-up replies sent again and again must not mean more place-name lookups each time
    started = planner.start_plan(
        origin=fakes.DALLAS,
        destination=fakes.NORTH,
        departure_time=T0,
        timezone="America/Chicago",
        load_weight_lb=20_000,
        interval_miles=25,
    )
    lookups, reads = [], []

    def place_names(positions, *args, **kwargs):
        lookups.append(positions)
        return [None] * len(positions)

    monkeypatch.setattr(tomtom, "place_names", place_names)
    read = weather.read_forecasts
    monkeypatch.setattr(weather, "read_forecasts", lambda *a, **k: reads.append(1) or read(*a, **k))
    first = planner.finish_plan(started["plan_id"], [{}] * len(started["forecast_urls"]))
    assert planner.finish_plan(started["plan_id"], ["made up"]) == first
    assert (len(lookups), len(reads)) == (1, 1)


def test_a_failed_finish_can_be_tried_again(faked, monkeypatch):
    started = planner.start_plan(
        origin=fakes.DALLAS,
        destination=fakes.NORTH,
        departure_time=T0,
        timezone="America/Chicago",
        load_weight_lb=20_000,
        interval_miles=25,
    )
    read = weather.read_forecasts

    def refuse(*args, **kwargs):
        raise weather.WeatherError("Expected 1 forecast replies, got 0")

    monkeypatch.setattr(weather, "read_forecasts", refuse)
    with pytest.raises(weather.WeatherError):
        planner.finish_plan(started["plan_id"], [])
    monkeypatch.setattr(weather, "read_forecasts", read)
    assert planner.finish_plan(started["plan_id"], [{}] * len(started["forecast_urls"]))["routes"]


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
    planner.heatmap_request(data["plan_id"])
    assert seen == [geometry.simplify(r.points, planner.SIMPLIFY_TOLERANCE_M) for r in faked]


def test_a_plan_and_its_heatmap_fit_open_meteos_minute_together(faked, monkeypatch):
    asked = []
    list_requests = weather.forecast_requests

    def forecast_requests(positions, start, end, **kwargs):
        asked.append(kwargs.get("max_cells"))
        return list_requests(positions, start, end, **kwargs)

    monkeypatch.setattr(weather, "forecast_requests", forecast_requests)
    planner.heatmap_request(plan()["plan_id"])
    # The heatmap's grid is capped at 250 points by corridor.build_grid itself
    assert asked == [planner.PLAN_WEATHER_CELLS, None]
    assert planner.PLAN_WEATHER_CELLS + 250 <= 600
