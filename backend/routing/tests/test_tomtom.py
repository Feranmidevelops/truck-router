from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import threading
import time

import httpx
import pytest

from routing.services import tomtom
from routing.services.geometry import METERS_PER_MILE, cumulative_m, haversine_m
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


def test_overlap_measures_east_west_gaps_in_miles_not_degrees():
    # At 40N a degree of longitude is ~53 miles, not ~69, so 0.45 mile east is 0.0085 degrees.
    # Taken as degrees of latitude, that gap would look like 0.59 mile.
    road = line(LatLon(39.5, -100.0), LatLon(40.5, -100.0), 200)
    east = 0.45 * DEG_LAT_PER_MILE / math.cos(math.radians(40.0))
    beside = [LatLon(p.lat, p.lon + east) for p in road]
    assert tomtom.overlap_fraction(beside, road) == 1.0


def test_routes_that_meet_only_at_their_ends_do_not_overlap():
    # Samples sit mid-slice, so the start and end every route shares are never counted.
    north, south = LatLon(36.0, -95.0), LatLon(34.0, -95.0)
    a = line(WEST, north, 500) + line(north, EAST, 500)[1:]
    b = line(WEST, south, 500) + line(south, EAST, 500)[1:]
    assert tomtom.overlap_fraction(a, b) == 0.0


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


def test_via_points_are_offset_by_the_straight_line_trip_not_the_route():
    bend = LatLon(37.0, -95.0)  # the route is 10% longer than the straight line
    route = line(WEST, bend, 500) + line(bend, EAST, 500)[1:]
    for p in tomtom.offset_via_points(route, WEST, EAST, 0.15):
        assert haversine_m(bend, p) == pytest.approx(0.15 * haversine_m(WEST, EAST), rel=0.001)


# Trips below run west to east along latitude 35, so "left" of the trip is north.
SPEED_MPS = 25.0


def through(via: LatLon) -> list[LatLon]:
    return line(WEST, via, 500) + line(via, EAST, 500)[1:]


FASTEST = line(WEST, EAST, 1000)
NORTHERN = through(LatLon(36.0, -95.0))
SOUTHERN = through(LatLon(34.0, -95.0))


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
    (points, slowdown), None for an HTTP 400, or an httpx.Response to send as it is. Every via
    point asked for is kept in `vias`.
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
        if isinstance(result, httpx.Response):
            return result
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


def test_tomtom_routes_are_sorted_before_filtering():
    # A near copy of the fastest route listed first: the copy is the one to drop.
    routes = [drive("r1", shifted(FASTEST, 0.2), 1.01), drive("r0", FASTEST)]
    assert [r.id for r in ensure(routes, FakeTomTom())] == ["r0"]


def test_overlap_is_the_share_of_the_candidate_that_runs_along_a_kept_route():
    # The fastest road, except for a loop 50 miles north around 28 miles of it. 84% of the
    # loop route runs along the fastest one, while 96% of the fastest runs along the loop route.
    leave, rejoin = LatLon(35.0, -95.25), LatLon(35.0, -94.75)
    top = LatLon(35.0 + 50 * DEG_LAT_PER_MILE, -95.0)
    loop = line(WEST, leave, 500) + line(leave, top, 100)[1:] + line(top, rejoin, 100)[1:]
    loop += line(rejoin, EAST, 500)[1:]
    assert tomtom.overlap_fraction(loop, FASTEST) == pytest.approx(0.84, abs=0.01)
    assert tomtom.overlap_fraction(FASTEST, loop) == pytest.approx(0.96, abs=0.01)
    assert [r.id for r in ensure([drive("r0", FASTEST), drive("r1", loop)], FakeTomTom())] == ["r0", "r1"]


def test_a_route_exactly_90_percent_along_a_kept_one_is_kept():
    # Ten samples ~5 miles apart; one lands on a 4 mile stretch a mile north of the kept road.
    deg_lon_per_mile = DEG_LAT_PER_MILE / math.cos(math.radians(35.0))

    def at(miles_east: float, miles_north: float) -> LatLon:
        return LatLon(35.0 + miles_north * DEG_LAT_PER_MILE, -100.0 + miles_east * deg_lon_per_mile)

    road = line(at(0, 0), at(50, 0), 100)
    bypass = line(at(0, 0), at(20, 0), 40) + [at(20, 1), at(24, 1)] + line(at(24, 0), at(50, 0), 60)
    assert tomtom.overlap_fraction(bypass, road) == 0.9
    routes = [drive("r0", road), drive("r1", bypass)]
    fake = FakeTomTom()
    kept = tomtom.ensure_three_routes(routes, at(0, 0), at(50, 0), T0, 20_000, "test-key", client=fake.client)
    assert [r.id for r in kept] == ["r0", "r1"]


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


