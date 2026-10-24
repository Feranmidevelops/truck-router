from datetime import timedelta

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.settings import api_settings as drf_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
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


@override_settings(USE_FIXTURES=True, REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 2})
def test_behind_render_the_limit_follows_the_visitor_not_cloudflare(api):
    # On Render the header reads "<client-sent...>, <visitor>, <Cloudflare edge>". The visitor
    # is 2 from the end; anything further left is whatever the client chose to send.
    def via_render(forwarded_for):
        return post_plan(api, REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR=forwarded_for)

    for i in range(10):
        via_render(f"1.2.3.{i}, 198.51.100.7, 172.70.{i % 3}.10")
    assert via_render("9.9.9.9, 198.51.100.7, 172.70.0.10").status_code == 429
    # A different visitor through the same Cloudflare edge has a limit of their own.
    assert via_render("198.51.100.8, 172.70.0.10").status_code == 200


@override_settings(USE_FIXTURES=True)
def test_plan_limit_does_not_block_geocode(api):
    for _ in range(11):
        post_plan(api)
    assert api.get("/api/geocode?q=dallas").status_code == 200


def test_health_is_never_throttled(api):
    busiest = max(int(rate.split("/")[0]) for rate in drf_settings.DEFAULT_THROTTLE_RATES.values())
    for _ in range(busiest + 10):
        assert api.get("/api/health").status_code == 200
