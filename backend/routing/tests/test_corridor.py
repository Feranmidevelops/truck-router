import math
import time

import pytest

from routing.services.corridor import build_grid
from routing.services.geometry import EARTH_RADIUS_M, METERS_PER_MILE, haversine_m
from routing.services.types import LatLon

BUFFER = 30
NO_CAP = 1_000_000

DALLAS = LatLon(32.78, -96.80)
WACO = LatLon(31.55, -97.15)
TYLER = LatLon(32.35, -95.30)
FAIRBANKS = LatLon(64.84, -147.72)
LIVENGOOD = LatLon(65.52, -148.55)
LOS_ANGELES = LatLon(34.05, -118.24)
NEW_YORK = LatLon(40.71, -74.01)


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
    """All valid grid centres in the route's bounding box padded by about 69 miles each way."""
    lats, lons = [p.lat for p in route], [p.lon for p in route]
    edge = max(abs(x) for x in lats) + 1
    lon_pad = 1 / math.cos(math.radians(edge)) if edge < 89 else 360
    lat_lo, lat_hi = max(min(lats) - 1, -90), min(max(lats) + 1, 90)
    lon_lo, lon_hi = max(min(lons) - lon_pad, -180), min(max(lons) + lon_pad, 180)
    rows = range(math.floor(lat_lo / cell_deg), math.ceil(lat_hi / cell_deg))
    cols = range(math.floor(lon_lo / cell_deg), math.ceil(lon_hi / cell_deg))
    return [LatLon((i + 0.5) * cell_deg, (j + 0.5) * cell_deg) for i in rows for j in cols]


def miles_west_on_parallel(p: LatLon, miles: float) -> LatLon:
    """The point on p's parallel whose great-circle distance west of p is `miles`."""
    half_angle = math.sin(miles * METERS_PER_MILE / EARTH_RADIUS_M / 2)
    dlon = 2 * math.asin(half_angle / math.cos(math.radians(p.lat)))
    return LatLon(p.lat, p.lon - math.degrees(dlon))


def cells_near(p: LatLon, cell_deg: float, miles: float) -> set[tuple[int, int]]:
    """(row, col) of the cells touched by a box reaching `miles` each way from p.

    The box is smaller than a cell, so its corners land in every cell it touches.
    """
    dlat = math.degrees(miles * METERS_PER_MILE / EARTH_RADIUS_M)
    dlon = dlat / math.cos(math.radians(p.lat))
    return {
        (math.floor((p.lat + a) / cell_deg), math.floor((p.lon + b) / cell_deg))
        for a in (-dlat, dlat)
        for b in (-dlon, dlon)
    }


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
NEAR_POLE = polyline(LatLon(89.8, 100.0), LatLon(89.85, 120.0))
POLE_TO_ANTIMERIDIAN = polyline(LatLon(89.8, 100.0), LatLon(89.85, 179.95))
ACROSS_EQUATOR_AND_MERIDIAN = polyline(LatLon(-1.0, -1.0), LatLon(1.0, 1.0))


def test_no_routes_means_no_points():
    assert build_grid([]) == []
    assert build_grid([[], []]) == []


def test_points_sit_on_a_fixed_global_grid():
    grid = build_grid([TEXAS], max_points=NO_CAP)
    assert grid
    assert all(is_centre(p.lat, 0.25) and is_centre(p.lon, 0.25) for p in grid)
    # Dallas is in the cell spanning 32.75-33.00 N, 97.00-96.75 W.
    assert LatLon(32.875, -96.875) in grid


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


@pytest.mark.parametrize(
    "route",
    [TEXAS, ALASKA, SVALBARD, NEAR_POLE],
    ids=["texas", "alaska", "svalbard", "near-pole"],
)
def test_matches_a_brute_force_check_of_every_nearby_cell(route):
    grid = set(build_grid([route], max_points=NO_CAP))
    # Route samples are up to a mile from any vertex, hence the tolerance.
    distances = {c: miles_to_route(c, route) for c in every_centre_near(route, 0.25)}
    missing = [c for c, miles in distances.items() if miles <= BUFFER - 1.5 and c not in grid]
    assert missing == []
    too_far = [c for c in grid if miles_to_route(c, route) > BUFFER + 1]
    assert too_far == []