def test_the_time_limit_is_against_the_fastest_route_not_the_last_kept():
    fast, slower, slowest = drive("r0", FASTEST), drive("r1", NORTHERN), drive("r2", SOUTHERN)
    slower.duration_s = fast.duration_s * 1.2  # 12 h against 10 h
    slowest.duration_s = fast.duration_s * 1.6  # 16 h: within 1.5x of 12 h, not of 10 h
    routes = ensure([fast, slower, slowest], FakeTomTom(fallbacks()))
    assert [r.id for r in routes] == ["r0", "via15L-0", "r1"]


def attempt(via: LatLon) -> str:
    """Which fallback a via point is: "L15", "R15", "L25" or "R25"."""
    share = haversine_m(MIDDLE, via) / haversine_m(WEST, EAST)
    return f"{'L' if via.lat > 35.0 else 'R'}{round(share * 100)}"


def fallbacks(**special: str):
    """A FakeTomTom answer: a plain route through each via point, except for the attempts
    named, which "fail" (HTTP 400), come back as a "copy" of the fastest route, are "slow", or
    answer 200 with a body that is "not json", has "no summary" or has a "bad time"."""

    def answer(via: LatLon):
        kind = special.get(attempt(via), "ok")
        if kind == "fail":
            return None
        if kind == "copy":
            return shifted(FASTEST, 0.2), 1.01
        if kind == "slow":
            return through(via), 2.0
        if kind == "not json":
            return httpx.Response(200, text="<html>Bad gateway</html>")
        if kind == "no summary":
            return httpx.Response(200, json={"routes": [{"legs": []}]})
        if kind == "bad time":
            summary = {"lengthInMeters": 1000, "travelTimeInSeconds": 60, "departureTime": "soon"}
            return httpx.Response(200, json={"routes": [{"summary": summary, "legs": []}]})
        return through(via), 1.0

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


def test_the_faster_fallback_takes_the_last_slot_even_when_it_is_on_the_right():
    fake = FakeTomTom(lambda via: (through(via), 1.2 if attempt(via) == "L15" else 1.0))
    routes = ensure([drive("r0", FASTEST), drive("r1", NORTHERN)], fake)
    assert [r.id for r in routes] == ["r0", "r1", "via15R-0"]


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


@pytest.mark.parametrize("reply", ["not json", "no summary", "bad time"])
def test_a_fallback_answering_200_with_a_body_we_cannot_read_is_skipped(reply):
    fake = FakeTomTom(fallbacks(L15=reply))
    routes = ensure([drive("r0", FASTEST)], fake)
    assert [r.id for r in routes] == ["r0", "via15R-0", "via25L-0"]


def test_a_reply_that_is_not_json_is_a_routing_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>")))
    with pytest.raises(tomtom.RoutingError, match="not JSON"):
        tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)


@pytest.mark.parametrize("body", [{"routes": []}, {}])
def test_a_200_reply_without_routes_is_no_route(body):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    with pytest.raises(tomtom.NoRouteError, match="No truck route found"):
        tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)


# TomTom's real answer for Honolulu to Denver
NO_ROUTE_400 = {
    "formatVersion": "0.0.12",
    "detailedError": {
        "message": "Engine error while executing route request: NO_ROUTE_FOUND: No valid route found",
        "code": "NO_ROUTE_FOUND",
    },
}


def test_tomtoms_no_route_found_reply_is_no_route():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400, json=NO_ROUTE_400)))
    with pytest.raises(tomtom.NoRouteError, match="No truck route found"):
        tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"detailedError": {"code": "BAD_INPUT", "message": "Invalid request"}}),
        httpx.Response(400, text="Bad request"),
        httpx.Response(403, json={"detailedError": "Forbidden"}),
    ],
    ids=["other-code", "not-json", "unexpected-shape"],
)
def test_other_errors_are_not_mistaken_for_no_route(response):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(tomtom.RoutingError) as exc_info:
        tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)
    assert not isinstance(exc_info.value, tomtom.NoRouteError)


