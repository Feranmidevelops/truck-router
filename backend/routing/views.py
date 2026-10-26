import hashlib
import json
from functools import lru_cache
import logging
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ForecastRepliesSerializer, PlanRequestSerializer
from .services import planner, timezones, tomtom, weather

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PLACES_CACHE_S = 24 * 60 * 60  # place names and their positions hardly ever change
# A plan's forecasts are well under 1 MB and a heatmap's about 1 MB. DRF reads a JSON body
# without Django's upload limit, so this one is checked here.
MAX_FORECASTS_BYTES = 4_000_000

ROUTES_DOWN = "Couldn't get routes from TomTom right now. Try again in a minute."
SEARCH_DOWN = "Couldn't search for places right now. Try again in a minute."
NO_KEY = "TOMTOM_API_KEY is not set."
PLAN_GONE = "Plan expired or not found. Plan the trip again."
BAD_FORECASTS = "Those aren't the forecasts this plan asked for. Plan the trip again."
TOO_MUCH = "That's more forecast data than a plan needs."

log = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def upstream_failed(service: str, exc: Exception, message: str) -> Response:
    """Log what the upstream service said, and tell the driver something they can act on."""
    detail = str(exc)
    if settings.TOMTOM_API_KEY:
        # An HTTP error can quote the request URL, and the TomTom key is in its query string
        detail = detail.replace(settings.TOMTOM_API_KEY, "<TOMTOM_API_KEY>")
    log.error("%s failed: %s", service, detail)
    return Response({"detail": message}, status=502)


class HealthView(APIView):
    def get(self, request):
        return Response({"ok": True, "fixtures": settings.USE_FIXTURES})


class GeocodeView(APIView):
    throttle_scope = "geocode"

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2:
            return Response({"results": []})
        if settings.USE_FIXTURES:
            q = query.lower()
            places = [p for p in load_fixture("places.json") if q in p["label"].lower()]
            return Response({"results": places[:5]})
        if not settings.TOMTOM_API_KEY:
            return Response({"detail": NO_KEY}, status=503)
        # Typing "salt", "salt l", "salt la"... asks again for every prefix, and other drivers type
        # the same towns, so answers are cached and a repeat costs no TomTom request.
        normalized = " ".join(query.lower().split())
        key = "places:" + hashlib.sha1(f"{settings.GEOCODE_COUNTRY_SET}|{normalized}".encode()).hexdigest()
        results = cache.get(key)
        if results is None:
            try:
                places = tomtom.geocode(query, settings.TOMTOM_API_KEY, country_set=settings.GEOCODE_COUNTRY_SET)
            except tomtom.RoutingError as exc:
                return upstream_failed("TomTom search", exc, SEARCH_DOWN)
            # The zone lets the form say which local time the departure is in
            results = [{**p.__dict__, "timezone": timezones.zone_name(p.lat, p.lon)} for p in places]
            cache.set(key, results, PLACES_CACHE_S)
        return Response({"results": results})


def with_forecasts(request, finish) -> Response:
    """Pass the forecasts the browser fetched to `finish(plan_id, replies)` and answer with it."""
    try:
        too_big = int(request.META.get("CONTENT_LENGTH") or 0) > MAX_FORECASTS_BYTES
    except ValueError:
        too_big = True
    if too_big:
        return Response({"detail": TOO_MUCH}, status=413)
    serializer = ForecastRepliesSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    plan_id = serializer.validated_data["plan_id"]
    try:
        return Response(finish(plan_id, serializer.validated_data["forecasts"]))
    except planner.PlanNotFound:
        return Response({"detail": PLAN_GONE}, status=404)
    except weather.WeatherError as exc:
        log.warning("Forecasts for %s rejected: %s", plan_id, exc)
        return Response({"detail": BAD_FORECASTS}, status=400)


class PlanView(APIView):
    """Step 1 of a plan: the routes, and the forecasts for the browser to fetch.

    With sample data on, the whole plan comes back at once instead.
    """

    throttle_scope = "plan"

    def post(self, request):
        serializer = PlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if settings.USE_FIXTURES:
            return Response(load_fixture("plan.json"))
        if not settings.TOMTOM_API_KEY:
            return Response({"detail": NO_KEY}, status=503)
        try:
            pending = planner.start_plan(**serializer.validated_data)
        except planner.NoRouteFound as exc:
            data = serializer.validated_data
            log.info("No route: %s -> %s", data["origin"]["label"], data["destination"]["label"])
            return Response({"detail": str(exc)}, status=422)
        except tomtom.RoutingError as exc:
            return upstream_failed("TomTom routing", exc, ROUTES_DOWN)
        return Response(pending)


class PlanForecastsView(APIView):
    """Step 2 of a plan: the forecasts in, the scored and ranked plan out."""

    throttle_scope = "forecasts"

    def post(self, request):
        return with_forecasts(request, planner.finish_plan)


class HeatmapView(APIView):
    """Step 1 of a heatmap: the forecasts for the browser to fetch. Sample data comes back whole."""

    throttle_scope = "heatmap"

    def get(self, request):
        plan_id = request.query_params.get("plan_id", "")
        if settings.USE_FIXTURES:
            return Response(load_fixture("heatmap.json"))
        try:
            return Response(planner.heatmap_request(plan_id))
        except planner.PlanNotFound:
            return Response({"detail": PLAN_GONE}, status=404)


class HeatmapForecastsView(APIView):
    """Step 2 of a heatmap: the forecasts in, risk levels per grid point and hour out."""

    throttle_scope = "forecasts"

    def post(self, request):
        return with_forecasts(request, planner.build_heatmap)
