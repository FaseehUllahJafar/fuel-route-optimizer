"""Resolve user input to coordinates, avoiding network calls where possible.

"lat,lng" is parsed directly and "City, ST" is looked up in the bundled Census
Gazetteer extract; only free-text addresses go to Nominatim.
"""

import csv
import hashlib
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from functools import cache
from typing import NamedTuple

import requests
from django.conf import settings
from django.core.cache import cache as django_cache

from .errors import GeocodingError
from .http import http_session
from .placenames import normalize_place, parse_state

# A loose box around the 50 states; OSRM rejects anything unroutable anyway.
US_LAT_RANGE = (18.0, 72.0)
US_LON_RANGE = (-180.0, -64.0)

MAX_QUERY_LENGTH = 200
NOMINATIM_CACHE_SECONDS = 7 * 24 * 3600
NOMINATIM_MIN_INTERVAL = 1.0  # usage policy: at most one request per second

_LAT_LNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class Place(NamedTuple):
    lat: float
    lon: float
    name: str


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    label: str
    source: str  # "coordinates", "gazetteer" or "nominatim"

    def to_dict(self) -> dict:
        return {
            "query_resolved_to": self.label,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "geocoder": self.source,
        }


def in_us_bounds(lat: float, lon: float) -> bool:
    return US_LAT_RANGE[0] <= lat <= US_LAT_RANGE[1] and US_LON_RANGE[0] <= lon <= US_LON_RANGE[1]


class PlaceIndex:
    """(state, normalized name) -> Place, with aliases for common spelling variants."""

    def __init__(self, rows):
        self._exact: dict[tuple[str, str], Place] = {}
        self._alias: dict[tuple[str, str], Place] = {}
        for row in rows:
            state, key = row["state"], row["key"]
            place = Place(float(row["lat"]), float(row["lon"]), row["name"])
            self._exact[(state, key)] = place
            # "Mc Calla" vs "McCalla", "De Forest" vs "DeForest".
            self._alias.setdefault((state, key.replace(" ", "")), place)
            # Consolidated city-counties: "Macon-Bibb County", "Lexington-Fayette urban county".
            if "-" in row["name"]:
                head = normalize_place(row["name"].split("-")[0])
                if head:
                    self._alias.setdefault((state, head), place)
                    self._alias.setdefault((state, head.replace(" ", "")), place)

    def lookup(self, city: str, state: str) -> Place | None:
        key = normalize_place(city)
        if not key:
            return None
        return (
            self._exact.get((state, key))
            or self._alias.get((state, key))
            or self._alias.get((state, key.replace(" ", "")))
        )


@cache
def place_index() -> PlaceIndex:
    with open(settings.PLACES_CSV, encoding="utf-8") as fh:
        return PlaceIndex(csv.DictReader(fh))


def parse_city_state(text: str) -> tuple[str, str] | None:
    """Split 'Dallas, TX', 'Dallas, Texas' or 'Dallas TX' into (city, state code)."""
    if "," in text:
        city, _, state = text.rpartition(",")
        code = parse_state(state)
        if code and city.strip():
            return city.strip(), code
    city, _, state = text.rpartition(" ")
    code = parse_state(state) if city else None
    if code:
        return city.strip().rstrip(","), code
    return None


_nominatim_lock = threading.Lock()
_nominatim_last_call = 0.0


def nominatim_search(query: str) -> Place | None:
    """Geocode free text with Nominatim, restricted to the US and throttled to 1 req/s."""
    global _nominatim_last_call
    with _nominatim_lock:
        wait = _nominatim_last_call + NOMINATIM_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _nominatim_last_call = time.monotonic()

    response = http_session().get(
        settings.NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "countrycodes": "us", "limit": 1},
        timeout=settings.HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    top = results[0]
    return Place(float(top["lat"]), float(top["lon"]), top.get("display_name", query))


def resolve_location(text: str, calls: Counter | None = None) -> Location:
    """Resolve user input to a US location; external lookups are counted in `calls`."""
    text = (text or "").strip()
    if not text:
        raise GeocodingError("Location is empty.")
    if len(text) > MAX_QUERY_LENGTH:
        raise GeocodingError("Location is too long.")

    if match := _LAT_LNG.match(text):
        lat, lon = float(match[1]), float(match[2])
        if not in_us_bounds(lat, lon):
            raise GeocodingError(f"'{text}' is not inside the USA (expected 'lat,lng').")
        return Location(lat, lon, f"{lat:.5f},{lon:.5f}", "coordinates")

    if parsed := parse_city_state(text):
        city, state = parsed
        if place := place_index().lookup(city, state):
            return Location(place.lat, place.lon, f"{place.name}, {state}", "gazetteer")

    place = _cached_nominatim(text, calls)
    if place is None:
        raise GeocodingError(f"Could not find '{text}' in the USA.")
    if not in_us_bounds(place.lat, place.lon):
        raise GeocodingError(f"'{text}' is not inside the USA.")
    return Location(place.lat, place.lon, place.name, "nominatim")


def _cached_nominatim(text: str, calls: Counter | None) -> Place | None:
    canonical = " ".join(text.lower().split())
    key = "geocode:" + hashlib.sha1(canonical.encode(), usedforsecurity=False).hexdigest()
    cached = django_cache.get(key)
    if cached is not None:
        return Place(*cached) if cached else None  # False caches "not found"

    if calls is not None:
        calls["geocoding"] += 1
    try:
        place = nominatim_search(text)
    except requests.RequestException as exc:
        raise GeocodingError(f"Geocoding service unavailable for '{text}'.") from exc
    django_cache.set(key, tuple(place) if place else False, NOMINATIM_CACHE_SECONDS)
    return place
