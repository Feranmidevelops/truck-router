"""Route summaries and ranking. Pure functions, no I/O.

Ranking order (the brief, plus the decisions in the README):
  0. Fewest NO_TRAVEL miles (not in the brief; a route you can't drive can't win)
  1. Fewest SEVERE miles
  2. Fewest HIGH miles
  3. Lowest average risk (distance-weighted, rounded to 1 decimal so travel
     time can break near-ties)
  4. Shortest travel time
Miles are compared rounded to 0.1.
"""

from .types import Checkpoint, Level, RouteSummary

METERS_PER_MILE = 1609.344


def summarize_route(
    route_id: str, distance_m: float, duration_s: float, checkpoints: list[Checkpoint]
) -> RouteSummary:
    """Sum segment_miles per level, compute distance-weighted avg_risk and max_level.

    Every Level appears as a key in miles_by_level (0.0 when absent).
    Each checkpoint must already have an assessment.
    """
    miles_by_level = {level: 0.0 for level in Level}
    for cp in checkpoints:
        miles_by_level[cp.assessment.level] += cp.segment_miles

    total_miles = sum(miles_by_level.values())
    weighted = sum(level.value * miles for level, miles in miles_by_level.items())
    avg_risk = weighted / total_miles if total_miles > 0 else 0.0

    return RouteSummary(
        route_id=route_id,
        distance_miles=distance_m / METERS_PER_MILE,
        duration_s=duration_s,
        miles_by_level=miles_by_level,
        avg_risk=avg_risk,
        max_level=max((cp.assessment.level for cp in checkpoints), default=Level.LOW),
    )


def ranking_key(summary: RouteSummary) -> tuple:
    """Sort key implementing the order above. Lower sorts first."""
    miles = summary.miles_by_level
    return (
        round(miles.get(Level.NO_TRAVEL, 0.0), 1),
        round(miles.get(Level.SEVERE, 0.0), 1),
        round(miles.get(Level.HIGH, 0.0), 1),
        round(summary.avg_risk, 1),
        summary.duration_s,
    )


def rank_routes(summaries: list[RouteSummary]) -> tuple[list[str], str]:
    """Return (route ids best-first, reason).

    `reason` is a short sentence naming the first criterion that separates
    the winner from the runner-up, e.g. "Fewest severe miles (0.0 vs 25.0)".
    With a single route the reason is "Only route available".
    """
    raise NotImplementedError("rank_routes")
