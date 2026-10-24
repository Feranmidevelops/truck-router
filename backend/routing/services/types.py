"""Plain data types shared by the services. No Django imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


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
    source: str  # "tomtom" or "via-fallback"
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
    forecast_time: datetime  # the forecast hour the values came from


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


@dataclass
class RouteSummary:
    route_id: str
    distance_miles: float
    duration_s: float
    miles_by_level: dict[Level, float] = field(default_factory=dict)
    avg_risk: float = 0.0
    max_level: Level = Level.LOW
