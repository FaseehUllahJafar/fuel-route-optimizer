from rest_framework import serializers

from .services.geocoding import MAX_QUERY_LENGTH

LOCATION_HELP = "'City, ST', 'lat,lng' or a street address in the USA"


class TripRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=MAX_QUERY_LENGTH, help_text=LOCATION_HELP)
    finish = serializers.CharField(max_length=MAX_QUERY_LENGTH, help_text=LOCATION_HELP)
