"""Plan a trip: geocode, fetch one route, match stations, optimize fuel stops."""

import hashlib
import time
from collections import Counter

from django.conf import settings
from django.core.cache import cache

from .geo import decimate
from .geocoding import Location, resolve_location
from .optimizer import FuelPlan, Station, plan_fuel_stops
from .osrm import Route, fetch_route
from .stations import RouteStation, StationIndex, get_station_index, stations_along_route

MAX_RESPONSE_ROUTE_POINTS = 1500


def plan_trip(start_query: str, finish_query: str) -> dict:
    """Build the API response for a trip.

    Raises a TripPlanningError subclass when the trip cannot be planned.
    """
    started = time.perf_counter()
    calls = Counter()
    start = resolve_location(start_query, calls)
    finish = resolve_location(finish_query, calls)

    key = _cache_key(start, finish)
    if (result := cache.get(key)) is not None:
        result["start"], result["finish"] = start.to_dict(), finish.to_dict()
        result["meta"] = _meta(calls, started, cache_hit=True)
        return result

    route = fetch_route(start.lat, start.lon, finish.lat, finish.lon)
    calls["routing"] += 1

    index = get_station_index()
    matched, polyline_miles = stations_along_route(
        route.points, settings.STATION_CORRIDOR_MILES, index
    )
    # Mile markers are measured along the decoded polyline. Scale them to OSRM's
    # reported road distance (they differ by ~0.1%) so both use one yardstick.
    scale = route.distance_miles / polyline_miles if polyline_miles else 1.0
    plan = plan_fuel_stops(
        [Station(i, s.mile_marker * scale, s.price) for i, s in enumerate(matched)],
        route_miles=route.distance_miles,
        range_miles=settings.VEHICLE_RANGE_MILES,
        mpg=settings.VEHICLE_MPG,
        first_stop_max_miles=settings.FIRST_STOP_MAX_MILES,
        stop_penalty=settings.FUEL_STOP_PENALTY_USD,
    )

    result = _build_response(start, finish, route, matched, plan, index)
    result["meta"] = _meta(calls, started, cache_hit=False)
    cache.set(key, result, settings.ROUTE_CACHE_SECONDS)
    return result


def _cache_key(start: Location, finish: Location) -> str:
    parts = (
        f"{start.lat:.4f},{start.lon:.4f}",
        f"{finish.lat:.4f},{finish.lon:.4f}",
        settings.VEHICLE_RANGE_MILES,
        settings.VEHICLE_MPG,
        settings.STATION_CORRIDOR_MILES,
        settings.FIRST_STOP_MAX_MILES,
        settings.FUEL_STOP_PENALTY_USD,
    )
    digest = hashlib.sha1("|".join(map(str, parts)).encode(), usedforsecurity=False)
    return f"route:{digest.hexdigest()}"


def _build_response(
    start: Location,
    finish: Location,
    route: Route,
    matched: list[RouteStation],
    plan: FuelPlan,
    index: StationIndex,
) -> dict:
    mpg = settings.VEHICLE_MPG
    stops = []
    for number, purchase in enumerate(plan.purchases, start=1):
        on_route = matched[purchase.station.key]
        station = index.details[on_route.opis_id]
        stop = {
            "stop": number,
            "opis_id": station["opis_id"],
            "name": station["name"],
            "address": station["address"],
            "city": station["city"],
            "state": station["state"],
            "lat": round(station["lat"], 6),
            "lon": round(station["lon"], 6),
            "price_per_gallon": round(station["price"], 4),
            "mile_marker": round(purchase.station.mile, 1),
            "off_route_miles": round(on_route.off_route_miles, 1),
            "fuel_on_arrival_gallons": round(purchase.fuel_on_arrival_miles / mpg, 2),
            "gallons_purchased": round(purchase.tank_gallons(mpg), 2),
            "cost": round(purchase.cost(mpg), 2),
        }
        if purchase.start_fuel_miles:
            stop["start_fuel_gallons"] = round(purchase.start_fuel_miles / mpg, 2)
        stops.append(stop)

    return {
        "start": start.to_dict(),
        "finish": finish.to_dict(),
        "vehicle": {
            "max_range_miles": settings.VEHICLE_RANGE_MILES,
            "mpg": mpg,
            "tank_gallons": settings.VEHICLE_RANGE_MILES / mpg,
        },
        "assumptions": {
            "tank_at_start": (
                "empty; fuel used to reach the first stop is paid for there "
                "(start_fuel_gallons) and included in that stop's cost"
            ),
            "station_corridor_miles": settings.STATION_CORRIDOR_MILES,
            "stop_penalty_usd": settings.FUEL_STOP_PENALTY_USD,
        },
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_seconds / 3600, 2),
            "stations_considered": len(matched),
        },
        "fuel_stops": stops,
        "summary": {
            "number_of_stops": len(stops),
            "total_gallons": round(plan.total_gallons, 2),
            "total_fuel_cost": round(plan.total_cost, 2),
            "average_price_per_gallon": (
                round(plan.total_cost / plan.total_gallons, 4) if plan.total_gallons else None
            ),
        },
        "geojson": _to_geojson(decimate(route.points, MAX_RESPONSE_ROUTE_POINTS), stops),
    }


def _to_geojson(line, stops: list[dict]) -> dict:
    route_feature = {
        "type": "Feature",
        "properties": {"kind": "route"},
        "geometry": {
            "type": "LineString",
            "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in line.tolist()],
        },
    }
    stop_features = [
        {
            "type": "Feature",
            "properties": {
                "kind": "fuel_stop",
                "stop": s["stop"],
                "name": s["name"],
                "price_per_gallon": s["price_per_gallon"],
                "gallons_purchased": s["gallons_purchased"],
                "cost": s["cost"],
            },
            "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
        }
        for s in stops
    ]
    return {"type": "FeatureCollection", "features": [route_feature, *stop_features]}


def _meta(calls: Counter, started: float, cache_hit: bool) -> dict:
    return {
        "cache_hit": cache_hit,
        "geocoding_calls": calls["geocoding"],
        "routing_calls": calls["routing"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
