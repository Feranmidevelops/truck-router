"""TomTom Routing and Search client.

Calculate Route: https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/calculate-route/
- maxAlternatives 0-5; TomTom may return fewer than asked.
- travelMode=truck, vehicleWeight in kilograms, vehicleCommercial=true.
- departAt is RFC 3339 and must not be in the past; send "now" when it is.
- guidance.instructions[].routeOffsetInMeters / travelTimeInSeconds are both
  measured from the route start, which gives us a timing profile for ETAs.
- avoidAreas go in a POST body (the query stays the same): up to 10 rectangles, each a
  southWestCorner and northEastCorner, at most about 160 x 160 km.
Fuzzy Search: https://docs.tomtom.com/search-api/documentation/search-service/fuzzy-search/

A real route response, trimmed, is in routing/tests/data/tomtom_dallas_denver.json.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property
from itertools import accumulate
import math
from operator import attrgetter
from urllib.parse import quote

import httpx

from .geometry import METERS_PER_MILE, cumulative_m, haversine_m, point_at_distance
from .types import LatLon, RouteOption, TimingPoint

BASE_URL = "https://api.tomtom.com"
TRUCK_TARE_LB = 35_000  # assumed empty tractor-trailer weight; the user enters cargo weight
LB_TO_KG = 0.45359237

# Drop a route taking over 1.35x the fastest route's time or over 1.35x its length. Live,
# TomTom's own alternatives that drivers would consider came in at up to 1.29x the time and
# 1.22x the length; the detours worth dropping at 1.40x and more of either.
MAX_SLOWDOWN = 1.35
MAX_LONGER = 1.35
MAX_OVERLAP = 0.9  # drop a route when over 90% of it runs along a route already kept
AVOID_SPAN = (0.3, 0.7)  # the middle of a route, as shares of its length, for the next one to keep out of
AVOID_MARGIN_M = 5 * METERS_PER_MILE  # how far the boxes over it reach past the road
MAX_AVOID_BOXES = 10  # TomTom's limit per request
MAX_BOX_SIDE_M = 150_000  # TomTom allows about 160 km a side

REQUEST_TIMEOUT_S = 20
# A fallback request answered in 1.3-3.3 s live. One still silent after 10 s is skipped like any
# failed fallback, so it can't hold the plan up for the full 20 s.
FALLBACK_TIMEOUT_S = 10

OVERLAP_STEP_M = 5 * METERS_PER_MILE  # sample spacing along the candidate route
OVERLAP_NEAR_M = 0.5 * METERS_PER_MILE  # a sample this close to the other route counts as shared road
# A route doubles back when, over more than a quarter mile of it, it is back within 0.3 mile
# of road it drove at least 3 miles earlier. Checked every quarter mile. An interchange ramp
# loops back on itself within a mile, and a parallel road is further than 0.3 mile away.
LOOP_STEP_M = 0.25 * METERS_PER_MILE
LOOP_NEAR_M = 0.3 * METERS_PER_MILE
LOOP_GAP_M = 3 * METERS_PER_MILE
LOOP_MAX_M = 0.25 * METERS_PER_MILE
_M_PER_DEG_LAT = haversine_m(LatLon(0, 0), LatLon(1, 0))

_duration = attrgetter("duration_s")


class RoutingError(Exception):
    pass


NO_ROUTE_MESSAGE = "No truck route found between these places."


class NoRouteError(RoutingError):
    """TomTom answered, but has no truck route between the places."""


@dataclass(frozen=True)
class Place:
    label: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Box:
    """An area for TomTom to route around, in degrees."""

    south: float
    west: float
    north: float
    east: float

    def contains(self, p: LatLon) -> bool:
        return self.south <= p.lat <= self.north and self.west <= p.lon <= self.east

    def to_tomtom(self) -> dict:
        return {
            "southWestCorner": {"latitude": round(self.south, 6), "longitude": round(self.west, 6)},
            "northEastCorner": {"latitude": round(self.north, 6), "longitude": round(self.east, 6)},
        }


def vehicle_weight_kg(load_lb: float) -> int:
    return round((load_lb + TRUCK_TARE_LB) * LB_TO_KG)


def geocode(
    query: str, api_key: str, *, country_set: str = "US", limit: int = 5, client: httpx.Client | None = None
) -> list[Place]:
    url = f"{BASE_URL}/search/2/search/{quote(query)}.json"
    # Places, addresses and streets only: points of interest crowd out towns ("salt lake" brought up
    # a street in Huntington Park, CA) and make the search slower.
    params = {
        "key": api_key,
        "typeahead": "true",
        "limit": limit,
        "countrySet": country_set,
        "idxSet": "Geo,Addr,PAD,Str",
    }
    payload = _request(url, params, client)
    return [
        Place(
            label=r["address"]["freeformAddress"],
            lat=r["position"]["lat"],
            lon=r["position"]["lon"],
        )
        for r in payload.get("results", [])
    ]


def calculate_routes(
    origin: LatLon,
    destination: LatLon,
    departure: datetime,
    load_lb: float,
    api_key: str,
    *,
    avoid: list[Box] | None = None,
    max_alternatives: int = 2,
    id_prefix: str = "r",
    timeout: float = REQUEST_TIMEOUT_S,
    client: httpx.Client | None = None,
) -> list[RouteOption]:
    """TomTom's truck routes for the trip, fastest first. With `avoid`, none goes into those boxes."""
    locations = f"{origin.lat:.6f},{origin.lon:.6f}:{destination.lat:.6f},{destination.lon:.6f}"
    url = f"{BASE_URL}/routing/1/calculateRoute/{locations}/json"
    now = datetime.now(timezone.utc)
    # Whole seconds, like the dateTime examples in TomTom's docs: no ".123456" fraction.
    depart_at = "now" if departure <= now + timedelta(minutes=1) else departure.isoformat(timespec="seconds")
    params = {
        "key": api_key,
        "travelMode": "truck",
        "vehicleCommercial": "true",
        "vehicleWeight": vehicle_weight_kg(load_lb),
        "routeType": "fastest",
        "traffic": "true",
        "departAt": depart_at,
        "maxAlternatives": max_alternatives,
        "routeRepresentation": "polyline",
        "instructionsType": "coded",
    }
    body = {"avoidAreas": {"rectangles": [b.to_tomtom() for b in avoid]}} if avoid else None
    payload = _request(url, params, client, timeout, body)
    routes = parse_routes(payload, departure, source="tomtom", id_prefix=id_prefix)
    if not routes:
        raise NoRouteError(NO_ROUTE_MESSAGE)
    return routes


