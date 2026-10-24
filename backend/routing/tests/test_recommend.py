from datetime import datetime, timezone

import pytest

from routing.services.recommend import rank_routes, ranking_key, summarize_route
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


def test_ranking_key_order():
    s = summary("a", nt=1.24, severe=2.26, high=3.0, avg=1.5, duration=9000)
    assert ranking_key(s) == (1.2, 2.3, 3.0, 1.5, 9000)


def test_ranking_key_treats_missing_levels_as_zero():
    s = RouteSummary(route_id="a", distance_miles=10, duration_s=600)
    assert ranking_key(s) == (0.0, 0.0, 0.0, 0.0, 600)


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


def test_travel_time_breaks_equal_average_risk():
    order, reason = rank_routes(
        [summary("a", avg=0.80, duration=40_000), summary("b", avg=0.80, duration=30_000)]
    )
    assert order == ["b", "a"]
    assert "time" in reason.lower()


def test_lower_average_risk_wins_even_when_much_slower():
    order, reason = rank_routes(
        [summary("a", avg=0.84, duration=30_000), summary("b", avg=0.81, duration=45_000)]
    )
    assert order == ["b", "a"]
    assert reason == "Lower average risk (0.81 vs 0.84)"


def test_average_risk_ignores_float_noise():
    # 0.1 + 0.2 is 0.30000000000000004; it must still tie with 0.3 so time decides
    order, reason = rank_routes(
        [summary("a", avg=0.3, duration=40_000), summary("b", avg=0.1 + 0.2, duration=30_000)]
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


def test_no_routes_is_an_error():
    with pytest.raises(ValueError):
        rank_routes([])


@pytest.mark.parametrize(
    ("worse", "better", "expected"),
    [
        ({"nt": 12.5}, {}, "Fewest no-travel miles (0.0 vs 12.5)"),
        ({"severe": 25}, {}, "Fewest severe miles (0.0 vs 25.0)"),
        ({"high": 40}, {"high": 10}, "Fewest high-risk miles (10.0 vs 40.0)"),
        ({"duration": 36_720}, {"duration": 32_700}, "Shorter travel time (9 h 5 min vs 10 h 12 min)"),
        ({"duration": 3570}, {"duration": 2700}, "Shorter travel time (45 min vs 1 h 0 min)"),
        ({"duration": 36_020}, {"duration": 36_000}, "Shorter travel time by under a minute"),
    ],
)
def test_reason_names_the_first_key_that_differs(worse, better, expected):
    order, reason = rank_routes([summary("a", **worse), summary("b", **better)])
    assert order == ["b", "a"]
    assert reason == expected


def test_full_tie_keeps_input_order():
    order, reason = rank_routes([summary("a", high=10), summary("b", high=10)])
    assert order == ["a", "b"]
    assert reason == "Tied with the next route on risk and travel time"
    order, _ = rank_routes([summary("b", high=10), summary("a", high=10)])
    assert order == ["b", "a"]
