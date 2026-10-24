from datetime import datetime, timezone

import pytest

from routing.services.recommend import rank_routes, summarize_route
from routing.services.types import Assessment, Checkpoint, LatLon, Level, RouteSummary

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def cp(i: int, miles: float, level: Level) -> Checkpoint:
    return Checkpoint(
        index=i,
        mile=i * 25.0,
        position=LatLon(0, 0),
        segment_miles=miles,
        eta=T0,
        assessment=Assessment(level, level, Level.LOW, Level.LOW, False, ("wind",) if level else ()),
    )


def summary(rid: str, *, nt=0.0, severe=0.0, high=0.0, avg=0.0, duration=36_000) -> RouteSummary:
    return RouteSummary(
        route_id=rid,
        distance_miles=500,
        duration_s=duration,
        miles_by_level={
            Level.LOW: 0.0,
            Level.MODERATE: 0.0,
            Level.HIGH: high,
            Level.SEVERE: severe,
            Level.NO_TRAVEL: nt,
        },
        avg_risk=avg,
        max_level=Level.LOW,
    )


def test_summarize_route_weights_by_distance():
    checkpoints = [cp(0, 12.5, Level.LOW), cp(1, 25, Level.HIGH), cp(2, 12.5, Level.SEVERE)]
    s = summarize_route("r0", distance_m=80_467, duration_s=3600, checkpoints=checkpoints)
    assert s.miles_by_level[Level.HIGH] == pytest.approx(25)
    assert s.miles_by_level[Level.SEVERE] == pytest.approx(12.5)
    assert s.miles_by_level[Level.NO_TRAVEL] == 0.0
    # (0*12.5 + 2*25 + 3*12.5) / 50
    assert s.avg_risk == pytest.approx(1.75)
    assert s.max_level == Level.SEVERE


def test_summarize_route_converts_distance_to_miles():
    s = summarize_route("r0", distance_m=160_934.4, duration_s=7200, checkpoints=[cp(0, 100, Level.LOW)])
    assert s.route_id == "r0"
    assert s.distance_miles == pytest.approx(100)
    assert s.duration_s == 7200


def test_summarize_route_without_checkpoints():
    s = summarize_route("r0", distance_m=0, duration_s=0, checkpoints=[])
    assert s.miles_by_level == {level: 0.0 for level in Level}
    assert s.avg_risk == 0.0
    assert s.max_level == Level.LOW


def test_summarize_route_all_low():
    checkpoints = [cp(0, 12.5, Level.LOW), cp(1, 25, Level.LOW), cp(2, 12.5, Level.LOW)]
    s = summarize_route("r0", distance_m=80_467, duration_s=3600, checkpoints=checkpoints)
    assert s.miles_by_level[Level.LOW] == pytest.approx(50)
    assert all(s.miles_by_level[level] == 0.0 for level in Level if level != Level.LOW)
    assert s.avg_risk == 0.0
    assert s.max_level == Level.LOW


def test_fewest_severe_miles_wins_over_high_miles():
    order, reason = rank_routes([summary("a", severe=10), summary("b", high=200)])
    assert order == ["b", "a"]
    assert "severe" in reason.lower()


def test_fewest_high_miles_breaks_severe_tie():
    order, _ = rank_routes([summary("a", severe=5, high=40), summary("b", severe=5, high=10)])
    assert order == ["b", "a"]


def test_lower_average_risk_breaks_high_tie():
    order, _ = rank_routes([summary("a", avg=1.4), summary("b", avg=0.9)])
    assert order == ["b", "a"]


def test_travel_time_breaks_near_equal_average_risk():
    # 0.84 and 0.81 both round to 0.8, so the faster route wins
    order, reason = rank_routes(
        [summary("a", avg=0.81, duration=40_000), summary("b", avg=0.84, duration=30_000)]
    )
    assert order == ["b", "a"]
    assert "time" in reason.lower()


def test_no_travel_miles_rank_before_everything():
    order, _ = rank_routes([summary("a", nt=5), summary("b", severe=50, high=100, avg=2.5)])
    assert order == ["b", "a"]


def test_single_route():
    order, reason = rank_routes([summary("only")])
    assert order == ["only"]
    assert reason == "Only route available"
