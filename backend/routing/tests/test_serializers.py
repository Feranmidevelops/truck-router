from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone
import pytest

from routing.serializers import PlanRequestSerializer

DALLAS = {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797}
DENVER = {"label": "Denver, CO", "lat": 39.7392, "lon": -104.9903}
TOKYO = {"label": "Tokyo", "lat": 35.6762, "lon": 139.6503}  # UTC+9
OSAKA = {"label": "Osaka", "lat": 34.6937, "lon": 135.5023}
HONOLULU = {"label": "Honolulu, HI", "lat": 21.3069, "lon": -157.8583}  # UTC-10
HILO = {"label": "Hilo, HI", "lat": 19.7241, "lon": -155.0868}


def body(departure: str, origin=DALLAS, destination=DENVER) -> dict:
    return {
        "origin": origin,
        "destination": destination,
        "departure_time": departure,
        "load_weight_lb": 42_000,
        "interval_miles": 25,
    }


def naive(dt: datetime) -> str:
    """Wall-clock time without an offset, the way a datetime-local input sends it."""
    return dt.replace(tzinfo=None).isoformat(timespec="minutes")


def validate(data: dict) -> PlanRequestSerializer:
    s = PlanRequestSerializer(data=data)
    s.is_valid()
    return s


@pytest.fixture
def frozen_now(monkeypatch):
    def freeze(when: datetime):
        monkeypatch.setattr(timezone, "now", lambda: when)

    return freeze


def test_naive_departure_is_local_time_at_the_origin():
    chicago = ZoneInfo("America/Chicago")
    local = (timezone.now() + timedelta(hours=2)).astimezone(chicago).replace(second=0, microsecond=0)
    s = validate(body(naive(local)))
    assert s.is_valid(), s.errors
    got = s.validated_data["departure_time"]
    assert got == local
    assert got.utcoffset() == local.utcoffset()
    assert s.validated_data["timezone"] == "America/Chicago"


def test_departure_with_an_offset_keeps_its_instant_in_the_origin_zone():
    sent = (timezone.now() + timedelta(hours=2)).replace(microsecond=0)
    s = validate(body(sent.astimezone(dt_timezone(timedelta(hours=-4))).isoformat()))
    assert s.is_valid(), s.errors
    got = s.validated_data["departure_time"]
    assert got == sent
    assert got.utcoffset() == sent.astimezone(ZoneInfo("America/Chicago")).utcoffset()


def test_timezone_comes_from_the_origin_not_the_destination():
    s = validate(body(naive(timezone.now() + timedelta(days=1)), origin=DENVER, destination=DALLAS))
    assert s.is_valid(), s.errors
    assert s.validated_data["timezone"] == "America/Denver"


def test_past_check_uses_the_time_read_at_the_origin():
    # 30 min from now on a UTC clock, but 8.5 h ago on Tokyo clocks
    s = validate(body(naive(timezone.now() + timedelta(minutes=30)), origin=TOKYO, destination=OSAKA))
    assert not s.is_valid()
    assert s.errors["departure_time"] == ["Departure time is in the past."]

    # 3 h ago on a UTC clock, but 7 h from now on Honolulu clocks
    s = validate(body(naive(timezone.now() - timedelta(hours=3)), origin=HONOLULU, destination=HILO))
    assert s.is_valid(), s.errors


def test_days_ahead_check_uses_the_time_read_at_the_origin():
    later = timezone.now() + timedelta(days=5, hours=3)
    s = validate(body(naive(later), origin=TOKYO, destination=OSAKA))  # 4 days 18 h from now
    assert s.is_valid(), s.errors

    sooner = timezone.now() + timedelta(days=5) - timedelta(hours=3)
    s = validate(body(naive(sooner), origin=HONOLULU, destination=HILO))  # 5 days 7 h from now
    assert not s.is_valid()
    assert s.errors["departure_time"] == ["Departure must be within 5 days."]


def test_up_to_an_hour_in_the_past_is_allowed():
    chicago = ZoneInfo("America/Chicago")
    s = validate(body(naive((timezone.now() - timedelta(minutes=50)).astimezone(chicago))))
    assert s.is_valid(), s.errors
    s = validate(body(naive((timezone.now() - timedelta(minutes=70)).astimezone(chicago))))
    assert not s.is_valid()


def test_skipped_spring_forward_time_moves_an_hour_later(frozen_now):
    frozen_now(datetime(2026, 3, 7, 12, tzinfo=dt_timezone.utc))
    s = validate(body("2026-03-08T02:30"))
    assert s.is_valid(), s.errors
    assert s.validated_data["departure_time"].isoformat() == "2026-03-08T03:30:00-05:00"


def test_repeated_fall_back_time_is_the_first_one(frozen_now):
    frozen_now(datetime(2026, 10, 31, 12, tzinfo=dt_timezone.utc))
    s = validate(body("2026-11-01T01:30"))
    assert s.is_valid(), s.errors
    assert s.validated_data["departure_time"].isoformat() == "2026-11-01T01:30:00-05:00"


@pytest.mark.parametrize("value", ["9999-12-31T23:00", "0001-01-01T00:00+14:00"])
def test_out_of_range_departure_is_a_validation_error(value):
    s = validate(body(value, origin=DALLAS))
    assert not s.is_valid()
    assert "departure_time" in s.errors


@pytest.mark.parametrize("value", ["", "tomorrow", "08:00", "2026-13-01T08:00"])
def test_unparseable_departure_is_rejected(value):
    s = validate(body(value))
    assert not s.is_valid()
    assert "departure_time" in s.errors