def parse_routes(payload: dict, departure: datetime, *, source: str, id_prefix: str) -> list[RouteOption]:
    routes = []
    for i, r in enumerate(payload.get("routes", [])):
        summary = r["summary"]
        length = float(summary["lengthInMeters"])
        duration = float(summary["travelTimeInSeconds"])
        points: list[LatLon] = []
        for leg in r.get("legs", []):
            for p in leg.get("points", []):
                pt = LatLon(p["latitude"], p["longitude"])
                if not points or points[-1] != pt:
                    points.append(pt)
        dep = _parse_time(summary.get("departureTime")) or departure
        arr = _parse_time(summary.get("arrivalTime")) or dep + timedelta(seconds=duration)
        routes.append(
            RouteOption(
                id=f"{id_prefix}{i}",
                source=source,
                distance_m=length,
                duration_s=duration,
                departure_time=dep,
                arrival_time=arr,
                points=points,
                timing=_timing_profile(r.get("guidance", {}).get("instructions", []), length, duration),
            )
        )
    return routes


def _timing_profile(instructions: list[dict], length: float, duration: float) -> list[TimingPoint]:
    raw = [TimingPoint(0.0, 0.0)]
    for ins in instructions:
        if "routeOffsetInMeters" in ins and "travelTimeInSeconds" in ins:
            raw.append(TimingPoint(float(ins["routeOffsetInMeters"]), float(ins["travelTimeInSeconds"])))
    raw.append(TimingPoint(length, duration))
    raw.sort(key=lambda t: t.meters)
    # Keep a strictly increasing distance profile so interpolation never divides by zero.
    profile = [raw[0]]
    for t in raw[1:]:
        if t.meters > profile[-1].meters and t.seconds >= profile[-1].seconds:
            profile.append(t)
    return profile


