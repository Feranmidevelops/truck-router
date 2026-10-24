from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
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
