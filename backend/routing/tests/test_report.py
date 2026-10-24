from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from routing.services import report, risk
from routing.services.types import Checkpoint, LatLon, Level, WeatherSample

T0 = datetime(2026, 10, 26, 13, tzinfo=timezone.utc)
INTERVAL = 25
LOAD_LB = 20_000
LOW = {}
MODERATE_WIND = {"wind": 28.0}
HIGH_RAIN = {"rain": 0.3}
MODERATE_SNOW = {"snow": 0.6}


def route(*weathers: dict, load_lb: float = LOAD_LB) -> list[Checkpoint]:
    """One checkpoint per entry, every 25 miles and 25 minutes, Low unless the entry says otherwise."""
    checkpoints = []
    last = len(weathers) - 1
    for i, given in enumerate(weathers):
        w = {"wind": 5.0, "rain": 0.0, "snow": 0.0, "temp": 50.0, **given}
        sample = WeatherSample(
            wind_mph=w["wind"],
            gust_mph=w.get("gust", w["wind"] + 10),
            rain_in_hr=w["rain"],
            snow_in_hr=w["snow"],
            forecast_time=T0,
            temp_f=w["temp"],
            weather_code=0,
        )
        checkpoints.append(
            Checkpoint(
                index=i,
                mile=i * INTERVAL,
                position=LatLon(32.0 + 0.3 * i, -97.0),
                segment_miles=INTERVAL / 2 if i in (0, last) else INTERVAL,
                eta=T0 + timedelta(minutes=25 * i),
                weather=sample,
                assessment=risk.assess(w["wind"], w["rain"], w["snow"], load_lb),
            )
        )
    return checkpoints


def spans(stretches) -> list[tuple[int, int]]:
    return [(s.first_index, s.last_index) for s in stretches]


def test_a_route_that_is_low_all_the_way_has_no_stretches():
    assert report.build_stretches(route(LOW, LOW, LOW, LOW)) == []


def test_consecutive_bad_checkpoints_merge_into_one_stretch():
    cps = route(LOW, MODERATE_WIND, HIGH_RAIN, MODERATE_SNOW, LOW)
    [s] = report.build_stretches(cps)
    assert (s.first_index, s.last_index) == (1, 3)
    assert (s.start_mile, s.end_mile) == (25, 75)
    assert (s.start_eta, s.end_eta) == (cps[1].eta, cps[3].eta)
    assert s.miles == 75  # three checkpoints owning 25 miles each
    assert s.start == cps[1].position
    assert s.level == Level.HIGH
    assert s.drivers == ("rain",)


def test_one_low_checkpoint_between_bad_ones_splits_them():
    stretches = report.build_stretches(route(MODERATE_WIND, LOW, MODERATE_WIND, MODERATE_WIND))
    assert spans(stretches) == [(0, 0), (2, 3)]


def test_a_single_bad_checkpoint_is_a_stretch_of_its_own():
    cps = route(LOW, LOW, {"wind": 50.0}, LOW)
    [s] = report.build_stretches(cps)
    assert (s.first_index, s.last_index) == (2, 2)
    assert s.start_mile == s.end_mile == 50
    assert s.start_eta == s.end_eta == cps[2].eta
    assert s.miles == INTERVAL
    assert s.level == Level.SEVERE


def test_stretches_come_in_driving_order_and_reach_both_ends_of_the_route():
    cps = route(HIGH_RAIN, LOW, MODERATE_SNOW, LOW, LOW, MODERATE_WIND, HIGH_RAIN)
    stretches = report.build_stretches(cps)
    assert spans(stretches) == [(0, 0), (2, 2), (5, 6)]
    assert [s.start_mile for s in stretches] == sorted(s.start_mile for s in stretches)
    assert stretches[0].miles == INTERVAL / 2 and stretches[-1].miles == INTERVAL / 2 + INTERVAL


def test_drivers_are_every_factor_at_the_worst_level_anywhere_in_the_stretch():
    # High wind at one checkpoint, High rain at the next, and Moderate snow, which is below the worst
    cps = route({"wind": 36.0}, {"rain": 0.3}, {"snow": 0.6, "wind": 26.0})
    [s] = report.build_stretches(cps)
    assert s.level == Level.HIGH
    assert s.drivers == ("wind", "rain")


def test_the_worst_level_counts_load_rules():
    # 36 mph is High on its own, but Severe for a load over 40,000 lb
    [s] = report.build_stretches(route(LOW, {"wind": 36.0}, LOW, load_lb=42_000))
    assert s.level == Level.SEVERE
    assert s.drivers == ("wind",)


def test_peaks_are_the_extremes_across_the_stretch_and_no_further():
    cps = route(
        LOW,
        {"wind": 30.0, "gust": 41.0, "rain": 0.12, "temp": 38.0},
        {"wind": 26.0, "gust": 47.0, "snow": 0.7, "temp": 31.5},
        {"wind": 27.0, "gust": 35.0, "rain": 0.2, "snow": 0.5, "temp": 33.0},
        {"temp": -10.0},  # Low, so not part of either stretch
        {"wind": 60.0, "gust": 80.0, "rain": 0.9, "snow": 4.0, "temp": 5.0},
    )
    first, second = report.build_stretches(cps)
    assert first.max_wind_mph == 30.0
    assert first.max_gust_mph == 47.0
    assert first.max_rain_in_hr == pytest.approx(0.2)
    assert first.max_snow_in_hr == pytest.approx(0.7)
    assert (first.min_temp_f, first.max_temp_f) == (31.5, 38.0)
    assert (second.max_wind_mph, second.min_temp_f, second.max_temp_f) == (60.0, 5.0, 5.0)


def test_missing_temperatures_are_left_out_of_the_range():
    some = report.build_stretches(route({"rain": 0.3, "temp": None}, {"rain": 0.3, "temp": 40.0}))
    assert (some[0].min_temp_f, some[0].max_temp_f) == (40.0, 40.0)
    none = report.build_stretches(route({"rain": 0.3, "temp": None}))
    assert (none[0].min_temp_f, none[0].max_temp_f) == (None, None)


def test_where_names_the_place_or_falls_back_to_the_mile():
    [s] = report.build_stretches(route(LOW, HIGH_RAIN))
    assert report.where(s, "Amarillo, TX") == "near Amarillo, TX"
    assert report.where(s, None) == "at mile 25"
    assert report.where(replace(s, start_mile=412.4), "") == "at mile 412"
