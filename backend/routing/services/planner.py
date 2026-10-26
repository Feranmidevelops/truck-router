"""Orchestration: routes -> checkpoints -> weather -> risk -> recommendation.

A plan and its heatmap are made in two steps each, because the browser fetches the forecasts.
Open-Meteo's free limit is per IP address, and a free Render server shares its address with
other apps, which used the day's limit up. So start_plan finds the routes and checkpoints and
lists the Open-Meteo URLs that cover them; the browser fetches them and sends the replies to
finish_plan, which reads, samples, scores and ranks them. heatmap_request and build_heatmap
do the same for the heatmap grid. The server never calls Open-Meteo itself.

This is the only service module that touches Django (settings, cache).
The response shape here must match frontend/src/api/types.ts.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
import time
import uuid

from django.conf import settings
from django.core.cache import cache

from . import conditions, corridor, geometry, recommend, report, risk, tomtom, weather
from .types import Checkpoint, LatLon, Level, RouteOption, RouteSummary, Stretch, WeatherSample

PLAN_TTL_S = 60 * 60
# Between listing a plan's forecasts and getting them back; the browser takes a few seconds
PENDING_TTL_S = 15 * 60
HEATMAP_HOURS = 48
# Open-Meteo allows 600 locations a minute, and the heatmap that follows a plan asks for up to 250,
# so a plan's checkpoints ask for 340 at most. Seattle -> Miami at a 10-mile interval needs about
# 880 at 0.1 degrees; weather.snap_cells makes the grid coarser until they fit.
PLAN_WEATHER_CELLS = 340
METERS_PER_MILE = 1609.344
# Route lines are drawn and gridded from a simplified copy. 20 m is inside a highway's width and
# under 3 px up to city-level zoom (13), and cuts a ~10,000-point TomTom route to under 1,000 points.
SIMPLIFY_TOLERANCE_M = 20
NO_ROUTE_MESSAGE = tomtom.NO_ROUTE_MESSAGE

log = logging.getLogger(__name__)


class PlanNotFound(Exception):
    pass


class NoRouteFound(Exception):
    """TomTom answered, but has no truck route between the two places."""


def start_plan(
    *,
    origin: dict,
    destination: dict,
    departure_time: datetime,
    timezone: str,
    load_weight_lb: int,
    interval_miles: int,
) -> dict:
    """The routes and their checkpoints, kept for finish_plan, and the forecasts to fetch for them.

    Returns {"plan_id", "forecast_urls"}. `departure_time` is aware, with the origin's offset;
    `timezone` is the origin's IANA zone.
    """
    o = LatLon(origin["lat"], origin["lon"])
    d = LatLon(destination["lat"], destination["lon"])
    key = settings.TOMTOM_API_KEY
    # The form accepts a departure up to an hour ago, but TomTom can only leave now. The whole
    # plan (ETAs, arrival, heatmap start, the departure echoed back) starts at the same instant.
    now = datetime.now(UTC).replace(microsecond=0)
    departure = max(departure_time, now).astimezone(departure_time.tzinfo)
    clock = _Stopwatch()

    try:
        found = tomtom.calculate_routes(o, d, departure, load_weight_lb, key)
    except tomtom.NoRouteError as exc:
        raise NoRouteFound(NO_ROUTE_MESSAGE) from exc
    if not found:
        raise NoRouteFound(NO_ROUTE_MESSAGE)
    clock.lap("routing")
    routes = tomtom.ensure_three_routes(found, o, d, departure, load_weight_lb, key)
    clock.lap("fallback routes")  # only when TomTom's own routes weren't enough
    # Every lookup below is keyed by route id, so number them here rather than trust the
    # routing client to keep TomTom's routes and the fallbacks apart. Top-up fallbacks are
    # requested a few seconds after the first call, so their "now" is reset to the plan's too.
    routes = [
        replace(
            r,
            id=f"r{i}",
            departure_time=departure,
            arrival_time=departure + timedelta(seconds=r.duration_s),
        )
        for i, r in enumerate(routes)
    ]

    checkpoints = {
        r.id: geometry.sample_checkpoints(r.points, r.timing, r.departure_time, interval_miles) for r in routes
    }
    flat = [cp for cps in checkpoints.values() for cp in cps]
    clock.lap("checkpoints")
    # Checkpoints above were sampled on the full line; the map and the heatmap only need its shape
    shapes = {r.id: geometry.simplify(r.points, SIMPLIFY_TOLERANCE_M) for r in routes}
    clock.lap("simplify")
    forecasts = weather.forecast_requests(
        [cp.position for cp in flat],
        min(cp.eta for cp in flat),
        max(cp.eta for cp in flat),
        max_cells=PLAN_WEATHER_CELLS,
    )

    plan_id = uuid.uuid4().hex
    log.info(
        "Plan %s started: %d routes, %d checkpoints, %d forecast locations, %s",
        plan_id, len(routes), len(flat), len(set(forecasts.cells)), clock,
    )
    cache.set(
        f"pending:{plan_id}",
        {
            "input": {
                "origin": origin,
                "destination": destination,
                "departure_time": departure.isoformat(),
                "timezone": timezone,
                "load_weight_lb": load_weight_lb,
                "interval_miles": interval_miles,
            },
            "departure_time": departure,
            # The full lines are done with: the checkpoints are sampled and the shapes made
            "routes": [replace(r, points=[], timing=[]) for r in routes],
            "shapes": shapes,
            "checkpoints": checkpoints,
            "forecasts": forecasts,
        },
        PENDING_TTL_S,
    )
    return {"plan_id": plan_id, "forecast_urls": forecasts.urls()}


def finish_plan(plan_id: str, replies: list) -> dict:
    """The plan, from Open-Meteo's replies to the forecast_urls start_plan listed, in that order.

    Raises PlanNotFound when there's no started plan with this id, and weather.WeatherError when
    the replies aren't the forecasts that were asked for. A plan is finished once: asked again
    (say the first answer never reached the browser), it gives the same answer without reading
    the replies, so made-up ones can't make it look up more place names.
    """
    finished = cache.get(f"finished:{plan_id}")
    if finished is not None:
        return finished
    pending = cache.get(f"pending:{plan_id}")
    if pending is None:
        raise PlanNotFound(plan_id)
    routes: list[RouteOption] = pending["routes"]
    checkpoints: dict[str, list[Checkpoint]] = pending["checkpoints"]
    shapes: dict[str, list[LatLon]] = pending["shapes"]
    load_weight_lb = pending["input"]["load_weight_lb"]
    clock = _Stopwatch()

    flat = [cp for cps in checkpoints.values() for cp in cps]
    series = weather.read_forecasts(pending["forecasts"], replies)
    clock.lap("weather")
    for cp, s in zip(flat, series):
        cp.weather = weather.sample_at(s, cp.eta)
        cp.assessment = risk.assess(
            cp.weather.wind_mph, cp.weather.rain_in_hr, cp.weather.snow_in_hr, load_weight_lb
        )

    summaries = {
        r.id: recommend.summarize_route(r.id, r.distance_m, r.duration_s, checkpoints[r.id]) for r in routes
    }
    order, reason = recommend.rank_routes(list(summaries.values()))
    stretches = {rid: report.build_stretches(checkpoints[rid]) for rid in order}
    clock.lap("risk + ranking")

    # In rank order, so when there are more places than lookups allowed, the best routes keep their names
    starts = [s.start for rid in order for s in stretches[rid]]
    names = dict(zip(starts, tomtom.place_names(starts, settings.TOMTOM_API_KEY, cache=cache)))
    clock.lap("place names")

    log.info("Plan %s: %d routes, %d checkpoints, %s", plan_id, len(routes), len(flat), clock)
    cache.set(
        f"plan:{plan_id}",
        {
            "routes": [shapes[r.id] for r in routes],
            "departure_time": pending["departure_time"],
            "load_weight_lb": load_weight_lb,
        },
        PLAN_TTL_S,
    )

    by_id = {r.id: r for r in routes}
    origin, destination = pending["input"]["origin"], pending["input"]["destination"]
    # Lettered in the order shown, so Route A is always the recommended one
    labels = {rid: f"Route {chr(65 + i)}" for i, rid in enumerate(order)}
    reports = {
        rid: _report_json(by_id[rid], checkpoints[rid], stretches[rid], names, origin, destination)
        for rid in order
    }
    finished = {
        "plan_id": plan_id,
        "input": pending["input"],
        "recommended_route_id": order[0],
        "recommendation_reason": reason,
        "routes": [
            _route_json(
                by_id[rid], shapes[rid], labels[rid], rank, summaries[rid], checkpoints[rid], reports[rid]
            )
            for rank, rid in enumerate(order, 1)
        ],
    }
    cache.set(f"finished:{plan_id}", finished, PENDING_TTL_S)
    cache.delete(f"pending:{plan_id}")
    return finished


def heatmap_request(plan_id: str) -> dict:
    """The forecasts to fetch for a finished plan's heatmap: {"plan_id", "forecast_urls"}."""
    stored = _stored_plan(plan_id)
    return {"plan_id": plan_id, "forecast_urls": _heatmap_forecasts(stored).urls()}


