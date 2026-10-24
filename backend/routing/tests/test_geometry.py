from datetime import datetime, timedelta, timezone

import pytest

from routing.services.geometry import (
    METERS_PER_MILE,
    cumulative_m,
    haversine_m,
    point_at_distance,
    sample_checkpoints,
    seconds_at_distance,
)
from routing.services.types import LatLon, TimingPoint

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
MILES_PER_DEG_LAT = 69.09  # approx, for a north-south line


def straight_line(miles: float) -> list[LatLon]:
    """North-south polyline of roughly `miles`, with a point every ~1 mile."""
    n = int(miles) + 1
    step = miles / MILES_PER_DEG_LAT / (n - 1)
    return [LatLon(30 + i * step, -97.0) for i in range(n)]


def uniform_timing(points: list[LatLon], mph: float) -> list[TimingPoint]:
    total = cumulative_m(points)[-1]
    return [TimingPoint(0, 0), TimingPoint(total, total / METERS_PER_MILE / mph * 3600)]


def test_haversine_one_degree_latitude():
    assert haversine_m(LatLon(0, 0), LatLon(1, 0)) / METERS_PER_MILE == pytest.approx(69.09, abs=0.1)


def test_point_at_distance_interpolates_and_clamps():
    pts = [LatLon(0, 0), LatLon(1, 0)]
    cum = cumulative_m(pts)
    mid = point_at_distance(pts, cum, cum[-1] / 2)
    assert mid.lat == pytest.approx(0.5, abs=1e-6)
    assert point_at_distance(pts, cum, -10) == pts[0]
    assert point_at_distance(pts, cum, cum[-1] + 10) == pts[-1]


def test_point_at_distance_picks_the_right_segment():
    pts = [LatLon(0, 0), LatLon(1, 0), LatLon(1, 1)]
    cum = cumulative_m(pts)
    assert point_at_distance(pts, cum, cum[1]) == pts[1]
    on_second = point_at_distance(pts, cum, (cum[1] + cum[2]) / 2)
    assert on_second.lat == pytest.approx(1.0)
    assert on_second.lon == pytest.approx(0.5, abs=1e-6)


def test_point_at_distance_survives_zero_length_segments():
    pts = [LatLon(0, 0), LatLon(1, 0), LatLon(1, 0), LatLon(1, 0), LatLon(2, 0)]
    cum = cumulative_m(pts)
    assert point_at_distance(pts, cum, cum[1]) == LatLon(1, 0)
    assert point_at_distance(pts, cum, cum[1] / 2).lat == pytest.approx(0.5, abs=1e-6)
    assert point_at_distance(pts, cum, cum[1] * 1.5).lat == pytest.approx(1.5, abs=1e-6)


def test_point_at_distance_on_a_single_point():
    pts = [LatLon(30, -97)]
    assert point_at_distance(pts, [0.0], 0) == pts[0]
    assert point_at_distance(pts, [0.0], 100) == pts[0]


def test_seconds_at_distance_uses_timing_profile_not_average_speed():
    m = METERS_PER_MILE
    timing = [TimingPoint(0, 0), TimingPoint(50 * m, 3600), TimingPoint(100 * m, 5400)]
    assert seconds_at_distance(timing, 25 * m) == pytest.approx(1800)
    assert seconds_at_distance(timing, 75 * m) == pytest.approx(4500)
    assert seconds_at_distance(timing, 500 * m) == pytest.approx(5400)


def test_seconds_at_distance_hits_timing_points_and_clamps_below_zero():
    m = METERS_PER_MILE
    timing = [TimingPoint(0, 0), TimingPoint(10 * m, 1800), TimingPoint(80 * m, 5400)]
    assert seconds_at_distance(timing, -5) == 0
    assert seconds_at_distance(timing, 0) == 0
    assert seconds_at_distance(timing, 10 * m) == pytest.approx(1800)
    assert seconds_at_distance(timing, 80 * m) == 5400


def test_seconds_at_distance_with_one_or_two_timing_points():
    assert seconds_at_distance([TimingPoint(0, 0)], 1000) == 0
    two = [TimingPoint(0, 0), TimingPoint(1000, 100)]
    assert seconds_at_distance(two, 250) == pytest.approx(25)
    assert seconds_at_distance(two, 1000) == 100


def test_checkpoints_every_25_miles_plus_destination():
    pts = straight_line(110)
    cps = sample_checkpoints(pts, uniform_timing(pts, 50), T0, 25)
    total = cumulative_m(pts)[-1] / METERS_PER_MILE
    assert [round(c.mile) for c in cps] == [0, 25, 50, 75, 100, round(total)]
    assert [c.index for c in cps] == list(range(len(cps)))
    assert sum(c.segment_miles for c in cps) == pytest.approx(total, rel=1e-6)
    assert cps[0].segment_miles == pytest.approx(12.5, abs=0.01)  # half an interval at the start
    assert cps[0].eta == T0
    # mile 50 at 50 mph
    assert abs((cps[2].eta - (T0 + timedelta(hours=1))).total_seconds()) < 1


def test_short_route_gets_origin_and_destination():
    pts = straight_line(30)
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, 50)
    assert len(cps) == 2
    assert cps[0].mile == 0
    assert cps[1].position.lat == pytest.approx(pts[-1].lat)
    assert cps[1].position.lon == pytest.approx(pts[-1].lon)


def test_no_duplicate_destination_checkpoint():
    pts = straight_line(50.2)
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, 25)
    assert [round(c.mile) for c in cps] == [0, 25, 50]
