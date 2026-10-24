import math

import pytest

from routing.services.corridor import build_grid
from routing.services.geometry import METERS_PER_MILE, haversine_m
from routing.services.types import LatLon

BUFFER = 30
NO_CAP = 1_000_000

DALLAS = LatLon(32.78, -96.80)
WACO = LatLon(31.55, -97.15)
TYLER = LatLon(32.35, -95.30)
FAIRBANKS = LatLon(64.84, -147.72)
LIVENGOOD = LatLon(65.52, -148.55)


def polyline(*waypoints: LatLon, step_miles: float = 1.0) -> list[LatLon]:
    """Straight lat/lon legs between the waypoints, with a vertex about every `step_miles`."""
    out = [waypoints[0]]
    for a, b in zip(waypoints, waypoints[1:]):
        n = max(1, round(haversine_m(a, b) / METERS_PER_MILE / step_miles))
        out += [
            LatLon(a.lat + (b.lat - a.lat) * k / n, a.lon + (b.lon - a.lon) * k / n)
            for k in range(1, n + 1)
        ]
    return out


def is_centre(value: float, cell_deg: float) -> bool:
    k = value / cell_deg - 0.5
    return abs(k - round(k)) < 1e-6


def miles_to_route(p: LatLon, route: list[LatLon]) -> float:
    return min(haversine_m(p, q) for q in route) / METERS_PER_MILE


def every_centre_near(route: list[LatLon], cell_deg: float) -> list[LatLon]:
    """All grid centres in the route's bounding box padded by about 69 miles each way."""
    lats, lons = [p.lat for p in route], [p.lon for p in route]
    lon_pad = 1 / math.cos(math.radians(max(abs(x) for x in lats) + 1))
    rows = range(math.floor((min(lats) - 1) / cell_deg), math.ceil((max(lats) + 1) / cell_deg))
    cols = range(
        math.floor((min(lons) - lon_pad) / cell_deg), math.ceil((max(lons) + lon_pad) / cell_deg)
    )
    return [LatLon((i + 0.5) * cell_deg, (j + 0.5) * cell_deg) for i in rows for j in cols]


def three_long_routes(step_miles: float) -> list[list[LatLon]]:
    """A straight 1,000-mile route and two longer ones bending east and west of it."""
    start, end = LatLon(30.0, -100.0), LatLon(44.5, -100.0)
    return [
        polyline(start, end, step_miles=step_miles),
        polyline(start, LatLon(37.0, -104.0), end, step_miles=step_miles),
        polyline(start, LatLon(37.0, -96.0), end, step_miles=step_miles),
    ]


TEXAS = polyline(DALLAS, WACO)
ALASKA = polyline(FAIRBANKS, LIVENGOOD)
SVALBARD = polyline(LatLon(78.22, 15.65), LatLon(78.45, 16.30))


def test_no_routes_means_no_points():
    assert build_grid([]) == []
    assert build_grid([[], []]) == []


def test_points_sit_on_a_fixed_global_grid():
    grid = build_grid([TEXAS], max_points=NO_CAP)
    assert grid
    assert all(is_centre(p.lat, 0.25) and is_centre(p.lon, 0.25) for p in grid)
    # Dallas is in the cell spanning 32.75-33.00 N, 97.00-96.75 W.
    assert LatLon(32.875, -96.875) in grid


@pytest.mark.parametrize("route", [TEXAS, ALASKA, SVALBARD], ids=["texas", "alaska", "svalbard"])
def test_matches_a_brute_force_check_of_every_nearby_cell(route):
    grid = set(build_grid([route], max_points=NO_CAP))
    # Route samples are up to a mile from any vertex, hence the tolerance.
    distances = {c: miles_to_route(c, route) for c in every_centre_near(route, 0.25)}
    missing = [c for c, miles in distances.items() if miles <= BUFFER - 1.5 and c not in grid]
    assert missing == []
    too_far = [c for c in grid if miles_to_route(c, route) > BUFFER + 1]
    assert too_far == []


def test_buffer_reaching_over_the_pole_stays_on_valid_coordinates():
    route = polyline(LatLon(89.5, 0.0), LatLon(89.6, 40.0))
    grid = build_grid([route], max_points=NO_CAP)
    assert grid
    assert all(-90 <= p.lat <= 90 and -180 <= p.lon <= 180 for p in grid)
    assert all(miles_to_route(p, route) <= BUFFER + 1 for p in grid)
    # This close to the pole, a cell 80 degrees of longitude away is only 27.5 miles off.
    assert LatLon(89.875, 119.875) in grid


def test_too_many_cells_coarsens_the_grid_until_it_fits_the_cap():
    routes = three_long_routes(step_miles=1.0)
    assert len(build_grid(routes, max_points=NO_CAP)) > 250

    capped = build_grid(routes)
    assert 0 < len(capped) <= 250

    cell = 0.25
    while len(build_grid(routes, cell_deg=cell, max_points=NO_CAP)) > 250:
        cell *= 1.5
    assert cell > 0.25
    assert capped == build_grid(routes, cell_deg=cell, max_points=NO_CAP)
    assert all(is_centre(p.lat, cell) and is_centre(p.lon, cell) for p in capped)


def test_small_cap_on_a_short_route():
    grid = build_grid([TEXAS], max_points=10)
    assert 0 < len(grid) <= 10


def test_rejects_a_grid_that_could_never_fit():
    with pytest.raises(ValueError):
        build_grid([TEXAS], cell_deg=0)
    with pytest.raises(ValueError):
        build_grid([TEXAS], max_points=0)


def test_single_point_route_still_gets_cells_around_it():
    assert LatLon(32.875, -96.875) in build_grid([[DALLAS]], max_points=NO_CAP)


def test_output_is_sorted_without_duplicates_and_ignores_route_order():
    routes = [TEXAS, polyline(DALLAS, TYLER)]
    grid = build_grid(routes, max_points=NO_CAP)
    assert grid == sorted(grid, key=lambda p: (p.lat, p.lon))
    assert len(set(grid)) == len(grid)
    assert build_grid([list(reversed(r)) for r in reversed(routes)], max_points=NO_CAP) == grid
    assert build_grid([TEXAS, TEXAS], max_points=NO_CAP) == build_grid([TEXAS], max_points=NO_CAP)


def test_centres_carry_no_float_noise():
    grid = build_grid([TEXAS], cell_deg=0.1, max_points=NO_CAP)
    assert all(p.lat == round(p.lat, 6) and p.lon == round(p.lon, 6) for p in grid)
    assert LatLon(32.75, -96.75) in grid
