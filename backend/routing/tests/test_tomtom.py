import copy
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


def test_place_search_leaves_out_points_of_interest():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": [{"address": {"freeformAddress": "Salt Lake City, UT"}, "position": {"lat": 40.76, "lon": -111.89}}]})

    places = tomtom.geocode("salt lake", "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [p.label for p in places] == ["Salt Lake City, UT"]
    assert seen[0].url.params["idxSet"] == "Geo,Addr,PAD,Str"
    assert seen[0].url.params["countrySet"] == "US"


@pytest.mark.parametrize(
    "query, path",
    [
        ("a/b", "/search/2/search/a%2Fb.json"),
        ("../x", "/search/2/search/..%2Fx.json"),
        ("123 1/2 Main St", "/search/2/search/123%201%2F2%20Main%20St.json"),
    ],
)
def test_place_search_keeps_the_query_inside_the_search_path(query, path):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    tomtom.geocode(query, "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen[0].url.raw_path.decode().split("?")[0] == path


# A live reverse geocode with entityType=Municipality in open country in New Mexico, trimmed
CAPULIN = {
    "summary": {"queryTime": 11, "numResults": 1},
    "addresses": [
        {
            "address": {
                "routeNumbers": [],
                "countryCode": "US",
                "countrySubdivision": "NM",
                "countrySecondarySubdivision": "Union",
                "municipality": "Capulin",
                "country": "United States",
                "countryCodeISO3": "USA",
                "freeformAddress": "Capulin, NM",
                "countrySubdivisionName": "New Mexico",
                "countrySubdivisionCode": "NM",
            },
            "position": "36.700001,-104.000000",
            "entityType": "Municipality",
        }
    ],
}


def reverse_client(respond, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return respond(request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reverse_geocode_asks_for_the_town_and_names_it_with_the_state():
    seen: list[httpx.Request] = []
    client = reverse_client(lambda r: httpx.Response(200, json=CAPULIN), seen)
    assert tomtom.reverse_geocode(LatLon(36.7, -104.0), "test-key", client=client) == "Capulin, NM"
    [request] = seen
    assert request.url.path == "/search/2/reverseGeocode/36.700000,-104.000000.json"
    assert request.url.params["entityType"] == "Municipality"
    assert request.url.params["key"] == "test-key"


def test_reverse_geocode_falls_back_to_the_state_name_without_a_code():
    payload = copy.deepcopy(CAPULIN)
    del payload["addresses"][0]["address"]["countrySubdivisionCode"]
    payload["addresses"][0]["address"]["countrySubdivision"] = "New Mexico"
    client = reverse_client(lambda r: httpx.Response(200, json=payload))
    assert tomtom.reverse_geocode(LatLon(36.7, -104.0), "test-key", client=client) == "Capulin, New Mexico"


@pytest.mark.parametrize(
    "addresses",
    [[], [{"address": {"countryCode": "US", "countrySubdivision": "NM", "freeformAddress": "NM"}}]],
    ids=["no result", "no town in the result"],
)
def test_reverse_geocode_without_a_town_is_none(addresses):
    payload = {"summary": {"numResults": len(addresses)}, "addresses": addresses}
    client = reverse_client(lambda r: httpx.Response(200, json=payload))
    assert tomtom.reverse_geocode(LatLon(36.7, -104.0), "test-key", client=client) is None


def test_reverse_geocode_errors_are_routing_errors():
    client = reverse_client(lambda r: httpx.Response(403, json={"detailedError": {"code": "Forbidden"}}))
    with pytest.raises(tomtom.RoutingError, match="403"):
        tomtom.reverse_geocode(LatLon(36.7, -104.0), "test-key", client=client)


class RecordingCache(dict):
    """A dict with Django's cache.set signature, keeping each timeout it was given."""

    def __init__(self, *args):
        super().__init__(*args)
        self.timeouts: dict = {}

    def set(self, key, value, timeout=None):
        self[key] = value
        self.timeouts[key] = timeout


def town_per_cell(request: httpx.Request) -> httpx.Response:
    """Names every place after its rounded position, e.g. "Town 36.7 -104.0, NM"."""
    lat, lon = request.url.path.split("/")[-1].removesuffix(".json").split(",")
    payload = copy.deepcopy(CAPULIN)
    payload["addresses"][0]["address"]["municipality"] = f"Town {float(lat)} {float(lon)}"
    return httpx.Response(200, json=payload)


def test_place_names_round_positions_and_ask_once_per_place():
    seen: list[httpx.Request] = []
    near_capulin = [LatLon(36.71, -104.02), LatLon(36.68, -103.97)]  # both round to 36.7,-104.0
    positions = [near_capulin[0], LatLon(35.2, -101.8), near_capulin[1]]
    names = tomtom.place_names(positions, "test-key", client=reverse_client(town_per_cell, seen))
    assert names == ["Town 36.7 -104.0, NM", "Town 35.2 -101.8, NM", "Town 36.7 -104.0, NM"]
    assert sorted(r.url.path for r in seen) == [
        "/search/2/reverseGeocode/35.200000,-101.800000.json",
        "/search/2/reverseGeocode/36.700000,-104.000000.json",
    ]


def test_place_names_are_cached_for_30_days_and_a_cache_hit_sends_no_request():
    cache = RecordingCache()
    seen: list[httpx.Request] = []
    client = reverse_client(town_per_cell, seen)
    tomtom.place_names([LatLon(36.7, -104.0)], "test-key", cache=cache, client=client)
    assert cache == {"place:36.7,-104.0": "Town 36.7 -104.0, NM"}
    assert cache.timeouts == {"place:36.7,-104.0": 30 * 24 * 60 * 60}

    again = tomtom.place_names([LatLon(36.72, -103.96)], "test-key", cache=cache, client=client)
    assert again == ["Town 36.7 -104.0, NM"]
    assert len(seen) == 1


def test_no_town_is_cached_too_and_comes_back_as_none():
    cache = RecordingCache()
    seen: list[httpx.Request] = []
    client = reverse_client(lambda r: httpx.Response(200, json={"addresses": []}), seen)
    assert tomtom.place_names([LatLon(36.7, -104.0)], "test-key", cache=cache, client=client) == [None]
    assert tomtom.place_names([LatLon(36.7, -104.0)], "test-key", cache=cache, client=client) == [None]
    assert cache == {"place:36.7,-104.0": ""}
    assert len(seen) == 1


@pytest.mark.parametrize(
    "fail",
    [
        lambda r: httpx.Response(500, text="Internal error for key test-key"),
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json={"addresses": [{"position": "36.7,-104.0"}]}),
        lambda r: httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["server error", "not json", "no address", "unexpected shape"],
)
def test_a_failed_lookup_is_none_is_not_cached_and_does_not_leak_the_key(fail, caplog):
    cache = RecordingCache()
    seen: list[httpx.Request] = []
    client = reverse_client(fail, seen)
    positions = [LatLon(36.7, -104.0), LatLon(35.2, -101.8)]
    assert tomtom.place_names(positions, "test-key", cache=cache, client=client) == [None, None]
    assert cache == {}
    assert "No place name for" in caplog.text
    assert "test-key" not in caplog.text
    tomtom.place_names(positions, "test-key", cache=cache, client=client)
    assert len(seen) == 4  # asked again next time


def test_a_network_error_is_none():
    def unreachable(request):
        raise httpx.ConnectError("no route to host", request=request)

    assert tomtom.place_names([LatLon(36.7, -104.0)], "test-key", client=reverse_client(unreachable)) == [None]


def test_place_names_ask_for_at_most_max_lookups_places_in_input_order():
    cache = RecordingCache({"place:30.0,-100.0": "Cached Town, TX"})
    seen: list[httpx.Request] = []
    positions = [LatLon(30.0, -100.0)] + [LatLon(31.0 + i, -100.0) for i in range(8)]
    names = tomtom.place_names(positions, "test-key", cache=cache, client=reverse_client(town_per_cell, seen))
    assert len(seen) == tomtom.MAX_PLACE_LOOKUPS == 6
    assert names[0] == "Cached Town, TX"  # from the cache, so it doesn't count
    assert names[1:7] == [f"Town {31.0 + i} -100.0, NM" for i in range(6)]
    assert names[7:] == [None, None]


def test_place_names_are_looked_up_at_the_same_time():
    all_in_flight = threading.Barrier(3, timeout=5)

    def answer(request):
        all_in_flight.wait()  # raises unless the other two are requested meanwhile
        return town_per_cell(request)

    positions = [LatLon(31.0, -100.0), LatLon(32.0, -100.0), LatLon(33.0, -100.0)]
    assert None not in tomtom.place_names(positions, "test-key", client=reverse_client(answer))


def test_no_positions_send_no_request():
    seen: list[httpx.Request] = []
    assert tomtom.place_names([], "test-key", client=reverse_client(town_per_cell, seen)) == []
    assert seen == []


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


def sent_for(**kwargs) -> httpx.Request:
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=SAMPLE)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    origin, destination = LatLon(32.0, -97.0), LatLon(32.09, -97.0)
    tomtom.calculate_routes(origin, destination, T0, 20_000, "test-key", client=client, **kwargs)
    [request] = sent
    return request


def test_a_route_request_is_a_get_with_no_body():
    request = sent_for()
    assert request.method == "GET"
    assert request.content == b""


def test_avoid_areas_are_posted_as_json_with_the_same_query():
    boxes = [tomtom.Box(34.5, -99.25, 35.123456789, -98.5), tomtom.Box(35.0, -98.75, 35.5, -98.0)]
    request = sent_for(avoid=boxes, max_alternatives=1)
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "avoidAreas": {
            "rectangles": [
                {
                    "southWestCorner": {"latitude": 34.5, "longitude": -99.25},
                    "northEastCorner": {"latitude": 35.123457, "longitude": -98.5},
                },
                {
                    "southWestCorner": {"latitude": 35.0, "longitude": -98.75},
                    "northEastCorner": {"latitude": 35.5, "longitude": -98.0},
                },
            ]
        }
    }
    assert dict(request.url.params) == dict(sent_for(max_alternatives=1).url.params)
    assert request.url.params["key"] == "test-key"
    assert request.url.params["maxAlternatives"] == "1"


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


DEG_LON_PER_MILE = DEG_LAT_PER_MILE / math.cos(math.radians(35.0))


def drawn(*corners: tuple[float, float]) -> list[LatLon]:
    """Straight lines through `corners`, given as (miles east, miles north) of (35, -100),
    with a point every ~0.1 mile like a real route."""
    pts = [LatLon(35.0 + n * DEG_LAT_PER_MILE, -100.0 + e * DEG_LON_PER_MILE) for e, n in corners]
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        out += line(a, b, max(2, round(haversine_m(a, b) / 160) + 1))[1:]
    return out


@pytest.mark.parametrize("back_east", [0.0, 0.05], ids=["same-road", "other-carriageway"])
def test_a_spur_out_to_a_point_and_back_doubles_back(back_east):
    # 3 miles up a side road to a via point, then back down it to the highway
    spur = drawn((0, 0), (25, 0), (25, 3), (25 + back_east, 3), (25 + back_east, 0), (50, 0))
    assert tomtom.doubles_back(spur)


def test_a_mile_up_a_side_road_and_back_is_not_doubling_back():
    # Say to a truck stop: the way back is never 3 miles of road away from the way out
    assert not tomtom.doubles_back(drawn((0, 0), (25, 0), (25, 1), (25, 0), (50, 0)))


def test_a_loop_that_crosses_its_own_road_doubles_back():
    # East, then north, west and south across the road it came along, 9 miles after it
    assert tomtom.doubles_back(drawn((0, 0), (30, 0), (30, 3), (27, 3), (27, -3), (50, -3)))


def test_a_loop_round_a_block_doubles_back():
    # Up from the south, once round a square mile and back to the same corner, then west
    assert tomtom.doubles_back(drawn((25, -25), (25, 0), (26, 0), (26, 1), (25, 1), (25, 0), (0, 0)))


def test_an_interchange_loop_ramp_is_not_doubling_back():
    # East to north on a cloverleaf: three quarters of a circle 0.2 mile across, turning right,
    # then north over the road it came along
    r = 0.1
    turn = [k / 20 * 1.5 * math.pi for k in range(21)]
    ramp = [(25 + r * math.sin(t), r * math.cos(t) - r) for t in turn]
    assert not tomtom.doubles_back(drawn((0, 0), *ramp, (25 - r, 30)))


@pytest.mark.parametrize("apart", [1.0, 0.5])
def test_coming_back_on_a_parallel_road_is_not_doubling_back(apart):
    # Round the end of a lake: 20 miles north, across, and 20 miles back south
    assert not tomtom.doubles_back(drawn((0, 0), (0, 20), (apart, 20), (apart, 0), (30, 0)))


def test_doubling_back_is_quick_on_a_long_dense_route():
    n = 10_000  # ~1,000 miles east from (35, -100), gently curving, like a real TomTom route
    route = [LatLon(35 + 0.5 * math.sin(12 * i / n), -100 + 17.0 * i / (n - 1)) for i in range(n)]
    start = time.perf_counter()
    assert not tomtom.doubles_back(route)
    assert time.perf_counter() - start < 0.5  # about 40 ms on a laptop


def along(points: list[LatLon]) -> list[float]:
    """How far along the route each point is, as a share of its length."""
    cum = cumulative_m(points)
    return [m / cum[-1] for m in cum]


def boxed(point: LatLon, boxes: list[tomtom.Box]) -> bool:
    return any(b.contains(point) for b in boxes)


def test_avoid_boxes_cover_the_middle_of_the_route_and_no_more():
    route = line(WEST, EAST, 2000)  # 570 miles, so 5 miles is 0.9% of it
    boxes = tomtom.avoid_boxes([route], WEST, EAST)
    assert len(boxes) == tomtom.MAX_AVOID_BOXES == 10
    for p, share in zip(route, along(route)):
        if 0.3 <= share <= 0.7:
            assert boxed(p, boxes)
        elif share < 0.29 or share > 0.71:
            assert not boxed(p, boxes)


def test_avoid_boxes_reach_5_miles_past_the_road():
    boxes = tomtom.avoid_boxes([line(WEST, EAST, 2000)], WEST, EAST)
    start = -97.0  # 30% of the way from -100 to -90
    for miles, inside in [(4.9, True), (5.1, False)]:
        assert boxed(LatLon(35.0 + miles * DEG_LAT_PER_MILE, -95.0), boxes) is inside  # beside the road
        assert boxed(LatLon(35.0, start - miles * DEG_LON_PER_MILE), boxes) is inside  # before the stretch


def box_sides_m(b: tomtom.Box) -> list[float]:
    sw, nw = LatLon(b.south, b.west), LatLon(b.north, b.west)
    se, ne = LatLon(b.south, b.east), LatLon(b.north, b.east)
    return [haversine_m(sw, nw), haversine_m(sw, se), haversine_m(nw, ne)]


@pytest.mark.parametrize("kept", [1, 2])
def test_avoid_boxes_stay_within_tomtoms_limits_on_the_longest_trips(kept):
    seattle, miami = LatLon(47.6, -122.3), LatLon(25.8, -80.2)
    route = line(seattle, miami, 3000)  # about 2,700 miles
    boxes = tomtom.avoid_boxes([route] * kept, seattle, miami)
    assert len(boxes) == 10
    assert max(side for b in boxes for side in box_sides_m(b)) < 160_000
    # The blocked stretch shrinks round the middle instead of growing the boxes
    shares = [s for p, s in zip(route, along(route)) if boxed(p, boxes)]
    assert max(shares) - min(shares) < 0.35
    assert (min(shares) + max(shares)) / 2 == pytest.approx(0.5, abs=0.01)


def test_two_routes_get_five_boxes_each():
    boxes = tomtom.avoid_boxes([NORTHERN, SOUTHERN], WEST, EAST)
    assert len(boxes) == 10
    for route in (NORTHERN, SOUTHERN):
        middle = [p for p, s in zip(route, along(route)) if 0.3 <= s <= 0.7]
        assert sum(any(b.contains(p) for p in middle) for b in boxes) == 5


def test_a_box_over_the_origin_or_destination_is_left_out():
    # On a 12 mile trip, the 5 mile margin round the middle reaches both ends
    origin, destination = LatLon(35.0, -100.0), LatLon(35.0, -100.0 + 12 * DEG_LON_PER_MILE)
    boxes = tomtom.avoid_boxes([line(origin, destination, 200)], origin, destination)
    assert 0 < len(boxes) < 10
    assert not boxed(origin, boxes) and not boxed(destination, boxes)


def test_blocking_the_real_fastest_route_leaves_tomtoms_other_two_routes_open():
    r0, r1, r2 = real_routes()
    origin, destination = r0.points[0], r0.points[-1]
    boxes = tomtom.avoid_boxes([r0.points], origin, destination)
    assert any(boxed(p, boxes) for p in r0.points)
    assert not any(boxed(p, boxes) for p in r1.points + r2.points)
    boxes = tomtom.avoid_boxes([r0.points, r1.points], origin, destination)
    assert not any(boxed(p, boxes) for p in r2.points)


# Trips below run west to east along latitude 35, so "left" of the trip is north.
SPEED_MPS = 25.0


def through(bend: LatLon) -> list[LatLon]:
    """A road from WEST to EAST that bends at `bend`."""
    return line(WEST, bend, 500) + line(bend, EAST, 500)[1:]


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

    The request for the trip itself, a GET, is answered with `trip`: a list of (points,
    slowdown) or an httpx.Response. With no `trip`, that request is a test failure. A request
    that avoids areas, a POST, is answered with `answer(boxes, alternatives)`: a list of
    (points, slowdown), None for an HTTP 400, or an httpx.Response to send as it is. The
    rectangles and maxAlternatives of every such request are kept in `asked`.
    """

    def __init__(self, answer=lambda boxes, alternatives: None, *, trip=None):
        self.answer = answer
        self.trip = trip
        self.asked: list[tuple[list[dict], int]] = []
        self.trip_calls = 0
        self.client = httpx.Client(transport=httpx.MockTransport(self._respond))

    def _respond(self, request: httpx.Request) -> httpx.Response:
        alternatives = int(request.url.params["maxAlternatives"])
        if request.method == "GET":
            assert self.trip is not None, "only fallback requests were expected"
            self.trip_calls += 1
            result = self.trip
        else:
            rectangles = json.loads(request.content)["avoidAreas"]["rectangles"]
            self.asked.append((rectangles, alternatives))
            boxes = [
                tomtom.Box(
                    r["southWestCorner"]["latitude"],
                    r["southWestCorner"]["longitude"],
                    r["northEastCorner"]["latitude"],
                    r["northEastCorner"]["longitude"],
                )
                for r in rectangles
            ]
            result = self.answer(boxes, alternatives)
        if result is None:
            return httpx.Response(400, json=NO_ROUTE_400)
        if isinstance(result, httpx.Response):
            return result
        routes = []
        for points, slowdown in result:
            length = cumulative_m(points)[-1]
            routes.append(
                {
                    "summary": {"lengthInMeters": length, "travelTimeInSeconds": length / SPEED_MPS * slowdown},
                    "legs": [{"points": [{"latitude": p.lat, "longitude": p.lon} for p in points]}],
                }
            )
        return httpx.Response(200, json={"routes": routes})


def roads_round(*roads: tuple[list[LatLon], float]):
    """A FakeTomTom answer: of `roads`, given as (points, slowdown), the fastest ones that keep
    out of every box, as many as the request asks for, or an HTTP 400 if none does."""

    def answer(boxes: list[tomtom.Box], alternatives: int):
        clear = [road for road in roads if not any(boxed(p, boxes) for p in road[0])]
        clear.sort(key=lambda road: cumulative_m(road[0])[-1] * road[1])
        return clear[: alternatives + 1] or None

    return answer


def one_at_a_time(answer):
    """`answer`, but never with an alternative, whatever the request asks for."""
    return lambda boxes, alternatives: answer(boxes, 0)


def ensure(routes: list[RouteOption], fake: FakeTomTom) -> list[RouteOption]:
    return tomtom.ensure_three_routes(routes, WEST, EAST, T0, 20_000, "test-key", client=fake.client)


def test_three_distinct_routes_come_back_fastest_first_with_no_extra_calls():
    fake = FakeTomTom()
    routes = [drive("r0", NORTHERN, 1.1), drive("r1", FASTEST), drive("r2", SOUTHERN)]
    assert [r.id for r in ensure(routes, fake)] == ["r1", "r2", "r0"]
    assert fake.asked == []


def test_a_tomtom_alternative_on_the_same_road_as_a_faster_one_is_dropped():
    fake = FakeTomTom()
    routes = [
        drive("r0", FASTEST),
        drive("r1", shifted(FASTEST, 0.2), 1.01),
        drive("r2", NORTHERN),
        drive("r3", SOUTHERN),
    ]
    assert [r.id for r in ensure(routes, fake)] == ["r0", "r2", "r3"]
    assert fake.asked == []


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


@pytest.mark.parametrize("field", ["duration_s", "distance_m"])
def test_a_tomtom_alternative_over_1_35_times_slower_or_longer_is_dropped(field):
    fast, other = drive("r0", FASTEST), drive("r1", NORTHERN)
    setattr(other, field, getattr(fast, field) * 1.35 + 1)
    assert [r.id for r in ensure([fast, other], FakeTomTom())] == ["r0"]


@pytest.mark.parametrize("field", ["duration_s", "distance_m"])
def test_exactly_1_35_times_slower_or_longer_is_kept(field):
    fast, other = drive("r0", FASTEST), drive("r1", NORTHERN)
    setattr(other, field, getattr(fast, field) * 1.35)
    assert [r.id for r in ensure([fast, other], FakeTomTom())] == ["r0", "r1"]


def test_a_shorter_but_slower_alternative_is_kept():
    # Like Dallas -> Denver through Amarillo: 798 miles against 882, but 12.4 h against 11.9 h
    fast, other = drive("r0", FASTEST), drive("r1", NORTHERN, 1.05)
    other.distance_m = fast.distance_m * 0.9
    assert [r.id for r in ensure([fast, other], FakeTomTom())] == ["r0", "r1"]


FAR_NORTH = through(LatLon(37.0, -95.0))
FAR_SOUTH = through(LatLon(33.0, -95.0))
# Every road is clear of boxes over the middle of any other: they are 69 miles apart halfway.
ROADS = roads_round((FASTEST, 1.0), (NORTHERN, 1.0), (SOUTHERN, 1.0), (FAR_NORTH, 1.0), (FAR_SOUTH, 1.0))


def test_the_time_limit_is_against_the_fastest_route_not_the_last_kept():
    fast, slower, slowest = drive("r0", FASTEST), drive("r1", NORTHERN), drive("r2", SOUTHERN)
    slower.duration_s = fast.duration_s * 1.2  # 12 h against 10 h
    slowest.duration_s = fast.duration_s * 1.6  # 16 h: within 1.35x of 12 h, not of 10 h
    routes = ensure([fast, slower, slowest], FakeTomTom(roads_round((FAR_SOUTH, 1.0))))
    assert [r.id for r in routes] == ["r0", "around2-0", "r1"]


def with_spur(points: list[LatLon], miles: float = 5) -> list[LatLon]:
    """`points` with a drive `miles` north up a side road and back, halfway along."""
    k = len(points) // 2
    tip = LatLon(points[k].lat + miles * DEG_LAT_PER_MILE, points[k].lon)
    return points[:k] + line(points[k], tip, 50) + line(tip, points[k], 50)[1:] + points[k + 1 :]


def test_one_tomtom_route_is_topped_up_with_two_routes_round_its_middle():
    fake = FakeTomTom(ROADS)
    routes = ensure([drive("r0", FASTEST)], fake)
    # The northern road is a little shorter: degrees of longitude shrink going north.
    assert [r.id for r in routes] == ["r0", "around1-0", "around1-1"]
    assert [r.points for r in routes] == [FASTEST, NORTHERN, SOUTHERN]
    assert {r.source for r in routes} == {"tomtom"}
    # One request: the best road round the fastest route's middle, and an alternative
    [(rectangles, alternatives)] = fake.asked
    assert rectangles == [b.to_tomtom() for b in tomtom.avoid_boxes([FASTEST], WEST, EAST)]
    assert alternatives == 1


def test_two_tomtom_routes_get_a_third_round_the_middle_of_both():
    fake = FakeTomTom(ROADS)
    routes = ensure([drive("r0", FASTEST), drive("r1", NORTHERN)], fake)
    assert [r.id for r in routes] == ["r0", "r1", "around2-0"]
    assert routes[2].points == SOUTHERN
    [(rectangles, alternatives)] = fake.asked
    assert rectangles == [b.to_tomtom() for b in tomtom.avoid_boxes([FASTEST, NORTHERN], WEST, EAST)]
    assert alternatives == 0


def test_a_second_request_goes_round_the_route_the_first_one_found():
    fake = FakeTomTom(one_at_a_time(ROADS))
    routes = ensure([drive("r0", FASTEST)], fake)
    assert [r.points for r in routes] == [FASTEST, NORTHERN, SOUTHERN]
    assert [r.id for r in routes] == ["r0", "around1-0", "around2-0"]
    first, second = fake.asked
    assert first == ([b.to_tomtom() for b in tomtom.avoid_boxes([FASTEST], WEST, EAST)], 1)
    assert second == ([b.to_tomtom() for b in tomtom.avoid_boxes([FASTEST, NORTHERN], WEST, EAST)], 0)


def test_at_most_three_tomtom_requests_per_plan():
    # TomTom has one route for the trip, then one more round each set of boxes, for ever
    fake = FakeTomTom(one_at_a_time(ROADS), trip=[(FASTEST, 1.0)])
    found = tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=fake.client)
    assert len(ensure(found, fake)) == 3
    assert fake.trip_calls + len(fake.asked) == 3


BAD_TIME = {"lengthInMeters": 1000, "travelTimeInSeconds": 60, "departureTime": "soon"}


@pytest.mark.parametrize(
    "reply",
    [
        None,  # HTTP 400: no road round the boxes
        [(shifted(FASTEST, 0.2), 1.01)],  # the boxes ignored, so the same road again
        [(NORTHERN, 2.0)],  # over 1.35x the fastest route's time
        [(with_spur(NORTHERN), 1.0)],  # doubles back
        httpx.Response(200, text="<html>Bad gateway</html>"),
        httpx.Response(200, json={"routes": [{"legs": []}]}),  # no summary
        httpx.Response(200, json={"routes": [{"summary": BAD_TIME, "legs": []}]}),
        httpx.Response(200, json={"routes": [None]}),
    ],
    ids=["no-route", "same-road", "too-slow", "doubles-back", "not-json", "no-summary", "bad-time", "null-route"],
)
def test_the_top_up_stops_at_a_request_that_adds_no_route(reply):
    fake = FakeTomTom(lambda boxes, alternatives: reply)
    assert [r.id for r in ensure([drive("r0", FASTEST)], fake)] == ["r0"]
    assert len(fake.asked) == 1  # asking again would get the same answer


def test_a_route_round_the_boxes_that_doubles_back_is_dropped_but_its_alternative_kept():
    fake = FakeTomTom(lambda boxes, alternatives: [(with_spur(NORTHERN), 1.0), (SOUTHERN, 1.0)])
    routes = ensure([drive("r0", FASTEST), drive("r1", FAR_NORTH)], fake)
    assert [r.points for r in routes] == [FASTEST, SOUTHERN, FAR_NORTH]


def test_no_request_when_every_box_would_cover_an_end():
    # On a 2 mile trip, boxes reaching 5 miles past the middle cover both ends
    end = LatLon(35.0, -100.0 + 2 * DEG_LON_PER_MILE)
    fake = FakeTomTom(ROADS)
    trip = [drive("r0", line(WEST, end, 30))]
    kept = tomtom.ensure_three_routes(trip, WEST, end, T0, 20_000, "test-key", client=fake.client)
    assert [r.id for r in kept] == ["r0"]
    assert fake.asked == []


def test_a_tomtom_alternative_that_doubles_back_is_dropped():
    routes = [drive("r0", FASTEST), drive("r1", with_spur(NORTHERN)), drive("r2", SOUTHERN)]
    assert [r.id for r in ensure(routes, FakeTomTom())] == ["r0", "r2"]


def test_turning_round_where_the_fastest_route_does_too_is_not_doubling_back():
    # Every route sets off the wrong way along a divided road and turns round at an exit
    # 3 miles back, so that can't count against the alternatives.
    exit_ = LatLon(35.0, -100.0 - 3 * DEG_LON_PER_MILE)
    turn = line(WEST, exit_, 30) + line(exit_, WEST, 30)[1:]
    other = turn + NORTHERN[1:]
    assert tomtom.doubles_back(other)
    routes = [drive("r0", turn + FASTEST[1:]), drive("r1", other), drive("r2", turn + SOUTHERN[1:])]
    assert [r.id for r in ensure(routes, FakeTomTom())] == ["r0", "r1", "r2"]


def test_a_reply_that_is_not_json_is_a_routing_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>")))
    with pytest.raises(tomtom.RoutingError, match="not JSON"):
        tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)


@pytest.mark.parametrize(
    "body",
    [
        {"routes": [{"legs": []}]},
        {"routes": [{"summary": BAD_TIME, "legs": []}]},
        {"routes": [{"summary": {"lengthInMeters": "far", "travelTimeInSeconds": 60}}]},
        {"routes": [None]},
        [{"routes": []}],
    ],
    ids=["no-summary", "bad-time", "bad-length", "null-route", "list"],
)
def test_a_200_reply_in_an_unexpected_shape_is_a_routing_error(body):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    with pytest.raises(tomtom.RoutingError, match="unexpected shape") as exc_info:
        tomtom.calculate_routes(WEST, EAST, T0, 20_000, "test-key", client=client)
    assert not isinstance(exc_info.value, tomtom.NoRouteError)


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


def test_a_fallback_faster_than_tomtoms_route_comes_first():
    fake = FakeTomTom(roads_round((NORTHERN, 0.9), (SOUTHERN, 0.9)))
    assert [r.id for r in ensure([drive("r0", FASTEST)], fake)] == ["around1-0", "around1-1", "r0"]


def test_a_fallback_faster_than_tomtoms_route_sets_the_time_limit():
    # TomTom gives 10 h and 13.4 h, then a fallback takes 9.3 h: 13.4 h is now over 1.35x that.
    fast, slow = drive("r0", FASTEST), drive("r1", NORTHERN)
    slow.duration_s = fast.duration_s * 1.34
    fake = FakeTomTom(roads_round((SOUTHERN, 0.9)))
    assert [r.id for r in ensure([fast, slow], fake)] == ["around2-0", "r0"]


def test_never_more_than_three_routes():
    routes = [drive("r0", FASTEST), drive("r1", NORTHERN), drive("r2", SOUTHERN), drive("r3", FAR_NORTH)]
    assert [r.id for r in ensure(routes, FakeTomTom())] == ["r0", "r1", "r2"]


def test_route_ids_are_unique_across_tomtom_and_every_fallback():
    ids = [r.id for r in ensure([drive("r0", FASTEST)], FakeTomTom(one_at_a_time(ROADS)))]
    assert len(ids) == len(set(ids)) == 3


def test_no_routes_means_no_fallbacks():
    fake = FakeTomTom(ROADS)
    assert ensure([], fake) == []
    assert fake.asked == []


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
    assert fake.asked == []


def test_none_of_the_real_tomtom_routes_doubles_back():
    assert not any(tomtom.doubles_back(r.points) for r in real_routes())


# Two real TomTom routes through via points on Dallas -> Denver, cut down to 30 miles either
# side of where they turn back: a 4 mile spur up a side road off I-40 west of Amarillo, and a
# loop round a square mile in Nebraska.
DETOURS = json.loads((Path(__file__).parent / "data" / "via_point_detours.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(DETOURS))
def test_real_routes_through_via_points_that_double_back_are_caught(name):
    assert tomtom.doubles_back([LatLon(lat, lon) for lat, lon in DETOURS[name]])
