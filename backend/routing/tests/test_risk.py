"""Executable version of the risk rules. Boundary convention in risk.py."""

import pytest

from routing.services.risk import apply_load_rules, assess, classify_rain, classify_snow, classify_wind
from routing.services.types import Level

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
