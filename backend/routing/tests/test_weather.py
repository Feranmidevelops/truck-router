from datetime import datetime, timedelta, timezone
import logging
import math
import random
from zoneinfo import ZoneInfo

import httpx
import pytest

from routing.services import weather
from routing.services.types import LatLon

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def location_payload(snow_unit="inch", temp_unit="°F", hours=3):
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
            "temperature_2m": temp_unit,
            "weather_code": "wmo code",
        },
        "hourly": {
            "time": times,
            "wind_speed_10m": [10.0, 20.0, 30.0][:hours],
            "wind_gusts_10m": [15.0, 25.0, None][:hours],
            "rain": [0.0, 0.1, 0.2][:hours],
            "showers": [0.0, 0.05, 0.1][:hours],
            "snowfall": [0.0, 2.54, 5.08][:hours],
            "temperature_2m": [50.0, 41.0, 32.0][:hours],
            "weather_code": [2, 61, 73][:hours],
        },
    }


def mock_client(payload, calls: list | None = None):
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def browser_fetch(positions, start, end, client, max_cells=None):
    """What the frontend does: fetch each URL the server lists, then hand the replies back."""
    requests = weather.forecast_requests(positions, start, end, max_cells=max_cells)
    replies = [client.get(url).json() for url in requests.urls()]
    return weather.read_forecasts(requests, replies)


def url_params(url: str) -> dict[str, str]:
    parsed = httpx.URL(url)
    assert (parsed.scheme, parsed.host, parsed.path) == ("https", "api.open-meteo.com", "/v1/forecast")
    return dict(parsed.params)


def test_parses_multi_location_list_and_sums_rain_and_showers():
    positions = [LatLon(32.78, -96.80), LatLon(39.74, -104.99)]
    client = mock_client([location_payload(), location_payload()])
    series = browser_fetch(positions, T0, T0 + timedelta(hours=2), client)
    assert len(series) == 2
    assert series[1].position == positions[1]
    assert series[0].times[0] == T0
    assert series[0].rain_in_hr == pytest.approx([0.0, 0.15, 0.3])
    assert series[0].gust_mph[2] == 0.0  # null treated as 0


def test_converts_snowfall_from_cm():
    client = mock_client(location_payload(snow_unit="cm"))
    [s] = browser_fetch([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), client)
    assert s.snow_in_hr == pytest.approx([0.0, 1.0, 2.0])


def test_asks_for_temperature_in_fahrenheit_and_the_weather_code():
    calls: list = []
    [s] = browser_fetch([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), mock_client(location_payload(), calls))
    params = calls[0].url.params
    assert params["temperature_unit"] == "fahrenheit"
    assert {"temperature_2m", "weather_code"} <= set(params["hourly"].split(","))
    assert len(params["hourly"].split(",")) <= 10  # more than 10 variables would count as a second call
    assert s.temp_f == [50.0, 41.0, 32.0]
    assert s.weather_code == [2, 61, 73]


def test_converts_temperature_from_celsius():
    payload = location_payload(temp_unit="°C")
    payload["hourly"]["temperature_2m"] = [10.0, 0.0, -40.0]
    [s] = browser_fetch([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), mock_client(payload))
    assert s.temp_f == pytest.approx([50.0, 32.0, -40.0])


def test_a_missing_temperature_or_weather_code_stays_missing():
    # 0 is a real temperature and a real code (clear sky), so a gap must not turn into either
    payload = location_payload()
    payload["hourly"]["temperature_2m"] = [None, 0.0, 12.5]
    payload["hourly"]["weather_code"] = [None, 0, 3.0]
    [s] = browser_fetch([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), mock_client(payload))
    assert s.temp_f == [None, 0.0, 12.5]
    assert s.weather_code == [None, 0, 3]
    assert isinstance(s.weather_code[2], int)


