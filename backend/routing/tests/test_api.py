from datetime import timedelta
import gzip
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.settings import api_settings as drf_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
import pytest

from routing.services import planner, timezones, tomtom, weather
from routing.views import load_fixture

PLACE_A = {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797}
PLACE_B = {"label": "Denver, CO", "lat": 39.7392, "lon": -104.9903}


def plan_body(**overrides):
    body = {
        "origin": PLACE_A,
        "destination": PLACE_B,
        "departure_time": (timezone.now() + timedelta(hours=2)).isoformat(),
        "load_weight_lb": 42_000,
        "interval_miles": 25,
    }
    body.update(overrides)
    return body


@pytest.fixture
def api():
    return APIClient()


def test_health(api):
    assert api.get("/api/health").json()["ok"] is True


@override_settings(USE_FIXTURES=True)
def test_plan_fixture_matches_contract(api):
    res = api.post("/api/plan", plan_body(), format="json")
    assert res.status_code == 200
    data = res.json()
    assert len(data["routes"]) == 3
    assert data["recommended_route_id"] == data["routes"][0]["id"]
    cp = data["routes"][0]["checkpoints"][0]
    assert set(cp) >= {"mile", "lat", "lon", "eta", "segment_miles", "weather", "risk"}


@override_settings(USE_FIXTURES=True)
def test_plan_is_gzipped_when_the_client_accepts_it(api):
    plain = api.post("/api/plan", plan_body(), format="json")
    assert "Content-Encoding" not in plain
    res = api.post("/api/plan", plan_body(), format="json", HTTP_ACCEPT_ENCODING="gzip, deflate, br")
    assert res.status_code == 200
    assert res["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in res["Vary"]
    assert len(res.content) < len(plain.content) / 3
    assert json.loads(gzip.decompress(res.content)) == plain.json()


@override_settings(USE_FIXTURES=True)
def test_heatmap_fixture_has_49_hours(api):
    data = api.get("/api/heatmap?plan_id=fixture").json()
    assert data["hours"] == 48
    assert all(len(p["v"]) == 49 for p in data["points"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"interval_miles": 30},
        {"load_weight_lb": -1},
        {"departure_time": (timezone.now() - timedelta(days=1)).isoformat()},
        {"departure_time": (timezone.now() + timedelta(days=9)).isoformat()},
        {"destination": PLACE_A},
    ],
)
def test_plan_rejects_bad_input(api, overrides):
    assert api.post("/api/plan", plan_body(**overrides), format="json").status_code == 400


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_plan_reads_a_departure_without_offset_as_local_time_at_the_origin(api, monkeypatch):
    seen = {}

    def fake_start_plan(**kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(planner, "start_plan", fake_start_plan)
    local = (timezone.now() + timedelta(hours=2)).astimezone(ZoneInfo("America/Chicago"))
    local = local.replace(second=0, microsecond=0)
    res = api.post("/api/plan", plan_body(departure_time=local.strftime("%Y-%m-%dT%H:%M")), format="json")
    assert res.status_code == 200
    assert seen["timezone"] == "America/Chicago"
    assert seen["departure_time"] == local
    assert seen["departure_time"].isoformat() == local.isoformat()


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_geocode_results_carry_their_time_zone(api, monkeypatch):
    found = [
        SimpleNamespace(label="Dallas, TX", lat=32.7767, lon=-96.797),
        SimpleNamespace(label="El Paso, TX", lat=31.7619, lon=-106.485),
    ]
    monkeypatch.setattr(tomtom, "geocode", lambda query, key, **kwargs: found)
    res = api.get("/api/geocode?q=tx")
    assert res.status_code == 200
    assert res.json()["results"] == [
        {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797, "timezone": "America/Chicago"},
        {"label": "El Paso, TX", "lat": 31.7619, "lon": -106.485, "timezone": "America/Denver"},
    ]


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_the_same_place_search_is_answered_from_the_cache(api, monkeypatch):
    asked = []

    def geocode(query, key, **kwargs):
        asked.append(query)
        return [SimpleNamespace(label="Salt Lake City, UT", lat=40.7608, lon=-111.891)]

    monkeypatch.setattr(tomtom, "geocode", geocode)
    first = api.get("/api/geocode?q=salt lake").json()
    # Case and extra spaces don't make it a different search
    again = api.get("/api/geocode?q=Salt%20%20Lake").json()
    assert first == again
    assert first["results"][0]["timezone"] == "America/Denver"
    assert asked == ["salt lake"]
    api.get("/api/geocode?q=salt lake city")
    assert asked == ["salt lake", "salt lake city"]


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_a_failed_place_search_is_not_cached(api, monkeypatch):
    monkeypatch.setattr(tomtom, "geocode", raise_(tomtom.RoutingError("TomTom 503")))
    assert api.get("/api/geocode?q=denver").status_code == 502
    monkeypatch.setattr(tomtom, "geocode", lambda query, key, **kwargs: [SimpleNamespace(label="Denver, CO", lat=39.74, lon=-104.99)])
    assert api.get("/api/geocode?q=denver").json()["results"][0]["label"] == "Denver, CO"


@override_settings(USE_FIXTURES=True)
def test_fixture_places_and_plan_carry_time_zones(api):
    for p in load_fixture("places.json"):
        assert p["timezone"] == timezones.zone_name(p["lat"], p["lon"])
    assert api.get("/api/geocode?q=dallas").json()["results"][0]["timezone"] == "America/Chicago"
    plan = post_plan(api).json()
    assert plan["input"]["timezone"] == "America/Chicago"
    assert plan["input"]["departure_time"].endswith("-05:00")
    # Shaped like a live plan: lettered in ranked order, times in the origin's offset
    assert [r["label"] for r in plan["routes"]] == ["Route A", "Route B", "Route C"]
    for route in plan["routes"]:
        assert route["departure_time"] == plan["input"]["departure_time"]
        assert route["checkpoints"][0]["eta"] == plan["input"]["departure_time"]
        assert route["arrival_time"] == route["checkpoints"][-1]["eta"]


def raise_(exc):
    def fail(*args, **kwargs):
        raise exc

    return fail


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="sekrit-key")
def test_plan_routing_failure_is_a_friendly_502(api, monkeypatch, caplog):
    exc = tomtom.RoutingError("TomTom request failed: timed out for ...?key=sekrit-key")
    monkeypatch.setattr(planner, "start_plan", raise_(exc))
    res = post_plan(api)
    assert res.status_code == 502
    assert res.json() == {"detail": "Couldn't get routes from TomTom right now. Try again in a minute."}
    errors = [r for r in caplog.records if r.name == "routing.views" and r.levelname == "ERROR"]
    assert [r.getMessage() for r in errors] == [
        "TomTom routing failed: TomTom request failed: timed out for ...?key=<TOMTOM_API_KEY>"
    ]
    assert "sekrit-key" not in caplog.text


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
@pytest.mark.parametrize(
    "calculate_routes",
    [raise_(tomtom.NoRouteError(tomtom.NO_ROUTE_MESSAGE)), lambda *a, **k: []],
    ids=["tomtom-says-so", "empty-list"],
)
def test_no_truck_route_is_a_422(api, monkeypatch, calculate_routes):
    monkeypatch.setattr(tomtom, "calculate_routes", calculate_routes)
    res = post_plan(api)
    assert res.status_code == 422
    assert res.json() == {"detail": "No truck route found between these places."}


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_other_tomtom_errors_are_not_mistaken_for_no_route(api, monkeypatch):
    monkeypatch.setattr(tomtom, "calculate_routes", raise_(tomtom.RoutingError("TomTom 403: Forbidden")))
    assert post_plan(api).status_code == 502


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_geocode_upstream_failure_is_a_friendly_502(api, monkeypatch, caplog):
    monkeypatch.setattr(tomtom, "geocode", raise_(tomtom.RoutingError("TomTom 429: Too Many Requests")))
    res = api.get("/api/geocode?q=dallas")
    assert res.status_code == 502
    assert res.json() == {"detail": "Couldn't search for places right now. Try again in a minute."}
    assert "TomTom search failed: TomTom 429: Too Many Requests" in caplog.text


