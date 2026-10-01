from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import TripRequestSerializer
from .services.errors import (
    GeocodingError,
    NoFeasiblePlan,
    NoRouteError,
    RoutingUnavailableError,
    TripPlanningError,
)
from .services.planner import plan_trip

ERROR_RESPONSES = {
    GeocodingError: (status.HTTP_400_BAD_REQUEST, "invalid_location"),
    NoRouteError: (status.HTTP_422_UNPROCESSABLE_ENTITY, "no_route"),
    NoFeasiblePlan: (status.HTTP_422_UNPROCESSABLE_ENTITY, "no_feasible_fuel_plan"),
    RoutingUnavailableError: (status.HTTP_502_BAD_GATEWAY, "routing_unavailable"),
}


def _error_response(exc: TripPlanningError) -> Response:
    http_status, code = next(
        (ERROR_RESPONSES[cls] for cls in type(exc).__mro__ if cls in ERROR_RESPONSES),
        (status.HTTP_422_UNPROCESSABLE_ENTITY, "trip_planning_failed"),
    )
    return Response({"error": code, "detail": str(exc)}, status=http_status)


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops for a drive between two US locations.

    GET  /api/route/?start=Chicago, IL&finish=Dallas, TX
    POST /api/route/  {"start": "Chicago, IL", "finish": "Dallas, TX"}
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, data):
        serializer = TripRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        start, finish = serializer.validated_data["start"], serializer.validated_data["finish"]

        try:
            result = plan_trip(start, finish)
        except TripPlanningError as exc:
            return _error_response(exc)

        map_query = urlencode({"start": start, "finish": finish})
        map_url = request.build_absolute_uri(f"{reverse('route-map')}?{map_query}")
        return Response({"map_url": map_url, **result})


def route_map(request):
    """Render the route and fuel stops on a Leaflet map, reusing the cached plan."""
    start = request.GET.get("start", "").strip()
    finish = request.GET.get("finish", "").strip()
    context = {"start": start, "finish": finish}
    if start and finish:
        try:
            context["plan"] = plan_trip(start, finish)
        except TripPlanningError as exc:
            context["error"] = str(exc)
    return render(request, "routing/map.html", context)
