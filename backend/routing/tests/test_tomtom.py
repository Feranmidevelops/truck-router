from datetime import datetime, timedelta, timezone
import math
import threading
import time

import httpx
import pytest

from routing.services import tomtom
from routing.services.geometry import cumulative_m, haversine_m
from routing.services.types import LatLon, RouteOption, TimingPoint

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


def depart_at_sent_for(departure: datetime) -> str:
    sent = {}

    def respond(request: httpx.Request) -> httpx.Response:
        sent.update(request.url.params)
        return httpx.Response(200, json=SAMPLE)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    origin, destination = LatLon(32.0, -97.0), LatLon(32.09, -97.0)
    tomtom.calculate_routes(origin, destination, departure, 20_000, "test-key", client=client)
    return sent["departAt"]


def test_depart_at_is_sent_in_whole_seconds():
    departure = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=123_456)
    assert depart_at_sent_for(departure) == departure.replace(microsecond=0).isoformat()


def test_a_departure_already_passed_is_sent_as_now():
    assert depart_at_sent_for(datetime.now(timezone.utc) - timedelta(minutes=30)) == "now"


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


def test_via_points_sit_left_and_right_of_the_route_midpoint():
    left, right = tomtom.offset_via_points(line(WEST, EAST, 1000), WEST, EAST, 0.15)
    straight = haversine_m(WEST, EAST)
    assert left.lat > 35.0 > right.lat  # driving east, left is north
    for p in (left, right):
        assert p.lon == pytest.approx(-95.0)
        assert haversine_m(MIDDLE, p) == pytest.approx(0.15 * straight, rel=0.01)


def test_left_is_west_when_driving_north():
    south, north = LatLon(30.0, -100.0), LatLon(40.0, -100.0)
    left, right = tomtom.offset_via_points(line(south, north, 100), south, north, 0.25)
    assert left.lon < -100.0 < right.lon
    assert left.lat == pytest.approx(35.0)
    assert right.lat == pytest.approx(35.0)


def test_via_points_are_square_to_a_diagonal_trip():
    # Square to the straight line means as far from one end as from the other. Without the
    # cos(lat) scaling the points come out about 50 km closer to one end.
    dallas, denver = LatLon(32.8, -96.8), LatLon(39.7, -105.0)
    for p in tomtom.offset_via_points(line(dallas, denver, 500), dallas, denver, 0.15):
        assert haversine_m(p, dallas) == pytest.approx(haversine_m(p, denver), rel=0.01)


def test_via_points_start_from_halfway_along_the_route_by_distance():
    # Points bunched on the first leg, and a bend north: halfway by distance is the bend.
    bend = LatLon(36.0, -95.0)
    route = line(WEST, bend, 900) + line(bend, EAST, 50)[1:]
    left, right = tomtom.offset_via_points(route, WEST, EAST, 0.15)
    assert (left.lat + right.lat) / 2 == pytest.approx(36.0)
    assert left.lon == pytest.approx(-95.0)


# Trips below run west to east along latitude 35, so "left" of the trip is north.
SPEED_MPS = 25.0
FASTEST = line(WEST, EAST, 1000)
NORTHERN = line(WEST, LatLon(36.0, -95.0), 500) + line(LatLon(36.0, -95.0), EAST, 500)[1:]
SOUTHERN = line(WEST, LatLon(34.0, -95.0), 500) + line(LatLon(34.0, -95.0), EAST, 500)[1:]


def drive(route_id: str, points: list[LatLon], slowdown: float = 1.0) -> RouteOption:
    length = cumulative_m(points)[-1]
    seconds = length / SPEED_MPS * slowdown
    return RouteOption(
        id=route_id,
        source="tomtom",
        distance_m=length,
        duration_s=seconds,
        departure_time=T0,
        arrival_time=T0 + timedelta(seconds=seconds),
        points=points,
        timing=[TimingPoint(0, 0), TimingPoint(length, seconds)],
    )


class FakeTomTom:
    """Stands in for TomTom's Calculate Route.

    `answer(via)` gets the via point of each request and returns the route to send back as
    (points, slowdown), or None for an HTTP 400. Every via point asked for is kept in `vias`.
    """

    def __init__(self, answer=lambda via: None):
        self.answer = answer
        self.vias: list[LatLon] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._respond))

    def _respond(self, request: httpx.Request) -> httpx.Response:
        locations = request.url.path.split("/")[4]
        stops = [LatLon(*map(float, s.split(","))) for s in locations.split(":")]
        assert len(stops) == 3, "fallback requests go through exactly one via point"
        self.vias.append(stops[1])
        result = self.answer(stops[1])
        if result is None:
            return httpx.Response(400, json={"error": {"description": "NO_ROUTE_FOUND"}})
        points, slowdown = result
        length = cumulative_m(points)[-1]
        route = {
            "summary": {"lengthInMeters": length, "travelTimeInSeconds": length / SPEED_MPS * slowdown},
            "legs": [{"points": [{"latitude": p.lat, "longitude": p.lon} for p in points]}],
        }
        return httpx.Response(200, json={"routes": [route]})