PLAN_ID = "0123456789abcdef" * 2
FORECASTS_PATHS = ["/api/plan/forecasts", "/api/heatmap/forecasts"]
# Each second step and the planner function it hands the forecasts to
FINISHERS = [("/api/plan/forecasts", "finish_plan"), ("/api/heatmap/forecasts", "build_heatmap")]


def post_forecasts(api, path="/api/plan/forecasts", **body):
    return api.post(path, {"plan_id": PLAN_ID, "forecasts": [{}], **body}, format="json")


@override_settings(USE_FIXTURES=False)
@pytest.mark.parametrize("path", FORECASTS_PATHS)
def test_forecasts_for_a_plan_that_has_expired_are_a_404(api, path):
    res = post_forecasts(api, path)
    assert res.status_code == 404
    assert res.json() == {"detail": "Plan expired or not found. Plan the trip again."}


@override_settings(USE_FIXTURES=False)
def test_a_heatmap_for_a_plan_that_has_expired_is_a_404(api):
    res = api.get(f"/api/heatmap?plan_id={PLAN_ID}")
    assert res.status_code == 404
    assert res.json() == {"detail": "Plan expired or not found. Plan the trip again."}


@override_settings(USE_FIXTURES=False)
@pytest.mark.parametrize("path, finish", FINISHERS)
def test_forecasts_that_are_not_the_ones_asked_for_are_a_400(api, monkeypatch, caplog, path, finish):
    monkeypatch.setattr(planner, finish, raise_(weather.WeatherError("Expected 7 forecast replies, got 1")))
    res = post_forecasts(api, path)
    assert res.status_code == 400
    assert res.json() == {"detail": "Those aren't the forecasts this plan asked for. Plan the trip again."}
    assert f"Forecasts for {PLAN_ID} rejected: Expected 7 forecast replies, got 1" in caplog.text