def ensure_three_routes(
    routes: list[RouteOption],
    origin: LatLon,
    destination: LatLon,
    departure: datetime,
    load_lb: float,
    api_key: str,
    *,
    client: httpx.Client | None = None,
) -> list[RouteOption]:
    """Up to 3 distinct routes, fastest first, topped up with routes around the ones found.

    TomTom's fastest route is kept first. Any other route, TomTom's own alternatives included,
    is dropped when it takes over 1.35x the fastest route's time or is over 1.35x as long, when
    over 90% of it runs along a route already kept, or when it doubles back on itself (see
    _Trace.doubles_back).

    While fewer than 3 are kept, TomTom is asked for the trip again, this time keeping out of
    boxes over the middle of every route kept so far (see avoid_boxes), with an alternative
    too when two are missing. Nothing forces the truck through a point, so TomTom picks its
    own best road round the boxes, and the boxes keep that road off the ones already found.
    Asked again, a request that added nothing would get the same answer, so the top-up stops
    there. Each request either adds a route or ends the top-up, so a plan makes 2 at most. A
    request that fails is skipped. If fewer than 3 survive, the caller gets what is left.

    A fallback can, rarely, beat TomTom's fastest route. The 1.35x limits are then checked
    again against it at the end, so a route kept earlier, even TomTom's fastest, can drop out.
    """
    lat0 = (origin.lat + destination.lat) / 2
    kept: list[RouteOption] = []  # kept[0] is the fastest route TomTom gave us
    traces: list[_Trace] = []  # kept[i] projected for the overlap and doubling-back tests

    def consider(route: RouteOption) -> None:
        if len(kept) >= 3:
            return
        if kept and _too_far(route, kept[0]):
            return
        trace = _Trace(route.points, lat0)
        if any(trace.overlap(other) > MAX_OVERLAP for other in traces):
            return
        if traces and trace.doubles_back(traces[0]):
            return
        kept.append(route)
        traces.append(trace)

    for route in sorted(routes, key=_duration):
        consider(route)
    if not kept:
        return []  # no route for the trip itself, so a route around one would mislead

    own = client is None
    client = client or httpx.Client()
    try:
        while len(kept) < 3:
            boxes = avoid_boxes([r.points for r in kept], origin, destination)
            if not boxes:
                break
            missing = 3 - len(kept)
            found = _fallback(origin, destination, departure, load_lb, api_key, boxes, missing, client=client)
            for route in sorted(found, key=_duration):
                consider(route)
            if 3 - len(kept) == missing:
                break  # nothing new, and asking again would bring the same answer
    finally:
        if own:
            client.close()

    kept.sort(key=_duration)
    return [r for r in kept if not _too_far(r, kept[0])]


def _too_far(route: RouteOption, fastest: RouteOption) -> bool:
    """Whether `route` takes over 1.35x the fastest route's time or is over 1.35x as long."""
    slower = route.duration_s > MAX_SLOWDOWN * fastest.duration_s
    return slower or route.distance_m > MAX_LONGER * fastest.distance_m


def _fallback(
    origin: LatLon,
    destination: LatLon,
    departure: datetime,
    load_lb: float,
    api_key: str,
    avoid: list[Box],
    missing: int,
    *,
    client: httpx.Client,
) -> list[RouteOption]:
    """Up to `missing` routes that keep out of `avoid`, best first, or none if the request fails."""
    try:
        return calculate_routes(
            origin, destination, departure, load_lb, api_key, avoid=avoid, max_alternatives=missing - 1,
            id_prefix=f"around{3 - missing}-", timeout=FALLBACK_TIMEOUT_S, client=client,
        )
    except (RoutingError, KeyError, ValueError):
        return []  # e.g. no road round the boxes, or a reply we can't read


def avoid_boxes(routes: list[list[LatLon]], origin: LatLon, destination: LatLon) -> list[Box]:
    """Boxes over the middle of each route, for TomTom to route around. 10 at most in all.

    Each route's stretch from 30% to 70% of the way along is cut into equal pieces, one box
    each, reaching 5 miles past its piece of road all round. Small boxes hug a diagonal road,
    where one big box would also shut the country either side of it. A piece is never more
    than 134 km of road, which keeps its box under TomTom's 160 km limit; on a very long route
    the stretch shrinks round the middle instead. A box over the origin or destination is left
    out, since the route has to go there.
    """
    per_route = MAX_AVOID_BOXES // len(routes)
    longest = MAX_BOX_SIDE_M - 2 * AVOID_MARGIN_M
    boxes = []
    for points in routes:
        cum = cumulative_m(points)
        start, end = (share * cum[-1] for share in AVOID_SPAN)
        piece = min((end - start) / per_route, longest)
        start = (start + end - piece * per_route) / 2
        for k in range(per_route):
            boxes.append(_box_over(points, cum, start + k * piece, start + (k + 1) * piece))
    return [b for b in boxes if not b.contains(origin) and not b.contains(destination)]


