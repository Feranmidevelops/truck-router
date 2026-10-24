"""Orchestration: routes -> checkpoints -> weather -> risk -> recommendation.

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

from . import corridor, geometry, recommend, risk, tomtom, weather
from .types import Checkpoint, LatLon, Level, RouteOption, RouteSummary

PLAN_TTL_S = 60 * 60
HEATMAP_HOURS = 48
METERS_PER_MILE = 1609.344
# Route lines are drawn and gridded from a simplified copy. 20 m is inside a highway's width and
# about 2 px at street zoom, and cuts a ~10,000-point TomTom route to under 1,000 points.
SIMPLIFY_TOLERANCE_M = 20
NO_ROUTE_MESSAGE = "No truck route found between these places."

log = logging.getLogger(__name__)


class PlanNotFound(Exception):
    pass


class NoRouteFound(Exception):
    """TomTom answered, but has no truck route between the two places."""


def build_plan(
    *,
    origin: dict,
    destination: dict,
    departure_time: datetime,
    timezone: str,
    load_weight_lb: int,
    interval_miles: int,
) -> dict:
    """`departure_time` is aware, with the origin's offset; `timezone` is the origin's IANA zone."""
    o = LatLon(origin["lat"], origin["lon"])
    d = LatLon(destination["lat"], destination["lon"])
    key = settings.TOMTOM_API_KEY
    # The form accepts a departure up to an hour ago, but TomTom can only leave now. The whole
    # plan (ETAs, arrival, heatmap start, the departure echoed back) starts at the same instant.
    now = datetime.now(UTC).replace(microsecond=0)
    departure = max(departure_time, now).astimezone(departure_time.tzinfo)
    clock = _Stopwatch()

    try:
        routes = tomtom.calculate_routes(o, d, departure, load_weight_lb, key)
    except tomtom.RoutingError as exc:
        # calculate_routes raises this exact message when TomTom's reply has no routes in it
        if str(exc) == NO_ROUTE_MESSAGE:
            raise NoRouteFound(NO_ROUTE_MESSAGE) from exc
        raise
    if not routes:
        raise NoRouteFound(NO_ROUTE_MESSAGE)
    clock.lap("routing")
    routes = tomtom.ensure_three_routes(routes, o, d, departure, load_weight_lb, key)
    clock.lap("fallback routes")
    # Every lookup below is keyed by route id, so number them here rather than trust the
    # routing client to keep TomTom's routes and the fallbacks apart. Fallbacks are requested
    # a few seconds after the first call, so their "now" is reset to the plan's too.
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
    series = weather.fetch_hourly(
        [cp.position for cp in flat],
        min(cp.eta for cp in flat),
        max(cp.eta for cp in flat),
        cache=cache,
    )
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
    clock.lap("risk + ranking")

    # Checkpoints above were sampled on the full line; the map and the heatmap only need its shape
    shapes = {r.id: geometry.simplify(r.points, SIMPLIFY_TOLERANCE_M) for r in routes}
    clock.lap("simplify")

    plan_id = uuid.uuid4().hex
    log.info("Plan %s: %d routes, %d checkpoints, %s", plan_id, len(routes), len(flat), clock)
    cache.set(
        f"plan:{plan_id}",
        {
            "routes": [shapes[r.id] for r in routes],
            "departure_time": departure,
            "load_weight_lb": load_weight_lb,
        },
        PLAN_TTL_S,
    )

    by_id = {r.id: r for r in routes}
    # Lettered in the order shown, so Route A is always the recommended one
    labels = {rid: f"Route {chr(65 + i)}" for i, rid in enumerate(order)}
    return {
        "plan_id": plan_id,
        "input": {
            "origin": origin,
            "destination": destination,
            "departure_time": departure.isoformat(),
            "timezone": timezone,
            "load_weight_lb": load_weight_lb,
            "interval_miles": interval_miles,
        },
        "recommended_route_id": order[0],
        "recommendation_reason": reason,
        "routes": [
            _route_json(by_id[rid], shapes[rid], labels[rid], rank, summaries[rid], checkpoints[rid])
            for rank, rid in enumerate(order, 1)
        ],
    }


def build_heatmap(plan_id: str) -> dict:
    stored = cache.get(f"plan:{plan_id}")
    if stored is None:
        raise PlanNotFound(plan_id)
    clock = _Stopwatch()
    grid = corridor.build_grid(stored["routes"])
    clock.lap("grid")
    start = weather.floor_hour(stored["departure_time"])
    end = start + timedelta(hours=HEATMAP_HOURS)
    series = weather.fetch_hourly(grid, start, end, cache=cache)
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


def _route_json(
    route: RouteOption,
    shape: list[LatLon],
    label: str,
    rank: int,
    summary: RouteSummary,
    checkpoints: list[Checkpoint],
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
        "weather": {
            "wind_mph": round(w.wind_mph, 1),
            "gust_mph": round(w.gust_mph, 1),
            "rain_in_hr": round(w.rain_in_hr, 3),
            "snow_in_hr": round(w.snow_in_hr, 3),
            "forecast_time": w.forecast_time.isoformat(),
        },
        "risk": {
            "level": a.level.name,
            "wind": a.wind_level.name,
            "rain": a.rain_level.name,
            "snow": a.snow_level.name,
            "load_adjusted": a.load_adjusted,
            "drivers": list(a.drivers),
        },
    }


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