def test_when_every_fallback_fails_the_fastest_route_comes_back_alone():
    fake = FakeTomTom(fallbacks(L15="fail", R15="fail", L25="fail", R25="fail"))
    assert [r.id for r in ensure([drive("r0", FASTEST)], fake)] == ["r0"]
    assert len(fake.vias) == 4  # never more than two pairs


def test_a_fallback_on_the_same_road_as_an_earlier_fallback_is_dropped():
    _, right15 = tomtom.offset_via_points(FASTEST, WEST, EAST, 0.15)
    # Left fails both times; the 25% right point snaps back onto the 15% right road.
    fake = FakeTomTom(lambda via: None if attempt(via).startswith("L") else (through(right15), 1.0))
    assert [r.id for r in ensure([drive("r0", FASTEST)], fake)] == ["r0", "via15R-0"]
    assert len(fake.vias) == 4


def test_a_fallback_faster_than_tomtoms_route_comes_first():
    fake = FakeTomTom(lambda via: (through(via), 0.9))
    assert [r.id for r in ensure([drive("r0", FASTEST)], fake)] == ["via15L-0", "via15R-0", "r0"]


def test_a_fallback_faster_than_tomtoms_route_sets_the_time_limit():
    # TomTom gives 10 h and 14.9 h, then a fallback takes 9.3 h: 14.9 h is now over 1.5x that.
    fast, slow = drive("r0", FASTEST), drive("r1", NORTHERN)
    slow.duration_s = fast.duration_s * 1.49
    fake = FakeTomTom(lambda via: (through(via), 0.9))
    assert [r.id for r in ensure([fast, slow], fake)] == ["via15L-0", "r0"]


def test_never_more_than_three_routes():
    far_north = through(LatLon(37.0, -95.0))
    routes = [drive("r0", FASTEST), drive("r1", NORTHERN), drive("r2", SOUTHERN), drive("r3", far_north)]
    assert [r.id for r in ensure(routes, FakeTomTom())] == ["r0", "r1", "r2"]


def test_route_ids_are_unique_across_tomtom_and_every_fallback():
    fake = FakeTomTom(fallbacks(L15="copy", R15="slow"))
    ids = [r.id for r in ensure([drive("r0", FASTEST)], fake)]
    assert len(ids) == len(set(ids)) == 3


def test_no_routes_means_no_fallbacks():
    fake = FakeTomTom(fallbacks())
    assert ensure([], fake) == []
    assert fake.vias == []


def test_a_fallback_gets_less_time_to_answer_than_the_main_request():
    timeouts = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=SAMPLE)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)
    tomtom.ensure_three_routes([drive("r0", FASTEST)], WEST, EAST, T0, 20_000, "test-key", client=client)
    main, *fallbacks = timeouts
    assert main == tomtom.REQUEST_TIMEOUT_S
    assert fallbacks and set(fallbacks) == {tomtom.FALLBACK_TIMEOUT_S}
    assert tomtom.FALLBACK_TIMEOUT_S < tomtom.REQUEST_TIMEOUT_S


def test_both_sides_are_requested_at_the_same_time():
    both_in_flight = threading.Barrier(2, timeout=5)

    def answer(via: LatLon):
        both_in_flight.wait()  # raises if the other side is not requested meanwhile
        return fallbacks()(via)

    assert len(ensure([drive("r0", FASTEST)], FakeTomTom(answer))) == 3


# Via points off the straight line's midpoint. FASTEST runs along that line, so they are also
# its own 15% via points; NORTHERN's midpoint is 69 miles north, so its via points are not.
EARLY_LEFT, EARLY_RIGHT = EARLY_VIAS = tomtom.offset_via_points([WEST, EAST], WEST, EAST, 0.15)


def top_up(routes: list[RouteOption], fake: FakeTomTom, early=(), asked=EARLY_VIAS) -> list[RouteOption]:
    return tomtom.ensure_three_routes(
        routes, WEST, EAST, T0, 20_000, "test-key", early=early, asked=asked, client=fake.client
    )


def test_early_fallbacks_that_make_three_routes_save_the_top_up():
    fake = FakeTomTom(fallbacks())
    early = [drive("early15R-0", through(EARLY_RIGHT), 1.05), drive("early15L-0", through(EARLY_LEFT))]
    assert [r.id for r in top_up([drive("r0", FASTEST)], fake, early)] == ["r0", "early15L-0", "early15R-0"]
    assert fake.vias == []


