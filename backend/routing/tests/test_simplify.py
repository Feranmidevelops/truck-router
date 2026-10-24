import math
import random

import pytest

from routing.services.geometry import EARTH_RADIUS_M, simplify
from routing.services.types import LatLon

LAT0, LON0 = 35.0, -100.0


def at(east_m: float, north_m: float) -> LatLon:
    """A point `east_m` / `north_m` metres from (LAT0, LON0)."""
    lat = LAT0 + math.degrees(north_m / EARTH_RADIUS_M)
    lon = LON0 + math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(LAT0))))
    return LatLon(lat, lon)


def local_m(p: LatLon, origin: LatLon) -> tuple[float, float]:
    """Flat east/north metres from `origin`, scaled at `origin`'s latitude."""
    k = math.cos(math.radians(origin.lat))
    return (
        math.radians(p.lon - origin.lon) * EARTH_RADIUS_M * k,
        math.radians(p.lat - origin.lat) * EARTH_RADIUS_M,
    )


def distance_to_line_m(p: LatLon, line: list[LatLon]) -> float:
    best = math.inf
    for a, b in zip(line, line[1:]):
        (bx, by), (px, py) = local_m(b, a), local_m(p, a)
        s2 = bx * bx + by * by
        t = 0.0 if s2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / s2))
        best = min(best, math.hypot(px - t * bx, py - t * by))
    return best


@pytest.mark.parametrize("pts", [[], [at(0, 0)], [at(0, 0), at(100, 0)]])
def test_two_points_or_fewer_come_back_unchanged(pts):
    out = simplify(pts, 20)
    assert out == pts
    assert out is not pts


def test_straight_road_keeps_only_its_ends():
    pts = [at(i * 50, i * 20) for i in range(500)]
    assert simplify(pts, 20) == [pts[0], pts[-1]]


@pytest.mark.parametrize("offset_m, kept", [(10, False), (19.9, False), (20.5, True), (300, True)])
def test_a_bend_is_kept_only_when_it_is_further_off_than_the_tolerance(offset_m, kept):
    pts = [at(0, 0), at(5_000, offset_m), at(10_000, 0)]
    assert (pts[1] in simplify(pts, 20)) is kept


def test_tolerance_is_in_metres_at_any_latitude():
    # 25 m sideways is 0.0003 deg of longitude at 35N but 0.0006 deg at 68N
    for lat in (0.0, 35.0, 68.0):
        k = math.cos(math.radians(lat))
        shift = math.degrees(25 / (EARTH_RADIUS_M * k))
        pts = [LatLon(lat, -100.0), LatLon(lat + 0.05, -100.0 + shift), LatLon(lat + 0.1, -100.0)]
        assert len(simplify(pts, 20)) == 3
        assert len(simplify(pts, 30)) == 2


def test_a_road_that_doubles_back_past_its_end_keeps_the_turn():
    # Out 2 km north and back to 500 m: the turn lies on the line from start to end, but
    # 1.5 km beyond the end of that segment.
    pts = [at(0, 0), at(0, 1_000), at(0, 2_000), at(0, 1_000), at(0, 500)]
    assert at(0, 2_000) in simplify(pts, 20)


def test_an_out_and_back_route_keeps_its_far_point():
    # Start and end are the same point, so the first segment tested has no length
    pts = [at(0, 0), at(1_000, 0), at(2_000, 0), at(1_000, 0), at(0, 0)]
    assert simplify(pts, 20) == [pts[0], pts[2], pts[4]]


def test_repeated_points_collapse():
    pts = [at(0, 0)] * 5
    assert simplify(pts, 20) == [pts[0], pts[-1]]


def test_every_dropped_point_stays_within_the_tolerance_of_the_line():
    rng = random.Random(7)
    pts, x, y, heading = [], 0.0, 0.0, 0.0
    for _ in range(3_000):  # a wandering road, ~70 m between points like TomTom's
        heading += rng.gauss(0, 0.08)
        x, y = x + 70 * math.cos(heading), y + 70 * math.sin(heading)
        pts.append(at(x, y))
    out = simplify(pts, 20)
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert len(out) < len(pts) / 3
    assert all(p in pts for p in out)
    assert [pts.index(p) for p in out] == sorted(pts.index(p) for p in out)  # order kept
    assert max(distance_to_line_m(p, out) for p in pts) <= 20.01


def test_bigger_tolerance_never_keeps_more_points():
    rng = random.Random(3)
    pts = [at(i * 60, rng.uniform(-40, 40)) for i in range(1_000)]
    counts = [len(simplify(pts, tol)) for tol in (5, 10, 20, 40, 80)]
    assert counts == sorted(counts, reverse=True)
    assert counts[-1] == 2
