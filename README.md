# Fuel Route Optimizer

Django API that plans a drive between two US locations and returns the route, where to
refuel along it, and what the fuel will cost. The vehicle has a 500 mile range and does
10 mpg; fuel prices come from the supplied OPIS truck stop file.

Each request makes a single call to the routing API. City/state and coordinate inputs are
geocoded offline, and repeat requests are served from cache.

## Running it

Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations   # imports and geocodes the price file, a few seconds
python manage.py runserver
```

Open http://127.0.0.1:8000/ for the map UI, or call the API directly:

```bash
curl "http://127.0.0.1:8000/api/route/?start=Chicago,%20IL&finish=Dallas,%20TX"

curl -X POST http://127.0.0.1:8000/api/route/ \
     -H "Content-Type: application/json" \
     -d '{"start": "New York, NY", "finish": "Los Angeles, CA"}'
```

No API keys are needed.

## API

`GET /api/route/?start=...&finish=...` or `POST /api/route/` with `{"start": ..., "finish": ...}`.

Locations can be given as:

| Input | Example | External calls |
|---|---|---|
| City and state | `Dallas, TX`, `St. Louis, Missouri`, `Austin TX` | none |
| Coordinates | `39.7392,-104.9903` | none |
| Anything else | `1600 Pennsylvania Ave NW, Washington` | one Nominatim lookup, cached |

Response for Chicago to Dallas, trimmed:

```jsonc
{
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=Chicago%2C+IL&finish=Dallas%2C+TX",
  "start":  {"query_resolved_to": "Chicago city, IL", "lat": 41.837045, "lon": -87.684939, "geocoder": "gazetteer"},
  "finish": {"query_resolved_to": "Dallas city, TX", "lat": 32.794176, "lon": -96.765503, "geocoder": "gazetteer"},
  "vehicle": {"max_range_miles": 500.0, "mpg": 10.0, "tank_gallons": 50.0},
  "route": {"distance_miles": 961.5, "duration_hours": 17.04, "stations_considered": 230},
  "fuel_stops": [
    {
      "stop": 1, "opis_id": 70108, "name": "RICKY ROCKETS FUEL CENTER", "address": "SR-50 & SR-83",
      "city": "Midlothian", "state": "IL", "lat": 41.625428, "lon": -87.724233,
      "price_per_gallon": 3.149, "mile_marker": 17.6, "off_route_miles": 1.7,
      "fuel_on_arrival_gallons": 0.0, "gallons_purchased": 46.02, "start_fuel_gallons": 1.76,
      "cost": 150.45
    },
    {"stop": 2, "name": "SHELL", "city": "Osceola", "state": "AR", "mile_marker": 477.8,
     "gallons_purchased": 48.37, "cost": 145.06}
  ],
  "summary": {"number_of_stops": 2, "total_gallons": 96.15, "total_fuel_cost": 295.51,
              "average_price_per_gallon": 3.0735},
  "geojson": {"type": "FeatureCollection", "features": ["route LineString", "a Point per stop"]},
  "meta": {"cache_hit": false, "geocoding_calls": 0, "routing_calls": 1, "elapsed_ms": 204}
}
```

The map comes back two ways:
- `geojson`: the route plus the stops, ready for Leaflet, Mapbox or geojson.io.
- `map_url`: a page that draws them on OpenStreetMap. It reads the cached plan, so opening
  it doesn't call the routing API again.

Errors come back as `{"error": code, "detail": message}`:

| Status | `error` | Cause |
|---|---|---|
| 400 | | `start` or `finish` missing (standard DRF validation body) |
| 400 | `invalid_location` | location not found, or outside the US |
| 422 | `no_route` | nothing drivable between the two points |
| 422 | `no_feasible_fuel_plan` | a stretch longer than the vehicle's range has no station, or none near the start |
| 502 | `routing_unavailable` | the routing service failed |

## How it works

1. Resolve both locations, offline where possible.
2. Return the cached plan if there is one.
3. Fetch the route from OSRM: one call, full geometry as `polyline6`.
4. Find the stations within 15 miles of the route and their mile markers.
5. Choose the stops with an exact dynamic program.

### Routing and geocoding

Routing uses the public [OSRM](https://project-osrm.org/) server, which needs no key. For a
cross-country route, `polyline6` geometry is about a fifth the size of GeoJSON and is fetched
about four times faster. Point `OSRM_BASE_URL` at your own OSRM instance for anything beyond
a demo; the public server has no SLA.

City names are resolved against the US Census Gazetteer (public domain), committed as
`data/us_places.csv`. Nominatim is only used for free-text addresses, throttled to its
1 request/second policy and cached.

### Locating the stations

The price file has no coordinates, and its addresses are highway exits ("I-44, EXIT 283 &
US-69") that geocoders can't resolve. `load_stations` places each station at its city's
Gazetteer point:
- 97% of cities match directly.
- Spacing variants ("Mc Calla") and consolidated city-counties (Nashville-Davidson, Macon-Bibb)
  are matched through aliases.
- The remaining ~150 unincorporated places (Breezewood PA, Ruther Glen VA) were looked up
  once in Nominatim and committed as `data/station_geocode_overrides.csv`.

All 6,626 US stations are placed. The 620 Canadian rows are skipped.

City-centroid positions aren't exact, so stations within 15 miles of the route count as on
it, and each stop reports `off_route_miles`. 597 station IDs appear more than once with
different prices and nothing to tell the rows apart; their prices are averaged.

### Choosing the stops

The tank holds 50 gallons. It starts empty: the vehicle leaves with just enough fuel to reach
its first stop, and that fuel is paid for there (`start_fuel_gallons`, included in the stop's
`cost`). So `total_gallons` is always `distance / mpg`, and `total_fuel_cost` covers the
whole trip.

The stops are chosen by the dynamic program from Khuller, Malekian and Mestre, *To Fill or
not to Fill: The Gas Station Problem* (ACM TALG, 2011). At each stop the optimal policy either
buys just enough to reach a cheaper stop or fills the tank, which keeps the state space small.
A coast-to-coast route with ~500 candidate stations solves in under 10 ms. The tests check the
optimizer against exhaustive search.

Every stop also carries a $5 planning penalty (`FUEL_STOP_PENALTY_USD`), which is not part
of the reported cost. Without it, the cheapest plan from New York to Los Angeles makes 18
stops, some to buy half a gallon a cent cheaper. With it, the plan makes 7 stops for 1.2% more.
Set it to 0 for the strict minimum.

### Performance

Stations are loaded into numpy arrays once per process, so requests don't query the database.
To find the stations along a route:
1. Filter by the route's bounding box.
2. Resample the route to 0.5 mile spacing.
3. Query a KD-tree of unit-sphere vectors.

A route that passes the same station twice gets that station once per pass.

| Trip | Routing calls | Time |
|---|---|---|
| Chicago to Dallas, 962 mi | 1 | ~0.2 s |
| Seattle to Boston, 3,050 mi | 1 | ~1.5 s, of which OSRM ~1.4 s |
| Repeated request | 0 | ~1 ms |

Plans are cached for 6 hours in Django's cache. It is LocMem by default; use Redis when
running more than one worker.

## Configuration

| Variable | Default |
|---|---|
| `OSRM_BASE_URL` | `https://router.project-osrm.org` |
| `VEHICLE_RANGE_MILES` | `500` |
| `VEHICLE_MPG` | `10` |
| `STATION_CORRIDOR_MILES` | `15` |
| `FIRST_STOP_MAX_MILES` | `50` |
| `FUEL_STOP_PENALTY_USD` | `5` |
| `ROUTE_CACHE_SECONDS` | `21600` |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | development values |

## Code layout

```
routing/
  services/
    geocoding.py   input -> coordinates (Gazetteer, Nominatim fallback)
    osrm.py        the single routing call
    stations.py    in-memory station index, corridor matching
    optimizer.py   refueling dynamic program (pure Python, no Django)
    planner.py     ties the above together, caching, response shape
  management/commands/
    load_stations.py   import and geocode the price file
    build_places.py    rebuild data/us_places.csv from Census files
  views.py         API view and map page
```

## Tests

```bash
python manage.py test routing                  # offline, under a second
LIVE_OSRM=1 python manage.py test routing      # adds a real New York to Los Angeles request
```

What the tests cover:
- The optimizer is checked against brute-force search on 400 random instances, with and
  without the stop penalty. Each plan is also replayed to confirm the tank never runs dry or
  overfills.
- API tests mock OSRM and assert that each request makes exactly one routing call, and a
  cached one makes none.

`ruff check . && ruff format --check .` is clean.

## Refreshing the data

`data/us_places.csv` and `data/station_geocode_overrides.csv` are committed. To rebuild them
from newer source files:

```bash
python manage.py build_places --places 2024_Gaz_place_national.txt --cousubs 2024_Gaz_cousubs_national.txt
python manage.py load_stations --resolve-missing
```