def test_unknown_temperature_unit_is_an_error():
    client = mock_client(location_payload(temp_unit="K"))
    with pytest.raises(weather.WeatherError, match="Unexpected unit 'K'"):
        browser_fetch([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2), client)


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
    series = browser_fetch([LatLon(30 + i * 0.1, -97) for i in range(n)], T0, T0 + timedelta(hours=2), client)
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

    series = browser_fetch(positions, T0, T0 + timedelta(hours=2), client)

    assert len(calls) == 1
    params = calls[0].url.params
    assert [float(x) for x in params["latitude"].split(",")] == [32.8, 39.7]
    assert [float(x) for x in params["longitude"].split(",")] == [-96.8, -105.0]
    assert [s.position for s in series] == positions
    assert [s.wind_mph[0] for s in series] == [10.0, 40.0, 10.0, 10.0, 40.0]


def echo_client(calls: list):
    """Answers each requested location with wind = its latitude and gust = its longitude."""

    def handler(request: httpx.Request):
        calls.append(request)
        lats = [float(x) for x in request.url.params["latitude"].split(",")]
        lons = [float(x) for x in request.url.params["longitude"].split(",")]
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
    series = browser_fetch(positions, T0, T0, echo_client(calls))
    assert len(calls) == 1
    assert len(calls[0].url.params["latitude"].split(",")) == weather.CHUNK_SIZE
    assert echoed_cell(series[-1]) == pytest.approx((30.0, -97.0))


def along(*stops: tuple[float, float], every_deg: float = 0.14) -> list[LatLon]:
    """A position about every 10 miles (0.14 deg) on straight lines through the stops."""
    out = []
    for (lat1, lon1), (lat2, lon2) in zip(stops, stops[1:]):
        n = max(1, round(math.dist((lat1, lon1), (lat2, lon2)) / every_deg))
        out += [LatLon(lat1 + (lat2 - lat1) * k / n, lon1 + (lon2 - lon1) * k / n) for k in range(n)]
    return out + [LatLon(*stops[-1])]


SEATTLE, MIAMI = (47.61, -122.33), (25.76, -80.19)
# Three Seattle -> Miami routes through different parts of the country, at a 10-mile interval
COAST_TO_COAST = [
    *along(SEATTLE, (43.62, -116.2), (39.74, -104.99), (32.78, -96.8), MIAMI),
    *along(SEATTLE, (40.76, -111.89), (39.1, -94.58), (33.75, -84.39), MIAMI),
    *along(SEATTLE, (45.78, -108.5), (38.63, -90.2), (30.33, -81.66), MIAMI),
]


def requested_cells(calls: list) -> list[tuple[float, float]]:
    return [
        (float(lat), float(lon))
        for call in calls
        for lat, lon in zip(call.url.params["latitude"].split(","), call.url.params["longitude"].split(","))
    ]


def test_a_coast_to_coast_plan_asks_for_no_more_cells_than_allowed(caplog):
    caplog.set_level(logging.INFO, logger="routing.services.weather")
    positions = COAST_TO_COAST
    assert len(positions) > 1_000
    assert len(set(weather.snap_cells(positions))) > 340  # what the 0.1 deg grid would ask for
    calls: list = []

    series = browser_fetch(positions, T0, T0, echo_client(calls), max_cells=340)

    asked = requested_cells(calls)
    assert len(asked) == len(set(asked)) <= 340
    assert [s.position for s in series] == positions
    # Each position is answered from the nearest location asked for: its own cell
    for p, s in zip(positions, series):
        nearest = min(max(abs(lat - p.lat), abs(lon - p.lon)) for lat, lon in asked)
        lat, lon = echoed_cell(s)
        assert max(abs(lat - p.lat), abs(lon - p.lon)) == pytest.approx(nearest)
    assert "weather cells at 0.1 deg is over 340, so using 0.5 deg cells" in caplog.text


def test_cells_are_only_made_coarser_when_they_have_to_be(caplog):
    caplog.set_level(logging.INFO, logger="routing.services.weather")
    positions = [LatLon(30 + i * 0.1, -97 + i * 0.1) for i in range(40)]
    fine = weather.snap_cells(positions)
    assert len(set(fine)) == 40
    assert weather.snap_cells(positions, max_cells=40) == fine
    assert not caplog.records
    coarser = weather.snap_cells(positions, max_cells=39)
    assert len(set(coarser)) <= 39
    assert all(abs(c.lat - p.lat) <= 0.1 + 1e-9 for c, p in zip(coarser, positions))
    assert "40 weather cells at 0.1 deg is over 39, so using 0.2 deg cells" in caplog.text


