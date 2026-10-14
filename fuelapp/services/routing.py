"""Single-call OSRM routing, cached for seven days and shared across workers."""

from __future__ import annotations

import math

import requests

from . import cache, upstream

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
ROUTE_TTL = 7 * 24 * 3600


class RoutingError(Exception):
    pass


def _valid(value):
    if not isinstance(value, dict):
        return False
    for name in ("distance_m", "duration_s"):
        number = value.get(name)
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or number < 0
        ):
            return False
    coords = value.get("coordinates")
    return (
        isinstance(coords, list)
        and 2 <= len(coords) <= 100_000
        and all(
            isinstance(p, list)
            and len(p) == 2
            and all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                for v in p
            )
            and -180 <= p[0] <= 180
            and -90 <= p[1] <= 90
            for p in coords
        )
    )


def get_route(lat1, lon1, lat2, lon2):
    key = cache.key("route:v2", f"{lat1},{lon1};{lat2},{lon2}")
    cached = cache.get(key)
    if _valid(cached):
        return {**cached, "source": "cache"}
    with cache.lease(key, wait=2):
        cached = cache.get(key)
        if _valid(cached):
            return {**cached, "source": "cache"}
        try:
            payload = upstream.get_json(
                f"{OSRM_URL}/{lon1},{lat1};{lon2},{lat2}",
                params={
                    "overview": "full",
                    "geometries": "geojson",
                    "alternatives": "false",
                },
                headers={},
                timeout=(5, 30),
            )
        except requests.RequestException as exc:
            raise RoutingError("Routing service unavailable; retry later") from exc
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            raise RoutingError("Routing service could not find a driving route")
        try:
            route = payload["routes"][0]
            value = {
                "distance_m": float(route["distance"]),
                "duration_s": float(route["duration"]),
                "coordinates": route["geometry"]["coordinates"],
            }
        except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
            raise RoutingError("Routing service returned an invalid response") from exc
        if not _valid(value):
            raise RoutingError("Routing service returned an invalid response")
        cache.set(key, value, ttl=ROUTE_TTL)
        return {**value, "source": "live"}
