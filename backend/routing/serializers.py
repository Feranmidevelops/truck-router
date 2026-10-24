from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

MAX_DAYS_AHEAD = 5  # keeps the trip plus the 48h heatmap inside a reliable forecast window


class PlaceSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=200)
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lon = serializers.FloatField(min_value=-180, max_value=180)


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    departure_time = serializers.DateTimeField()
    load_weight_lb = serializers.IntegerField(min_value=0, max_value=80_000)
    interval_miles = serializers.ChoiceField(choices=[10, 25, 50])

    def validate_departure_time(self, value):
        now = timezone.now()
        if value < now - timedelta(hours=1):
            raise serializers.ValidationError("Departure time is in the past.")
        if value > now + timedelta(days=MAX_DAYS_AHEAD):
            raise serializers.ValidationError(f"Departure must be within {MAX_DAYS_AHEAD} days.")
        return value

    def validate(self, attrs):
        o, d = attrs["origin"], attrs["destination"]
        if abs(o["lat"] - d["lat"]) < 1e-4 and abs(o["lon"] - d["lon"]) < 1e-4:
            raise serializers.ValidationError("Origin and destination are the same place.")
        return attrs
