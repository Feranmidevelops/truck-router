"""Plain data types shared by the services. No Django imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Protocol


class Cache(Protocol):
    """The part of Django's cache the API clients use, so they can be tested without Django."""

    def get(self, key: str, default=None): ...
    def set(self, key: str, value, timeout: int | None = None): ...


class Level(IntEnum):
    """Risk levels. The integer value is the score used for average risk."""

    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float


@dataclass(frozen=True)
class TimingPoint:
    """Distance from the route start and the travel time to reach it."""

    meters: float
    seconds: float


@dataclass
class RouteOption:
    id: str
    source: str  # "tomtom": the fallbacks are TomTom's own routes round avoided areas
    distance_m: float
    duration_s: float
    departure_time: datetime
    arrival_time: datetime
    points: list[LatLon]
    timing: list[TimingPoint]  # sorted by meters, starts at (0, 0), ends at (distance_m, duration_s)


@dataclass(frozen=True)
class WeatherSample:
    wind_mph: float
    gust_mph: float
    rain_in_hr: float  # rain + showers
    snow_in_hr: float
    forecast_time: datetime  # the forecast hour of the wind, gusts, temperature and weather code
    temp_f: float | None  # shown, not scored; None when the forecast has no value
    weather_code: int | None  # WMO code, shown as text (conditions.describe), not scored


@dataclass(frozen=True)
class Assessment:
    level: Level
    wind_level: Level  # after load rules
    rain_level: Level
    snow_level: Level
    load_adjusted: bool  # True when a load rule raised the wind level
    drivers: tuple[str, ...]  # factors at the final level, e.g. ("wind",)


@dataclass
class Checkpoint:
    index: int
    mile: float  # distance from origin
    position: LatLon
    segment_miles: float  # miles of road this checkpoint represents
    eta: datetime
    weather: WeatherSample | None = None
    assessment: Assessment | None = None


@dataclass(frozen=True)
class Stretch:
    """A run of consecutive checkpoints at Moderate or worse, for the journey report."""

    first_index: int  # checkpoint indexes, both ends included
    last_index: int
    start_mile: float  # the first and last checkpoint's mile and ETA
    end_mile: float
    start_eta: datetime
    end_eta: datetime
    miles: float  # road its checkpoints own, as counted in a route's miles by level
    level: Level  # the worst level in the stretch
    drivers: tuple[str, ...]  # factors at that level, at any of its checkpoints
    start: LatLon  # where the stretch begins, to name the place
    max_wind_mph: float
    max_gust_mph: float
    max_rain_in_hr: float
    max_snow_in_hr: float
    min_temp_f: float | None  # None when no checkpoint in it has a temperature
    max_temp_f: float | None


@dataclass
class RouteSummary:
    route_id: str
    distance_miles: float
    duration_s: float
    miles_by_level: dict[Level, float] = field(default_factory=dict)
    avg_risk: float = 0.0
    max_level: Level = Level.LOW