def _box_over(points: list[LatLon], cum: list[float], start: float, end: float) -> Box:
    """The box round the road from `start` to `end` metres along, AVOID_MARGIN_M wider all round."""
    road = [point_at_distance(points, cum, start)]
    road += points[bisect_right(cum, start) : bisect_left(cum, end)]
    road.append(point_at_distance(points, cum, end))
    south, north = min(p.lat for p in road), max(p.lat for p in road)
    west, east = min(p.lon for p in road), max(p.lon for p in road)
    dlat = AVOID_MARGIN_M / _M_PER_DEG_LAT
    dlon = dlat / math.cos(math.radians((south + north) / 2))
    return Box(south - dlat, west - dlon, north + dlat, east + dlon)


def overlap_fraction(route: list[LatLon], other: list[LatLon]) -> float:
    """Share of `route`, sampled every ~5 miles, that lies within 0.5 mile of `other`."""
    if not route or not other:
        return 0.0
    lat0 = (route[0].lat + route[-1].lat) / 2
    return _Trace(route, lat0).overlap(_Trace(other, lat0))


def doubles_back(route: list[LatLon]) -> bool:
    """Whether the route comes back onto road it drove earlier: a spur out to some point and
    back the same way, or a loop. See LOOP_* for the thresholds."""
    if not route:
        return False
    lat0 = (route[0].lat + route[-1].lat) / 2
    return _Trace(route, lat0).doubles_back()