@pytest.mark.parametrize(
    "centre",
    [LatLon(32.875, -96.875), LatLon(-65.125, -64.875)],
    ids=["north", "south"],
)
def test_buffer_edge_is_exact_for_a_single_point(centre):
    # A one-point route is its own only sample, so distances to it are exact. In the
    # south, the cell only fits the longitude window if it is sized at the pole-ward edge.
    inside = miles_west_on_parallel(centre, BUFFER - 0.1)
    outside = miles_west_on_parallel(centre, BUFFER + 0.1)
    assert centre in build_grid([[inside]], max_points=NO_CAP)
    assert centre not in build_grid([[outside]], max_points=NO_CAP)


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


def test_default_cap_keeps_250_points_and_coarsens_at_251():
    # Straight routes whose 0.25 deg grids have exactly 250 and 251 cells.
    fits = polyline(LatLon(30.0, -100.05), LatLon(44.03, -100.05))
    one_over = polyline(LatLon(30.0, -100.05), LatLon(44.13, -100.05))
    assert len(build_grid([fits], max_points=NO_CAP)) == 250
    assert len(build_grid([one_over], max_points=NO_CAP)) == 251

    assert build_grid([fits]) == build_grid([fits], max_points=NO_CAP)
    coarse = build_grid([one_over])
    assert len(coarse) < 250
    assert all(is_centre(p.lat, 0.375) and is_centre(p.lon, 0.375) for p in coarse)


def test_coarse_cells_still_cover_the_whole_route():
    # Coast to coast needs three coarsenings, to cells of about 58 x 45 miles. A cell
    # the road crosses near its corner then has its centre more than 30 miles away.
    routes = [
        polyline(LOS_ANGELES, NEW_YORK),
        polyline(LOS_ANGELES, LatLon(39.0, -105.0), NEW_YORK),
        polyline(LOS_ANGELES, LatLon(33.0, -97.0), NEW_YORK),
    ]
    cell = 0.25 * 1.5**3
    grid = build_grid(routes)
    assert all(is_centre(p.lat, cell) and is_centre(p.lon, cell) for p in grid)
    kept = {(math.floor(p.lat / cell), math.floor(p.lon / cell)) for p in grid}
    # Cells are found from route samples 2 miles apart, so a vertex may be a mile off one.
    stranded = [p for route in routes for p in route if not cells_near(p, cell, 1.5) & kept]
    assert stranded == []


def test_three_1000_mile_routes_build_in_well_under_a_second():
    # A vertex every 0.1 mile, about as dense as TomTom route geometry.
    routes = three_long_routes(step_miles=0.1)
    started = time.perf_counter()
    grid = build_grid(routes)
    elapsed = time.perf_counter() - started
    assert 0 < len(grid) <= 250
    assert elapsed < 0.5


@pytest.mark.parametrize("cap", [4, 250])
@pytest.mark.parametrize(
    "route",
    [TEXAS, NEAR_POLE, POLE_TO_ANTIMERIDIAN, ACROSS_EQUATOR_AND_MERIDIAN],
    ids=["texas", "near-pole", "pole-to-antimeridian", "equator-and-meridian"],
)
def test_a_route_never_gets_an_empty_grid(route, cap):
    grid = build_grid([route], max_points=cap)
    assert 0 < len(grid) <= cap
    assert all(-90 <= p.lat <= 90 and -180 <= p.lon <= 180 for p in grid)


@pytest.mark.parametrize(
    "kwargs",
    [{"cell_deg": 0}, {"cell_deg": 91}, {"max_points": 3}],
    ids=["zero-cell", "cell-over-90", "cap-under-4"],
)
def test_rejects_a_cell_size_or_cap_no_grid_can_meet(kwargs):
    with pytest.raises(ValueError):
        build_grid([TEXAS], **kwargs)
