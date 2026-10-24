from datetime import datetime, timezone
import math
import time

import pytest

from routing.services import tomtom
from routing.services.geometry import cumulative_m
from routing.services.types import LatLon

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
DEG_LAT_PER_MILE = 1 / 69.09


def line(a: LatLon, b: LatLon, n: int) -> list[LatLon]:
    return [LatLon(a.lat + (b.lat - a.lat) * i / (n - 1), a.lon + (b.lon - a.lon) * i / (n - 1)) for i in range(n)]


def shifted(points: list[LatLon], miles_north: float) -> list[LatLon]:
    return [LatLon(p.lat + miles_north * DEG_LAT_PER_MILE, p.lon) for p in points]

SAMPLE = {
    "routes": [
        {
            "summary": {
                "lengthInMeters": 10_000,
                "travelTimeInSeconds": 600,
                "departureTime": "2026-10-24T07:00:00-05:00",
                "arrivalTime": "2026-10-24T07:10:00-05:00",
            },
            "legs": [
                {"points": [{"latitude": 32.0, "longitude": -97.0}, {"latitude": 32.05, "longitude": -97.0}]},
                {"points": [{"latitude": 32.05, "longitude": -97.0}, {"latitude": 32.09, "longitude": -97.0}]},
            ],
            "guidance": {
                "instructions": [
                    {"routeOffsetInMeters": 0, "travelTimeInSeconds": 0},
                    {"routeOffsetInMeters": 4_000, "travelTimeInSeconds": 300},
                    {"routeOffsetInMeters": 10_000, "travelTimeInSeconds": 600},
                ]
            },
        }
    ]
}


def test_vehicle_weight_adds_tare_and_converts_to_kg():
    assert tomtom.vehicle_weight_kg(0) == round(35_000 * 0.45359237)
    assert tomtom.vehicle_weight_kg(45_000) == round(80_000 * 0.45359237)


def test_parse_routes():
    [r] = tomtom.parse_routes(SAMPLE, T0, source="tomtom", id_prefix="r")
    assert r.id == "r0"
    assert len(r.points) == 3  # shared leg boundary point not duplicated
    assert r.departure_time == datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    assert [(t.meters, t.seconds) for t in r.timing] == [(0, 0), (4_000, 300), (10_000, 600)]


WEST, MIDDLE, EAST = LatLon(35.0, -100.0), LatLon(35.0, -95.0), LatLon(35.0, -90.0)


def test_overlap_with_itself_is_total():
    a = line(WEST, EAST, 1000)
    assert tomtom.overlap_fraction(a, a) == 1.0


def test_overlap_counts_a_parallel_road_only_within_half_a_mile():
    a = line(WEST, EAST, 1000)
    assert tomtom.overlap_fraction(shifted(a, 0.4), a) == 1.0
    assert tomtom.overlap_fraction(shifted(a, 0.6), a) == 0.0


def test_overlap_of_a_route_that_splits_halfway():
    a = line(WEST, EAST, 1000)
    first_half = line(WEST, MIDDLE, 500)
    b = first_half + line(MIDDLE, LatLon(37.0, -90.0), 500)[1:]
    expected = cumulative_m(first_half)[-1] / cumulative_m(b)[-1]
    assert tomtom.overlap_fraction(b, a) == pytest.approx(expected, abs=0.02)


def test_overlap_measures_distance_to_segments_not_just_vertices():
    sparse = [WEST, EAST]  # one 900 km segment
    dense = line(WEST, EAST, 1000)
    assert tomtom.overlap_fraction(dense, sparse) == 1.0
    assert tomtom.overlap_fraction(sparse, dense) == 1.0


def test_overlap_is_fast_on_two_long_dense_routes():
    def wiggly(miles_north: float, n: int = 10_000) -> list[LatLon]:
        # ~1,000 miles east from (35, -100), gently curving
        return [
            LatLon(35 + miles_north * DEG_LAT_PER_MILE + 0.5 * math.sin(12 * i / n), -100 + 17.0 * i / (n - 1))
            for i in range(n)
        ]

    a, near, apart = wiggly(0.0), wiggly(0.2), wiggly(1.0)
    start = time.perf_counter()
    assert tomtom.overlap_fraction(near, a) == 1.0
    assert tomtom.overlap_fraction(apart, a) == 0.0
    # About 20 ms each on a laptop. Checking every sample against every segment takes ~1.5 s.
    assert time.perf_counter() - start < 0.5
