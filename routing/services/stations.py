"""In-memory station index and route corridor matching.

The ~6.6k stations are read from the database once per process into numpy
arrays, so planning a trip never queries the database.
"""

from dataclasses import dataclass
from functools import cache

import numpy as np
from scipy.spatial import cKDTree

from routing.models import FuelStation

from .geo import chord_to_miles, cumulative_miles, densify, miles_to_chord, to_unit_xyz

# The route is resampled to this spacing before nearest-vertex matching, which
# bounds the error in a station's distance from the route by half of it.
ROUTE_SAMPLE_MILES = 0.5
MILES_PER_DEGREE_LAT = 69.0


@dataclass
class StationIndex:
    ids: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    price: np.ndarray
    details: dict[int, dict]  # opis_id -> fields used in the API response

    def __len__(self) -> int:
        return len(self.ids)


@dataclass
class RouteStation:
    opis_id: int
    price: float
    mile_marker: float
    off_route_miles: float


@cache
def get_station_index() -> StationIndex:
    rows = list(
        FuelStation.objects.values(
            "opis_id", "name", "address", "city", "state", "price", "lat", "lon"
        )
    )
    for row in rows:
        row["price"] = float(row["price"])
    return StationIndex(
        ids=np.array([r["opis_id"] for r in rows], dtype=np.int64),
        lat=np.array([r["lat"] for r in rows], dtype=float),
        lon=np.array([r["lon"] for r in rows], dtype=float),
        price=np.array([r["price"] for r in rows], dtype=float),
        details={r["opis_id"]: r for r in rows},
    )


def invalidate_station_index() -> None:
    get_station_index.cache_clear()


def stations_along_route(
    route: np.ndarray, corridor_miles: float, index: StationIndex | None = None
) -> tuple[list[RouteStation], float]:
    """Find stations within `corridor_miles` of a [lat, lon] polyline.

    Returns the stations ordered by distance along the route, and the route length.
    A station the route passes more than once is returned once per pass.
    """
    index = index if index is not None else get_station_index()
    dense = densify(route, ROUTE_SAMPLE_MILES)
    miles_along = cumulative_miles(dense)
    route_miles = float(miles_along[-1])

    candidates = np.flatnonzero(_in_bounding_box(index, dense, corridor_miles))
    if len(candidates) == 0:
        return [], route_miles

    route_xyz = to_unit_xyz(dense[:, 0], dense[:, 1])
    station_xyz = to_unit_xyz(index.lat[candidates], index.lon[candidates])
    nearby = cKDTree(route_xyz).query_ball_point(
        station_xyz, miles_to_chord(corridor_miles), return_sorted=True
    )

    found = []
    for k, vertices in enumerate(nearby):
        if not vertices:
            continue
        vertices = np.asarray(vertices)
        # Consecutive runs of vertices are separate passes by the station.
        passes = np.split(vertices, np.flatnonzero(np.diff(vertices) > 1) + 1)
        station = candidates[k]
        for run in passes:
            chords = np.linalg.norm(route_xyz[run] - station_xyz[k], axis=1)
            closest = np.argmin(chords)
            found.append(
                RouteStation(
                    opis_id=int(index.ids[station]),
                    price=float(index.price[station]),
                    mile_marker=float(miles_along[run[closest]]),
                    off_route_miles=float(chord_to_miles(chords[closest])),
                )
            )
    found.sort(key=lambda s: (s.mile_marker, s.price))
    return found, route_miles


def _in_bounding_box(index: StationIndex, route: np.ndarray, pad_miles: float) -> np.ndarray:
    """Cheap pre-filter: stations inside the route's bounding box, padded by `pad_miles`."""
    max_abs_lat = min(float(np.abs(route[:, 0]).max()), 70.0)
    pad_lat = pad_miles / MILES_PER_DEGREE_LAT
    pad_lon = pad_miles / (MILES_PER_DEGREE_LAT * np.cos(np.radians(max_abs_lat)))
    return (
        (index.lat >= route[:, 0].min() - pad_lat)
        & (index.lat <= route[:, 0].max() + pad_lat)
        & (index.lon >= route[:, 1].min() - pad_lon)
        & (index.lon <= route[:, 1].max() + pad_lon)
    )
