"""Offline US location lookup and bounded, cached Nominatim geocoding."""

from __future__ import annotations

import math
import re

import requests

from . import boundaries, cache, places, upstream

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_COORD_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*$")


class GeocodeError(Exception):
    pass


class GeocodeServiceError(GeocodeError):
    pass


def _valid(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("label"), str)
        and all(
            isinstance(value.get(k), (int, float))
            and not isinstance(value[k], bool)
            and math.isfinite(value[k])
            for k in ("lat", "lon")
        )
        and boundaries.within_usa(value["lat"], value["lon"])
    )


def locate(query: str) -> dict:
    query = query.strip()
    if not query or len(query) > 300:
        raise GeocodeError("Location must contain between 1 and 300 characters")
    match = _COORD_RE.fullmatch(query)
    if match:
        lat, lon = map(float, match.groups())
        if not boundaries.within_usa(lat, lon):
            raise GeocodeError("Coordinates must be within the USA")
        return {
            "lat": lat,
            "lon": lon,
            "label": f"{lat:.5f}, {lon:.5f}",
            "source": "coords",
        }

    # The bundled gazetteer already contains cities; avoid unnecessary calls.
    city, sep, state = query.rpartition(",")
    if sep and re.fullmatch(r"[A-Za-z]{2}", state.strip()):
        coords = places.lookup_with_fallback(city.strip(), state.strip())
        if coords and boundaries.within_usa(*coords):
            return {
                "lat": coords[0],
                "lon": coords[1],
                "label": query,
                "source": "offline",
            }

    key = cache.key("geo:v2", re.sub(r"\s+", " ", query.lower()))
    cached = cache.get(key)
    if _valid(cached):
        return {**cached, "source": "cache"}
    with cache.lease("nominatim", wait=2):
        cached = cache.get(key)
        if _valid(cached):
            return {**cached, "source": "cache"}
        cache.pace("nominatim")
        try:
            payload = upstream.get_json(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 1,
                    "countrycodes": "us",
                },
                headers={},
                timeout=(5, 15),
            )
            rows = payload
        except requests.RequestException as exc:
            raise GeocodeServiceError(
                "Geocoding service unavailable; retry later"
            ) from exc
        if not isinstance(rows, list):
            raise GeocodeServiceError("Geocoding service returned an invalid response")
        if not rows:
            raise GeocodeError("Could not find this location within the USA")
        try:
            row = rows[0]
            value = {
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "label": row.get("display_name", query),
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise GeocodeServiceError(
                "Geocoding service returned an invalid response"
            ) from exc
        if not _valid(value):
            raise GeocodeServiceError(
                "Geocoding service returned an invalid US location"
            )
        cache.set(key, value)
        return {**value, "source": "live"}
