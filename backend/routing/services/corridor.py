"""Heatmap grid around the trip corridor. Pure function."""

import math

from .geometry import EARTH_RADIUS_M, METERS_PER_MILE, cumulative_m, haversine_m
from .types import LatLon

SAMPLE_STEP_MILES = 2.0
COARSEN_FACTOR = 1.5
COORD_DECIMALS = 6  # ~0.1 m; hides float noise such as 32.650000000000006


def build_grid(
    routes: list[list[LatLon]],
    *,
    cell_deg: float = 0.25,
    buffer_miles: float = 30,
    max_points: int = 250,
) -> list[LatLon]:
    """Cell centres on a `cell_deg` lat/lon grid that lie within `buffer_miles` of any route,
    plus the centre of every cell a route passes through.

    - Snap to a fixed grid (not relative to the route) so neighbouring requests
      reuse cached weather.
    - Speed-up: only test cells near route points sampled every few miles,
      not every cell in the bounding box.
    - If the result exceeds max_points, multiply cell_deg by 1.5 and rebuild.
      Once cells are wider than the buffer, a cell the route crosses near its
      corner can have its centre outside the buffer. It is kept anyway, so the
      heatmap has no gaps along the road.
    Return points sorted by (lat, lon) so output is deterministic.

    Distances are measured to route samples, so a cell within a mile of the
    buffer's edge can be missed. Apart from cells a route passes through, a cell
    outside the buffer is never kept.
    """
    # Grid lines always fall on the equator and the prime meridian, so a route
    # crossing both needs 4 cells however coarse the grid gets. Starting at most
    # 90 degrees, coarsening hits a 4-cell world grid before cells outgrow the map.
    if not 0 < cell_deg <= 90 or max_points < 4:
        raise ValueError("cell_deg must be in (0, 90] and max_points at least 4")
    step_m = SAMPLE_STEP_MILES * METERS_PER_MILE
    samples = [p for route in routes for p in _sample_route(route, step_m)]
    if not samples:
        return []
    buffer_m = buffer_miles * METERS_PER_MILE
    cells = _cells_within(samples, cell_deg, buffer_m)
    while len(cells) > max_points:
        cell_deg *= COARSEN_FACTOR
        cells = _cells_within(samples, cell_deg, buffer_m)
    # Centres grow with their index, so sorting (row, col) sorts by (lat, lon).
    return [LatLon(_centre(i, cell_deg), _centre(j, cell_deg)) for i, j in sorted(cells)]


def _sample_route(points: list[LatLon], step_m: float) -> list[LatLon]:
    """Evenly spaced points along the polyline, at most `step_m` apart, both ends included."""
    if len(points) < 2:
        return list(points)
    cum = cumulative_m(points)
    total = cum[-1]
    n = max(1, math.ceil(total / step_m))
    out = []
    seg = 0
    for k in range(n + 1):
        d = total * k / n
        while seg < len(points) - 2 and cum[seg + 1] < d:
            seg += 1
        a, b = points[seg], points[seg + 1]
        span = cum[seg + 1] - cum[seg]
        t = (d - cum[seg]) / span if span > 0 else 0.0
        out.append(LatLon(a.lat + (b.lat - a.lat) * t, a.lon + (b.lon - a.lon) * t))
    return out


def _cells_within(samples: list[LatLon], cell_deg: float, buffer_m: float) -> set[tuple[int, int]]:
    """(row, col) of every cell holding a sample or with its centre within `buffer_m` of one."""
    lat_reach = math.degrees(buffer_m / EARTH_RADIUS_M)
    all_rows = _indices(-90.0, 90.0, cell_deg, 90.0)
    all_cols = _indices(-180.0, 180.0, cell_deg, 180.0)
    kept: set[tuple[int, int]] = set()
    for s in samples:
        # The cell the route passes through. Past a pole or the antimeridian its
        # centre is off the map, so take the nearest cell that is on it.
        row = _clamp(math.floor(s.lat / cell_deg), all_rows)
        col = _clamp(math.floor(s.lon / cell_deg), all_cols)
        kept.add((row, col))
        # A degree of longitude shrinks with cos(lat), so size the longitude window
        # at the pole-ward edge of the buffer, where it is widest.
        edge_lat = min(abs(s.lat) + lat_reach, 90.0)
        lon_reach = lat_reach / math.cos(math.radians(edge_lat))
        if lon_reach >= 180:
            # The window wraps all the way round, e.g. a buffer over the pole.
            cols = all_cols
        else:
            cols = _indices(s.lon - lon_reach, s.lon + lon_reach, cell_deg, 180.0)
        for i in _indices(s.lat - lat_reach, s.lat + lat_reach, cell_deg, 90.0):
            lat = _centre(i, cell_deg)
            for j in cols:
                if (i, j) not in kept and haversine_m(s, LatLon(lat, _centre(j, cell_deg))) <= buffer_m:
                    kept.add((i, j))
    return kept


def _indices(lo: float, hi: float, cell_deg: float, limit: float) -> range:
    """Indices k whose centre (k + 0.5) * cell_deg lies in [lo, hi], clipped to +-limit.

    Clipping keeps centres on valid coordinates; cells across the antimeridian
    are left out rather than wrapped.
    """
    lo, hi = max(lo, -limit), min(hi, limit)
    return range(math.ceil(lo / cell_deg - 0.5), math.floor(hi / cell_deg - 0.5) + 1)


def _clamp(k: int, valid: range) -> int:
    return min(max(k, valid.start), valid.stop - 1)


def _centre(k: int, cell_deg: float) -> float:
    return round((k + 0.5) * cell_deg, COORD_DECIMALS)
