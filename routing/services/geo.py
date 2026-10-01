"""Vectorized geodesy helpers on a spherical earth, with distances in miles."""

import numpy as np

EARTH_RADIUS_MILES = 3958.8


def to_unit_xyz(lat, lon) -> np.ndarray:
    """Lat/lon degrees -> points on the unit sphere, shape (n, 3)."""
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def miles_to_chord(miles: float) -> float:
    """Surface distance -> straight-line distance between unit-sphere points.

    KD-trees over unit vectors measure chords, not arcs: chord = 2 sin(d / 2R).
    """
    return 2.0 * np.sin(miles / (2.0 * EARTH_RADIUS_MILES))


def chord_to_miles(chord):
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0))


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def decode_polyline(encoded: str, precision: int = 6) -> np.ndarray:
    """Decode a Google encoded polyline (OSRM polyline6) into an (n, 2) [lat, lon] array."""
    coords, index, lat, lon = [], 0, 0, 0
    length = len(encoded)
    while index < length:
        for is_lon in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        coords.append((lat, lon))
    return np.asarray(coords, dtype=float).reshape(-1, 2) / (10**precision)


def segment_miles(points: np.ndarray) -> np.ndarray:
    """Length of each segment of a [lat, lon] polyline."""
    return haversine_miles(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])


def cumulative_miles(points: np.ndarray) -> np.ndarray:
    """Distance along a [lat, lon] polyline at each vertex, starting at 0."""
    return np.concatenate(([0.0], np.cumsum(segment_miles(points))))


def densify(points: np.ndarray, max_step_miles: float) -> np.ndarray:
    """Insert interpolated vertices so no segment exceeds max_step_miles.

    Straight interstate stretches can have vertices miles apart; after densifying,
    the distance to the nearest vertex is a good proxy for the distance to the route.
    """
    if len(points) < 2:
        return points
    pieces = np.maximum(1, np.ceil(segment_miles(points) / max_step_miles).astype(int))
    if pieces.max() == 1:
        return points
    seg_idx = np.repeat(np.arange(len(pieces)), pieces)
    step_in_seg = np.arange(len(seg_idx)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    t = (step_in_seg / pieces[seg_idx])[:, None]
    interpolated = points[seg_idx] + t * (points[seg_idx + 1] - points[seg_idx])
    return np.vstack((interpolated, points[-1:]))


def decimate(points: np.ndarray, max_points: int) -> np.ndarray:
    """Evenly thin a polyline for the response payload, keeping both endpoints."""
    if len(points) <= max_points:
        return points
    idx = np.unique(np.linspace(0, len(points) - 1, max_points).round().astype(int))
    return points[idx]
