"""Tests: planner optimality, route projection, API contract (external services mocked)."""

import json
from unittest import mock

from django.test import SimpleTestCase

from fuelapp.services import planner, routing, stations
from fuelapp.services.planner import RouteInfeasible


class StationStub:
    def __init__(self, id, name, price, city="Testville", state="TX"):
        self.id = id
        self.name = name
        self.address = "I-40"
        self.city = city
        self.state = state
        self.price = price
        self.lat = 35.0
        self.lon = -97.0


class PlannerTests(SimpleTestCase):
    def test_known_optimal_plan(self):
        """Hand-computed optimum: origin(4.00) -> 300(3.00) -> 600(2.50) -> dest."""
        cands = [
            {"mile": 300.0, "perp_miles": 1.0, "station": StationStub("a", "A", 3.00)},
            {"mile": 600.0, "perp_miles": 1.0, "station": StationStub("b", "B", 2.50)},
            {"mile": 700.0, "perp_miles": 1.0, "station": StationStub("c", "C", 3.50)},
        ]
        plan = planner.plan_fuel(1000.0, cands, origin_price=4.00)
        self.assertAlmostEqual(plan["total_cost"], 310.0, places=2)
        self.assertAlmostEqual(plan["total_gallons"], 100.0, places=3)
        self.assertEqual(len(plan["stops"]), 3)
        self.assertEqual(plan["stops"][0]["kind"], "origin")
        self.assertEqual([s["mile_marker"] for s in plan["stops"]], [0.0, 300.0, 600.0])

    def test_total_gallons_always_distance_over_mpg(self):
        cands = [
            {"mile": 100.0, "perp_miles": 0.5, "station": StationStub("a", "A", 3.10)},
            {"mile": 200.0, "perp_miles": 0.5, "station": StationStub("b", "B", 2.90)},
            {"mile": 460.0, "perp_miles": 0.5, "station": StationStub("c", "C", 3.30)},
            {"mile": 700.0, "perp_miles": 0.5, "station": StationStub("d", "D", 3.05)},
        ]
        plan = planner.plan_fuel(
            850.0, cands, origin_price=3.44, range_miles=500, mpg=10
        )
        self.assertAlmostEqual(plan["total_gallons"], 85.0, places=3)
        # every leg respects the vehicle range
        for leg in plan["legs"]:
            self.assertLessEqual(leg["miles"], 500.0 + 1e-6)
        # money in = gallons out
        bought = sum(s["gallons"] for s in plan["stops"])
        self.assertAlmostEqual(bought, plan["total_gallons"], places=2)

    def test_fill_within_range_uses_cheaper_station(self):
        """Buying 500 mi of cheap fuel beats filling twice at a pricey one."""
        cands = [
            {
                "mile": 480.0,
                "perp_miles": 0.5,
                "station": StationStub("cheap", "Cheap", 2.50),
            },
        ]
        plan = planner.plan_fuel(900.0, cands, origin_price=4.00)
        # origin -> 480 @4.00 (48 gal), 480 -> 900 @2.50 (42 gal)
        self.assertAlmostEqual(plan["total_cost"], 48 * 4.00 + 42 * 2.50, places=2)

    def test_infeasible_gap_raises(self):
        cands = [
            {
                "mile": 700.0,
                "perp_miles": 0.5,
                "station": StationStub("far", "Far", 3.00),
            },
        ]
        with self.assertRaises(RouteInfeasible):
            planner.plan_fuel(1200.0, cands, origin_price=4.00, range_miles=500)

    def test_short_trip_single_fill(self):
        plan = planner.plan_fuel(120.0, [], origin_price=3.75)
        self.assertEqual(len(plan["stops"]), 1)
        self.assertAlmostEqual(plan["total_cost"], 12.0 * 3.75, places=2)


