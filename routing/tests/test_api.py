import os
import unittest
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from routing.models import FuelStation
from routing.services.stations import invalidate_station_index
from routing.tests.test_geo import _encode

CHICAGO = (41.8373, -87.6862)  # Gazetteer point for "Chicago, IL"
INDIANAPOLIS = (39.7771, -86.1458)  # Gazetteer point for "Indianapolis, IN"


def fake_osrm_response(points, miles):
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {
        "code": "Ok",
        "routes": [
            {"geometry": _encode(points, 6), "distance": miles * 1609.344, "duration": miles * 60}
        ],
    }
    return resp


class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        invalidate_station_index()
        rows = [
            (1, "NEAR START", 41.80, -87.65, "3.50"),
            (2, "MIDWAY CHEAP", 40.80, -86.90, "2.90"),
            (3, "FAR AWAY", 35.00, -90.00, "1.00"),
        ]
        FuelStation.objects.bulk_create(
            [
                FuelStation(
                    opis_id=i,
                    name=n,
                    address="I-65",
                    city="X",
                    state="IN",
                    price=Decimal(p),
                    lat=la,
                    lon=lo,
                    geocode_source="gazetteer",
                )
                for i, n, la, lo, p in rows
            ]
        )

    def tearDown(self):
        invalidate_station_index()

    def get(self, **params):
        return self.client.get(reverse("route-plan"), params)

    @mock.patch("routing.services.osrm.http_session")
    def test_plan_makes_one_routing_call_and_is_cached(self, session):
        session.return_value.get.return_value = fake_osrm_response([CHICAGO, INDIANAPOLIS], 180)

        r = self.get(start="Chicago, IL", finish="Indianapolis, IN")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(session.return_value.get.call_count, 1)
        self.assertEqual(body["meta"]["routing_calls"], 1)
        self.assertEqual(body["meta"]["geocoding_calls"], 0)
        self.assertEqual([s["opis_id"] for s in body["fuel_stops"]], [1, 2])
        self.assertAlmostEqual(body["summary"]["total_gallons"], 18.0, places=1)
        self.assertAlmostEqual(
            body["summary"]["total_fuel_cost"], sum(s["cost"] for s in body["fuel_stops"]), places=1
        )
        self.assertEqual(body["geojson"]["features"][0]["geometry"]["type"], "LineString")
        self.assertIn("/api/route/map/?", body["map_url"])

        again = self.get(start="Chicago, IL", finish="Indianapolis, IN").json()
        self.assertEqual(session.return_value.get.call_count, 1)
        self.assertTrue(again["meta"]["cache_hit"])
        self.assertEqual(again["summary"], body["summary"])

    @mock.patch("routing.services.osrm.http_session")
    def test_post_json(self, session):
        session.return_value.get.return_value = fake_osrm_response([CHICAGO, INDIANAPOLIS], 180)
        r = self.client.post(
            reverse("route-plan"),
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)

    def test_missing_params(self):
        r = self.get(start="Chicago, IL")
        self.assertEqual(r.status_code, 400)
        self.assertIn("finish", r.json())

    def test_location_outside_us(self):
        r = self.get(start="51.5,-0.12", finish="Chicago, IL")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_location")

    @mock.patch("routing.services.osrm.http_session")
    def test_no_route(self, session):
        resp = mock.Mock(status_code=400)
        resp.json.return_value = {"code": "NoRoute", "message": "Impossible route"}
        session.return_value.get.return_value = resp
        r = self.get(start="Chicago, IL", finish="Indianapolis, IN")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "no_route")

    @mock.patch("routing.services.osrm.http_session")
    def test_routing_service_down(self, session):
        session.return_value.get.side_effect = requests.ConnectionError("down")
        r = self.get(start="Chicago, IL", finish="Indianapolis, IN")
        self.assertEqual(r.status_code, 502)

    @mock.patch("routing.services.osrm.http_session")
    def test_malformed_route_geometry(self, session):
        response = fake_osrm_response([CHICAGO, INDIANAPOLIS], 180)
        response.json.return_value["routes"][0]["geometry"] = "_izlhA~rl"  # truncated
        session.return_value.get.return_value = response
        r = self.get(start="Chicago, IL", finish="Indianapolis, IN")
        self.assertEqual(r.status_code, 502)

    @mock.patch("routing.services.osrm.http_session")
    def test_infeasible_plan(self, session):
        FuelStation.objects.filter(opis_id__in=[1, 2]).delete()
        invalidate_station_index()
        session.return_value.get.return_value = fake_osrm_response([CHICAGO, INDIANAPOLIS], 180)
        r = self.get(start="Chicago, IL", finish="Indianapolis, IN")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "no_feasible_fuel_plan")

    @mock.patch("routing.services.osrm.http_session")
    def test_map_page_renders(self, session):
        session.return_value.get.return_value = fake_osrm_response([CHICAGO, INDIANAPOLIS], 180)
        r = self.client.get(
            reverse("route-map"), {"start": "Chicago, IL", "finish": "Indianapolis, IN"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "MIDWAY CHEAP")
        self.assertContains(r, 'id="route-geojson"')


@unittest.skipUnless(
    os.environ.get("LIVE_OSRM") == "1", "set LIVE_OSRM=1 to hit the real routing API"
)
class LiveRouteTests(TestCase):
    def test_cross_country(self):
        call_command("load_stations", stdout=StringIO())
        r = self.client.get(
            reverse("route-plan"), {"start": "New York, NY", "finish": "Los Angeles, CA"}
        )
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertGreaterEqual(body["summary"]["number_of_stops"], 6)
        self.assertEqual(body["meta"]["routing_calls"], 1)
