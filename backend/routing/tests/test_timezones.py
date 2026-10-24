from datetime import datetime, timedelta, timezone

import pytest

from routing.services import timezones
from routing.services.timezones import to_zone, zone_name


@pytest.mark.parametrize(
    "lat, lon, zone",
    [
        (32.7767, -96.797, "America/Chicago"),  # Dallas
        (39.7392, -104.9903, "America/Denver"),  # Denver
        (33.4484, -112.074, "America/Phoenix"),  # Phoenix, no DST
        (40.7128, -74.006, "America/New_York"),
        # Either side of the Central/Mountain line, both within ~25 km of it
        (31.0399, -104.8307, "America/Chicago"),  # Van Horn, TX
        (31.1746, -105.3575, "America/Denver"),  # Sierra Blanca, TX
        (39.3958, -101.0524, "America/Chicago"),  # Colby, KS
        (39.3508, -101.7102, "America/Denver"),  # Goodland, KS
        # I-40 crosses the Central/Eastern line between these two
        (35.9489, -85.0269, "America/Chicago"),  # Crossville, TN
        (35.8656, -84.6849, "America/New_York"),  # Rockwood, TN
    ],
)
def test_zone_name_at_us_places(lat, lon, zone):
    assert zone_name(lat, lon) == zone


def test_zone_name_at_sea_is_still_a_usable_zone():
    assert zone_name(30.0, -40.0) == "Etc/GMT+3"


def test_unknown_zone_falls_back_to_utc(monkeypatch):
    monkeypatch.setattr(timezones.tzfpy, "get_tz", lambda lon, lat: "Mars/Olympus_Mons")
    assert zone_name(0, 0) == "UTC"
    monkeypatch.setattr(timezones.tzfpy, "get_tz", lambda lon, lat: "")
    assert zone_name(0, 0) == "UTC"


def test_naive_time_is_local_time_in_the_zone():
    got = to_zone(datetime(2026, 10, 26, 8, 0), "America/Chicago")
    assert got.isoformat() == "2026-10-26T08:00:00-05:00"
    assert got == datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


def test_naive_time_in_winter_uses_standard_time():
    got = to_zone(datetime(2026, 12, 1, 8, 0), "America/Chicago")
    assert got.isoformat() == "2026-12-01T08:00:00-06:00"


def test_aware_time_keeps_its_instant_and_takes_the_zone_offset():
    sent = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
    got = to_zone(sent, "America/Denver")
    assert got == sent
    assert got.isoformat() == "2026-10-26T07:00:00-06:00"


def test_aware_time_with_another_offset_is_not_reread_as_local():
    sent = datetime(2026, 10, 26, 8, 0, tzinfo=timezone(timedelta(hours=-4)))  # 08:00 in New York
    assert to_zone(sent, "America/Chicago").isoformat() == "2026-10-26T07:00:00-05:00"


def test_time_skipped_by_spring_forward_moves_forward_an_hour():
    # Chicago clocks jump from 02:00 CST to 03:00 CDT on 2026-03-08; 02:30 never happens.
    got = to_zone(datetime(2026, 3, 8, 2, 30), "America/Chicago")
    assert got.isoformat() == "2026-03-08T03:30:00-05:00"
    assert got == datetime(2026, 3, 8, 8, 30, tzinfo=timezone.utc)


def test_time_repeated_by_fall_back_is_the_first_one():
    # Chicago clocks go from 02:00 CDT back to 01:00 CST on 2026-11-01; 01:30 happens twice.
    got = to_zone(datetime(2026, 11, 1, 1, 30), "America/Chicago")
    assert got.isoformat() == "2026-11-01T01:30:00-05:00"
    assert got == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)


def test_times_either_side_of_a_dst_change_are_unchanged():
    assert to_zone(datetime(2026, 3, 8, 1, 59), "America/Chicago").isoformat() == "2026-03-08T01:59:00-06:00"
    assert to_zone(datetime(2026, 3, 8, 3, 0), "America/Chicago").isoformat() == "2026-03-08T03:00:00-05:00"
    assert to_zone(datetime(2026, 11, 1, 2, 0), "America/Chicago").isoformat() == "2026-11-01T02:00:00-06:00"


def test_adding_travel_time_across_fall_back_counts_real_hours():
    leave = to_zone(datetime(2026, 11, 1, 0, 30), "America/Chicago")  # 05:30 UTC
    assert leave + timedelta(hours=3) == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)


def test_zone_without_dst():
    got = to_zone(datetime(2026, 3, 8, 2, 30), "America/Phoenix")
    assert got.isoformat() == "2026-03-08T02:30:00-07:00"
