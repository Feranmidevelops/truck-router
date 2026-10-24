"""Executable version of the risk rules. Boundary convention in risk.py."""

import pytest

from routing.services.risk import apply_load_rules, assess, classify_rain, classify_snow, classify_wind
from routing.services.types import Assessment, Level

L, M, H, S, N = Level.LOW, Level.MODERATE, Level.HIGH, Level.SEVERE, Level.NO_TRAVEL


@pytest.mark.parametrize(
    "mph, expected",
    [
        (0, L), (24.9, L),
        (25, M), (34.9, M),
        (35, H), (44.9, H),
        (45, S), (54.9, S),
        (55, N), (80, N),
    ],
)
def test_wind(mph, expected):
    assert classify_wind(mph) == expected


@pytest.mark.parametrize(
    "mph, expected",
    [
        (24.99, L), (25, M), (25.01, M),
        (34.99, M), (35, H), (35.01, H),
        (44.99, H), (45, S), (45.01, S),
        (54.99, S), (55, N), (55.01, N),
    ],
)
def test_wind_either_side_of_each_boundary(mph, expected):
    assert classify_wind(mph) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0, L), (0.09, L),
        (0.10, M), (0.24, M),
        (0.25, H), (0.49, H),
        (0.50, S), (1.00, S),  # brief says >1.00 is No Travel, so 1.00 stays Severe
        (1.01, N), (2.5, N),
    ],
)
def test_rain(rate, expected):
    assert classify_rain(rate) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0.0999, L), (0.10, M), (0.1001, M),
        (0.2499, M), (0.25, H), (0.2501, H),
        (0.4999, H), (0.50, S), (0.5001, S),
        (0.9999, S), (1.00, S), (1.0001, N),
    ],
)
def test_rain_either_side_of_each_boundary(rate, expected):
    assert classify_rain(rate) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0, L), (0.49, L),
        (0.5, M), (0.99, M),
        (1.0, H), (1.99, H),
        (2.0, S), (3.0, S),  # >3.0 is No Travel, so 3.0 stays Severe
        (3.01, N),
    ],
)
def test_snow(rate, expected):
    assert classify_snow(rate) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0.4999, L), (0.5, M), (0.5001, M),
        (0.9999, M), (1.0, H), (1.0001, H),
        (1.9999, H), (2.0, S), (2.0001, S),
        (2.9999, S), (3.0, S), (3.0001, N),
    ],
)
def test_snow_either_side_of_each_boundary(rate, expected):
    assert classify_snow(rate) == expected


@pytest.mark.parametrize(
    "mph, load, expected",
    [
        (60, 0, N),          # >=55 is No Travel for any load
        (50, 30_000, S),     # rule is "> 30,000", so exactly 30,000 is not raised
        (50, 30_001, N),
        (45, 35_000, N),
        (40, 40_000, H),     # rule is "> 40,000"
        (40, 40_001, S),
        (35, 45_000, S),
        (44.9, 45_000, S),
        (30, 80_000, M),     # no load rule below 35 mph
        (20, 80_000, L),
    ],
)
def test_load_rules(mph, load, expected):
    assert apply_load_rules(mph, classify_wind(mph), load) == expected


LOAD_EDGE_WEIGHTS = (30_000, 30_001, 40_000, 40_001)
LOAD_EDGE_GRID = [
    # mph    30,000  30,001  40,000  40,001
    (34.9,  (M,      M,      M,      M)),  # no load rule below 35 mph
    (35,    (H,      H,      H,      S)),
    (44.9,  (H,      H,      H,      S)),
    (45,    (S,      N,      N,      N)),
    (54.9,  (S,      N,      N,      N)),
    (55,    (N,      N,      N,      N)),
]


@pytest.mark.parametrize(
    "mph, load, expected",
    [(mph, load, level) for mph, row in LOAD_EDGE_GRID for load, level in zip(LOAD_EDGE_WEIGHTS, row)],
)
def test_load_rules_at_speed_and_weight_edges(mph, load, expected):
    assert apply_load_rules(mph, classify_wind(mph), load) == expected


@pytest.mark.parametrize("load", [0, 30_001, 40_001, 80_000])
def test_load_rules_never_lower_the_wind_level(load):
    assert apply_load_rules(40, N, load) == N
    assert apply_load_rules(50, N, load) == N


# The wind level passed in is LOW on purpose, so only the 55 mph rule can raise it.
@pytest.mark.parametrize(
    "mph, load, expected",
    [(54.9, 0, L), (55, 0, N), (55, 80_000, N), (80, 0, N)],
)
def test_load_rules_make_55_mph_no_travel_by_themselves(mph, load, expected):
    assert apply_load_rules(mph, L, load) == expected