def build_heatmap(plan_id: str, replies: list) -> dict:
    """Risk levels per grid point and hour, from Open-Meteo's replies to heatmap_request's URLs."""
    stored = _stored_plan(plan_id)
    clock = _Stopwatch()
    forecasts = _heatmap_forecasts(stored)
    clock.lap("grid")
    start = forecasts.start
    series = weather.read_forecasts(forecasts, replies)
    clock.lap("weather")
    load = stored["load_weight_lb"]
    points = []
    for s in series:
        levels = [
            int(risk.assess(w, r, sn, load).level)
            for w, r, sn in zip(s.wind_mph, s.rain_in_hr, s.snow_in_hr)
        ][: HEATMAP_HOURS + 1]
        points.append({"lat": s.position.lat, "lon": s.position.lon, "v": levels})
    clock.lap("risk")
    log.info("Heatmap %s: %d points, %s", plan_id, len(points), clock)
    return {
        "plan_id": plan_id,
        "start_time": start.isoformat(),
        "hours": HEATMAP_HOURS,
        "points": points,
    }


def _stored_plan(plan_id: str) -> dict:
    stored = cache.get(f"plan:{plan_id}")
    if stored is None:
        raise PlanNotFound(plan_id)
    return stored


def _heatmap_forecasts(stored: dict) -> weather.ForecastRequests:
    """The same every time for the same plan, so the URLs listed and the replies read agree."""
    grid = corridor.build_grid(stored["routes"])
    start = weather.floor_hour(stored["departure_time"])
    return weather.forecast_requests(grid, start, start + timedelta(hours=HEATMAP_HOURS))