def test_tomtoms_own_alternative_is_kept_before_a_faster_early_fallback():
    fake = FakeTomTom(fallbacks())
    tomtoms = [drive("r0", FASTEST), drive("r1", SOUTHERN, 1.3)]
    early = [drive("early15L-0", through(EARLY_LEFT)), drive("early15R-0", through(EARLY_RIGHT))]
    assert [r.id for r in top_up(tomtoms, fake, early)] == ["r0", "early15L-0", "r1"]


def test_rejected_early_fallbacks_are_topped_up_from_the_fastest_routes_midpoint():
    fake = FakeTomTom(lambda via: (through(via), 1.0))
    early = [drive("early15L-0", shifted(NORTHERN, 0.2), 1.01)]  # NORTHERN's road again
    routes = top_up([drive("r0", NORTHERN)], fake, early)
    assert sorted(r.id for r in routes) == ["r0", "via15L-0", "via15R-0"]
    left, right = tomtom.offset_via_points(NORTHERN, WEST, EAST, 0.15)
    assert sorted(v.lat for v in fake.vias) == pytest.approx([right.lat, left.lat])


def test_a_via_point_asked_early_is_not_asked_again():
    # The early pair failed. FASTEST's own 15% points are the same, so the top-up starts at 25%.
    fake = FakeTomTom(fallbacks())
    assert [r.id for r in top_up([drive("r0", FASTEST)], fake)] == ["r0", "via25L-0", "via25R-0"]
    assert sorted(map(attempt, fake.vias)) == ["L25", "R25"]


def test_no_more_than_six_via_points_are_asked_per_plan():
    fake = FakeTomTom()  # every fallback fails
    assert [r.id for r in top_up([drive("r0", NORTHERN)], fake)] == ["r0"]
    assert len(fake.vias) == 4  # and the 2 asked early
    elsewhere = [LatLon(45.0, -120.0 + i) for i in range(3)]
    fake = FakeTomTom()
    top_up([drive("r0", NORTHERN)], fake, asked=[*EARLY_VIAS, *elsewhere])
    assert len(fake.vias) == 1
    assert tomtom.MAX_FALLBACK_CALLS == 6


def test_early_fallbacks_alone_are_no_route():
    fake = FakeTomTom(fallbacks())
    assert top_up([], fake, [drive("early15L-0", through(EARLY_LEFT))]) == []
    assert fake.vias == []


# A real TomTom truck response for Dallas -> Denver with two alternatives, trimmed to every
# 100th polyline point and four fields per instruction.
REAL = json.loads((Path(__file__).parent / "data" / "tomtom_dallas_denver.json").read_text(encoding="utf-8"))


def real_routes() -> list[RouteOption]:
    return tomtom.parse_routes(REAL, T0, source="tomtom", id_prefix="r")


def test_parse_routes_reads_a_real_tomtom_response():
    routes = real_routes()
    assert [r.id for r in routes] == ["r0", "r1", "r2"]
    assert [round(r.distance_m / METERS_PER_MILE, 1) for r in routes] == [882.3, 798.4, 850.4]
    assert [round(r.duration_s / 3600, 2) for r in routes] == [11.89, 12.42, 13.14]
    for r in routes:
        assert r.timing[0] == TimingPoint(0, 0)
        assert r.timing[-1] == TimingPoint(r.distance_m, r.duration_s)
        assert all(a.meters < b.meters for a, b in zip(r.timing, r.timing[1:]))
        # Dallas is on Central time, Denver on Mountain time.
        assert r.departure_time.utcoffset() == timedelta(hours=-5)
        assert r.arrival_time.utcoffset() == timedelta(hours=-6)
        assert r.arrival_time - r.departure_time == timedelta(seconds=r.duration_s)


def test_the_three_real_routes_are_all_kept_with_no_extra_requests():
    routes = real_routes()
    origin, destination = routes[0].points[0], routes[0].points[-1]
    fake = FakeTomTom()
    kept = tomtom.ensure_three_routes(routes, origin, destination, T0, 20_000, "test-key", client=fake.client)
    assert [r.id for r in kept] == ["r0", "r1", "r2"]
    assert fake.vias == []
