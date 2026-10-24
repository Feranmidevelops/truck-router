"""TomTom Routing and Search client.

Calculate Route: https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/calculate-route/
- maxAlternatives 0-5; TomTom may return fewer than asked.
- travelMode=truck, vehicleWeight in kilograms, vehicleCommercial=true.
- departAt is RFC 3339 and must not be in the past; send "now" when it is.
- guidance.instructions[].routeOffsetInMeters / travelTimeInSeconds are both
  measured from the route start, which gives us a timing profile for ETAs.
Fuzzy Search: https://docs.tomtom.com/search-api/documentation/search-service/fuzzy-search/

Field names in the route response (summary.*, legs[].points[]) were taken from
the docs. Check them against the first live response.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property
from itertools import accumulate
import math
from operator import attrgetter
from urllib.parse import quote

import httpx

from .geometry import cumulative_m, haversine_m
from .types import LatLon, RouteOption, TimingPoint

BASE_URL = "https://api.tomtom.com"
TRUCK_TARE_LB = 35_000  # assumed empty tractor-trailer weight; the user enters cargo weight
LB_TO_KG = 0.45359237

MAX_SLOWDOWN = 1.5  # drop a route taking over 1.5x the fastest route's time
MAX_OVERLAP = 0.9  # drop a route when over 90% of it runs along a route already kept
FALLBACK_OFFSETS = (0.15, 0.25)  # via point distance from the route, as a share of the straight-line trip

METERS_PER_MILE = 1609.344
OVERLAP_STEP_M = 5 * METERS_PER_MILE  # sample spacing along the candidate route
OVERLAP_NEAR_M = 0.5 * METERS_PER_MILE  # a sample this close to the other route counts as shared road
_M_PER_DEG_LAT = haversine_m(LatLon(0, 0), LatLon(1, 0))

_duration = attrgetter("duration_s")


class RoutingError(Exception):
    pass


@dataclass(frozen=True)
class Place:
    label: str
    lat: float
    lon: float


def vehicle_weight_kg(load_lb: float) -> int:
    return round((load_lb + TRUCK_TARE_LB) * LB_TO_KG)


def geocode(
    query: str, api_key: str, *, country_set: str = "US", limit: int = 5, client: httpx.Client | None = None
) -> list[Place]:
    url = f"{BASE_URL}/search/2/search/{quote(query)}.json"
    params = {"key": api_key, "typeahead": "true", "limit": limit, "countrySet": country_set}
    payload = _get(url, params, client)
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
    via: list[LatLon] | None = None,
    max_alternatives: int = 2,
    source: str = "tomtom",
    id_prefix: str = "r",
    client: httpx.Client | None = None,
) -> list[RouteOption]:
    stops = [origin, *(via or []), destination]
    locations = ":".join(f"{p.lat:.6f},{p.lon:.6f}" for p in stops)
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
        "maxAlternatives": max_alternatives if not via else 0,
        "routeRepresentation": "polyline",
        "instructionsType": "coded",
    }
    payload = _get(url, params, client)
    return parse_routes(payload, departure, source=source, id_prefix=id_prefix)


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
    """Up to 3 distinct routes, fastest first, topped up with via-point fallbacks if needed.

    The fastest route is always kept. Any other route, TomTom's own alternatives included,
    is dropped when it takes over 1.5x the fastest route's time, or when over 90% of it runs
    along a route already kept.

    While fewer than 3 are kept, ask TomTom for a route through a point left and one right of
    the fastest route's midpoint, 15% of the straight-line trip away, then 25%. That is at most
    4 extra calls, sent two at a time. A fallback that fails is skipped. If fewer than 3
    survive, the caller gets what is left.
    """
    lat0 = (origin.lat + destination.lat) / 2
    kept: list[tuple[RouteOption, _Trace]] = []

    def consider(route: RouteOption) -> None:
        if len(kept) >= 3:
            return
        if kept and route.duration_s > MAX_SLOWDOWN * kept[0][0].duration_s:
            return
        trace = _Trace(route.points, lat0)
        if any(trace.overlap(other) > MAX_OVERLAP for _, other in kept):
            return
        kept.append((route, trace))

    def fetch(via: LatLon, id_prefix: str) -> list[RouteOption]:
        try:
            return calculate_routes(
                origin, destination, departure, load_lb, api_key,
                via=[via], source="via-fallback", id_prefix=id_prefix, client=client,
            )
        except RoutingError:
            return []  # e.g. no road near the via point

    for route in sorted(routes, key=_duration):
        consider(route)

    for share in FALLBACK_OFFSETS:
        if not kept or len(kept) >= 3:
            break
        vias = offset_via_points(kept[0][0].points, origin, destination, share)
        prefixes = [f"via{round(share * 100)}{side}-" for side in "LR"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            candidates = [r for found in pool.map(fetch, vias, prefixes) for r in found]
        for route in sorted(candidates, key=_duration):
            consider(route)

    return sorted((route for route, _ in kept), key=_duration)


def offset_via_points(route: list[LatLon], origin: LatLon, destination: LatLon, share: float) -> list[LatLon]:
    """Points left and right of the route's halfway point, at right angles to the straight
    origin->destination line and `share` of its length away. Left first.

    The right angle is found in a flat local projection around the halfway point. There a
    degree of longitude is only cos(lat) as long as a degree of latitude, so longitudes are
    multiplied by cos(lat) on the way in and divided by it on the way out. Without that, the
    offset would lean east-west and the left and right points would end up uneven.
    """
    cum = cumulative_m(route)
    mid = _point_at(route, cum, cum[-1] / 2)
    k = math.cos(math.radians(mid.lat))
    east, north = (destination.lon - origin.lon) * k, destination.lat - origin.lat
    length = math.hypot(east, north)
    if length == 0:
        return []
    away = share * haversine_m(origin, destination) / _M_PER_DEG_LAT  # in degrees of latitude
    # A quarter turn anticlockwise takes the direction of travel (east, north) to (-north, east),
    # which points to the driver's left.
    left_east, left_north = -north / length * away, east / length * away
    return [
        LatLon(mid.lat + left_north, mid.lon + left_east / k),
        LatLon(mid.lat - left_north, mid.lon - left_east / k),
    ]


def overlap_fraction(route: list[LatLon], other: list[LatLon]) -> float:
    """Share of `route`, sampled every ~5 miles, that lies within 0.5 mile of `other`."""
    if not route or not other:
        return 0.0
    lat0 = (route[0].lat + route[-1].lat) / 2
    return _Trace(route, lat0).overlap(_Trace(other, lat0))


class _Trace:
    """A route in flat local metres, plus what the overlap test needs, built on first use.

    Longitude degrees are scaled by cos(lat0) so that x and y are both metres. One lat0 for
    the whole trip is close enough: 250 miles north or south of it the east-west scale is
    about 5% off, 500 miles about 10%, so at worst the 0.5 mile test acts like 0.45-0.55 mile.
    """

    def __init__(self, points: list[LatLon], lat0: float):
        kx = _M_PER_DEG_LAT * math.cos(math.radians(lat0))
        self.xy = [(p.lon * kx, p.lat * _M_PER_DEG_LAT) for p in points]

    @cached_property
    def samples(self) -> list[tuple[float, float]]:
        """The centre of each of n equal slices of the route, n chosen so slices are ~5 miles.

        Centres rather than slice ends, so the start and end that every route shares don't count.
        """
        xy = self.xy
        if len(xy) < 2:
            return list(xy)
        cum = list(accumulate(map(math.dist, xy, xy[1:]), initial=0.0))
        total = cum[-1]
        n = max(1, round(total / OVERLAP_STEP_M))
        out = []
        i = 0
        for k in range(n):
            d = (k + 0.5) * total / n
            while cum[i + 1] < d:
                i += 1
            seg = cum[i + 1] - cum[i]
            t = (d - cum[i]) / seg if seg else 0.0
            (ax, ay), (bx, by) = xy[i], xy[i + 1]
            out.append((ax + t * (bx - ax), ay + t * (by - ay)))
        return out

    @cached_property
    def cells(self) -> dict[tuple[int, int], list[tuple[float, float, float, float]]]:
        """Segments bucketed into square cells one OVERLAP_NEAR_M wide.

        Long segments are cut into pieces no longer than a cell first, so a piece lands in
        at most 2x2 cells and a straight 30 km stretch doesn't fill a huge bounding box.
        """
        size = OVERLAP_NEAR_M
        cells = defaultdict(list)
        for (ax, ay), (bx, by) in zip(self.xy, self.xy[1:]):
            i, j = math.floor(ax / size), math.floor(ay / size)
            if i == math.floor(bx / size) and j == math.floor(by / size):
                cells[(i, j)].append((ax, ay, bx, by))  # the usual case on a dense polyline
                continue
            pieces = max(1, math.ceil(math.dist((ax, ay), (bx, by)) / size))
            dx, dy = (bx - ax) / pieces, (by - ay) / pieces
            for k in range(pieces):
                px, py = ax + k * dx, ay + k * dy
                qx, qy = px + dx, py + dy
                piece = (px, py, qx, qy)
                for i in range(math.floor(min(px, qx) / size), math.floor(max(px, qx) / size) + 1):
                    for j in range(math.floor(min(py, qy) / size), math.floor(max(py, qy) / size) + 1):
                        cells[(i, j)].append(piece)
        return cells

    def is_near(self, x: float, y: float) -> bool:
        # Any road within OVERLAP_NEAR_M of (x, y) must pass through this cell or one of its
        # eight neighbours, because cells are exactly that wide.
        size = OVERLAP_NEAR_M
        i, j = math.floor(x / size), math.floor(y / size)
        cells = self.cells
        for ci in (i - 1, i, i + 1):
            for cj in (j - 1, j, j + 1):
                for piece in cells.get((ci, cj), ()):
                    if _dist2_to_segment(x, y, *piece) <= size * size:
                        return True
        return False

    def overlap(self, other: _Trace) -> float:
        samples = self.samples
        if not samples:
            return 0.0
        return sum(other.is_near(x, y) for x, y in samples) / len(samples)


def _point_at(points: list[LatLon], cumulative: list[float], meters: float) -> LatLon:
    """Position `meters` along the polyline, interpolated between the points either side."""
    i = bisect_left(cumulative, meters)
    if i == 0:
        return points[0]
    if i == len(points):
        return points[-1]
    a, b = points[i - 1], points[i]
    t = (meters - cumulative[i - 1]) / (cumulative[i] - cumulative[i - 1])
    return LatLon(a.lat + t * (b.lat - a.lat), a.lon + t * (b.lon - a.lon))


def _dist2_to_segment(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Squared distance from (x, y) to the segment a-b, in flat coordinates."""
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
    ex, ey = ax + t * dx - x, ay + t * dy - y
    return ex * ex + ey * ey


def _get(url: str, params: dict, client: httpx.Client | None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise RoutingError(f"TomTom request failed: {exc}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code != 200:
        raise RoutingError(f"TomTom {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
