"""HTTP API: plan a route + cost-optimal fuel stops."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from urllib.parse import urlencode

from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.html import format_html
from django.views.decorators.csrf import csrf_exempt

from .services import cache, geo, planner, routing
from .services import stations as stations_mod

M_PER_MILE = 1609.344
logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


def _parse_params(request) -> dict:
    if request.method == "POST":
        if request.content_type != "application/json":
            raise ApiError(415, "Use Content-Type: application/json")
        try:
            body = request.body
        except RequestDataTooBig:
            raise ApiError(413, "JSON body exceeds 16 KiB")
        if len(body) > 16_384:
            raise ApiError(413, "JSON body exceeds 16 KiB")
        try:
            data = json.loads(body or b"{}")
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ApiError(400, "Invalid JSON body")
    else:
        data = request.GET.dict()

    if not isinstance(data, dict):
        raise ApiError(400, "JSON body must be an object")
    for name in ("start", "end"):
        value = data.get(name, "")
        if (
            not isinstance(value, str)
            or len(value) > 300
            or any(ord(c) < 32 for c in value)
        ):
            raise ApiError(
                400,
                f"Parameter {name!r} must be a string of at most 300 characters without control characters",
            )
    start = data.get("start", "").strip()
    end = data.get("end", "").strip()
    if not start or not end:
        raise ApiError(400, "Both 'start' and 'end' locations are required")

    def _num(name, default, lo, hi):
        raw = data.get(name)
        if raw in (None, ""):
            return default
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ApiError(400, f"Parameter {name!r} must be a number")
        try:
            val = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise ApiError(400, f"Parameter {name!r} must be a number")
        if not (lo <= val <= hi):
            raise ApiError(400, f"Parameter {name!r} must be between {lo} and {hi}")
        return val

    return {
        "start": start,
        "end": end,
        "range_miles": _num("range", 500.0, 50.0, 2000.0),
        "mpg": _num("mpg", 10.0, 1.0, 100.0),
        "corridor_miles": _num("corridor_miles", 15.0, 1.0, 100.0),
    }


def build_plan(params: dict) -> dict:
    t0 = time.perf_counter()

    # Nominatim calls are serialized; offline and cached lookups are cheap.
    origin = geo.locate(params["start"])
    dest = geo.locate(params["end"])
    t_geo = time.perf_counter()

    # 2) exactly one routing request per unique trip (cached afterwards)
    route = routing.get_route(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
    t_route = time.perf_counter()

    total_miles = route["distance_m"] / M_PER_MILE
    if total_miles <= 0:
        raise ApiError(400, "Route has zero distance")

    points = stations_mod.build_route_points(route["coordinates"], total_miles)
    all_stations, stats = stations_mod.load_stations()
    candidates = stations_mod.stations_on_route(
        all_stations, points, corridor_miles=params["corridor_miles"]
    )
    t_match = time.perf_counter()

    origin_pick = stations_mod.origin_fill_station(origin, candidates)
    if origin_pick is None:
        raise ApiError(
            422,
            "No fuel station found near the trip origin — cannot price the initial fill",
        )

    try:
        plan = planner.plan_fuel(
            total_miles=total_miles,
            candidates=candidates,
            origin_price=origin_pick["station"].price,
            range_miles=params["range_miles"],
            mpg=params["mpg"],
            origin_station=origin_pick["station"],
            corridor_miles=params["corridor_miles"],
        )
    except planner.RouteInfeasible as exc:
        raise ApiError(422, str(exc), exc.detail)
    t_plan = time.perf_counter()

    live = sum(
        1 for s in (origin["source"], dest["source"], route["source"]) if s == "live"
    )
    external_calls = live

    map_qs = urlencode(
        {
            "start": params["start"],
            "end": params["end"],
            "range": params["range_miles"],
            "mpg": params["mpg"],
            "corridor_miles": params["corridor_miles"],
        }
    )

    return {
        "request": {
            "start": {
                "query": params["start"],
                **{k: origin[k] for k in ("lat", "lon", "label", "source")},
            },
            "end": {
                "query": params["end"],
                **{k: dest[k] for k in ("lat", "lon", "label", "source")},
            },
            "vehicle": {
                "range_miles": params["range_miles"],
                "mpg": params["mpg"],
            },
            "corridor_miles": params["corridor_miles"],
        },
        "route": {
            "distance_miles": round(total_miles, 2),
            "duration_minutes": round(route["duration_s"] / 60, 1),
            "geometry": {"type": "LineString", "coordinates": route["coordinates"]},
        },
        "fuel_plan": plan,
        "map_url": "/map?" + map_qs,
        "meta": {
            "external_calls_this_request": external_calls,
            "geocode_sources": {"start": origin["source"], "end": dest["source"]},
            "routing_source": route["source"],
            "stations": {
                "located_in_dataset": stats["located"],
                "on_route": len(candidates),
                "unmatched_cities": len(stats["unmatched_cities"]),
                "skipped_non_us": stats["skipped_non_us"],
            },
            "timing_ms": {
                "geocode": round((t_geo - t0) * 1000),
                "routing": round((t_route - t_geo) * 1000),
                "station_matching": round((t_match - t_route) * 1000),
                "optimization": round((t_plan - t_match) * 1000),
                "total": round((t_plan - t0) * 1000),
            },
        },
    }


def _error(status: int, message: str, extra: dict | None = None) -> JsonResponse:
    response = JsonResponse({"error": message, **(extra or {})}, status=status)
    if status == 405:
        response["Allow"] = "GET, POST"
    if status == 503:
        response["Retry-After"] = "2"
    return response


def _payload(request):
    request.get_host()
    params = _parse_params(request)
    with cache.lease("planning", limit=4, ttl=300):
        return build_plan(params)


@csrf_exempt
def plan_api(request):
    if request.method not in ("GET", "POST"):
        return _error(405, "Use GET or POST")
    try:
        payload = _payload(request)
    except ApiError as exc:
        return _error(exc.status, exc.message, exc.extra)
    except geo.GeocodeServiceError as exc:
        return _error(502, str(exc))
    except geo.GeocodeError as exc:
        return _error(400, str(exc))
    except routing.RoutingError as exc:
        return _error(502, str(exc))
    except cache.Busy:
        return _error(503, "Service busy; retry shortly")
    except sqlite3.Error:
        logger.warning("Planner cache unavailable", exc_info=True)
        return _error(503, "Service busy; retry shortly")
    return JsonResponse(payload)


@csrf_exempt
def map_page(request):
    if request.method not in ("GET", "POST"):
        return _error(405, "Use GET or POST")
    try:
        payload = _payload(request)
    except ApiError as exc:
        return HttpResponse(
            format_html("<h3>Could not plan route</h3><p>{}</p>", exc.message),
            status=exc.status,
        )
    except (geo.GeocodeError, routing.RoutingError) as exc:
        status = (
            400
            if isinstance(exc, geo.GeocodeError)
            and not isinstance(exc, geo.GeocodeServiceError)
            else 502
        )
        return HttpResponse(
            format_html("<h3>Could not plan route</h3><p>{}</p>", str(exc)),
            status=status,
        )
    except cache.Busy:
        return _error(503, "Service busy; retry shortly")
    except sqlite3.Error:
        logger.warning("Planner cache unavailable", exc_info=True)
        return _error(503, "Service busy; retry shortly")
    return render(request, "fuelapp/map.html", {"plan": payload})


def health(request):
    return JsonResponse(
        {"status": "ok", "service": "fuel-route-planner", "version": "1.0.0"}
    )


def index(request):
    return JsonResponse(
        {
            "service": "fuel-route-planner",
            "endpoints": {
                "GET /api/v1/plan?start=...&end=...[&range=500][&mpg=10][&corridor_miles=15]": "JSON route + fuel plan",
                "POST /api/v1/plan": "same, JSON body {start, end, range, mpg}",
                "GET /map?start=...&end=...": "interactive Leaflet map of route + fuel stops",
                "GET /api/v1/health": "liveness probe",
            },
            "location_formats": [
                "City, ST address",
                "lat,lon (skips geocoding entirely)",
            ],
        }
    )
