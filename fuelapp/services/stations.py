"""Fuel stations from the OPIS CSV, matched onto a routed polyline.

The CSV has no coordinates (City/State only), so city -> lat/lon comes
from the offline Census gazetteer (see places.py). Stations are then
projected onto the route to get a mile marker, keeping only those within
a corridor of the route.
"""

from __future__ import annotations

import csv
import functools
import math
from dataclasses import dataclass
from pathlib import Path

from .places import lookup_with_fallback

_DATA = Path(__file__).resolve().parents[2] / "data"
_M_PER_MILE = 1609.344
CORRIDOR_MILES_DEFAULT = 15.0

CANADA_PROVINCES = {
    "AB",
    "BC",
    "MB",
    "NB",
    "NL",
    "NS",
    "NT",
    "NU",
    "ON",
    "PE",
    "QC",
    "SK",
    "YT",
}


@dataclass(slots=True)
class Station:
    id: str
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lon: float


@dataclass(slots=True)
class RoutePoint:
    mile: float  # along-route mile marker
    lat: float
    lon: float


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 3958.7613 * math.asin(math.sqrt(a))


@functools.lru_cache(maxsize=1)
def load_stations() -> tuple[list[Station], dict]:
    """Parse CSV -> unique stations (min price per truckstop) with coordinates."""
    path = _DATA / "fuel-prices.csv"
    best: dict[tuple[str, str, str], dict] = {}
    rows = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows += 1
            sid = (row.get("OPIS Truckstop ID") or "").strip()
            city = (row.get("City") or "").strip()
            state = (row.get("State") or "").strip().upper()
            price_raw = (row.get("Retail Price") or "").strip()
            if not sid or not city or not price_raw:
                continue
            try:
                price = float(price_raw)
            except ValueError:
                continue
            if not math.isfinite(price) or price <= 0:
                continue
            key = (sid, city, state)
            cur = best.get(key)
            if cur is None or price < cur["price"]:
                best[key] = {
                    "id": sid,
                    "name": (row.get("Truckstop Name") or "").strip(),
                    "address": (row.get("Address") or "").strip(),
                    "city": city,
                    "state": state,
                    "price": price,
                }

    stations: list[Station] = []
    unmatched: dict[str, str] = {}
    skipped_non_us = 0
    for (sid, city, state), info in best.items():
        if state in CANADA_PROVINCES:
            skipped_non_us += 1
            continue  # assignment is USA-only
        coords = lookup_with_fallback(city, state)
        if coords is None:
            unmatched[f"{city}, {state}"] = sid
            continue
        stations.append(Station(lat=coords[0], lon=coords[1], **info))

    stats = {
        "csv_rows": rows,
        "unique_stations": len(best),
        "located": len(stations),
        "unmatched_cities": sorted(unmatched),
        "skipped_non_us": skipped_non_us,
    }
    return stations, stats


# ---------------------------------------------------------------------------
# Route projection
# ---------------------------------------------------------------------------


def build_route_points(
    coords: list[list[float]], total_miles: float
) -> list[RoutePoint]:
    """coords = OSRM [[lon, lat], ...]; mile markers scaled to the OSRM total."""
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    cum = [0.0]
    for i in range(1, len(coords)):
        cum.append(
            cum[-1] + haversine_miles(lats[i - 1], lons[i - 1], lats[i], lons[i])
        )
    scale = total_miles / cum[-1] if cum[-1] > 0 else 1.0
    return [
        RoutePoint(mile=cum[i] * scale, lat=lats[i], lon=lons[i])
        for i in range(len(coords))
    ]