def _route_json(
    route: RouteOption,
    shape: list[LatLon],
    label: str,
    rank: int,
    summary: RouteSummary,
    checkpoints: list[Checkpoint],
    journey_report: dict,
) -> dict:
    return {
        "id": route.id,
        "label": label,
        "rank": rank,
        "source": route.source,
        "distance_miles": round(route.distance_m / METERS_PER_MILE, 1),
        "duration_s": round(route.duration_s),
        "departure_time": route.departure_time.isoformat(),
        "arrival_time": route.arrival_time.isoformat(),
        "geometry": [[round(p.lon, 5), round(p.lat, 5)] for p in shape],
        "summary": {
            "miles_by_level": {lvl.name: round(summary.miles_by_level.get(lvl, 0.0), 1) for lvl in Level},
            "avg_risk": round(summary.avg_risk, 2),
            "max_level": summary.max_level.name,
        },
        "checkpoints": [_checkpoint_json(cp) for cp in checkpoints],
        "report": journey_report,
    }


def _report_json(
    route: RouteOption,
    checkpoints: list[Checkpoint],
    stretches: list[Stretch],
    names: dict[LatLon, str | None],
    origin: dict,
    destination: dict,
) -> dict:
    """Leaving the origin, each stretch of bad weather in driving order, then arriving."""
    first, last = checkpoints[0], checkpoints[-1]
    return {
        "departure": _report_stop(origin["label"], route.departure_time, first),
        "stretches": [_stretch_json(s, names.get(s.start)) for s in stretches],
        "arrival": _report_stop(destination["label"], route.arrival_time, last),
    }