class _Trace:
    """A route in flat local metres, plus what the overlap and doubling-back tests need, built
    on first use.

    Longitude degrees are scaled by cos(lat0) so that x and y are both metres. One lat0 for
    the whole trip is close enough. North-south gaps come out exact, but an east-west gap at
    latitude lat comes out cos(lat0) / cos(lat) times its true size, so there the 0.5 mile test
    acts like 0.5 * cos(lat) / cos(lat0) mile: 0.48-0.52 mile on Dallas->Denver, 0.42-0.56 mile
    on the longest north-south US trips (Laredo->Duluth, Miami->Seattle). Shared road lies
    within metres of itself and separate roads are usually miles apart, so that is fine.
    """

    def __init__(self, points: list[LatLon], lat0: float):
        kx = _M_PER_DEG_LAT * math.cos(math.radians(lat0))
        self.xy = [(p.lon * kx, p.lat * _M_PER_DEG_LAT) for p in points]
        self.along = list(accumulate(map(math.dist, self.xy, self.xy[1:]), initial=0.0))

    def slices(self, step: float) -> list[tuple[float, float, float]]:
        """The route cut into n equal slices, n chosen so slices are about `step` long: the
        centre of each, and how far along the route that is.

        Centres rather than slice ends, so the start and end that every route shares don't count.
        """
        xy, cum = self.xy, self.along
        if len(xy) < 2:
            return [(x, y, 0.0) for x, y in xy]
        total = cum[-1]
        n = max(1, round(total / step))
        out = []
        i = 0
        for k in range(n):
            d = (k + 0.5) * total / n
            while cum[i + 1] < d:
                i += 1
            seg = cum[i + 1] - cum[i]
            t = (d - cum[i]) / seg if seg else 0.0
            (ax, ay), (bx, by) = xy[i], xy[i + 1]
            out.append((ax + t * (bx - ax), ay + t * (by - ay), d))
        return out

    @cached_property
    def samples(self) -> list[tuple[float, float]]:
        """A point every ~5 miles, for the overlap test."""
        return [(x, y) for x, y, _ in self.slices(OVERLAP_STEP_M)]

    @cached_property
    def cells(self) -> dict[tuple[int, int], list[tuple[float, float, float, float, float]]]:
        """Segments bucketed into square cells one OVERLAP_NEAR_M wide.

        Long segments are cut into pieces no longer than a cell first, so a piece lands in
        at most 2x2 cells and a straight 30 km stretch doesn't fill a huge bounding box.
        Each piece is (ax, ay, bx, by, how far along the route b is).
        """
        size = OVERLAP_NEAR_M
        cells = defaultdict(list)
        for (ax, ay), (bx, by), start, end in zip(self.xy, self.xy[1:], self.along, self.along[1:]):
            i, j = math.floor(ax / size), math.floor(ay / size)
            if i == math.floor(bx / size) and j == math.floor(by / size):
                cells[(i, j)].append((ax, ay, bx, by, end))  # the usual case on a dense polyline
                continue
            pieces = max(1, math.ceil(math.dist((ax, ay), (bx, by)) / size))
            dx, dy = (bx - ax) / pieces, (by - ay) / pieces
            for k in range(pieces):
                px, py = ax + k * dx, ay + k * dy
                qx, qy = px + dx, py + dy
                piece = (px, py, qx, qy, start + (end - start) * (k + 1) / pieces)
                for i in range(math.floor(min(px, qx) / size), math.floor(max(px, qx) / size) + 1):
                    for j in range(math.floor(min(py, qy) / size), math.floor(max(py, qy) / size) + 1):
                        cells[(i, j)].append(piece)
        return cells

    def is_near(self, x: float, y: float, within: float = OVERLAP_NEAR_M, before: float = math.inf) -> bool:
        """Whether the route passes within `within` of (x, y), counting only road that ends
        no more than `before` along it. `within` can't be more than a cell's width."""
        # Any road within a cell's width of (x, y) must pass through this cell or one of its
        # eight neighbours.
        size = OVERLAP_NEAR_M
        i, j = math.floor(x / size), math.floor(y / size)
        cells = self.cells
        for ci in (i - 1, i, i + 1):
            for cj in (j - 1, j, j + 1):
                for ax, ay, bx, by, along in cells.get((ci, cj), ()):
                    if along <= before and _dist2_to_segment(x, y, ax, ay, bx, by) <= within * within:
                        return True
        return False

    def overlap(self, other: _Trace) -> float:
        samples = self.samples
        if not samples:
            return 0.0
        return sum(other.is_near(x, y) for x, y in samples) / len(samples)

    @cached_property
    def back_on_itself(self) -> list[tuple[float, float]]:
        """Centres of the ~LOOP_STEP_M slices of the route that lie within LOOP_NEAR_M of road
        it drove at least LOOP_GAP_M before."""
        centres = self.slices(LOOP_STEP_M)
        return [(x, y) for x, y, d in centres if self.is_near(x, y, LOOP_NEAR_M, before=d - LOOP_GAP_M)]

    def doubles_back(self, fastest: _Trace | None = None) -> bool:
        """Whether more than LOOP_MAX_M of the route is back on road it drove earlier.

        Where `fastest` is back on itself too, it doesn't count: if the fastest route has to
        turn round at an exit near the start, say, every route from that start does the same.
        """
        theirs = fastest.back_on_itself if fastest else []
        own = [p for p in self.back_on_itself if not any(math.dist(p, q) <= LOOP_NEAR_M for q in theirs)]
        return len(own) * LOOP_STEP_M > LOOP_MAX_M


def _dist2_to_segment(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Squared distance from (x, y) to the segment a-b, in flat coordinates."""
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
    ex, ey = ax + t * dx - x, ay + t * dy - y
    return ex * ex + ey * ey


def _request(
    url: str,
    params: dict,
    client: httpx.Client | None,
    timeout: float = REQUEST_TIMEOUT_S,
    body: dict | None = None,
) -> dict:
    """GET `url`, or POST `body` to it as JSON when there is one, and return the JSON reply."""
    own = client is None
    client = client or httpx.Client()
    try:
        if body is None:
            resp = client.get(url, params=params, timeout=timeout)
        else:
            resp = client.post(url, params=params, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        raise RoutingError(f"TomTom request failed: {exc}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code != 200:
        # An unroutable trip (e.g. Honolulu to Denver) comes back as a 400, not as an empty list
        if _error_code(resp) == "NO_ROUTE_FOUND":
            raise NoRouteError(NO_ROUTE_MESSAGE)
        raise RoutingError(f"TomTom {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RoutingError(f"TomTom sent a reply that is not JSON: {resp.text[:200]}") from exc


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    detail = body.get("detailedError") if isinstance(body, dict) else None
    return detail.get("code") if isinstance(detail, dict) else None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
