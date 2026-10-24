"""Distance, checkpoint sampling, ETA interpolation and line simplification. Pure functions.

Decisions (README, "Decisions"):
- Checkpoints sit at mile 0, every `interval_miles`, and at the destination.
- Each checkpoint owns the road centred on it: from halfway back to the
  previous checkpoint to halfway on to the next, clipped to the route ends.
  So segment_miles across all checkpoints adds up to the route length.
- ETA comes from the route's own timing profile (TomTom instruction offsets),
  interpolated linearly between timing points. Not from an average speed.
"""

from bisect import bisect_right
from datetime import datetime, timedelta
import math

from .types import Checkpoint, LatLon, TimingPoint

EARTH_RADIUS_M = 6_371_008.8
METERS_PER_MILE = 1609.344
DESTINATION_MERGE_MILES = 0.5
MERCATOR_MAX_LAT = 85.051129  # where Web Mercator tiles stop


def haversine_m(a: LatLon, b: LatLon) -> float:
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = phi2 - phi1
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def cumulative_m(points: list[LatLon]) -> list[float]:
    """Distance from the first point to each point, in meters."""
    out = [0.0]
    for prev, cur in zip(points, points[1:]):
        out.append(out[-1] + haversine_m(prev, cur))
    return out


def point_at_distance(points: list[LatLon], cumulative: list[float], meters: float) -> LatLon:
    """Linearly interpolated position `meters` along the polyline (clamped to the ends)."""
    if meters <= 0:
        return points[0]
    if meters >= cumulative[-1]:
        return points[-1]
    # bisect_right skips past repeated distances (duplicate points), so
    # cumulative[i - 1] <= meters < cumulative[i] and the segment has length > 0.
    i = bisect_right(cumulative, meters)
    a, b = points[i - 1], points[i]
    t = (meters - cumulative[i - 1]) / (cumulative[i] - cumulative[i - 1])
    return LatLon(a.lat + (b.lat - a.lat) * t, a.lon + (b.lon - a.lon) * t)


def seconds_at_distance(timing: list[TimingPoint], meters: float) -> float:
    """Travel time to `meters`, interpolated between timing points (clamped to the ends)."""
    if meters <= timing[0].meters:
        return timing[0].seconds
    if meters >= timing[-1].meters:
        return timing[-1].seconds
    i = bisect_right(timing, meters, key=lambda t: t.meters)
    a, b = timing[i - 1], timing[i]
    return a.seconds + (b.seconds - a.seconds) * (meters - a.meters) / (b.meters - a.meters)


def sample_checkpoints(
    points: list[LatLon],
    timing: list[TimingPoint],
    departure: datetime,
    interval_miles: int,
) -> list[Checkpoint]:
    """Checkpoints at 0, interval, 2*interval, ... and the destination, with ETA and segment_miles.

    If the last interval checkpoint lands within 0.5 mile of the destination,
    don't add a separate destination checkpoint.
    """
    if interval_miles <= 0:
        raise ValueError("interval_miles must be positive")
    if not points:
        raise ValueError("route has no geometry")
    if not timing:
        raise ValueError("route has no timing profile")
    cumulative = cumulative_m(points)
    length = cumulative[-1]
    step = interval_miles * METERS_PER_MILE

    marks = [0.0]
    k = 1
    while k * step < length:
        marks.append(k * step)
        k += 1
    # Rounded so float noise from summing thousands of segments can't flip the boundary.
    if round((length - marks[-1]) / METERS_PER_MILE, 6) > DESTINATION_MERGE_MILES:
        marks.append(length)

    # Midpoints between neighbours, plus the two route ends: checkpoint i owns bounds[i]..bounds[i + 1].
    bounds = [0.0] + [(a + b) / 2 for a, b in zip(marks, marks[1:])] + [length]

    checkpoints = []
    for i, m in enumerate(marks):
        # The summed polyline and TomTom's own length differ by a fraction of a percent, so look
        # the time up at the same fraction of TomTom's length. The destination is then exactly
        # 1.0 of the way and gets the full route duration.
        along = m / length if length > 0 else 0.0
        checkpoints.append(
            Checkpoint(
                index=i,
                mile=m / METERS_PER_MILE,
                position=point_at_distance(points, cumulative, m),
                segment_miles=(bounds[i + 1] - bounds[i]) / METERS_PER_MILE,
                eta=eta(departure, seconds_at_distance(timing, along * timing[-1].meters)),
            )
        )
    return checkpoints


def eta(departure: datetime, seconds: float) -> datetime:
    return departure + timedelta(seconds=seconds)


def simplify(points: list[LatLon], tolerance_m: float) -> list[LatLon]:
    """Douglas-Peucker: drop points until any more would move the line over `tolerance_m`.

    Every input point stays within `tolerance_m` of the returned line, and the first and last
    points are always kept. Straight means straight on the map, so distances are taken in Web
    Mercator (what MapLibre draws in) and turned into metres with the scale at the middle of the
    segment being tested. Iterative, so a 10k-point route can't hit the recursion limit.
    """
    n = len(points)
    if n <= 2:
        return list(points)
    x = [math.radians(p.lon) for p in points]
    y = [_mercator_y(p.lat) for p in points]
    keep = [False] * n
    keep[0] = keep[-1] = True
    tol = tolerance_m / EARTH_RADIUS_M
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        x0, y0 = x[first], y[first]
        bx, by = x[last] - x0, y[last] - y0
        seg2 = bx * bx + by * by
        # Mercator stretches by 1/cos(lat); compare in Mercator units against a stretched tolerance
        mid_lat = (points[first].lat + points[last].lat) / 2
        worst, worst_d2 = -1, (tol / math.cos(math.radians(mid_lat))) ** 2
        for i in range(first + 1, last):
            px, py = x[i] - x0, y[i] - y0
            # Distance to the segment, not the infinite line, so a route that doubles back
            # past an end point still counts as far away.
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
            dx, dy = px - t * bx, py - t * by
            d2 = dx * dx + dy * dy
            if d2 > worst_d2:
                worst, worst_d2 = i, d2
        if worst >= 0:
            keep[worst] = True
            stack.append((first, worst))
            stack.append((worst, last))
    return [p for p, k in zip(points, keep) if k]


def _mercator_y(lat: float) -> float:
    lat = max(-MERCATOR_MAX_LAT, min(MERCATOR_MAX_LAT, lat))
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
