"""Driving routes from OSRM (the public demo server by default; no API key needed)."""

from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings

from .errors import NoRouteError, RoutingUnavailableError
from .geo import decode_polyline
from .http import http_session

METERS_PER_MILE = 1609.344

# OSRM codes that mean the request itself has no answer, as opposed to a server fault.
_NO_ROUTE_CODES = {"NoRoute", "NoSegment", "InvalidValue", "InvalidQuery"}


@dataclass
class Route:
    points: np.ndarray  # (n, 2) array of [lat, lon]
    distance_miles: float
    duration_seconds: float


def fetch_route(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> Route:
    url = (
        f"{settings.OSRM_BASE_URL.rstrip('/')}/route/v1/driving/"
        f"{start_lon:.6f},{start_lat:.6f};{end_lon:.6f},{end_lat:.6f}"
    )
    # Full-resolution geometry is needed for corridor matching. As polyline6 it is
    # about a fifth of the size of GeoJSON for a cross-country route.
    params = {"overview": "full", "geometries": "polyline6", "steps": "false"}
    try:
        response = http_session().get(url, params=params, timeout=settings.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RoutingUnavailableError("Routing service is unreachable.") from exc

    try:
        body = response.json()
    except ValueError:
        body = {}

    if body.get("code") in _NO_ROUTE_CODES:
        raise NoRouteError(body.get("message") or "No drivable route between these locations.")
    if response.status_code != 200 or body.get("code") != "Ok" or not body.get("routes"):
        raise RoutingUnavailableError(f"Routing service error (HTTP {response.status_code}).")

    route = body["routes"][0]
    try:
        points = decode_polyline(route["geometry"], precision=6)
    except (IndexError, KeyError, TypeError) as exc:
        raise RoutingUnavailableError("Routing service returned malformed geometry.") from exc
    return Route(
        points=points,
        distance_miles=route["distance"] / METERS_PER_MILE,
        duration_seconds=route["duration"],
    )
