"""A whole trip through the API: the real TomTom and Open-Meteo clients, route fallbacks,
checkpoints, weather sampling, risk, ranking and the heatmap grid, talking to fake servers."""

from datetime import datetime, timedelta
import json
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
import pytest

from routing.services import recommend, risk, tomtom, weather
from routing.services.types import Level, RouteSummary
from routing.tests.fakes import (
    FakeUpstream,
    install_fake_http,
    upstream_rain_in_hr,
    upstream_temp_f,
    upstream_town,
    upstream_wind_mph,
)

DALLAS = {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797}
DENVER = {"label": "Denver, CO", "lat": 39.7392, "lon": -104.9903}
LOAD_LB = 42_000
NEARBY_DEG = (-0.1, 0.0, 0.1)  # the weather client may round coordinates before asking


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def upstream(monkeypatch):
    fake = FakeUpstream()
    install_fake_http(monkeypatch, fake)
    return fake


def post_plan(api, departure: str):
    body = {
        "origin": DALLAS,
        "destination": DENVER,
        "departure_time": departure,
        "load_weight_lb": LOAD_LB,
        "interval_miles": 25,
    }
    return api.post("/api/plan", body, format="json")


def route_requests(upstream: FakeUpstream) -> list:
    return [c for c in upstream.calls("api.tomtom.com") if c.url.path.startswith("/routing/")]


def place_requests(upstream: FakeUpstream) -> list:
    return [c for c in upstream.calls("api.tomtom.com") if c.url.path.startswith("/search/2/reverseGeocode/")]


def stops(request) -> list[str]:
    """The places a TomTom route request goes through, as "lat,lon"."""
    return unquote(request.url.path.split("/")[4]).split(":")


def levels_near(lat: float, lon: float) -> set[Level]:
    """Risk levels the fake weather gives within NEARBY_DEG of a point, at the trip's load."""
    return {
        risk.assess(upstream_wind_mph(lon + dx), upstream_rain_in_hr(lat + dy), 0, LOAD_LB).level
        for dx in NEARBY_DEG
        for dy in NEARBY_DEG
    }


