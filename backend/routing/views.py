import hashlib
import json
from functools import lru_cache
import logging
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PlanRequestSerializer
from .services import planner, timezones, tomtom, weather

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PLACES_CACHE_S = 24 * 60 * 60  # place names and their positions hardly ever change

ROUTES_DOWN = "Couldn't get routes from TomTom right now. Try again in a minute."
SEARCH_DOWN = "Couldn't search for places right now. Try again in a minute."
WEATHER_DOWN = "Couldn't get the weather forecast right now. Try again in a minute."

log = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def not_ready(exc: NotImplementedError) -> Response:
    return Response({"detail": f"Not implemented yet: {exc}"}, status=status.HTTP_501_NOT_IMPLEMENTED)


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
        body = {"ok": True, "fixtures": settings.USE_FIXTURES}
        # Temporary: one Open-Meteo request from this server, to see why weather fails on Render
        if request.query_params.get("upstream") == "1":
            import httpx

            try:
                r = httpx.get(
                    weather.BASE_URL,
                    params={"latitude": "32.8", "longitude": "-96.8", "hourly": "wind_speed_10m", "forecast_days": 1},
                    timeout=20,
                )
                body["open_meteo"] = {"status": r.status_code, "body": r.text[:300]}
            except httpx.HTTPError as exc:
                body["open_meteo"] = {"error": repr(exc)[:300]}
        return Response(body)


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
            return Response({"detail": "TOMTOM_API_KEY is not set."}, status=503)
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


class PlanView(APIView):
    throttle_scope = "plan"

    def post(self, request):
        serializer = PlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if settings.USE_FIXTURES:
            return Response(load_fixture("plan.json"))
        try:
            plan = planner.build_plan(**serializer.validated_data)
        except NotImplementedError as exc:
            return not_ready(exc)
        except planner.NoRouteFound as exc:
            data = serializer.validated_data
            log.info("No route: %s -> %s", data["origin"]["label"], data["destination"]["label"])
            return Response({"detail": str(exc)}, status=422)
        except tomtom.RoutingError as exc:
            return upstream_failed("TomTom routing", exc, ROUTES_DOWN)
        except weather.WeatherError as exc:
            return upstream_failed("Open-Meteo forecast", exc, WEATHER_DOWN)
        return Response(plan)


class HeatmapView(APIView):
    throttle_scope = "heatmap"

    def get(self, request):
        plan_id = request.query_params.get("plan_id", "")
        if settings.USE_FIXTURES:
            return Response(load_fixture("heatmap.json"))
        try:
            data = planner.build_heatmap(plan_id)
        except planner.PlanNotFound:
            return Response({"detail": "Plan expired or not found. Plan the trip again."}, status=404)
        except NotImplementedError as exc:
            return not_ready(exc)
        except weather.WeatherError as exc:
            return upstream_failed("Open-Meteo heatmap forecast", exc, WEATHER_DOWN)
        return Response(data)
