"""The journey report: where on a route the weather turns bad, and how bad. Pure functions, no I/O.

A stretch is a run of consecutive checkpoints at Moderate or worse. A single Low checkpoint
between two bad ones splits them into two stretches, so the report never claims bad weather
on road the checkpoints say is fine.
"""

from itertools import groupby

from .types import Checkpoint, Level, Stretch

NOTABLE = Level.MODERATE
FACTORS = ("wind", "rain", "snow")  # the order risk.assess lists drivers in


def build_stretches(checkpoints: list[Checkpoint]) -> list[Stretch]:
    """Stretches in driving order; empty when every checkpoint is Low.

    `checkpoints` are one route's, in driving order, each with weather and an assessment.
    """
    runs = groupby(checkpoints, key=lambda cp: cp.assessment.level >= NOTABLE)
    return [_stretch(list(run)) for bad, run in runs if bad]


def _stretch(run: list[Checkpoint]) -> Stretch:
    first, last = run[0], run[-1]
    level = max(cp.assessment.level for cp in run)
    at_worst = [cp for cp in run if cp.assessment.level == level]
    temps = [cp.weather.temp_f for cp in run if cp.weather.temp_f is not None]
    return Stretch(
        first_index=first.index,
        last_index=last.index,
        start_mile=first.mile,
        end_mile=last.mile,
        start_eta=first.eta,
        end_eta=last.eta,
        miles=sum(cp.segment_miles for cp in run),
        level=level,
        drivers=tuple(f for f in FACTORS if any(f in cp.assessment.drivers for cp in at_worst)),
        start=first.position,
        max_wind_mph=max(cp.weather.wind_mph for cp in run),
        max_gust_mph=max(cp.weather.gust_mph for cp in run),
        max_rain_in_hr=max(cp.weather.rain_in_hr for cp in run),
        max_snow_in_hr=max(cp.weather.snow_in_hr for cp in run),
        min_temp_f=min(temps, default=None),
        max_temp_f=max(temps, default=None),
    )


def where(stretch: Stretch, place: str | None) -> str:
    """Where the stretch starts: "near Amarillo, TX", or "at mile 412" when the place has no name."""
    return f"near {place}" if place else f"at mile {round(stretch.start_mile)}"