class ProjectionTests(SimpleTestCase):
    def test_mile_markers_scale_to_route_distance(self):
        # straight north-south line, 10 segments
        coords = [[-97.0, 35.0 + i * 0.1] for i in range(11)]
        raw = sum(
            stations.haversine_miles(
                coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]
            )
            for i in range(10)
        )
        points = stations.build_route_points(coords, total_miles=raw * 2)
        self.assertAlmostEqual(points[0].mile, 0.0, places=6)
        self.assertAlmostEqual(points[-1].mile, raw * 2, places=4)
        self.assertAlmostEqual(points[5].mile, raw, places=3)  # midpoint

    def test_station_projects_to_its_mile_marker(self):
        coords = [[-97.0, 35.0 + i * 0.1] for i in range(11)]
        raw = sum(
            stations.haversine_miles(
                coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]
            )
            for i in range(10)
        )
        points = stations.build_route_points(coords, total_miles=raw)
        st = StationStub("x", "X", 3.0)
        st.lat, st.lon = 35.26, -97.005  # ~mid-route, a few miles east
        cands = stations.stations_on_route([st], points, corridor_miles=10)
        self.assertEqual(len(cands), 1)
        self.assertAlmostEqual(cands[0]["mile"], raw * 0.26, delta=2.0)

    def test_station_far_from_route_filtered(self):
        coords = [[-97.0, 35.0 + i * 0.1] for i in range(11)]
        points = stations.build_route_points(coords, total_miles=100.0)
        st = StationStub("far", "Far", 3.0)
        st.lat, st.lon = 36.5, -95.0  # ~150 mi away
        self.assertEqual(
            stations.stations_on_route([st], points, corridor_miles=15), []
        )


class GeoTests(SimpleTestCase):
    def test_coordinate_input_needs_no_network(self):
        out = __import__("fuelapp.services.geo", fromlist=["locate"]).locate(
            "39.7392,-104.9903"
        )
        self.assertEqual(out["source"], "coords")
        self.assertAlmostEqual(out["lat"], 39.7392)

    def test_out_of_usa_rejected(self):
        from fuelapp.services.geo import GeocodeError, locate

        with self.assertRaises(GeocodeError):
            locate("51.5074,-0.1278")  # London -> outside bbox


def _synthetic_route():
    """A straight ~248 mi line Kansas City -> St. Louis with plausible distance."""
    start = (39.0997, -94.5786)
    end = (38.6270, -90.1994)
    coords = [
        [
            start[1] + (end[1] - start[1]) * i / 40,
            start[0] + (end[0] - start[0]) * i / 40,
        ]
        for i in range(41)
    ]
    miles = sum(
        stations.haversine_miles(
            coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]
        )
        for i in range(40)
    )
    return {
        "distance_m": miles * 1609.344,
        "duration_s": miles * 90,
        "coordinates": coords,
        "source": "cache",
    }