def _report_stop(place: str, when: datetime, cp: Checkpoint) -> dict:
    assert cp.weather is not None and cp.assessment is not None
    return {
        "place": place,
        "time": when.isoformat(),
        "level": cp.assessment.level.name,
        "weather": _weather_json(cp.weather),
    }


def _stretch_json(s: Stretch, place: str | None) -> dict:
    return {
        "where": report.where(s, place),
        "first_index": s.first_index,
        "last_index": s.last_index,
        "start_mile": round(s.start_mile, 1),
        "end_mile": round(s.end_mile, 1),
        "start_eta": s.start_eta.isoformat(),
        "end_eta": s.end_eta.isoformat(),
        "miles": round(s.miles, 1),
        "level": s.level.name,
        "drivers": list(s.drivers),
        "max_wind_mph": round(s.max_wind_mph, 1),
        "max_gust_mph": round(s.max_gust_mph, 1),
        "max_rain_in_hr": round(s.max_rain_in_hr, 3),
        "max_snow_in_hr": round(s.max_snow_in_hr, 3),
        "min_temp_f": _temp_f(s.min_temp_f),
        "max_temp_f": _temp_f(s.max_temp_f),
    }


def _checkpoint_json(cp: Checkpoint) -> dict:
    w, a = cp.weather, cp.assessment
    assert w is not None and a is not None
    return {
        "index": cp.index,
        "mile": round(cp.mile, 1),
        "lat": round(cp.position.lat, 5),
        "lon": round(cp.position.lon, 5),
        "segment_miles": round(cp.segment_miles, 2),
        "eta": cp.eta.isoformat(),
        "weather": _weather_json(w),
        "risk": {
            "level": a.level.name,
            "wind": a.wind_level.name,
            "rain": a.rain_level.name,
            "snow": a.snow_level.name,
            "load_adjusted": a.load_adjusted,
            "drivers": list(a.drivers),
        },
    }


def _weather_json(w: WeatherSample) -> dict:
    return {
        "wind_mph": round(w.wind_mph, 1),
        "gust_mph": round(w.gust_mph, 1),
        "rain_in_hr": round(w.rain_in_hr, 3),
        "snow_in_hr": round(w.snow_in_hr, 3),
        "temp_f": _temp_f(w.temp_f),
        "conditions": conditions.describe(w.weather_code),
        "weather_code": w.weather_code,
        "forecast_time": w.forecast_time.isoformat(),
    }


def _temp_f(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class _Stopwatch:
    """Time between laps, printed as "6.21 s total (routing 2.10 s, weather 1.40 s, ...)"."""

    def __init__(self):
        self.start = self.last = time.perf_counter()
        self.laps: list[tuple[str, float]] = []

    def lap(self, step: str) -> None:
        now = time.perf_counter()
        self.laps.append((step, now - self.last))
        self.last = now

    def __str__(self) -> str:
        steps = ", ".join(f"{step} {seconds:.2f} s" for step, seconds in self.laps)
        return f"{self.last - self.start:.2f} s total ({steps})"
