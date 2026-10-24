from datetime import datetime, timedelta, timezone
import math

import pytest

from routing.services.geometry import (
    EARTH_RADIUS_M,
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


def meridian_line(miles: float, step_miles: float = 1.0) -> list[LatLon]:
    """North-south polyline whose haversine length is exactly `miles` (to float precision)."""
    marks = [min(k * step_miles, miles) for k in range(math.ceil(miles / step_miles) + 1)]
    return [LatLon(30 + math.degrees(m * METERS_PER_MILE / EARTH_RADIUS_M), -97.0) for m in marks]


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


@pytest.mark.parametrize(
    "interval, expected_miles, last_interval_owns",
    [
        (10, list(range(0, 240, 10)) + [237], 5 + 3.5),
        (50, [0, 50, 100, 150, 200, 237], 25 + 18.5),
    ],
)
def test_checkpoints_on_a_long_route(interval, expected_miles, last_interval_owns):
    pts = meridian_line(237)
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, interval)
    assert [c.mile for c in cps] == [pytest.approx(m) for m in expected_miles]
    assert [c.index for c in cps] == list(range(len(cps)))
    assert sum(c.segment_miles for c in cps) == pytest.approx(237)
    assert cps[0].segment_miles == pytest.approx(interval / 2)
    assert [c.segment_miles for c in cps[1:-2]] == [pytest.approx(interval)] * (len(cps) - 3)
    assert cps[-2].segment_miles == pytest.approx(last_interval_owns)
    assert cps[-1].segment_miles == pytest.approx((237 - expected_miles[-2]) / 2)
    # 60 mph: one minute per mile
    for c in cps:
        assert (c.eta - T0).total_seconds() == pytest.approx(c.mile * 60, abs=0.5)


def test_eta_follows_the_timing_profile_not_an_average_speed():
    pts = meridian_line(100)
    m = METERS_PER_MILE
    # 10 city miles at 20 mph, then 90 highway miles at 60 mph: 2 h in total.
    timing = [TimingPoint(0, 0), TimingPoint(10 * m, 1800), TimingPoint(100 * m, 7200)]
    cps = sample_checkpoints(pts, timing, T0, 10)
    minutes = {round(c.mile): (c.eta - T0).total_seconds() / 60 for c in cps}
    assert minutes[10] == pytest.approx(30)  # an average speed would say 12
    assert minutes[50] == pytest.approx(70)  # an average speed would say 60
    assert minutes[100] == pytest.approx(120)


@pytest.mark.parametrize("tomtom_ratio", [0.9963, 1.0041])
def test_eta_scales_polyline_distance_onto_tomtom_length(tomtom_ratio):
    pts = meridian_line(105)
    tomtom_m = cumulative_m(pts)[-1] * tomtom_ratio
    duration = 7213.4
    # Slow first 20 miles, then fast.
    timing = [TimingPoint(0, 0), TimingPoint(tomtom_m * 20 / 105, 1800), TimingPoint(tomtom_m, duration)]
    cps = sample_checkpoints(pts, timing, T0, 10)
    assert cps[-1].mile == pytest.approx(105)
    assert cps[-1].eta == T0 + timedelta(seconds=duration)
    seconds = {round(c.mile): (c.eta - T0).total_seconds() for c in cps}
    assert seconds[20] == pytest.approx(1800, abs=1e-3)
    assert seconds[50] == pytest.approx(1800 + (duration - 1800) * 30 / 85, abs=1e-3)


def test_destination_exactly_half_a_mile_past_the_last_interval_is_merged():
    pts = meridian_line(25.5)
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, 25)
    assert [c.mile for c in cps] == [0, pytest.approx(25)]
    assert cps[-1].segment_miles == pytest.approx(12.5 + 0.5)

    pts = meridian_line(25.51)
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, 25)
    assert [c.mile for c in cps] == [0, pytest.approx(25), pytest.approx(25.51)]


def test_route_ending_on_an_interval_mark_has_one_checkpoint_there():
    pts = meridian_line(50)
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, 25)
    assert [c.mile for c in cps] == [0, pytest.approx(25), pytest.approx(50)]


def test_route_shorter_than_half_a_mile_is_one_checkpoint():
    pts = meridian_line(0.3)
    cps = sample_checkpoints(pts, uniform_timing(pts, 30), T0, 10)
    assert len(cps) == 1
    assert cps[0].mile == 0
    assert cps[0].position == pts[0]
    assert cps[0].eta == T0
    assert cps[0].segment_miles == pytest.approx(0.3)


def test_checkpoint_positions_lie_on_the_polyline():
    pts = [LatLon(30, -97)]
    for i in range(40):
        last = pts[-1]
        pts.append(LatLon(last.lat + 0.05 + 0.01 * (i % 3), last.lon + (0.08 if i % 2 else -0.03)))
    cum = cumulative_m(pts)
    cps = sample_checkpoints(pts, uniform_timing(pts, 55), T0, 10)
    assert len(cps) == 23
    for c in cps:
        m = c.mile * METERS_PER_MILE
        i = max(j for j in range(len(pts) - 1) if cum[j] <= m)
        a, b = pts[i], pts[i + 1]
        # On the segment: going through the checkpoint is no longer than going straight from a to b.
        detour = haversine_m(a, c.position) + haversine_m(c.position, b) - haversine_m(a, b)
        assert detour == pytest.approx(0, abs=0.01)
        assert haversine_m(a, c.position) == pytest.approx(m - cum[i], abs=1)


def test_duplicate_points_do_not_change_checkpoints():
    clean = meridian_line(60)
    doubled = [p for p in clean for _ in range(2)]
    timing = uniform_timing(clean, 60)

    def fields(cps):
        return [(c.mile, c.position, c.segment_miles, c.eta) for c in cps]

    assert fields(sample_checkpoints(doubled, timing, T0, 25)) == fields(sample_checkpoints(clean, timing, T0, 25))


@pytest.mark.parametrize("pts", [[LatLon(30, -97)], [LatLon(30, -97)] * 3])
def test_zero_length_route_is_one_checkpoint_at_departure(pts):
    cps = sample_checkpoints(pts, [TimingPoint(0, 0)], T0, 10)
    assert len(cps) == 1
    assert cps[0].position == pts[0]
    assert cps[0].segment_miles == 0
    assert cps[0].eta == T0


def test_dense_polyline_like_a_real_tomtom_route():
    pts = meridian_line(600, step_miles=0.05)
    assert len(pts) == 12_001
    cps = sample_checkpoints(pts, uniform_timing(pts, 60), T0, 10)
    assert len(cps) == 61
    assert sum(c.segment_miles for c in cps) == pytest.approx(600)
    assert all(a.eta < b.eta for a, b in zip(cps, cps[1:]))


def test_interval_must_be_positive():
    pts = meridian_line(10)
    with pytest.raises(ValueError):
        sample_checkpoints(pts, uniform_timing(pts, 60), T0, 0)


@pytest.mark.parametrize(
    "pts, timing, message",
    [
        ([], [TimingPoint(0, 0)], "no geometry"),
        (meridian_line(5), [], "no timing profile"),
    ],
)
def test_empty_route_raises_a_clear_error(pts, timing, message):
    with pytest.raises(ValueError, match=message):
        sample_checkpoints(pts, timing, T0, 10)