@pytest.mark.parametrize("max_cells", [1, 7, 50, 340])
def test_any_spread_of_positions_fits_the_cap(max_cells):
    rng = random.Random(42)
    # Anywhere in the lower 48, far more spread out than any trip
    positions = [LatLon(rng.uniform(25, 49), rng.uniform(-125, -67)) for _ in range(1_000)]
    calls: list = []
    series = browser_fetch(positions, T0, T0, echo_client(calls), max_cells=max_cells)
    asked = requested_cells(calls)
    assert len(asked) <= max_cells
    assert [s.position for s in series] == positions
    assert all(echoed_cell(s) in asked for s in series)


def test_max_cells_must_allow_at_least_one():
    with pytest.raises(ValueError):
        weather.snap_cells([LatLon(30, -97)], max_cells=0)


def test_forecast_urls_ask_for_each_grid_point_once_sorted_and_in_chunks():
    n = weather.CHUNK_SIZE + 1
    # Backwards, with a second position in the first one's cell
    positions = [LatLon(30 + i * 0.1, -97) for i in reversed(range(n))] + [LatLon(30.03, -97.02)]
    requests = weather.forecast_requests(positions, T0 + timedelta(minutes=20), T0 + timedelta(hours=2, minutes=5))
    first, second = (url_params(url) for url in requests.urls())
    lats = [float(v) for p in (first, second) for v in p["latitude"].split(",")]
    assert lats == sorted(lats) and len(lats) == n
    assert len(first["latitude"].split(",")) == weather.CHUNK_SIZE
    assert first["hourly"].split(",") == list(weather.HOURLY_VARS)
    assert len(weather.HOURLY_VARS) <= 10  # more than 10 variables would count as a second call
    assert (first["wind_speed_unit"], first["precipitation_unit"]) == ("mph", "inch")
    assert (first["temperature_unit"], first["timezone"]) == ("fahrenheit", "GMT")
    # From the hour the trip starts in to the hour after it ends, both included
    assert (first["start_hour"], first["end_hour"]) == ("2026-10-24T12:00", "2026-10-24T15:00")
    assert requests.hours == 4
    assert requests.cells[-1] == requests.cells[n - 1] == LatLon(30.0, -97.0)


def test_the_same_cells_in_any_order_make_the_same_urls():
    a = [LatLon(39.74, -104.99), LatLon(32.78, -96.80)]
    b = [LatLon(32.81, -96.77), LatLon(39.66, -105.04)]  # the same two cells, the other way round
    assert weather.forecast_requests(a, T0, T0).urls() == weather.forecast_requests(b, T0, T0).urls()


def test_no_positions_need_no_requests():
    requests = weather.forecast_requests([], T0, T0)
    assert requests.urls() == []
    assert weather.read_forecasts(requests, []) == []


def test_replies_are_read_back_to_every_position_across_chunks():
    calls: list = []
    n = 2 * weather.CHUNK_SIZE + 20
    cells = [LatLon(round(30 + i * 0.1, 1), -97.0) for i in range(n)]
    positions = cells + [LatLon(c.lat + 0.03, c.lon - 0.02) for c in reversed(cells)]
    series = browser_fetch(positions, T0, T0, echo_client(calls))
    assert len(calls) == 3
    assert [s.position for s in series] == positions
    for p, s in zip(positions, series):
        assert echoed_cell(s) == pytest.approx((round(p.lat, 1), round(p.lon, 1)))


def one_reply(**hourly_changes):
    payload = location_payload()
    payload["hourly"].update(hourly_changes)
    return payload


def metres_per_second(payload):
    payload["hourly_units"]["wind_speed_10m"] = "m/s"
    return payload


