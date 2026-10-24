import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PlanRequestSerializer
from .services import planner, tomtom, weather

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@lru_cache(maxsize=None)
def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def not_ready(exc: NotImplementedError) -> Response:
    return Response({"detail": f"Not implemented yet: {exc}"}, status=status.HTTP_501_NOT_IMPLEMENTED)


class HealthView(APIView):
    def get(self, request):
        return Response({"ok": True, "fixtures": settings.USE_FIXTURES})


class GeocodeView(APIView):
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
        try:
            places = tomtom.geocode(query, settings.TOMTOM_API_KEY, country_set=settings.GEOCODE_COUNTRY_SET)
        except tomtom.RoutingError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"results": [p.__dict__ for p in places]})


class PlanView(APIView):
    def post(self, request):
        serializer = PlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if settings.USE_FIXTURES:
            return Response(load_fixture("plan.json"))
        try:
            plan = planner.build_plan(**serializer.validated_data)
        except NotImplementedError as exc:
            return not_ready(exc)
        except (tomtom.RoutingError, weather.WeatherError) as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response(plan)


class HeatmapView(APIView):
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
            return Response({"detail": str(exc)}, status=502)
        return Response(data)
