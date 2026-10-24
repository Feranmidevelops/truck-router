"""Distance, checkpoint sampling and ETA interpolation along a route. Pure functions.

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
