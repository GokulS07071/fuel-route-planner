# Fuel Route Planner

A Django 6.1.2 API that returns a US driving route, an interactive map with fuel stops, and the minimum fuel cost for a fixed route. Assignment defaults are **500 miles of range** and **10 mpg**. Fuel prices come from the supplied OPIS CSV.

## Run with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync --locked
uv run --locked python manage.py runserver
```

Open `http://127.0.0.1:8000`. Python 3.12 or newer is required; uv can install the version specified in `.python-version`. `pyproject.toml` and `uv.lock` are the dependency source of truth; installation uses uv.

No API keys, accounts, migrations, or environment variables are needed for local use. Debug defaults to off; enable it explicitly with `DJANGO_DEBUG=1` for local debugging.

## API

`GET /api/v1/plan` takes:

| Parameter | Default | Meaning |
|---|---|---|
| `start` | required | US city/state, address, or `lat,lon` |
| `end` | required | US destination in the same formats |
| `range` | 500 | Tank range in miles; 50–2000 |
| `mpg` | 10 | Economy; 1–100 |
| `corridor_miles` | 15 | Maximum station distance from the route; 1–100 |

```bash
curl "http://127.0.0.1:8000/api/v1/plan?start=Dallas,TX&end=Houston,TX"
```

`POST /api/v1/plan` accepts the same fields as an `application/json` object. Locations are limited to 300 characters and request bodies to 16 KiB. Numeric inputs must be finite and within their permitted ranges.

The response contains:

- `request`: resolved endpoints and vehicle parameters.
- `route`: distance, duration, and GeoJSON LineString geometry.
- `fuel_plan`: total cost, total gallons, purchase stops, and assumptions. Each stop includes its station and fuel remaining on arrival.
- `map_url`: an interactive Leaflet map preserving every planning parameter.
- `meta`: actual upstream calls for this successful request, cache/offline sources, station coverage, and timings.

Open the returned `map_url`, or use `/map?start=Dallas,TX&end=Houston,TX` directly. `GET /api/v1/health` is a liveness probe.

Errors use 400 for invalid/unresolved locations, 413 for oversized bodies, 415 for unsupported POST content types, 422 for infeasible fuel coverage, 502 for upstream failures, and 503 with `Retry-After` when local capacity is exhausted. Planning endpoints accept GET and POST only.

## Performance and upstream calls

- City/state inputs resolve from the bundled Census gazetteer where possible. Coordinates and offline cities require **one OSRM routing call** on a cold trip.
- Addresses not in the gazetteer require at most **two Nominatim calls plus one OSRM call** for a successful cold request. There are no automatic retries or redirect requests.
- Repeated requests require **zero upstream calls while their cache entries remain valid**. Routes expire after seven days; geocodes after 30 days. Eviction can also require a fresh call.
- SQLite entries are shared across local worker processes. Leases coalesce route misses and serialize Nominatim calls, with starts spaced at least 1.1 seconds apart. Four plans may run concurrently; excess work returns 503 instead of growing an unbounded queue.
- Response byte/time limits, cache entry/byte limits, and explicit connection closure bound resource use.
- Map tiles and Leaflet assets are browser requests, separate from server routing/geocoding call counts. Leaflet assets have verified integrity hashes.
- OSM tiles use the canonical `https://tile.openstreetmap.org/{z}/{x}/{y}.png` endpoint. The page and tile layer use `strict-origin-when-cross-origin`, sending the site origin as the required referrer without exposing route queries or addresses.

A live Dallas–Chicago check (961 miles, six purchase stops) took approximately 1.55 seconds cold with one routing call and 0.11 seconds warm with zero calls on the review machine. These are observed timings, not service guarantees.

OSRM: https://project-osrm.org

Nominatim: https://operations.osmfoundation.org/policies/nominatim/

Both public services can be unavailable or throttled. Configure a suitable deployment gateway/rate limiter and self-host or select a service with appropriate capacity for sustained traffic. This public, stateless assignment API does not require authentication.

## Fuel optimization and limitations

The optimizer allows fuel to remain in the tank between stops. At each station it buys just enough to reach the first cheaper or equally priced station within range; otherwise it fills to capacity or buys enough to finish. A monotonic stack finds those next stations in linear time. Sorting makes the overall planner O(n log n).

For example, on a 600-mile trip with a 500-mile tank, 10 mpg, $1 fuel at the origin, and $10 fuel at mile 400: buy 50 gallons at the origin and 10 at mile 400. Total cost is $150, and the tank is empty at the destination.

The trip starts and ends with an empty tank, so total purchased gallons equal route miles divided by mpg. Initial fuel is priced at the cheapest dataset station within 25 miles of the origin, falling back to 100 miles. Its identity is returned and displayed; if none exists, the API returns 422 rather than using a distant station's price.

**The supplied CSV contains city/state rather than pump coordinates.** Station pins use city centroids, and off-route detour miles are excluded from cost/range calculations. The result is optimal for this approximate fixed-route model, not a guarantee of the cheapest real journey. Verify exact station locations and current prices before travel.

Endpoint country checks use bundled public-domain Census 2025 state and equivalent-entity polygons, including Alaska, Hawaii, and US territories. The 1:500,000 boundaries are simplified; a point extremely close to a shoreline/border may be classified differently from a surveyed boundary. No country-validation API calls are needed. A driving route and sufficient dataset stations must still exist between accepted endpoints.

Boundary source: https://www.census.gov/geographies/mapping-files/2025/geo/carto-boundary-file.html

Refresh the bundled boundaries only when needed:

```bash
uv run --locked python scripts/update_us_boundaries.py
```

The existing `data/custom_places.json` is already bundled. `uv run --locked python manage.py enrich_places` refreshes missing towns; it is not needed to run the app.

## Validation

```bash
uv run --locked python manage.py test
uv run --locked ruff check .
uv run --locked ruff format --check .
```

35 tests cover fuel carryover, 150 independently enumerated optimality cases, capacity/gallon invariants, gaps, route projection, US/offline location validation, JSON limits, map escaping and parameter preservation, upstream failures, cache eviction/expiry, leases/pacing, simultaneous route misses, three cold calls, and zero warm calls. All upstream calls in tests are mocked.

## Production settings

Set `DJANGO_ENV=production`, a strong `DJANGO_SECRET_KEY`, and explicit comma-separated `DJANGO_ALLOWED_HOSTS`. Production startup rejects missing secrets, wildcard hosts, and debug mode, and enables HTTPS redirect and HSTS. Ensure the deployment terminates HTTPS correctly. Local mode generates an ephemeral secret; any future signed state would require a persistent configured secret.