@override_settings(USE_FIXTURES=False)
@pytest.mark.parametrize("path, finish", FINISHERS)
def test_forecasts_go_to_the_plan_they_were_listed_for(api, monkeypatch, path, finish):
    seen = []

    def finish_it(plan_id, replies):
        seen.append((plan_id, replies))
        return {"ok": 1}

    monkeypatch.setattr(planner, finish, finish_it)
    replies = [[{"hourly": {"time": []}}, {"hourly": {"time": []}}], {"hourly": {"time": []}}]
    res = post_forecasts(api, path, forecasts=replies)
    assert res.status_code == 200
    assert res.json() == {"ok": 1}
    assert seen == [(PLAN_ID, replies)]


@override_settings(USE_FIXTURES=False)
@pytest.mark.parametrize(
    "body",
    [
        {"plan_id": "../../etc/passwd"},
        {"plan_id": PLAN_ID.upper()},
        {"forecasts": {"0": {}}},
        {"forecasts": [{}] * 21},
        {"forecasts": None},
    ],
    ids=["path in the id", "not lowercase hex", "not a list", "too many", "null"],
)
def test_forecasts_with_a_bad_id_or_list_are_a_400(api, monkeypatch, body):
    monkeypatch.setattr(planner, "finish_plan", raise_(AssertionError("the planner was called")))
    res = post_forecasts(api, **body)
    assert res.status_code == 400
    assert set(res.json()) <= {"plan_id", "forecasts"}


@override_settings(USE_FIXTURES=False)
@pytest.mark.parametrize("hours", [150_000, 250_000], ids=["3 MB", "5 MB"])
def test_more_forecast_data_than_a_plan_needs_is_a_413(api, monkeypatch, caplog, hours):
    # 3 MB is over Django's own 2.5 MB limit, which would answer with an HTML 400 and log an error
    monkeypatch.setattr(planner, "finish_plan", raise_(AssertionError("the planner was called")))
    huge = [{"hourly": {"time": ["2026-10-24T12:00"] * hours}}]
    res = post_forecasts(api, forecasts=huge)
    assert res.status_code == 413
    assert res.json() == {"detail": "That's more forecast data than a plan needs."}
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


@override_settings(USE_FIXTURES=False)
def test_a_long_list_of_forecasts_is_refused_before_any_is_looked_at(api, monkeypatch):
    from rest_framework import serializers

    monkeypatch.setattr(serializers.ListField, "run_child_validation", raise_(AssertionError("items checked")))
    res = post_forecasts(api, forecasts=[[]] * 100_000)
    assert res.status_code == 400
    assert list(res.json()) == ["forecasts"]


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="")
def test_geocode_without_key_is_503(api):
    res = api.get("/api/geocode?q=dallas")
    assert res.status_code == 503
    assert res.json() == {"detail": "TOMTOM_API_KEY is not set."}


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="")
def test_plan_without_key_is_503_and_asks_tomtom_nothing(api, monkeypatch):
    monkeypatch.setattr(tomtom, "calculate_routes", raise_(AssertionError("TomTom was called")))
    res = post_plan(api)
    assert res.status_code == 503
    assert res.json() == {"detail": "TOMTOM_API_KEY is not set."}


