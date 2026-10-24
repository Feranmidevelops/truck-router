from datetime import datetime, timedelta, timezone

import httpx
import pytest

from routing.services import weather
from routing.services.types import LatLon

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def location_payload(snow_unit="inch", hours=3):
    times = [(T0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    return {
        "latitude": 32.75,
        "longitude": -96.75,
        "hourly_units": {
            "time": "iso8601",
            "wind_speed_10m": "mp/h",
            "wind_gusts_10m": "mp/h",
            "rain": "inch",
            "showers": "inch",
            "snowfall": snow_unit,
        },
        "hourly": {
            "time": times,
            "wind_speed_10m": [10.0, 20.0, 30.0][:hours],
            "wind_gusts_10m": [15.0, 25.0, None][:hours],
            "rain": [0.0, 0.1, 0.2][:hours],
            "showers": [0.0, 0.05, 0.1][:hours],
            "snowfall": [0.0, 2.54, 5.08][:hours],
        },
    }


def mock_client(payload, calls: list | None = None):
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_multi_location_list_and_sums_rain_and_showers():
    positions = [LatLon(32.78, -96.80), LatLon(39.74, -104.99)]
    client = mock_client([location_payload(), location_payload()])
    series = weather.fetch_hourly(positions, T0, T0 + timedelta(hours=2), client=client)
    assert len(series) == 2
    assert series[1].position == positions[1]
    assert series[0].times[0] == T0
    assert series[0].rain_in_hr == pytest.approx([0.0, 0.15, 0.3])
    assert series[0].gust_mph[2] == 0.0  # null treated as 0


def test_converts_snowfall_from_cm():
    client = mock_client(location_payload(snow_unit="cm"))
    [s] = weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), client=client)
    assert s.snow_in_hr == pytest.approx([0.0, 1.0, 2.0])


def test_requests_are_chunked():
    calls: list = []
    n = weather.CHUNK_SIZE + 1
    payloads = {weather.CHUNK_SIZE: [location_payload()] * weather.CHUNK_SIZE, 1: location_payload()}

    def handler(request: httpx.Request):
        calls.append(request)
        count = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=payloads[count])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    series = weather.fetch_hourly([LatLon(30 + i * 0.01, -97) for i in range(n)], T0, T0, client=client)
    assert len(series) == n
    assert len(calls) == 2
    assert calls[0].url.params["wind_speed_unit"] == "mph"


def test_unknown_unit_is_an_error():
    client = mock_client(location_payload(snow_unit="furlongs"))
    with pytest.raises(weather.WeatherError):
        weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0, client=client)


def test_sample_at_uses_nearest_hour_for_wind_and_next_hour_for_precip():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    w = weather.sample_at(s, T0 + timedelta(minutes=20))
    assert w.wind_mph == 10.0  # nearest hour is T0
    assert w.rain_in_hr == pytest.approx(0.15)  # window (T0, T0+1h] is reported at T0+1h
    assert w.forecast_time == T0

    late = weather.sample_at(s, T0 + timedelta(minutes=40))
    assert late.wind_mph == 20.0

    clamped = weather.sample_at(s, T0 + timedelta(hours=10))
    assert clamped.wind_mph == 30.0
