from routing.services.corridor import build_grid
from routing.services.geometry import METERS_PER_MILE, haversine_m
from routing.services.types import LatLon

NO_CAP = 1_000_000

DALLAS = LatLon(32.78, -96.80)
WACO = LatLon(31.55, -97.15)
TYLER = LatLon(32.35, -95.30)


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


TEXAS = polyline(DALLAS, WACO)


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
