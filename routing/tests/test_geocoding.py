from collections import Counter
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from routing.services.errors import GeocodingError
from routing.services.geocoding import Place, resolve_location
from routing.services.placenames import normalize_place


class NormalizeTests(SimpleTestCase):
    def test_gazetteer_suffixes_and_abbreviations(self):
        self.assertEqual(normalize_place("Dallas city"), "dallas")
        self.assertEqual(normalize_place("St. Louis"), "saint louis")
        self.assertEqual(normalize_place("Ft Worth"), "fort worth")
        self.assertEqual(
            normalize_place("Nashville-Davidson metropolitan government (balance)"),
            "nashville davidson",
        )
        self.assertEqual(normalize_place("O'Fallon"), "ofallon")


class ResolveTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_city_state_resolves_offline(self):
        with mock.patch("routing.services.geocoding.nominatim_search") as nom:
            calls = Counter()
            loc = resolve_location("Chicago, IL", calls)
            nom.assert_not_called()
        self.assertEqual(loc.source, "gazetteer")
        self.assertAlmostEqual(loc.lat, 41.84, delta=0.2)
        self.assertEqual(calls["geocoding"], 0)

    def test_state_name_and_no_comma(self):
        self.assertEqual(resolve_location("Austin Texas").source, "gazetteer")
        self.assertEqual(resolve_location("st louis, mo").source, "gazetteer")

    def test_consolidated_city_alias(self):
        loc = resolve_location("Nashville, TN")
        self.assertAlmostEqual(loc.lat, 36.17, delta=0.2)

    def test_lat_lng(self):
        loc = resolve_location("39.7392, -104.9903")
        self.assertEqual(loc.source, "coordinates")

    def test_coordinates_outside_us_rejected(self):
        with self.assertRaises(GeocodingError):
            resolve_location("51.5072,-0.1276")

    def test_free_text_uses_nominatim_once_and_caches(self):
        with mock.patch(
            "routing.services.geocoding.nominatim_search",
            return_value=Place(38.8977, -77.0365, "White House"),
        ) as nom:
            calls = Counter()
            resolve_location("1600 Pennsylvania Ave NW Washington", calls)
            resolve_location("1600 Pennsylvania Ave NW Washington", calls)
        self.assertEqual(nom.call_count, 1)
        self.assertEqual(calls["geocoding"], 1)

    def test_not_found_is_cached_too(self):
        with mock.patch("routing.services.geocoding.nominatim_search", return_value=None) as nom:
            for _ in range(2):
                with self.assertRaises(GeocodingError):
                    resolve_location("Atlantis Underwater")
        self.assertEqual(nom.call_count, 1)