def test_assess_takes_worst_factor():
    a = assess(wind_mph=30, rain_in_hr=0.3, snow_in_hr=0, load_lb=10_000)
    assert a.level == H
    assert (a.wind_level, a.rain_level, a.snow_level) == (M, H, L)
    assert a.drivers == ("rain",)
    assert a.load_adjusted is False


def test_assess_reports_load_adjustment():
    a = assess(wind_mph=40, rain_in_hr=0.3, snow_in_hr=0, load_lb=45_000)
    assert a.level == S
    assert a.wind_level == S
    assert a.load_adjusted is True
    assert a.drivers == ("wind",)


def test_assess_lists_all_drivers_at_final_level():
    a = assess(wind_mph=36, rain_in_hr=0.3, snow_in_hr=1.2, load_lb=0)
    assert a.level == H
    assert a.drivers == ("wind", "rain", "snow")


def test_assess_low_has_no_drivers():
    a = assess(wind_mph=5, rain_in_hr=0, snow_in_hr=0, load_lb=40_000)
    assert a.level == L
    assert a.drivers == ()


def test_assess_all_zero_inputs():
    assert assess(wind_mph=0, rain_in_hr=0, snow_in_hr=0, load_lb=0) == Assessment(
        level=L, wind_level=L, rain_level=L, snow_level=L, load_adjusted=False, drivers=()
    )


@pytest.mark.parametrize(
    "mph, load, wind_level, adjusted",
    [
        (40, 40_000, H, False),
        (40, 40_001, S, True),
        (50, 30_000, S, False),
        (50, 30_001, N, True),
        (60, 80_000, N, False),  # wind alone is already No Travel
        (30, 80_000, M, False),
        (0, 80_000, L, False),
    ],
)
def test_assess_load_adjusted_only_when_a_load_rule_raised_wind(mph, load, wind_level, adjusted):
    a = assess(wind_mph=mph, rain_in_hr=0, snow_in_hr=0, load_lb=load)
    assert a.wind_level == wind_level
    assert a.load_adjusted is adjusted


@pytest.mark.parametrize(
    "mph, rain, snow, load, level, wind_level, drivers",
    [
        (40, 1.5, 0, 45_000, N, S, ("rain",)),  # rain is worse than the raised wind
        (40, 0, 3.5, 45_000, N, S, ("snow",)),
        (40, 0.75, 0, 45_000, S, S, ("wind", "rain")),  # raised wind ties with rain
        (50, 1.5, 0, 35_000, N, N, ("wind", "rain")),
    ],
)
def test_assess_load_adjusted_even_when_wind_is_not_the_only_driver(
    mph, rain, snow, load, level, wind_level, drivers
):
    a = assess(wind_mph=mph, rain_in_hr=rain, snow_in_hr=snow, load_lb=load)
    assert (a.level, a.wind_level, a.drivers) == (level, wind_level, drivers)
    assert a.load_adjusted is True


def test_assess_load_rules_do_not_touch_rain_or_snow():
    a = assess(wind_mph=10, rain_in_hr=0.6, snow_in_hr=1.5, load_lb=80_000)
    assert (a.level, a.wind_level, a.rain_level, a.snow_level) == (S, L, S, H)
    assert a.load_adjusted is False
    assert a.drivers == ("rain",)


@pytest.mark.parametrize(
    "mph, rain, snow, load, level, drivers",
    [
        (36, 0.3, 0, 0, H, ("wind", "rain")),
        (10, 0.6, 2.5, 0, S, ("rain", "snow")),
        (50, 0.05, 2.0, 0, S, ("wind", "snow")),
        (25, 0.10, 0.5, 0, M, ("wind", "rain", "snow")),
        (50, 0.3, 0.6, 0, S, ("wind",)),  # lower factors are not drivers
        (40, 0.75, 0, 45_000, S, ("wind", "rain")),  # load-raised wind ties with rain
        (55, 1.5, 3.5, 0, N, ("wind", "rain", "snow")),
    ],
)
def test_assess_drivers_with_ties(mph, rain, snow, load, level, drivers):
    a = assess(wind_mph=mph, rain_in_hr=rain, snow_in_hr=snow, load_lb=load)
    assert a.level == level
    assert a.drivers == drivers


def test_assess_rain_alone_can_make_no_travel():
    a = assess(wind_mph=0, rain_in_hr=1.0001, snow_in_hr=0, load_lb=0)
    assert (a.level, a.wind_level, a.rain_level, a.snow_level) == (N, L, N, L)
    assert a.drivers == ("rain",)
    assert a.load_adjusted is False


def test_assess_snow_alone_can_make_no_travel():
    a = assess(wind_mph=0, rain_in_hr=0, snow_in_hr=3.0001, load_lb=0)
    assert a.level == N
    assert a.drivers == ("snow",)
