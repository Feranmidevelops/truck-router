from datetime import timedelta

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.settings import api_settings as drf_settings
from rest_framework.test import APIClient
import pytest

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


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="")
def test_geocode_without_key_is_503(api):
    assert api.get("/api/geocode?q=dallas").status_code == 503


def post_plan(api, **extra):
    return api.post("/api/plan", plan_body(), format="json", **extra)


@override_settings(USE_FIXTURES=True)
def test_eleventh_plan_in_a_minute_is_throttled(api):
    for _ in range(10):
        assert post_plan(api).status_code == 200
    res = post_plan(api)
    assert res.status_code == 429
    assert res.json()["detail"].startswith("Request was throttled.")
    assert 0 < int(res["Retry-After"]) <= 60


@override_settings(USE_FIXTURES=True)
@pytest.mark.parametrize(
    "path, limit",
    [("/api/geocode?q=dallas", 120), ("/api/heatmap?plan_id=fixture", 30)],
)
def test_geocode_and_heatmap_allow_more_than_plan(api, path, limit):
    for _ in range(limit):
        assert api.get(path).status_code == 200
    assert api.get(path).status_code == 429


@override_settings(USE_FIXTURES=True)
def test_each_ip_has_its_own_plan_limit(api):
    for _ in range(10):
        post_plan(api, REMOTE_ADDR="203.0.113.1")
    assert post_plan(api, REMOTE_ADDR="203.0.113.1").status_code == 429
    assert post_plan(api, REMOTE_ADDR="203.0.113.2").status_code == 200


@override_settings(USE_FIXTURES=True)
def test_without_a_proxy_forwarded_for_is_ignored(api):
    for i in range(10):
        post_plan(api, REMOTE_ADDR="203.0.113.1", HTTP_X_FORWARDED_FOR=f"1.2.3.{i}")
    res = post_plan(api, REMOTE_ADDR="203.0.113.1", HTTP_X_FORWARDED_FOR="1.2.3.99")
    assert res.status_code == 429


@override_settings(USE_FIXTURES=True, REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1})
def test_behind_one_proxy_the_limit_follows_the_last_forwarded_address(api):
    # The proxy appends the address it saw, so only the last entry can be trusted. Everything
    # before it is whatever the client chose to send.
    def via_proxy(forwarded_for):
        return post_plan(api, REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR=forwarded_for)

    for i in range(10):
        via_proxy(f"1.2.3.{i}, 198.51.100.7")
    assert via_proxy("9.9.9.9, 198.51.100.7").status_code == 429
    assert via_proxy("198.51.100.8").status_code == 200


@override_settings(USE_FIXTURES=True)
def test_plan_limit_does_not_block_geocode(api):
    for _ in range(11):
        post_plan(api)
    assert api.get("/api/geocode?q=dallas").status_code == 200


def test_health_is_never_throttled(api):
    busiest = max(int(rate.split("/")[0]) for rate in drf_settings.DEFAULT_THROTTLE_RATES.values())
    for _ in range(busiest + 10):
        assert api.get("/api/health").status_code == 200
