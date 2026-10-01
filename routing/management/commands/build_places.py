"""Build data/us_places.csv from the US Census Gazetteer files.

Only needed to refresh the committed file. Source files:
https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
  *_Gaz_place_national.txt    incorporated places and CDPs
  *_Gaz_cousubs_national.txt  county subdivisions (New England towns, townships)
"""

import csv
from collections.abc import Iterator

from django.conf import settings
from django.core.management.base import BaseCommand

from routing.services.placenames import US_STATES, normalize_place

OUTPUT_FIELDS = ["state", "key", "name", "lat", "lon", "kind"]


class Command(BaseCommand):
    help = "Build the offline US place lookup used to geocode stations and user input."

    def add_arguments(self, parser):
        parser.add_argument("--places", required=True, help="path to *_Gaz_place_national.txt")
        parser.add_argument("--cousubs", help="path to *_Gaz_cousubs_national.txt")
        parser.add_argument("--out", default=str(settings.PLACES_CSV))

    def handle(self, *args, **options):
        sources = [("place", options["places"])]
        if options["cousubs"]:
            sources.append(("cousub", options["cousubs"]))

        # When several entries normalize to the same name in a state, prefer an
        # actual place over a county subdivision, then the largest by land area
        # (the city rather than a same-named CDP).
        best: dict[tuple[str, str], tuple[tuple[int, int], dict]] = {}
        for rank, (kind, path) in enumerate(sources):
            for row in _read_gazetteer(path):
                place = {
                    "state": row["USPS"],
                    "key": normalize_place(row["NAME"]),
                    "name": row["NAME"],
                    "lat": f"{float(row['INTPTLAT']):.6f}",
                    "lon": f"{float(row['INTPTLONG']):.6f}",
                    "kind": kind,
                }
                if place["state"] not in US_STATES or not place["key"]:
                    continue
                priority = (rank, -int(row["ALAND"]))
                slot = (place["state"], place["key"])
                if slot not in best or priority < best[slot][0]:
                    best[slot] = (priority, place)

        with open(options["out"], "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(place for _, (_, place) in sorted(best.items()))

        self.stdout.write(self.style.SUCCESS(f"Wrote {len(best)} places to {options['out']}"))


def _read_gazetteer(path: str) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        for row in reader:
            yield {key: value.strip() for key, value in row.items()}