def ensure(routes: list[RouteOption], fake: FakeTomTom) -> list[RouteOption]:
    return tomtom.ensure_three_routes(routes, WEST, EAST, T0, 20_000, "test-key", client=fake.client)


def test_three_distinct_routes_come_back_fastest_first_with_no_extra_calls():
    fake = FakeTomTom()
    routes = [drive("r0", NORTHERN, 1.1), drive("r1", FASTEST), drive("r2", SOUTHERN)]
    assert [r.id for r in ensure(routes, fake)] == ["r1", "r2", "r0"]
    assert fake.vias == []


def test_a_tomtom_alternative_on_the_same_road_as_a_faster_one_is_dropped():
    fake = FakeTomTom()
    routes = [
        drive("r0", FASTEST),
        drive("r1", shifted(FASTEST, 0.2), 1.01),
        drive("r2", NORTHERN),
        drive("r3", SOUTHERN),
    ]
    assert [r.id for r in ensure(routes, fake)] == ["r0", "r2", "r3"]
    assert fake.vias == []


def test_a_tomtom_alternative_over_one_and_a_half_times_slower_is_dropped():
    fast = drive("r0", FASTEST)
    slow = drive("r1", NORTHERN)
    slow.duration_s = fast.duration_s * 1.5 + 1
    assert [r.id for r in ensure([fast, slow], FakeTomTom())] == ["r0"]


def test_exactly_one_and_a_half_times_slower_is_kept():
    fast = drive("r0", FASTEST)
    slow = drive("r1", NORTHERN)
    slow.duration_s = fast.duration_s * 1.5
    assert [r.id for r in ensure([fast, slow], FakeTomTom())] == ["r0", "r1"]


def attempt(via: LatLon) -> str:
    """Which fallback a via point is: "L15", "R15", "L25" or "R25"."""
    share = haversine_m(MIDDLE, via) / haversine_m(WEST, EAST)
    return f"{'L' if via.lat > 35.0 else 'R'}{round(share * 100)}"


def fallbacks(**special: str):
    """A FakeTomTom answer: a plain route through each via point, except for the attempts
    named, which "fail" (HTTP 400), come back as a "copy" of the fastest route, or are "slow"."""

    def answer(via: LatLon):
        through = line(WEST, via, 500) + line(via, EAST, 500)[1:]
        kind = special.get(attempt(via), "ok")
        if kind == "fail":
            return None
        if kind == "copy":
            return shifted(FASTEST, 0.2), 1.01
        if kind == "slow":
            return through, 2.0
        return through, 1.0

    return answer


def test_one_tomtom_route_is_topped_up_to_three():
    fake = FakeTomTom(fallbacks())
    routes = ensure([drive("r0", FASTEST)], fake)
    # The northern fallback is a little shorter: degrees of longitude shrink going north.
    assert [r.id for r in routes] == ["r0", "via15L-0", "via15R-0"]
    assert [r.source for r in routes] == ["tomtom", "via-fallback", "via-fallback"]
    assert sorted(map(attempt, fake.vias)) == ["L15", "R15"]


def test_two_tomtom_routes_take_the_faster_of_two_fallbacks():
    fake = FakeTomTom(fallbacks())
    routes = ensure([drive("r0", FASTEST), drive("r1", SOUTHERN)], fake)
    assert [r.id for r in routes] == ["r0", "r1", "via15L-0"]
    assert len(fake.vias) == 2


def test_a_fallback_on_the_same_road_as_the_fastest_route_is_dropped():
    fake = FakeTomTom(fallbacks(L15="copy", L25="copy"))
    routes = ensure([drive("r0", FASTEST)], fake)
    assert [r.id for r in routes] == ["r0", "via15R-0", "via25R-0"]
    assert sorted(map(attempt, fake.vias)) == ["L15", "L25", "R15", "R25"]


def test_a_slow_detour_is_dropped():
    fake = FakeTomTom(fallbacks(R15="slow", R25="slow"))
    routes = ensure([drive("r0", FASTEST)], fake)
    assert [r.id for r in routes] == ["r0", "via15L-0", "via25L-0"]


def test_a_failed_fallback_is_skipped():
    fake = FakeTomTom(fallbacks(L15="fail"))
    routes = ensure([drive("r0", FASTEST)], fake)
    assert [r.id for r in routes] == ["r0", "via15R-0", "via25L-0"]


def test_when_every_fallback_fails_the_fastest_route_comes_back_alone():
    fake = FakeTomTom(fallbacks(L15="fail", R15="fail", L25="fail", R25="fail"))
    assert [r.id for r in ensure([drive("r0", FASTEST)], fake)] == ["r0"]
    assert len(fake.vias) == 4  # never more than two pairs


def test_no_routes_means_no_fallbacks():
    fake = FakeTomTom(fallbacks())
    assert ensure([], fake) == []
    assert fake.vias == []


def test_both_sides_are_requested_at_the_same_time():
    both_in_flight = threading.Barrier(2, timeout=5)

    def answer(via: LatLon):
        both_in_flight.wait()  # raises if the other side is not requested meanwhile
        return fallbacks()(via)

    assert len(ensure([drive("r0", FASTEST)], FakeTomTom(answer))) == 3
