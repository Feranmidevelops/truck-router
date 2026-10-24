"""Checkpoint risk classification. Pure functions, no I/O.

Thresholds and load rules come from the assessment brief. Boundary decisions
are in the README ("Decisions"); tests/test_risk.py is the executable version.

Boundary convention: a value on a shared boundary goes to the higher band
(e.g. rain 0.25 in/hr is HIGH), except where the brief states the operator
explicitly (rain > 1.00 is NO_TRAVEL, so 1.00 itself is SEVERE).
"""

from .types import Assessment, Level


def classify_wind(mph: float) -> Level:
    """<25 LOW, 25-34 MODERATE, 35-44 HIGH, 45-54 SEVERE, >=55 NO_TRAVEL."""
    # Bands are half-open, so 34.9 is MODERATE and 35 is HIGH.
    if mph >= 55:
        return Level.NO_TRAVEL
    if mph >= 45:
        return Level.SEVERE
    if mph >= 35:
        return Level.HIGH
    if mph >= 25:
        return Level.MODERATE
    return Level.LOW


def classify_rain(in_per_hr: float) -> Level:
    """<0.10 LOW, 0.10-0.25 MODERATE, 0.25-0.50 HIGH, 0.50-1.00 SEVERE, >1.00 NO_TRAVEL."""
    if in_per_hr > 1.00:
        return Level.NO_TRAVEL
    if in_per_hr >= 0.50:
        return Level.SEVERE
    if in_per_hr >= 0.25:
        return Level.HIGH
    if in_per_hr >= 0.10:
        return Level.MODERATE
    return Level.LOW


def classify_snow(in_per_hr: float) -> Level:
    """<0.5 LOW, 0.5-1.0 MODERATE, 1.0-2.0 HIGH, 2.0-3.0 SEVERE, >3.0 NO_TRAVEL."""
    if in_per_hr > 3.0:
        return Level.NO_TRAVEL
    if in_per_hr >= 2.0:
        return Level.SEVERE
    if in_per_hr >= 1.0:
        return Level.HIGH
    if in_per_hr >= 0.5:
        return Level.MODERATE
    return Level.LOW


def apply_load_rules(wind_mph: float, wind_level: Level, load_lb: float) -> Level:
    """Raise the wind level for heavy loads.

    - >=55 mph: NO_TRAVEL for any load
    - 45-54 mph and load > 30,000 lb: NO_TRAVEL
    - 35-44 mph and load > 40,000 lb: SEVERE
    Never lowers a level.
    """
    if wind_mph >= 55:
        rule_level = Level.NO_TRAVEL
    elif 45 <= wind_mph < 55 and load_lb > 30_000:
        rule_level = Level.NO_TRAVEL
    elif 35 <= wind_mph < 45 and load_lb > 40_000:
        rule_level = Level.SEVERE
    else:
        rule_level = Level.LOW
    return max(wind_level, rule_level)


def assess(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float) -> Assessment:
    """Final checkpoint level = worst of wind (after load rules), rain and snow.

    `drivers` lists every factor that sits at the final level, in the order
    wind, rain, snow. When the final level is LOW, drivers is empty.
    """
    raw_wind = classify_wind(wind_mph)
    wind = apply_load_rules(wind_mph, raw_wind, load_lb)
    rain = classify_rain(rain_in_hr)
    snow = classify_snow(snow_in_hr)
    level = max(wind, rain, snow)

    if level == Level.LOW:
        drivers = ()
    else:
        factors = (("wind", wind), ("rain", rain), ("snow", snow))
        drivers = tuple(name for name, factor_level in factors if factor_level == level)

    return Assessment(
        level=level,
        wind_level=wind,
        rain_level=rain,
        snow_level=snow,
        load_adjusted=wind > raw_wind,
        drivers=drivers,
    )
