from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .services import timezones

MAX_DAYS_AHEAD = 5  # keeps the trip plus the 48h heatmap inside a reliable forecast window


class PlaceSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=200)
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lon = serializers.FloatField(min_value=-180, max_value=180)


class LocalDateTimeField(serializers.DateTimeField):
    """Parses ISO 8601 but leaves a time without an offset naive, to be read at the origin.

    The stock field would attach the server's zone (UTC) to it straight away.
    """

    def enforce_timezone(self, value):
        return value


class BoundedListField(serializers.ListField):
    """A ListField that checks max_length before it looks at any item, not after all of them."""

    def to_internal_value(self, data):
        if isinstance(data, list) and self.max_length is not None and len(data) > self.max_length:
            self.fail("max_length", max_length=self.max_length)
        return super().to_internal_value(data)


class ForecastRepliesSerializer(serializers.Serializer):
    """Open-Meteo's replies to the forecast_urls a plan or heatmap listed, in the same order."""

    plan_id = serializers.RegexField(r"^[0-9a-f]{32}$")
    # A plan lists at most 7 URLs and a heatmap 5. The replies themselves are checked by
    # weather.read_forecasts, which also checks the exact number.
    forecasts = BoundedListField(max_length=20)


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    # "2026-10-26T08:00" is local time at the origin; a time with an offset is kept as that instant
    departure_time = LocalDateTimeField()
    load_weight_lb = serializers.IntegerField(min_value=0, max_value=80_000)
    interval_miles = serializers.ChoiceField(choices=[10, 25, 50])

    def validate(self, attrs):
        o, d = attrs["origin"], attrs["destination"]
        if abs(o["lat"] - d["lat"]) < 1e-4 and abs(o["lon"] - d["lon"]) < 1e-4:
            raise serializers.ValidationError("Origin and destination are the same place.")

        zone = timezones.zone_name(o["lat"], o["lon"])
        try:
            departure = timezones.to_zone(attrs["departure_time"], zone)
        except OverflowError:  # e.g. 9999-12-31T23:00 read west of UTC
            raise serializers.ValidationError({"departure_time": "Datetime value out of range."})
        now = timezone.now()
        if departure < now - timedelta(hours=1):
            raise serializers.ValidationError({"departure_time": "Departure time is in the past."})
        if departure > now + timedelta(days=MAX_DAYS_AHEAD):
            raise serializers.ValidationError(
                {"departure_time": f"Departure must be within {MAX_DAYS_AHEAD} days."}
            )
        attrs["departure_time"] = departure
        attrs["timezone"] = zone
        return attrs