@pytest.mark.parametrize(
    "replies, message",
    [
        ([], "Expected 1 forecast replies, got 0"),
        ([location_payload(), location_payload()], "Expected 1 forecast replies, got 2"),
        ({"0": location_payload()}, "Expected 1 forecast replies, got no list of"),
        ([[location_payload(), location_payload()]], "2 locations for 1 requested"),
        ([None], "unexpected shape"),
        (["<html>Bad gateway</html>"], "unexpected shape"),
        ([{"latitude": 39.7, "longitude": -105.0}], "unexpected shape"),
        ([{"hourly": [1, 2, 3]}], "unexpected shape"),
        ([one_reply(wind_speed_10m=["calm", "breezy", "windy"])], "unexpected shape"),
        ([location_payload(snow_unit="furlongs")], "Unexpected unit 'furlongs'"),
        ([location_payload(hours=2)], "other hours"),
        ([one_reply(time=["2026-10-24T13:00", "2026-10-24T14:00", "2026-10-24T15:00"])], "other hours"),
        ([one_reply(wind_speed_10m=[10.0, 20.0])], "missing values"),
        ([one_reply(weather_code=[2, 61])], "missing values"),
        ([metres_per_second(one_reply(wind_speed_10m=[10.0, 1e308, 30.0]))], "finite"),
        ([one_reply(wind_speed_10m=[10.0, 10**400, 30.0])], "unexpected shape"),
        ([one_reply(temperature_2m=[50.0, 10**400, 32.0])], "unexpected shape"),
        ([one_reply(weather_code=[2, float("inf"), 73])], "unexpected shape"),
    ],
    ids=[
        "none",
        "too many",
        "not a list",
        "too many locations",
        "null",
        "text",
        "no hourly data",
        "hourly not an object",
        "words for numbers",
        "unknown unit",
        "too few hours",
        "an hour late",
        "short wind",
        "short weather code",
        "too big to be a number in mph",
        "an integer too big for a float",
        "a temperature too big for a float",
        "an endless weather code",
    ],
)
def test_replies_that_are_not_the_forecast_asked_for_are_a_weather_error(replies, message):
    requests = weather.forecast_requests([LatLon(39.7, -105.0)], T0, T0 + timedelta(hours=2))
    with pytest.raises(weather.WeatherError, match=message):
        weather.read_forecasts(requests, replies)


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


def test_sample_at_reads_temperature_and_weather_code_from_the_winds_hour():
    # Both are instantaneous like the wind, so at :20 they come from the hour before the rain's
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    w = weather.sample_at(s, T0 + timedelta(minutes=20))
    assert w.forecast_time == T0
    assert (w.temp_f, w.weather_code) == (50.0, 2)
    assert w.rain_in_hr == pytest.approx(0.15)  # from T0+1h

    late = weather.sample_at(s, T0 + timedelta(minutes=40))
    assert late.forecast_time == T0 + timedelta(hours=1)
    assert (late.temp_f, late.weather_code) == (41.0, 61)

    clamped = weather.sample_at(s, T0 + timedelta(hours=10))
    assert (clamped.temp_f, clamped.weather_code) == (32.0, 73)


def test_sample_at_passes_a_missing_temperature_or_code_through():
    payload = location_payload()
    payload["hourly"]["temperature_2m"] = [None, 41.0, 32.0]
    payload["hourly"]["weather_code"] = [None, 61, 73]
    w = weather.sample_at(weather.parse_location(payload, LatLon(32.78, -96.80)), T0)
    assert (w.temp_f, w.weather_code) == (None, None)
    assert w.wind_mph == 10.0


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
    s = weather.HourlySeries(
        LatLon(41.9, -87.6), times, [6.0, 7.0, 8.0], [0.0] * 3, [0.0] * 3, [0.0] * 3, [40.0] * 3, [0] * 3
    )
    # 01:45 CDT is 06:45 UTC, an hour before Chicago falls back to CST.
    w = weather.sample_at(s, datetime(2026, 11, 1, 1, 45, tzinfo=ZoneInfo("America/Chicago")))
    assert w.forecast_time == datetime(2026, 11, 1, 7, tzinfo=timezone.utc)
    assert w.wind_mph == 7.0


def test_sample_at_rejects_a_naive_datetime():
    s = weather.parse_location(location_payload(), LatLon(32.78, -96.80))
    with pytest.raises(ValueError):
        weather.sample_at(s, datetime(2026, 10, 24, 12, 20))
