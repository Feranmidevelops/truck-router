"""Stand-ins for TomTom and Open-Meteo, so planner tests never touch the network.

Two levels:
- install_service_fakes replaces the routing and weather functions the planner calls, so only
  the planner's own glue runs.
- FakeUpstream answers HTTP requests the way TomTom and Open-Meteo do, so the real clients,
  route fallbacks, weather sampling and heatmap grid run too. install_fake_http sends every
  httpx.Client through it and makes any other real request fail.
"""

from datetime import datetime, timedelta, timezone
import math
import threading
from urllib.parse import unquote

import httpx

from routing.services import corridor, planner, tomtom, weather
from routing.services.geometry import METERS_PER_MILE, cumulative_m, haversine_m
from routing.services.types import LatLon, RouteOption, TimingPoint, WeatherSample

# In the future whenever the tests run, since a past departure is moved up to now
T0 = (datetime.now(timezone.utc) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
DALLAS = {"label": "Dallas, TX", "lat": 32.0, "lon": -97.0}
NORTH = {"label": "Up the road", "lat": 33.5, "lon": -97.0}
M_PER_DEG_LAT = 111_195
# Sustained wind on each fake route, picked by its longitude: LOW, HIGH, MODERATE
WIND_BY_ROUTE = [5.0, 36.0, 26.0]


def wiggly_route(k: int, miles: float = 110, step_m: float = 70, wiggle_m: float = 3) -> RouteOption:
    """A north-south route with a point every `step_m`, zig-zagging `wiggle_m` either side."""
    lon0 = -97.0 - 0.5 * k
    kx = M_PER_DEG_LAT * math.cos(math.radians(32))
    n = int(miles * METERS_PER_MILE / step_m) + 1
    pts = [
        LatLon(32.0 + i * step_m / M_PER_DEG_LAT, lon0 + (wiggle_m if i % 2 else -wiggle_m) / kx)
        for i in range(n)
    ]
    length = cumulative_m(pts)[-1]
    duration = length / METERS_PER_MILE / 60 * 3600  # 60 mph
    return RouteOption(
        id=f"r{k}",
        source="tomtom",
        distance_m=length,
        duration_s=duration,
        departure_time=T0,
        arrival_time=T0 + timedelta(seconds=duration),
        points=pts,
        timing=[TimingPoint(0, 0), TimingPoint(length, duration)],
    )


def wind_at(p: LatLon) -> float:
    return WIND_BY_ROUTE[min(2, max(0, round((-97.0 - p.lon) / 0.5)))]


def fake_fetch_hourly(positions, start, end, **kwargs):
    first = weather.floor_hour(start)
    hours = int((weather.ceil_hour(end) - first) / timedelta(hours=1)) + 1
    times = [first + timedelta(hours=h) for h in range(hours)]
    return [
        weather.HourlySeries(
            position=p,
            times=times,
            wind_mph=[wind_at(p)] * hours,
            gust_mph=[wind_at(p) + 10] * hours,
            rain_in_hr=[0.0] * hours,
            snow_in_hr=[0.0] * hours,
        )
        for p in positions
    ]


def fake_sample_at(series, when):
    return WeatherSample(
        wind_mph=series.wind_mph[0],
        gust_mph=series.gust_mph[0],
        rain_in_hr=series.rain_in_hr[0],
        snow_in_hr=series.snow_in_hr[0],
        forecast_time=weather.floor_hour(when),
    )


def install_service_fakes(monkeypatch, routes: list[RouteOption]) -> None:
    monkeypatch.setattr(tomtom, "calculate_routes", lambda *a, **k: list(routes))
    monkeypatch.setattr(tomtom, "ensure_three_routes", lambda found, *a, **k: found)
    monkeypatch.setattr(weather, "fetch_hourly", fake_fetch_hourly)
    monkeypatch.setattr(weather, "sample_at", fake_sample_at)
    monkeypatch.setattr(corridor, "build_grid", lambda lines: [line[0] for line in lines])


def plan(**overrides) -> dict:
    kwargs = {
        "origin": DALLAS,
        "destination": NORTH,
        "departure_time": T0,
        "timezone": "America/Chicago",
        "load_weight_lb": 20_000,
        "interval_miles": 25,
    }
    kwargs.update(overrides)
    return planner.build_plan(**kwargs)


ROAD_SPEED_MPS = 55 * METERS_PER_MILE / 3600
POINT_SPACING_M = 500
OPEN_METEO_UNITS = {
    "wind_speed_10m": "mp/h",
    "wind_gusts_10m": "mp/h",
    "rain": "inch",
    "showers": "inch",
    "snowfall": "inch",
}


def upstream_wind_mph(lon: float) -> float:
    """5 mph at Dallas's longitude, rising 5 mph per degree west to 45 mph at Denver's."""
    return min(45.0, max(5.0, 5.0 + 5.0 * (-97.0 - lon)))


def upstream_rain_in_hr(lat: float) -> float:
    return 0.3 if lat > 37.0 else 0.0  # a High rain band over Kansas and Colorado


class FakeUpstream:
    """Answers TomTom and Open-Meteo requests the way the real services do, from made-up data.

    Roads run straight between the requested stops, with a point every 500 m, at 55 mph.
    TomTom offers no alternatives, so the planner has to ask for via-point fallbacks. The
    weather is the same every hour: wind from upstream_wind_mph, rain from upstream_rain_in_hr.
    """

    def __init__(self):
        self.no_route = False  # answer route requests like TomTom does when nothing connects
        self.requests: list[httpx.Request] = []
        self._lock = threading.Lock()  # fallbacks and weather chunks arrive from worker threads

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "api.tomtom.com" and path.startswith("/routing/1/calculateRoute/"):
            return self._route(request)
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            return self._forecast(request)
        return httpx.Response(404, json={"error": f"no fake for {host}{path}"})

    def calls(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]

    def _route(self, request: httpx.Request) -> httpx.Response:
        if self.no_route:
            return httpx.Response(
                400,
                json={
                    "formatVersion": "0.0.12",
                    "detailedError": {"message": "NO_ROUTE_FOUND: No valid route found", "code": "NO_ROUTE_FOUND"},
                },
            )
        locations = unquote(request.url.path.split("/")[4])
        stops = [LatLon(*(float(v) for v in stop.split(","))) for stop in locations.split(":")]
        legs = []  # one leg per pair of stops, each including both ends, like TomTom's
        for a, b in zip(stops, stops[1:]):
            n = max(2, round(haversine_m(a, b) / POINT_SPACING_M))
            dlat, dlon = (b.lat - a.lat) / n, (b.lon - a.lon) / n
            legs.append([LatLon(a.lat + dlat * i, a.lon + dlon * i) for i in range(n + 1)])
        points = [p for i, leg in enumerate(legs) for p in (leg if i == 0 else leg[1:])]
        offsets = cumulative_m(points)
        length = round(offsets[-1])
        seconds = round(length / ROAD_SPEED_MPS)
        depart_at = request.url.params.get("departAt", "now")
        if depart_at == "now":
            leave = datetime.now(timezone.utc).replace(microsecond=0)
        else:
            leave = datetime.fromisoformat(depart_at)
        # An instruction every ~20 km, then the arrival
        instructions = [
            {"routeOffsetInMeters": round(m), "travelTimeInSeconds": round(m / ROAD_SPEED_MPS)}
            for m in offsets[::40]
        ] + [{"routeOffsetInMeters": length, "travelTimeInSeconds": seconds}]
        route = {
            "summary": {
                "lengthInMeters": length,
                "travelTimeInSeconds": seconds,
                "trafficDelayInSeconds": 0,
                "departureTime": leave.isoformat(),
                "arrivalTime": (leave + timedelta(seconds=seconds)).isoformat(),
            },
            "legs": [{"points": [{"latitude": p.lat, "longitude": p.lon} for p in leg]} for leg in legs],
            "guidance": {"instructions": instructions},
        }
        return httpx.Response(200, json={"formatVersion": "0.0.12", "routes": [route]})

    def _forecast(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params
        lats = [float(v) for v in q["latitude"].split(",")]
        lons = [float(v) for v in q["longitude"].split(",")]
        names = q["hourly"].split(",")
        if "start_hour" in q:
            first, last = datetime.fromisoformat(q["start_hour"]), datetime.fromisoformat(q["end_hour"])
        else:
            first = datetime.fromisoformat(q["start_date"])
            last = datetime.fromisoformat(q["end_date"]) + timedelta(hours=23)
        hours = int((last - first) / timedelta(hours=1)) + 1
        times = [(first + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
        items = []
        for lat, lon in zip(lats, lons):
            wind = upstream_wind_mph(lon)
            values = {"wind_speed_10m": wind, "wind_gusts_10m": wind * 1.4, "rain": upstream_rain_in_hr(lat)}
            items.append(
                {
                    "latitude": lat,
                    "longitude": lon,
                    "utc_offset_seconds": 0,
                    "timezone": "GMT",
                    "hourly_units": {"time": "iso8601", **{n: OPEN_METEO_UNITS.get(n, "") for n in names}},
                    "hourly": {"time": times, **{n: [values.get(n, 0.0)] * hours for n in names}},
                }
            )
        return httpx.Response(200, json=items if len(items) > 1 else items[0])


def install_fake_http(monkeypatch, upstream: FakeUpstream) -> None:
    real_client = httpx.Client

    class FakeClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(upstream)
            super().__init__(*args, **kwargs)

    def no_network(transport, request):
        raise RuntimeError(f"A test tried to reach {request.url.host} for real")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