class _RouteIndex:
    """Index segment bounding boxes, including long segments between vertices."""

    CELL = 0.25

    def __init__(self, points):
        self.grid = {}
        for i, (a, b) in enumerate(zip(points, points[1:])):
            for cy in range(
                math.floor(min(a.lat, b.lat) / self.CELL),
                math.floor(max(a.lat, b.lat) / self.CELL) + 1,
            ):
                for cx in range(
                    math.floor(min(a.lon, b.lon) / self.CELL),
                    math.floor(max(a.lon, b.lon) / self.CELL) + 1,
                ):
                    self.grid.setdefault((cy, cx), []).append(i)

    def segments(self, lat, lon, corridor):
        pad_lat = (corridor + 1) / 69.0
        pad_lon = pad_lat / max(0.1, math.cos(math.radians(abs(lat) + pad_lat)))
        result = set()
        for cy in range(
            math.floor((lat - pad_lat) / self.CELL),
            math.floor((lat + pad_lat) / self.CELL) + 1,
        ):
            for cx in range(
                math.floor((lon - pad_lon) / self.CELL),
                math.floor((lon + pad_lon) / self.CELL) + 1,
            ):
                result.update(self.grid.get((cy, cx), ()))
        return sorted(result)


def _project_segment(a: RoutePoint, b: RoutePoint, lat: float, lon: float):
    """Return (along_fraction, perp_miles) of (lat,lon) projected on a->b."""
    # local equirectangular metres
    mid_lat = math.radians((a.lat + b.lat) / 2)
    mx_per_deg_lat = 69.05
    mx_per_deg_lon = 69.047 * math.cos(mid_lat)
    bx = (b.lon - a.lon) * mx_per_deg_lon
    by = (b.lat - a.lat) * mx_per_deg_lat
    px = (lon - a.lon) * mx_per_deg_lon
    py = (lat - a.lat) * mx_per_deg_lat
    seg2 = bx * bx + by * by
    if seg2 == 0:
        return 0.0, math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg2))
    cx, cy = t * bx, t * by
    return t, math.hypot(px - cx, py - cy)


def stations_on_route(
    stations: list[Station],
    points: list[RoutePoint],
    corridor_miles: float = CORRIDOR_MILES_DEFAULT,
) -> list[dict]:
    """Project stations onto the route; return candidates sorted by mile marker."""
    if len(points) < 2:
        raise ValueError("Route requires at least two points")
    index = _RouteIndex(points)
    out: list[dict] = []

    # Bounding box of the route, padded by the corridor stations outside
    # cannot possibly project onto the route, and this prunes ~90% of the
    # dataset before any distance math runs.
    pad = (corridor_miles + 5.0) / 69.0  # degrees of latitude
    min_lat = min(p.lat for p in points) - pad
    max_lat = max(p.lat for p in points) + pad
    lon_pad = pad / max(0.1, math.cos(math.radians(max(abs(min_lat), abs(max_lat)))))
    min_lon = min(p.lon for p in points) - lon_pad
    max_lon = max(p.lon for p in points) + lon_pad

    for st in stations:
        if not (min_lat <= st.lat <= max_lat and min_lon <= st.lon <= max_lon):
            continue
        nearby = index.segments(st.lat, st.lon, corridor_miles)
        if not nearby:
            continue
        best_t, best_perp, best_i = 0.0, math.inf, nearby[0]
        for j in nearby:
            t, perp = _project_segment(points[j], points[j + 1], st.lat, st.lon)
            if perp < best_perp:
                best_t, best_perp, best_i = t, perp, j
        if best_perp > corridor_miles:
            continue
        a, b = points[best_i], points[best_i + 1]
        mile = a.mile + best_t * (b.mile - a.mile)
        out.append(
            {
                "mile": mile,
                "perp_miles": best_perp,
                "station": st,
            }
        )
    out.sort(key=lambda c: c["mile"])
    return out


def origin_fill_station(
    origin: dict, candidates: list[dict], max_miles: float = 100.0
) -> dict | None:
    """Cheapest station within max_miles of the trip origin (for the initial fill).

    Preference: cheapest within 25 mi; falls back to cheapest within 100 mi.
    """
    near25 = []
    near100 = []
    for st in load_stations()[0]:
        d = haversine_miles(origin["lat"], origin["lon"], st.lat, st.lon)
        if d <= 25:
            near25.append((st.price, d, st))
        elif d <= max_miles:
            near100.append((st.price, d, st))
    pool = near25 or near100
    if not pool:
        return None
    price, dist, st = min(pool, key=lambda x: x[0])
    return {"mile": 0.0, "perp_miles": dist, "station": st}
