"""Heatmap grid around the trip corridor. Pure function."""

from .types import LatLon


def build_grid(
    routes: list[list[LatLon]],
    *,
    cell_deg: float = 0.25,
    buffer_miles: float = 30,
    max_points: int = 400,
) -> list[LatLon]:
    """Cell centres on a `cell_deg` lat/lon grid that lie within `buffer_miles` of any route.

    - Snap to a fixed grid (not relative to the route) so neighbouring requests
      reuse cached weather.
    - Speed-up: only test cells near route points sampled every few miles,
      not every cell in the bounding box.
    - If the result exceeds max_points, multiply cell_deg by 1.5 and rebuild.
    Return points sorted by (lat, lon) so output is deterministic.
    """
    raise NotImplementedError("build_grid")
