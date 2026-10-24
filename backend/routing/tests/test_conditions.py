import pytest

from routing.services import conditions

# Every code in Open-Meteo's "WMO Weather interpretation codes" table, with its wording there
DOCUMENTED = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    97: "Heavy thunderstorm",
    99: "Thunderstorm with heavy hail",
}


def test_every_documented_code_has_a_description_and_nothing_else_does():
    assert set(conditions.DESCRIPTIONS) == set(DOCUMENTED)


@pytest.mark.parametrize(
    "code, text",
    [
        (0, "Clear"),
        (1, "Mostly clear"),
        (2, "Partly cloudy"),
        (3, "Overcast"),
        (45, "Fog"),
        (48, "Freezing fog"),
        (51, "Light drizzle"),
        (53, "Drizzle"),
        (55, "Heavy drizzle"),
        (56, "Light freezing drizzle"),
        (57, "Heavy freezing drizzle"),
        (61, "Light rain"),
        (63, "Rain"),
        (65, "Heavy rain"),
        (66, "Light freezing rain"),
        (67, "Heavy freezing rain"),
        (71, "Light snow"),
        (73, "Snow"),
        (75, "Heavy snow"),
        (77, "Snow grains"),
        (80, "Light rain showers"),
        (81, "Rain showers"),
        (82, "Violent rain showers"),
        (85, "Light snow showers"),
        (86, "Heavy snow showers"),
        (95, "Thunderstorm"),
        (96, "Thunderstorm with hail"),
        (97, "Heavy thunderstorm"),
        (99, "Thunderstorm with heavy hail"),
    ],
)
def test_describe(code, text):
    assert conditions.describe(code) == text


def test_descriptions_never_say_moderate():
    # "Moderate" is a risk level; a description using it would read like one
    assert not [text for text in conditions.DESCRIPTIONS.values() if "oderate" in text]


@pytest.mark.parametrize("code", [None, 4, 50, 100, -1])
def test_missing_or_undocumented_codes_are_unknown(code):
    assert conditions.describe(code) == "Unknown"