def post_plan(api, **extra):
    return api.post("/api/plan", plan_body(), format="json", **extra)


def get_geocode(api):
    return api.get("/api/geocode?q=dallas")


def get_heatmap(api):
    return api.get("/api/heatmap?plan_id=fixture")


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    # Throttles read the time through SimpleRateThrottle.timer. A callable object is not bound
    # as a method the way a plain function would be, so it can stand in for time.time as is.
    fake = FakeClock()
    monkeypatch.setattr(SimpleRateThrottle, "timer", fake)
    return fake


@override_settings(USE_FIXTURES=True)
def test_eleventh_plan_in_a_minute_is_throttled(api, clock):
    for _ in range(10):
        assert post_plan(api).status_code == 200
    res = post_plan(api)
    assert res.status_code == 429
    assert res.json()["detail"].startswith("Request was throttled.")
    assert res["Retry-After"] == "60"


@override_settings(USE_FIXTURES=True)
@pytest.mark.parametrize(
    "send, limit",
    [(post_plan, 10), (get_geocode, 120), (get_heatmap, 30)],
    ids=["plan", "geocode", "heatmap"],
)
def test_limit_frees_up_a_minute_after_the_first_request(api, clock, send, limit):
    for _ in range(limit):
        assert send(api).status_code == 200
    res = send(api)
    assert res.status_code == 429
    assert res["Retry-After"] == "60"
    clock.now += 59
    assert send(api).status_code == 429
    clock.now += 1.5
    assert send(api).status_code == 200


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="")
def test_forecasts_have_their_own_limit_with_room_for_every_plan_and_heatmap(api, clock):
    # Both second steps share one limit of 40 a minute: 10 plans and 30 heatmaps
    for i in range(40):
        assert post_forecasts(api, FORECASTS_PATHS[i % 2]).status_code == 404
    res = post_forecasts(api)
    assert res.status_code == 429
    assert res["Retry-After"] == "60"
    assert post_plan(api).status_code == 503  # the first steps keep their own limits (no key: no TomTom call)


@override_settings(USE_FIXTURES=True)
def test_each_ip_has_its_own_plan_limit(api):
    for _ in range(10):
        post_plan(api, REMOTE_ADDR="203.0.113.1")
    assert post_plan(api, REMOTE_ADDR="203.0.113.1").status_code == 429
    assert post_plan(api, REMOTE_ADDR="203.0.113.2").status_code == 200


@override_settings(USE_FIXTURES=True, REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 0})
def test_without_a_proxy_forwarded_for_is_ignored(api):
    for i in range(10):
        post_plan(api, REMOTE_ADDR="203.0.113.1", HTTP_X_FORWARDED_FOR=f"1.2.3.{i}")
    res = post_plan(api, REMOTE_ADDR="203.0.113.1", HTTP_X_FORWARDED_FOR="1.2.3.99")
    assert res.status_code == 429


@override_settings(USE_FIXTURES=True, REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 3})
def test_behind_render_the_limit_follows_the_visitor_not_cloudflare(api):
    # A live request on Render read "<client-sent...>, <visitor>, <Cloudflare edge>,
    # <Render load balancer>", with the edge and balancer changing between requests. The visitor
    # is 3 from the end; anything further left is whatever the client chose to send.
    def via_render(forwarded_for):
        return post_plan(api, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=forwarded_for)

    for i in range(10):
        via_render(f"1.2.3.{i}, 198.51.100.7, 172.71.146.{i % 3}, 10.29.97.{i % 2}")
    assert via_render("9.9.9.9, 198.51.100.7, 104.22.76.134, 10.26.61.133").status_code == 429
    # A different visitor through the same edge and balancer has a limit of their own.
    assert via_render("198.51.100.8, 172.71.146.0, 10.29.97.0").status_code == 200


@override_settings(USE_FIXTURES=True)
def test_plan_limit_does_not_block_geocode(api):
    for _ in range(11):
        post_plan(api)
    assert api.get("/api/geocode?q=dallas").status_code == 200


def test_health_is_never_throttled(api):
    busiest = max(int(rate.split("/")[0]) for rate in drf_settings.DEFAULT_THROTTLE_RATES.values())
    for _ in range(busiest + 10):
        assert api.get("/api/health").status_code == 200