def tomorrow_in_dallas() -> datetime:
    local = (timezone.now() + timedelta(days=1)).astimezone(ZoneInfo("America/Chicago"))
    return local.replace(minute=0, second=0, microsecond=0)


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_dallas_to_denver_plan_and_heatmap(api, upstream):
    leave = tomorrow_in_dallas()
    res = post_plan(api, leave.strftime("%Y-%m-%dT%H:%M"))  # local time, no offset
    assert res.status_code == 200, res.json()
    plan = res.json()

    assert plan["input"]["timezone"] == "America/Chicago"
    assert datetime.fromisoformat(plan["input"]["departure_time"]) == leave
    assert plan["input"]["departure_time"] == leave.isoformat()

    # TomTom was asked for a truck route with alternatives. It had just the one, so the top-up
    # asked for the best route round the middle of it, then round the middle of both.
    calls = route_requests(upstream)
    trip, *fallbacks = calls
    assert trip.method == "GET"
    assert trip.url.params["travelMode"] == "truck"
    assert trip.url.params["maxAlternatives"] == "2"
    assert trip.url.params["vehicleWeight"] == str(tomtom.vehicle_weight_kg(LOAD_LB))
    assert [c.method for c in fallbacks] == ["POST", "POST"]
    assert [len(json.loads(c.content)["avoidAreas"]["rectangles"]) for c in fallbacks] == [10, 10]
    assert all(stops(c) == stops(trip) for c in fallbacks)
    # Then once per place a stretch starts in, for the journey report
    towns = place_requests(upstream)
    assert 0 < len(towns) <= tomtom.MAX_PLACE_LOOKUPS
    assert len({t.url.path for t in towns}) == len(towns)

    routes = plan["routes"]
    assert len(routes) == 3
    assert sorted(r["id"] for r in routes) == ["r0", "r1", "r2"]
    assert [r["label"] for r in routes] == ["Route A", "Route B", "Route C"]
    assert [r["rank"] for r in routes] == [1, 2, 3]
    assert plan["recommended_route_id"] == routes[0]["id"]
    assert {r["source"] for r in routes} == {"tomtom"}

    for route in routes:
        start, end = route["geometry"][0], route["geometry"][-1]
        assert start == pytest.approx([DALLAS["lon"], DALLAS["lat"]], abs=1e-4)
        assert end == pytest.approx([DENVER["lon"], DENVER["lat"]], abs=1e-4)
        assert len(route["geometry"]) < 100  # simplified from a point every 500 m, ~2,200 of them
        assert route["departure_time"] == plan["input"]["departure_time"]

        cps = route["checkpoints"]
        assert cps[0]["eta"] == plan["input"]["departure_time"]
        assert cps[-1]["eta"] == route["arrival_time"]
        assert [cp["index"] for cp in cps] == list(range(len(cps)))
        assert all(a["mile"] < b["mile"] for a, b in zip(cps, cps[1:]))
        assert all(a["eta"] < b["eta"] for a, b in zip(cps, cps[1:]))
        assert sum(cp["segment_miles"] for cp in cps) == pytest.approx(route["distance_miles"], abs=0.5)
        for cp in cps:
            w = cp["weather"]
            # Each checkpoint got the weather for its own place, give or take the rounding of
            # coordinates before Open-Meteo is asked
            assert w["wind_mph"] == pytest.approx(upstream_wind_mph(cp["lon"]), abs=1.0)
            assert w["rain_in_hr"] in {upstream_rain_in_hr(cp["lat"] + d) for d in NEARBY_DEG}
            assert w["temp_f"] == pytest.approx(upstream_temp_f(cp["lat"]), abs=0.3)
            # The fake's code is heavy rain wherever it rains, read from the same cell as the rain
            shown = (w["weather_code"], w["conditions"])
            assert shown == ((65, "Heavy rain") if w["rain_in_hr"] else (1, "Mostly clear"))
            level = risk.assess(w["wind_mph"], w["rain_in_hr"], w["snow_in_hr"], LOAD_LB).level
            assert cp["risk"]["level"] == level.name
        miles = route["summary"]["miles_by_level"]
        assert sum(miles.values()) == pytest.approx(route["distance_miles"], abs=0.5)

        # The report: leaving Dallas, every run of Moderate-or-worse checkpoints, arriving in Denver
        journey = route["report"]
        assert (journey["departure"]["place"], journey["arrival"]["place"]) == ("Dallas, TX", "Denver, CO")
        assert journey["departure"]["weather"] == cps[0]["weather"]
        assert journey["arrival"]["weather"] == cps[-1]["weather"]
        stretches = journey["stretches"]
        assert stretches, "the fake weather has wind and rain to report on every route"
        bad = [cp["index"] for cp in cps if cp["risk"]["level"] != "LOW"]
        assert [i for s in stretches for i in range(s["first_index"], s["last_index"] + 1)] == bad
        not_low = sum(v for level, v in miles.items() if level != "LOW")
        assert sum(s["miles"] for s in stretches) == pytest.approx(not_low, abs=0.1 * len(stretches))
        for s in stretches:
            start = cps[s["first_index"]]
            assert s["where"] == f"near {upstream_town(round(start['lat'], 1), round(start['lon'], 1))}"

    keys = [
        recommend.ranking_key(
            RouteSummary(
                route_id=r["id"],
                distance_miles=r["distance_miles"],
                duration_s=r["duration_s"],
                miles_by_level={Level[k]: v for k, v in r["summary"]["miles_by_level"].items()},
                avg_risk=r["summary"]["avg_risk"],
            )
        )
        for r in routes
    ]
    assert keys == sorted(keys)

    res = api.get(f"/api/heatmap?plan_id={plan['plan_id']}")
    assert res.status_code == 200, res.json()
    heatmap = res.json()
    assert heatmap["plan_id"] == plan["plan_id"]
    assert heatmap["hours"] == 48
    assert datetime.fromisoformat(heatmap["start_time"]) == weather.floor_hour(leave)
    assert 0 < len(heatmap["points"]) <= 250
    for p in heatmap["points"]:
        assert len(p["v"]) == 49
        assert len(set(p["v"])) == 1  # the fake weather is the same every hour
        assert p["v"][0] in {int(level) for level in levels_near(p["lat"], p["lon"])}

    # Every request went to one of the two fakes; anything else would have raised
    assert {r.url.host for r in upstream.requests} == {"api.tomtom.com", "api.open-meteo.com"}


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_a_second_plan_takes_its_place_names_from_the_cache(api, upstream):
    leave = tomorrow_in_dallas().strftime("%Y-%m-%dT%H:%M")
    first = post_plan(api, leave).json()
    asked = len(place_requests(upstream))
    assert asked > 0
    second = post_plan(api, leave).json()
    assert len(place_requests(upstream)) == asked
    names = [[s["where"] for s in r["report"]["stretches"]] for r in first["routes"]]
    assert [[s["where"] for s in r["report"]["stretches"]] for r in second["routes"]] == names
    assert any(where.startswith("near ") for route in names for where in route)


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_the_plan_still_works_when_place_names_fail(api, upstream):
    upstream.no_place_names = True
    res = post_plan(api, tomorrow_in_dallas().strftime("%Y-%m-%dT%H:%M"))
    assert res.status_code == 200, res.json()
    assert place_requests(upstream)
    stretches = [s for route in res.json()["routes"] for s in route["report"]["stretches"]]
    assert stretches
    assert [s["where"] for s in stretches] == [f"at mile {round(s['start_mile'])}" for s in stretches]


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_no_truck_route_through_the_real_client_is_a_422(api, upstream):
    upstream.no_route = True
    res = post_plan(api, tomorrow_in_dallas().strftime("%Y-%m-%dT%H:%M"))
    assert res.status_code == 422
    assert res.json() == {"detail": "No truck route found between these places."}


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_no_route_for_the_trip_is_a_422_without_asking_for_routes_round_it(api, upstream):
    upstream.no_direct_route = True
    res = post_plan(api, tomorrow_in_dallas().strftime("%Y-%m-%dT%H:%M"))
    assert res.status_code == 422
    assert res.json() == {"detail": "No truck route found between these places."}
    assert [c.method for c in upstream.calls("api.tomtom.com")] == ["GET"]
