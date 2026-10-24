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


@pytest.mark.parametrize(
    ("level", "score"),
    [(Level.LOW, 0), (Level.MODERATE, 1), (Level.HIGH, 2), (Level.SEVERE, 3), (Level.NO_TRAVEL, 4)],
    ids=["LOW", "MODERATE", "HIGH", "SEVERE", "NO_TRAVEL"],
)
def test_summarize_route_scores_each_level(level, score):
    s = summarize_route("r0", distance_m=64_374, duration_s=3600, checkpoints=[cp(0, 40, level)])
    assert s.miles_by_level == {lvl: 40.0 if lvl == level else 0.0 for lvl in Level}
    assert s.avg_risk == pytest.approx(score)
    assert s.max_level == level


def test_summarize_route_with_every_level():
    checkpoints = [
        cp(0, 10, Level.LOW),
        cp(1, 20, Level.MODERATE),
        cp(2, 5, Level.NO_TRAVEL),
        cp(3, 15, Level.SEVERE),
        cp(4, 30, Level.HIGH),
    ]
    s = summarize_route("r0", distance_m=128_748, duration_s=7200, checkpoints=checkpoints)
    assert s.miles_by_level == {
        Level.LOW: 10.0,
        Level.MODERATE: 20.0,
        Level.HIGH: 30.0,
        Level.SEVERE: 15.0,
        Level.NO_TRAVEL: 5.0,
    }
    # (0*10 + 1*20 + 2*30 + 3*15 + 4*5) / 80
    assert s.avg_risk == pytest.approx(1.8125)
    # the worst level, even though it covers the fewest miles and isn't last
    assert s.max_level == Level.NO_TRAVEL


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
    s = summary("a", nt=1.24, severe=2.26, high=3.0, avg=1.234, duration=9000)
    assert ranking_key(s) == (1.2, 2.3, 3.0, 1.23, 9000)


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


@pytest.mark.parametrize(
    ("fast_avg", "expected_order", "expected_reason"),
    [
        # 0.804 rounds to 0.80, ties with the slow route, so time decides
        (0.804, ["fast", "slow"], "Shorter travel time (8 h 20 min vs 11 h 7 min)"),
        # 0.806 rounds to 0.81, so the slow route's lower average wins
        (0.806, ["slow", "fast"], "Lower average risk (0.80 vs 0.81)"),
    ],
    ids=["same-at-2-decimals", "apart-at-2-decimals"],
)
def test_average_risk_is_compared_at_two_decimals(fast_avg, expected_order, expected_reason):
    order, reason = rank_routes(
        [summary("slow", avg=0.80, duration=40_000), summary("fast", avg=fast_avg, duration=30_000)]
    )
    assert order == expected_order
    assert reason == expected_reason


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
        ({"severe": 10.06}, {"severe": 10.04}, "Fewest severe miles (10.0 vs 10.1)"),
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


@pytest.mark.parametrize("level", ["nt", "severe", "high"])
def test_miles_within_rounding_tie_and_fall_through_to_next_key(level):
    # 10.04 rounds to 10.0, so b's extra 0.04 miles don't count and its lower average decides
    order, reason = rank_routes(
        [summary("a", **{level: 10.0}, avg=0.9), summary("b", **{level: 10.04}, avg=0.5)]
    )
    assert order == ["b", "a"]
    assert reason == "Lower average risk (0.50 vs 0.90)"


def test_three_routes_sorted_best_first():
    order, reason = rank_routes(
        [
            summary("fast", severe=20, duration=30_000),
            summary("safe", avg=0.4, duration=42_000),
            summary("middle", high=15, avg=0.9, duration=36_000),
        ]
    )
    assert order == ["safe", "middle", "fast"]
    # the reason compares the winner with the runner-up only
    assert reason == "Fewest high-risk miles (0.0 vs 15.0)"


def test_every_route_with_no_travel_still_gets_an_order():
    order, reason = rank_routes([summary("a", nt=30), summary("b", nt=5, severe=80), summary("c", nt=12)])
    assert order == ["b", "c", "a"]
    assert reason == "Fewest no-travel miles (5.0 vs 12.0)"


def test_summaries_feed_the_ranking():
    risky = summarize_route("risky", 80_467, 3000, [cp(0, 25, Level.LOW), cp(1, 25, Level.SEVERE)])
    calm = summarize_route("calm", 96_561, 4000, [cp(0, 30, Level.MODERATE), cp(1, 30, Level.HIGH)])
    order, reason = rank_routes([risky, calm])
    assert order == ["calm", "risky"]
    assert reason == "Fewest severe miles (0.0 vs 25.0)"
