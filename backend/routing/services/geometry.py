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
    raise NotImplementedError("sample_checkpoints")


def eta(departure: datetime, seconds: float) -> datetime:
    return departure + timedelta(seconds=seconds)
