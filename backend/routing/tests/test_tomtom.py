from datetime import datetime, timezone

from routing.services import tomtom

T0 = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)

SAMPLE = {
    "routes": [
        {
            "summary": {
                "lengthInMeters": 10_000,
                "travelTimeInSeconds": 600,
                "departureTime": "2026-10-24T07:00:00-05:00",
                "arrivalTime": "2026-10-24T07:10:00-05:00",
            },
            "legs": [
                {"points": [{"latitude": 32.0, "longitude": -97.0}, {"latitude": 32.05, "longitude": -97.0}]},
                {"points": [{"latitude": 32.05, "longitude": -97.0}, {"latitude": 32.09, "longitude": -97.0}]},
            ],
            "guidance": {
                "instructions": [
                    {"routeOffsetInMeters": 0, "travelTimeInSeconds": 0},
                    {"routeOffsetInMeters": 4_000, "travelTimeInSeconds": 300},
                    {"routeOffsetInMeters": 10_000, "travelTimeInSeconds": 600},
                ]
            },
        }
    ]
}


def test_vehicle_weight_adds_tare_and_converts_to_kg():
    assert tomtom.vehicle_weight_kg(0) == round(35_000 * 0.45359237)
    assert tomtom.vehicle_weight_kg(45_000) == round(80_000 * 0.45359237)


def test_parse_routes():
    [r] = tomtom.parse_routes(SAMPLE, T0, source="tomtom", id_prefix="r")
    assert r.id == "r0"
    assert len(r.points) == 3  # shared leg boundary point not duplicated
    assert r.departure_time == datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    assert [(t.meters, t.seconds) for t in r.timing] == [(0, 0), (4_000, 300), (10_000, 600)]
