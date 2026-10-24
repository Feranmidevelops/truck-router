from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from routing.services import weather
from routing.services.types import LatLon

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
RATE_LIMITED = {"error": True, "reason": "rate limited"}


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Records each retry wait instead of sleeping."""
    waits: list = []
    monkeypatch.setattr(weather, "time", SimpleNamespace(sleep=waits.append))
    return waits


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
    # 0.1 deg apart, so rounding keeps every position as its own location.
    series = weather.fetch_hourly([LatLon(30 + i * 0.1, -97) for i in range(n)], T0, T0, client=client)
    assert len(series) == n
    assert len(calls) == 2
    assert calls[0].url.params["wind_speed_unit"] == "mph"


def test_nearby_positions_share_one_location_and_keep_their_own_position():
    dallas = [LatLon(32.78, -96.80), LatLon(32.81, -96.77), LatLon(32.83, -96.84)]
    denver = [LatLon(39.74, -104.99), LatLon(39.66, -105.04)]
    positions = [dallas[0], denver[0], dallas[1], dallas[2], denver[1]]
    windy = location_payload()
    windy["hourly"]["wind_speed_10m"] = [40.0, 50.0, 60.0]
    calls: list = []
    client = mock_client([location_payload(), windy], calls)

    series = weather.fetch_hourly(positions, T0, T0 + timedelta(hours=2), client=client)

    assert len(calls) == 1
    params = calls[0].url.params
    assert [float(x) for x in params["latitude"].split(",")] == [32.8, 39.7]
    assert [float(x) for x in params["longitude"].split(",")] == [-96.8, -105.0]
    assert [s.position for s in series] == positions
    assert [s.wind_mph[0] for s in series] == [10.0, 40.0, 10.0, 10.0, 40.0]


def echo_client(calls: list, rate_limited_lat: float | None = None):
    """Answers each requested location with wind = its latitude and gust = its longitude.

    Any request that includes `rate_limited_lat` gets a 429 every time.
    """

    def handler(request: httpx.Request):
        calls.append(request)
        lats = [float(x) for x in request.url.params["latitude"].split(",")]
        lons = [float(x) for x in request.url.params["longitude"].split(",")]
        if rate_limited_lat in lats:
            return httpx.Response(429, json=RATE_LIMITED)
        body = []
        for lat, lon in zip(lats, lons):
            payload = location_payload(hours=1)
            payload["hourly"]["wind_speed_10m"] = [lat]
            payload["hourly"]["wind_gusts_10m"] = [lon]
            body.append(payload)
        return httpx.Response(200, json=body if len(body) > 1 else body[0])

    return httpx.Client(transport=httpx.MockTransport(handler))


def echoed_cell(series: weather.HourlySeries) -> tuple[float, float]:
    return series.wind_mph[0], series.gust_mph[0]


def test_a_duplicate_just_past_a_chunk_boundary_is_not_requested_again():
    calls: list = []
    positions = [LatLon(30 + i * 0.1, -97) for i in range(weather.CHUNK_SIZE)]
    positions.append(LatLon(30.03, -97.02))  # same cell as the first position
    series = weather.fetch_hourly(positions, T0, T0, client=echo_client(calls))
    assert len(calls) == 1
    assert len(calls[0].url.params["latitude"].split(",")) == weather.CHUNK_SIZE
    assert echoed_cell(series[-1]) == pytest.approx((30.0, -97.0))


def test_every_position_maps_to_its_own_cell_across_chunks():
    calls: list = []
    n = 2 * weather.CHUNK_SIZE + 20
    cells = [LatLon(round(30 + i * 0.1, 1), -97.0) for i in range(n)]
    # Each cell again, later in the list and in reverse, so repeats land in other chunks.
    positions = cells + [LatLon(c.lat + 0.03, c.lon - 0.02) for c in reversed(cells)]

    series = weather.fetch_hourly(positions, T0, T0, client=echo_client(calls))

    requested = [lat for call in calls for lat in call.url.params["latitude"].split(",")]
    assert len(calls) == 3
    assert len(requested) == n
    assert [s.position for s in series] == positions
    for p, s in zip(positions, series):
        assert echoed_cell(s) == pytest.approx((round(p.lat, 1), round(p.lon, 1)))


class DictCache(dict):
    def set(self, key, value, timeout=None):
        self[key] = value


def test_the_same_cells_asked_for_again_in_any_order_come_from_the_cache():
    calls: list = []
    cache = DictCache()  # empty, so falsy: the cache must still be used
    client = echo_client(calls)
    weather.fetch_hourly([LatLon(39.74, -104.99), LatLon(32.78, -96.80)], T0, T0, client=client, cache=cache)
    # Other positions in the same two cells, in the other order.
    nearby = [LatLon(32.81, -96.77), LatLon(39.66, -105.04)]
    series = weather.fetch_hourly(nearby, T0, T0, client=client, cache=cache)
    assert len(calls) == 1
    assert [s.position for s in series] == nearby
    assert [echoed_cell(s) for s in series] == [(32.8, -96.8), (39.7, -105.0)]


def scripted_client(responses: list[httpx.Response], calls: list):
    def handler(request: httpx.Request):
        calls.append(request)
        return responses[len(calls) - 1]

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retries_once_after_a_429(sleeps):
    calls: list = []
    client = scripted_client(
        [httpx.Response(429, json=RATE_LIMITED), httpx.Response(200, json=location_payload())], calls
    )
    [s] = weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), client=client)
    assert len(calls) == 2
    assert sleeps == [weather.RETRY_WAIT_S]  # waited before the retry
    assert s.wind_mph == [10.0, 20.0, 30.0]


def test_a_second_429_gives_a_friendly_error(sleeps):
    calls: list = []
    client = scripted_client([httpx.Response(429, json=RATE_LIMITED)] * 2, calls)
    with pytest.raises(weather.WeatherError) as exc_info:
        weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0, client=client)
    assert len(calls) == 2
    assert sleeps == [weather.RETRY_WAIT_S]
    assert str(exc_info.value) == "The weather service is rate-limiting requests. Try again in a minute."


def test_other_http_errors_are_not_retried(sleeps):
    calls: list = []
    client = scripted_client([httpx.Response(400, json={"error": True, "reason": "bad"})], calls)
    with pytest.raises(weather.WeatherError, match="400"):
        weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0, client=client)
    assert len(calls) == 1
    assert sleeps == []


def test_one_rate_limited_chunk_fails_the_whole_lookup_with_the_friendly_message():
    calls: list = []
    positions = [LatLon(30 + i * 0.1, -97) for i in range(weather.CHUNK_SIZE + 1)]
    client = echo_client(calls, rate_limited_lat=positions[-1].lat)  # only the second chunk
    with pytest.raises(weather.WeatherError) as exc_info:
        weather.fetch_hourly(positions, T0, T0, client=client)
    assert len(calls) == 3  # the first chunk once, the rate-limited one twice
    assert str(exc_info.value) == weather.RATE_LIMITED_MESSAGE


def test_a_response_that_is_not_json_is_a_weather_error():
    calls: list = []
    client = scripted_client([httpx.Response(200, text="<html>Bad gateway</html>")], calls)
    with pytest.raises(weather.WeatherError, match="isn't JSON"):
        weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0, client=client)
    assert len(calls) == 1


def test_unknown_unit_is_an_error():
    client = mock_client(location_payload(snow_unit="furlongs"))
    with pytest.raises(weather.WeatherError):
        weather.fetch_hourly([LatLon(39.7, -105.0)], T0, T0, client=client)


def test_sample_at_uses_nearest_hour_for_wind_and_next_hour_for_precip():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    w = weather.sample_at(s, T0 + timedelta(minutes=20))
    assert w.wind_mph == 10.0  # nearest hour is T0
    assert w.gust_mph == 15.0  # gusts follow the wind's hour
    assert w.rain_in_hr == pytest.approx(0.15)  # window (T0, T0+1h] is reported at T0+1h
    assert w.snow_in_hr == pytest.approx(2.54)  # snow follows the rain's hour, not the wind's
    assert w.forecast_time == T0

    late = weather.sample_at(s, T0 + timedelta(minutes=40))
    assert late.wind_mph == 20.0

    clamped = weather.sample_at(s, T0 + timedelta(hours=10))
    assert clamped.wind_mph == 30.0


def test_sample_at_half_past_goes_to_the_later_hour():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    just_before = weather.sample_at(s, T0 + timedelta(minutes=29, seconds=59))
    assert just_before.forecast_time == T0

    half = weather.sample_at(s, T0 + timedelta(minutes=30))
    assert half.forecast_time == T0 + timedelta(hours=1)
    assert half.wind_mph == 20.0
    assert half.gust_mph == 25.0
    assert half.rain_in_hr == pytest.approx(0.15)  # wind and precip from the same row


def test_sample_at_exactly_on_the_hour_uses_that_hour_for_everything():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    w = weather.sample_at(s, T0 + timedelta(hours=1))
    assert w.forecast_time == T0 + timedelta(hours=1)
    assert w.wind_mph == 20.0
    assert w.rain_in_hr == pytest.approx(0.15)  # window (T0, T0+1h] includes its end
    assert w.snow_in_hr == pytest.approx(2.54)


def test_sample_at_clamps_before_the_first_and_after_the_last_hour():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    early = weather.sample_at(s, T0 - timedelta(hours=3))
    assert early.forecast_time == T0
    assert (early.wind_mph, early.rain_in_hr, early.snow_in_hr) == (10.0, 0.0, 0.0)

    late = weather.sample_at(s, T0 + timedelta(hours=10))
    assert late.forecast_time == T0 + timedelta(hours=2)
    assert late.wind_mph == 30.0
    assert late.gust_mph == 0.0
    assert late.rain_in_hr == pytest.approx(0.3)
    assert late.snow_in_hr == pytest.approx(5.08)


def test_sample_at_accepts_any_timezone():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    central = datetime(2026, 10, 24, 7, 20, tzinfo=timezone(timedelta(hours=-5)))  # 12:20 UTC
    w = weather.sample_at(s, central)
    assert w.forecast_time == T0
    assert w.forecast_time.tzinfo == timezone.utc
    assert w.rain_in_hr == pytest.approx(0.15)

    # Half-hour offsets must still land on whole UTC hours.
    newfoundland = datetime(2026, 10, 24, 10, 40, tzinfo=timezone(timedelta(hours=-2, minutes=-30)))
    assert weather.sample_at(s, newfoundland).forecast_time == T0 + timedelta(hours=1)  # 13:10 UTC


def test_sample_at_measures_real_time_across_a_dst_change():
    start = datetime(2026, 11, 1, 6, tzinfo=timezone.utc)
    times = [start + timedelta(hours=h) for h in range(3)]
    s = weather.HourlySeries(LatLon(41.9, -87.6), times, [6.0, 7.0, 8.0], [0.0] * 3, [0.0] * 3, [0.0] * 3)
    # 01:45 CDT is 06:45 UTC, an hour before Chicago falls back to CST.
    w = weather.sample_at(s, datetime(2026, 11, 1, 1, 45, tzinfo=ZoneInfo("America/Chicago")))
    assert w.forecast_time == datetime(2026, 11, 1, 7, tzinfo=timezone.utc)
    assert w.wind_mph == 7.0


def test_sample_at_rejects_a_naive_datetime():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    with pytest.raises(ValueError):
        weather.sample_at(s, datetime(2026, 10, 24, 12, 20))