class ApiTests(SimpleTestCase):
    def _patched(self):
        return [
            mock.patch(
                "fuelapp.services.geo.locate",
                side_effect=lambda q: (
                    {
                        "lat": 39.0997,
                        "lon": -94.5786,
                        "label": q,
                        "source": "cache",
                    }
                    if q.lower().startswith("k")
                    else {
                        "lat": 38.6270,
                        "lon": -90.1994,
                        "label": q,
                        "source": "cache",
                    }
                ),
            ),
            mock.patch(
                "fuelapp.services.routing.get_route", return_value=_synthetic_route()
            ),
        ]

    def test_plan_endpoint(self):
        p = self._patched()
        with p[0], p[1]:
            resp = self.client.get(
                "/api/v1/plan", {"start": "Kansas City, MO", "end": "St Louis, MO"}
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = json.loads(resp.content)
        self.assertIn("route", data)
        self.assertIn("fuel_plan", data)
        self.assertAlmostEqual(
            data["fuel_plan"]["total_gallons"],
            data["route"]["distance_miles"] / 10.0,
            places=2,
        )
        self.assertEqual(data["meta"]["external_calls_this_request"], 0)
        self.assertGreater(data["route"]["distance_miles"], 200)
        self.assertTrue(data["map_url"].startswith("/map?"))
        for stop in data["fuel_plan"]["stops"]:
            self.assertLessEqual(stop["gallons"] * 10, 500.0 + 1e-6)

    def test_missing_params_400(self):
        resp = self.client.get("/api/v1/plan")
        self.assertEqual(resp.status_code, 400)

    def test_health(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)

    def test_bad_location_400(self):
        with mock.patch(
            "fuelapp.services.geo.locate",
            side_effect=__import__(
                "fuelapp.services.geo", fromlist=["GeocodeError"]
            ).GeocodeError("nope"),
        ):
            resp = self.client.get("/api/v1/plan", {"start": "x", "end": "y"})
        self.assertEqual(resp.status_code, 400)


class DataTests(SimpleTestCase):
    def test_csv_loads_with_good_coverage(self):
        st, stats = stations.load_stations()
        self.assertGreater(stats["csv_rows"], 8000)
        self.assertGreater(stats["located"], 3500)
        self.assertLess(len(stats["unmatched_cities"]), 500)

    def test_route_is_one_osrm_call_shape(self):
        # get_route caches: second call must hit the cache (source == "cache")
        with mock.patch("fuelapp.services.routing.requests.get") as get:
            get.return_value.iter_content.return_value = [
                json.dumps(
                    {
                        "code": "Ok",
                        "routes": [
                            {
                                "distance": 1000,
                                "duration": 100,
                                "geometry": {
                                    "coordinates": [[-97.0, 35.0], [-96.0, 36.0]]
                                },
                            }
                        ],
                    }
                ).encode()
            ]
            from fuelapp.services import cache

            with (
                mock.patch.object(cache, "get", return_value=None),
                mock.patch.object(cache, "set") as cset,
            ):
                r1 = routing.get_route(35.0, -97.0, 36.0, -96.0)
                self.assertEqual(r1["source"], "live")
                self.assertEqual(get.call_count, 1)
                cset.assert_called_once()


class RegressionTests(SimpleTestCase):
    def test_carrying_cheap_fuel_through_expensive_stop(self):
        plan = planner.plan_fuel(
            600, [{"mile": 400, "station": StationStub("x", "X", 10)}], 1
        )
        self.assertEqual(plan["total_cost"], 150)
        self.assertEqual([s["gallons"] for s in plan["stops"]], [50, 10])
        self.assertEqual(plan["stops"][1]["fuel_on_arrival_gallons"], 10)

    def test_optimum_against_independent_exhaustive_fuel_states(self):
        import random

        rng = random.Random(19)
        for case in range(150):
            capacity = rng.randint(2, 6)
            distance = 12
            prices = [rng.randint(1, 9) for _ in range(distance)]
            # Enumerate all feasible integer purchases and remaining-fuel states.
            costs = {0: 0}
            for price in prices:
                next_costs = {}
                for fuel, cost in costs.items():
                    for purchase in range(capacity - fuel + 1):
                        left = fuel + purchase - 1
                        if left >= 0:
                            value = cost + purchase * price
                            next_costs[left] = min(
                                next_costs.get(left, float("inf")), value
                            )
                costs = next_costs
            candidates = [
                {"mile": i, "station": StationStub(str(i), str(i), prices[i])}
                for i in range(1, distance)
            ]
            plan = planner.plan_fuel(distance, candidates, prices[0], capacity, 1)
            with self.subTest(case=case):
                self.assertEqual(plan["total_cost"], costs[0])
                self.assertEqual(sum(s["gallons"] for s in plan["stops"]), distance)
                for stop in plan["stops"]:
                    self.assertLessEqual(
                        stop["fuel_on_arrival_gallons"] + stop["gallons"], capacity
                    )
                for leg in plan["legs"]:
                    self.assertLessEqual(leg["miles"], capacity)

    def test_station_close_to_destination_is_not_discarded(self):
        plan = planner.plan_fuel(
            500.2, [{"mile": 499.9, "station": StationStub("x", "X", 4)}], 3
        )
        self.assertEqual(len(plan["stops"]), 2)

    def test_wide_corridor_and_long_segment(self):
        points = stations.build_route_points([[-97, 35], [-97, 37]], 138)
        station = StationStub("x", "X", 3)
        station.lat, station.lon = 36, -96
        candidates = stations.stations_on_route([station], points, 100)
        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0]["mile"], 69, delta=1)
        self.assertEqual(stations.stations_on_route([station], points, 15), [])

    def test_no_arbitrary_distant_origin_fill(self):
        station = StationStub("x", "X", 3)
        with mock.patch.object(stations, "load_stations", return_value=([station], {})):
            self.assertIsNone(
                stations.origin_fill_station(
                    {"lat": 40, "lon": -120}, [{"mile": 800, "station": station}]
                )
            )

    def test_post_schema_and_encoding_errors_are_client_errors(self):
        bodies = [
            b"[]",
            b"null",
            b"123",
            b"\xff",
            b'{"start":123,"end":"Dallas"}',
            b'{"start":["Dallas"],"end":"Austin"}',
        ]
        with mock.patch("fuelapp.views.build_plan") as build:
            for body in bodies:
                with self.subTest(body=body):
                    self.assertEqual(
                        self.client.post(
                            "/api/v1/plan", body, content_type="application/json"
                        ).status_code,
                        400,
                    )
            build.assert_not_called()

    def test_request_limits_numeric_validation_and_methods(self):
        with mock.patch("fuelapp.views.build_plan") as build:
            for number in [True, [], {}, "nan", "inf", -1, 0]:
                response = self.client.post(
                    "/api/v1/plan",
                    json.dumps({"start": "x", "end": "y", "mpg": number}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
            self.assertEqual(
                self.client.get(
                    "/api/v1/plan", {"start": "x" * 301, "end": "y"}
                ).status_code,
                400,
            )
            self.assertEqual(
                self.client.post(
                    "/api/v1/plan", "{}", content_type="text/plain"
                ).status_code,
                415,
            )
            self.assertEqual(
                self.client.post(
                    "/api/v1/plan", " " * 17000, content_type="application/json"
                ).status_code,
                413,
            )
            for path in ("/map", "/api/v1/plan"):
                response = self.client.delete(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET, POST")
            build.assert_not_called()

    def test_map_error_escapes_html(self):
        from fuelapp.services.geo import GeocodeError

        markup = "<img src=x onerror=alert(1)>"
        with mock.patch("fuelapp.views.build_plan", side_effect=GeocodeError(markup)):
            response = self.client.get("/map", {"start": "x", "end": "y"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(markup.encode(), response.content)
        self.assertIn(b"&lt;img", response.content)

    def test_map_preserves_parameters_and_initial_station(self):
        from urllib.parse import parse_qs, urlsplit

        with (
            mock.patch(
                "fuelapp.services.geo.locate",
                return_value={
                    "lat": 39.0997,
                    "lon": -94.5786,
                    "label": "<script>test</script>",
                    "source": "cache",
                },
            ),
            mock.patch(
                "fuelapp.services.routing.get_route", return_value=_synthetic_route()
            ),
        ):
            response = self.client.get(
                "/api/v1/plan",
                {"start": "x", "end": "y", "mpg": 20, "corridor_miles": 50},
            )
            data = response.json()
            query = parse_qs(urlsplit(data["map_url"]).query)
            self.assertEqual(query["mpg"], ["20.0"])
            self.assertEqual(query["corridor_miles"], ["50.0"])
            self.assertIsNotNone(data["fuel_plan"]["stops"][0]["station"])
            page = self.client.get(data["map_url"])
            self.assertEqual(page.status_code, 200)
            # SecurityMiddleware must allow an origin-only cross-site Referer:
            # OSM blocks unidentified tiles, while route query strings stay private.
            self.assertEqual(page["Referrer-Policy"], "strict-origin-when-cross-origin")
            self.assertIn(
                b"https://tile.openstreetmap.org/{z}/{x}/{y}.png", page.content
            )
            self.assertIn(
                b'referrerPolicy: "strict-origin-when-cross-origin"', page.content
            )
            self.assertNotIn(b"<script>test</script>", page.content)
            self.assertIn(b"20.0 mpg", page.content)

    def test_busy_returns_retryable_response(self):
        from fuelapp.services import cache

        with mock.patch("fuelapp.views.build_plan", side_effect=cache.Busy()):
            response = self.client.get("/api/v1/plan", {"start": "x", "end": "y"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "2")


class CacheAndUpstreamTests(SimpleTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from fuelapp.services import cache

        self.directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1] / "data", prefix=".test-"
        )
        self.addCleanup(self.directory.cleanup)
        patcher = mock.patch.object(
            cache, "_DB", Path(self.directory.name) / "cache.db"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cache_parameterized_keys_expiry_corruption_and_bounds(self):
        from contextlib import closing

        from fuelapp.services import cache

        key = "'; DROP TABLE kv; --"
        cache.set(key, {"safe": True})
        self.assertEqual(cache.get(key), {"safe": True})
        cache.set("expired", 1, ttl=-1)
        self.assertIsNone(cache.get("expired"))
        with closing(cache._conn()) as connection, connection:
            connection.execute("UPDATE kv SET v='bad json' WHERE k=?", (key,))
        self.assertIsNone(cache.get(key))
        with mock.patch.object(cache, "MAX_ENTRIES", 3):
            for i in range(10):
                cache.set(str(i), i)
            with closing(cache._conn()) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM kv").fetchone()[0], 3
                )
        with mock.patch.object(cache, "MAX_CACHE_BYTES", 10):
            cache.set("big", "x" * 50)
            self.assertIsNone(cache.get("big"))

    def test_lease_capacity_release_and_pacing(self):
        from fuelapp.services import cache

        with cache.lease("test"):
            with self.assertRaises(cache.Busy):
                with cache.lease("test"):
                    pass
        with cache.lease("test"):
            pass
        with (
            mock.patch.object(cache.time, "time", return_value=100),
            mock.patch.object(cache.time, "sleep") as sleep,
        ):
            cache.pace("test")
            cache.pace("test")
            self.assertAlmostEqual(sleep.call_args.args[0], 1.1)

    def test_three_cold_calls_and_zero_warm_calls(self):
        from fuelapp.services import cache, geo

        starts = ["123 Example Road, Kansas City, MO", "456 Example Road, St Louis, MO"]
        responses = [
            [{"lat": "39.0997", "lon": "-94.5786", "display_name": starts[0]}],
            [{"lat": "38.6270", "lon": "-90.1994", "display_name": starts[1]}],
            {
                "code": "Ok",
                "routes": [
                    {
                        "distance": _synthetic_route()["distance_m"],
                        "duration": 100,
                        "geometry": {"coordinates": _synthetic_route()["coordinates"]},
                    }
                ],
            },
        ]
        with (
            mock.patch.object(geo.requests, "get") as get,
            mock.patch.object(cache, "pace"),
        ):
            get.return_value.iter_content.side_effect = [
                [json.dumps(p).encode()] for p in responses
            ]
            first = self.client.get(
                "/api/v1/plan", {"start": starts[0], "end": starts[1]}
            )
            self.assertEqual(first.status_code, 200, first.content)
            self.assertEqual(first.json()["meta"]["external_calls_this_request"], 3)
            second = self.client.get(
                "/api/v1/plan", {"start": starts[0], "end": starts[1]}
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["meta"]["external_calls_this_request"], 0)
            self.assertEqual(get.call_count, 3)

    def test_us_boundaries_and_offline_city_lookup(self):
        from fuelapp.services import boundaries, geo

        for coords in [
            (39.7392, -104.9903),
            (61.2181, -149.9003),
            (21.3069, -157.8583),
        ]:
            self.assertTrue(boundaries.within_usa(*coords))
        for coords in [(43.6532, -79.3832), (19.4326, -99.1332), (35, -130), (0, 0)]:
            self.assertFalse(boundaries.within_usa(*coords))
        with mock.patch.object(geo.requests, "get") as get:
            self.assertEqual(geo.locate("Dallas, TX")["source"], "offline")
            self.assertEqual(geo.locate("39,-104")["source"], "coords")
            with self.assertRaises(geo.GeocodeError):
                geo.locate("43.6532,-79.3832")
            get.assert_not_called()

    def test_upstream_failures_are_sanitized(self):
        import requests

        from fuelapp.services import cache, geo

        with (
            mock.patch.object(cache, "pace"),
            mock.patch.object(
                geo.requests,
                "get",
                side_effect=requests.Timeout("private network detail"),
            ),
        ):
            response = self.client.get(
                "/api/v1/plan", {"start": "123 Unknown Road", "end": "Dallas, TX"}
            )
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(b"private network detail", response.content)
        with mock.patch.object(routing.requests, "get") as get:
            for payload in [
                [],
                {"code": "Ok", "routes": []},
                {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": "nan",
                            "duration": 1,
                            "geometry": {"coordinates": []},
                        }
                    ],
                },
            ]:
                get.return_value.iter_content.return_value = [
                    json.dumps(payload).encode()
                ]
                with self.assertRaises(routing.RoutingError):
                    routing.get_route(35, -97, 36, -96)

    def test_second_route_call_uses_cache(self):
        with mock.patch.object(routing.requests, "get") as get:
            get.return_value.iter_content.return_value = [
                json.dumps(
                    {
                        "code": "Ok",
                        "routes": [
                            {
                                "distance": 1000,
                                "duration": 100,
                                "geometry": {"coordinates": [[-97, 35], [-96, 36]]},
                            }
                        ],
                    }
                ).encode()
            ]
            self.assertEqual(routing.get_route(35, -97, 36, -96)["source"], "live")
            self.assertEqual(routing.get_route(35, -97, 36, -96)["source"], "cache")
            self.assertEqual(get.call_count, 1)

    def test_concurrent_route_misses_share_one_request(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        started = threading.Event()
        release = threading.Event()
        response = mock.Mock()
        response.iter_content.return_value = [
            json.dumps(
                {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 1000,
                            "duration": 100,
                            "geometry": {"coordinates": [[-97, 35], [-96, 36]]},
                        }
                    ],
                }
            ).encode()
        ]

        def fetch(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(2))
            return response

        with (
            mock.patch.object(routing.requests, "get", side_effect=fetch) as get,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(routing.get_route, 35, -97, 36, -96)
            self.assertTrue(started.wait(2))
            second = pool.submit(routing.get_route, 35, -97, 36, -96)
            release.set()
            sources = {first.result()["source"], second.result()["source"]}
            self.assertEqual(sources, {"live", "cache"})
            self.assertEqual(get.call_count, 1)

    def test_upstream_limits_invalid_json_and_response_closure(self):
        import requests

        from fuelapp.services import upstream

        with mock.patch.object(upstream.requests, "get") as get:
            get.return_value.iter_content.return_value = [b"not json"]
            with self.assertRaises(requests.RequestException):
                upstream.get_json(
                    "https://example.com", params={}, headers={}, timeout=1
                )
            get.return_value.close.assert_called_once()
            get.return_value.iter_content.return_value = [b"01234567890"]
            with (
                mock.patch.object(upstream, "MAX_BYTES", 10),
                self.assertRaises(requests.RequestException),
            ):
                upstream.get_json(
                    "https://example.com", params={}, headers={}, timeout=1
                )
            self.assertEqual(get.return_value.close.call_count, 2)
            self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_nearby_distinct_endpoints_do_not_collide(self):
        with mock.patch.object(routing.requests, "get") as get:
            get.return_value.iter_content.return_value = [
                json.dumps(
                    {
                        "code": "Ok",
                        "routes": [
                            {
                                "distance": 1000,
                                "duration": 100,
                                "geometry": {"coordinates": [[-97, 35], [-96, 36]]},
                            }
                        ],
                    }
                ).encode()
            ]
            routing.get_route(35.000001, -97, 36, -96)
            routing.get_route(35.000002, -97, 36, -96)
            self.assertEqual(get.call_count, 2)
