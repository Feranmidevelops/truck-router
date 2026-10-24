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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from .types import LatLon, RouteOption, TimingPoint

BASE_URL = "https://api.tomtom.com"
TRUCK_TARE_LB = 35_000  # assumed empty tractor-trailer weight; the user enters cargo weight
LB_TO_KG = 0.45359237


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
    depart_at = "now" if departure <= now + timedelta(minutes=1) else departure.isoformat()
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
    """Top up to 3 distinct routes when TomTom returns fewer.

    Approach:
    1. Take the midpoint of the fastest route.
    2. Offset it perpendicular to the origin->destination line, left and right,
       by ~15% of the straight-line distance (try 25% if still too similar).
    3. Request a route through each offset point with calculate_routes(via=[...]),
       source="via-fallback".
    4. Drop candidates that overlap an existing route by >90% (sample every ~5 mi
       and count points within 0.5 mi of the other route).
    Return at most 3 routes, fastest first. If still under 3, return what you
    have; the UI must handle 1 or 2 routes.
    """
    raise NotImplementedError("ensure_three_routes")


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
