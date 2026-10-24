"""Short, plain descriptions of Open-Meteo's `weather_code`. Pure, no I/O.

The codes are the WMO weather interpretation codes Open-Meteo documents at
https://open-meteo.com/en/docs (the "WMO Weather interpretation codes" table).
Codes 96 and 99 only come from models with a hail forecast; the rest of the
models report thunderstorms as 95 and 97.

"Moderate" is left out ("Rain", not "Moderate rain") so it can't be read as the
Moderate risk level, which comes from the rain rate and the brief's thresholds,
not from this code.
"""

UNKNOWN = "Unknown"

DESCRIPTIONS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",  # "depositing rime fog": it leaves ice on the road and the truck
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Light freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    97: "Heavy thunderstorm",
    99: "Thunderstorm with heavy hail",
}


def describe(code: int | None) -> str:
    """The description for a code, e.g. 61 is "Light rain". A missing or undocumented code is "Unknown"."""
    return DESCRIPTIONS.get(code, UNKNOWN)
