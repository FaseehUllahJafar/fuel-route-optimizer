import numpy as np
from django.test import SimpleTestCase

from routing.services.geo import (
    chord_to_miles,
    cumulative_miles,
    decode_polyline,
    densify,
    haversine_miles,
    miles_to_chord,
    to_unit_xyz,
)
from routing.services.stations import StationIndex, stations_along_route


def _encode(coords, precision):
    out, prev = [], (0, 0)
    for lat, lon in coords:
        cur = (round(lat * 10**precision), round(lon * 10**precision))
        for value in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(value << 1) if value < 0 else value << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


class GeoTests(SimpleTestCase):
    def test_chord_round_trip_matches_haversine(self):
        # Chicago -> Dallas, ~800 mi great circle.
        a, b = (41.8781, -87.6298), (32.7767, -96.7970)
        arc = haversine_miles(*a, *b)
        chord = np.linalg.norm(to_unit_xyz([a[0]], [a[1]]) - to_unit_xyz([b[0]], [b[1]]))
        self.assertAlmostEqual(chord_to_miles(chord), arc, places=6)
        self.assertAlmostEqual(miles_to_chord(arc), chord, places=9)
        self.assertAlmostEqual(arc, 803, delta=5)

    def test_decode_polyline5_spec_example(self):
        # Example from Google's encoded polyline algorithm documentation.
        pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        np.testing.assert_allclose(pts, [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]])

    def test_decode_polyline6_round_trip(self):
        coords = [(40.712776, -74.005974), (34.052235, -118.243683), (34.052236, -118.243684)]
        pts = decode_polyline(_encode(coords, 6), precision=6)
        np.testing.assert_allclose(pts, coords, atol=1e-6)

    def test_densify_preserves_endpoints_and_length(self):
        pts = np.array([[40.0, -100.0], [40.0, -99.0], [41.0, -99.0]])
        dense = densify(pts, 0.5)
        np.testing.assert_allclose(dense[0], pts[0])
        np.testing.assert_allclose(dense[-1], pts[-1])
        steps = haversine_miles(dense[:-1, 0], dense[:-1, 1], dense[1:, 0], dense[1:, 1])
        self.assertLessEqual(steps.max(), 0.5 + 1e-9)
        self.assertAlmostEqual(cumulative_miles(dense)[-1], cumulative_miles(pts)[-1], delta=0.01)


class CorridorTests(SimpleTestCase):
    def index(self, rows):
        return StationIndex(
            ids=np.array([r[0] for r in rows]),
            lat=np.array([r[1] for r in rows], float),
            lon=np.array([r[2] for r in rows], float),
            price=np.array([r[3] for r in rows], float),
            details={},
        )

    def test_stations_matched_within_corridor_and_ordered_by_mile(self):
        route = np.array([[40.0, -100.0], [40.0, -95.0]])  # ~265 mi due east
        idx = self.index(
            [
                (1, 40.05, -96.0, 3.0),  # ~3.5 mi off route, near the end
                (2, 40.00, -99.0, 3.1),  # on route, near the start
                (3, 41.00, -97.0, 2.0),  # ~69 mi off route
            ]
        )
        found, length = stations_along_route(route, 15, idx)
        self.assertEqual([s.opis_id for s in found], [2, 1])
        self.assertAlmostEqual(found[1].off_route_miles, 3.45, delta=0.3)
        self.assertAlmostEqual(found[0].mile_marker, haversine_miles(40, -100, 40, -99), delta=0.5)
        self.assertAlmostEqual(length, cumulative_miles(densify(route, 0.5))[-1])
