"""Import the OPIS fuel price CSV into FuelStation, geocoding each stop offline.

The CSV has no coordinates and its addresses are highway exits ("I-44, EXIT 283"),
so each station is placed at its city's Census Gazetteer point. Cities missing
from the Gazetteer (unincorporated places such as Breezewood, PA) are read from
the committed overrides file; --resolve-missing looks new ones up in Nominatim.
"""

import csv
import statistics
from collections import defaultdict
from decimal import Decimal

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation
from routing.services.geocoding import in_us_bounds, nominatim_search, place_index
from routing.services.placenames import US_STATES
from routing.services.stations import invalidate_station_index

OVERRIDE_FIELDS = ["city", "state", "lat", "lon", "label"]

CityKey = tuple[str, str]


class Command(BaseCommand):
    help = "Import fuel prices from the CSV and geocode the stations."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.FUEL_PRICES_CSV))
        parser.add_argument(
            "--resolve-missing",
            action="store_true",
            help="Look up cities missing from the Gazetteer in Nominatim and save them.",
        )

    def handle(self, *args, **options):
        rows_by_id, non_us_rows = self._read_prices(options["csv"])
        stations = [self._station_from_rows(opis_id, rows) for opis_id, rows in rows_by_id.items()]

        unmatched = self._geocode(stations)
        if unmatched and options["resolve_missing"]:
            self._resolve_with_nominatim(unmatched)
            unmatched = {city: group for city, group in unmatched.items() if group[0].lat is None}

        located = [s for s in stations if s.lat is not None]
        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(located, batch_size=1000)
        invalidate_station_index()

        price_rows = sum(len(rows) for rows in rows_by_id.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(located)} stations from {price_rows} US price rows "
                f"({non_us_rows} non-US rows skipped)."
            )
        )
        if unmatched:
            skipped = sum(len(group) for group in unmatched.values())
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {skipped} stations in {len(unmatched)} cities that could not be "
                    "geocoded. Run with --resolve-missing to look them up."
                )
            )

    def _read_prices(self, path: str) -> tuple[dict[int, list[dict]], int]:
        rows_by_id = defaultdict(list)
        non_us_rows = 0
        with open(path, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row["State"].strip().upper() in US_STATES:
                    rows_by_id[int(row["OPIS Truckstop ID"])].append(row)
                else:
                    non_us_rows += 1
        return rows_by_id, non_us_rows

    @staticmethod
    def _station_from_rows(opis_id: int, rows: list[dict]) -> FuelStation:
        # Some stations appear several times with different prices and nothing
        # in the file to tell the rows apart, so use their mean.
        first = rows[0]
        mean_price = statistics.fmean(float(r["Retail Price"]) for r in rows)
        return FuelStation(
            opis_id=opis_id,
            name=first["Truckstop Name"].strip(),
            address=first["Address"].strip(),
            city=first["City"].strip(),
            state=first["State"].strip().upper(),
            price=Decimal(f"{mean_price:.4f}"),
            price_samples=len(rows),
        )

    def _geocode(self, stations: list[FuelStation]) -> dict[CityKey, list[FuelStation]]:
        """Set coordinates in place; return the stations that could not be located, by city."""
        places = place_index()
        overrides = read_overrides()
        unmatched = defaultdict(list)
        for station in stations:
            city = (station.city, station.state)
            if place := places.lookup(*city):
                station.lat, station.lon = place.lat, place.lon
                station.geocode_source = FuelStation.GeocodeSource.GAZETTEER
            elif city in overrides:
                station.lat, station.lon = overrides[city]
                station.geocode_source = FuelStation.GeocodeSource.OVERRIDE
            else:
                unmatched[city].append(station)
        return unmatched

    def _resolve_with_nominatim(self, unmatched: dict[CityKey, list[FuelStation]]) -> None:
        path = settings.STATION_GEOCODE_OVERRIDES_CSV
        is_new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=OVERRIDE_FIELDS)
            if is_new_file:
                writer.writeheader()
            for (city, state), group in sorted(unmatched.items()):
                try:
                    place = nominatim_search(f"{city}, {state}")
                except requests.RequestException as exc:
                    self.stderr.write(f"  {city}, {state}: {exc}")
                    continue
                if place is None or not in_us_bounds(place.lat, place.lon):
                    self.stderr.write(f"  {city}, {state}: not found")
                    continue
                writer.writerow(
                    {
                        "city": city,
                        "state": state,
                        "lat": f"{place.lat:.6f}",
                        "lon": f"{place.lon:.6f}",
                        "label": place.name,
                    }
                )
                for station in group:
                    station.lat, station.lon = place.lat, place.lon
                    station.geocode_source = FuelStation.GeocodeSource.OVERRIDE
                self.stdout.write(f"  {city}, {state} -> {place.lat:.4f}, {place.lon:.4f}")


def read_overrides() -> dict[CityKey, tuple[float, float]]:
    path = settings.STATION_GEOCODE_OVERRIDES_CSV
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return {
            (row["city"], row["state"]): (float(row["lat"]), float(row["lon"]))
            for row in csv.DictReader(fh)
        }
