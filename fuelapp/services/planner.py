"""Minimum fuel cost on a fixed route with a finite tank.

Buy just enough to reach the first cheaper or equally priced station within
range. If none is reachable, fill the tank (or buy enough to finish).
A monotonic stack finds those next stations in O(n); fuel is carried through
intermediate stations. Overall complexity is O(n log n), including sorting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MILES_PER_GALLON_DEFAULT = 10.0
RANGE_DEFAULT = 500.0


class RouteInfeasible(Exception):
    def __init__(
        self, gap_from: float, gap_to: float, gap_miles: float, range_miles: float
    ):
        super().__init__(
            f"No fuel station within range between mile {gap_from:.0f} and mile {gap_to:.0f} "
            f"({gap_miles:.0f} mi > {range_miles:.0f} mi vehicle range)"
        )
        self.detail = {
            "gap_from_mile": round(gap_from, 2),
            "gap_to_mile": round(gap_to, 2),
            "gap_miles": round(gap_miles, 2),
            "vehicle_range_miles": range_miles,
        }


@dataclass(slots=True)
class FillPoint:
    mile: float
    price: float | None  # None => destination (never purchases)
    station: object | None  # Station | None (None = origin fill)
    perp_miles: float = 0.0


def plan_fuel(
    total_miles: float,
    candidates: list[dict],
    origin_price: float,
    range_miles: float = RANGE_DEFAULT,
    mpg: float = MILES_PER_GALLON_DEFAULT,
    origin_station=None,
    corridor_miles: float = 15.0,
) -> dict:
    """Return the minimum-cost refuelling plan for a route."""
    if not all(
        math.isfinite(v) and v > 0
        for v in (total_miles, origin_price, range_miles, mpg)
    ):
        raise ValueError("Distance, price, range and mpg must be finite and positive")

    points: list[FillPoint] = [FillPoint(0.0, origin_price, origin_station)]
    for c in candidates:
        mile = min(max(c["mile"], 0.0), total_miles)
        if (
            not math.isfinite(c["mile"])
            or not math.isfinite(c["station"].price)
            or c["station"].price <= 0
        ):
            continue
        if mile >= total_miles:
            continue
        points.append(
            FillPoint(
                mile, float(c["station"].price), c["station"], c.get("perp_miles", 0.0)
            )
        )
    points.sort(key=lambda p: p.mile)
    # de-duplicate identical (mile, price) points
    deduped: list[FillPoint] = []
    seen: set[tuple[float, float]] = set()
    for p in points:
        k = (p.mile, p.price if p.price is not None else -1)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(p)
    points = deduped
    points.append(FillPoint(total_miles, None, None))  # destination

    for a, b in zip(points, points[1:]):
        if b.mile - a.mile > range_miles + 1e-6:
            raise RouteInfeasible(a.mile, b.mile, b.mile - a.mile, range_miles)

    # First cheaper-or-equal point to the right, computed in linear time.
    # Buy only enough to reach it if reachable; otherwise fill to capacity
    # (or to the destination). Fuel remaining on arrival is carried forward.
    next_cheaper = [None] * len(points)
    stack = [len(points) - 1]
    for i in range(len(points) - 2, -1, -1):
        while stack and (points[stack[-1]].price or 0) > points[i].price:
            stack.pop()
        next_cheaper[i] = stack[-1] if stack else None
        stack.append(i)

    stops = []
    total_cost = 0.0
    fuel_miles = 0.0
    for i in range(len(points) - 1):
        p_i = points[i]
        j = next_cheaper[i]
        target = min(range_miles, total_miles - p_i.mile)
        if j is not None and points[j].mile - p_i.mile <= range_miles:
            target = points[j].mile - p_i.mile
        purchase = max(0.0, target - fuel_miles)
        arrival_fuel = fuel_miles
        fuel_miles += purchase
        fuel_miles -= points[i + 1].mile - p_i.mile
        if purchase <= 1e-9:
            continue
        gallons = purchase / mpg
        cost = gallons * p_i.price
        total_cost += cost
        st = p_i.station
        stops.append(
            {
                "seq": len(stops) + 1,
                "kind": "origin" if p_i.mile == 0 else "en_route",
                "fuel_on_arrival_gallons": round(arrival_fuel / mpg, 3),
                "mile_marker": round(p_i.mile, 2),
                "gallons": round(gallons, 3),
                "price_per_gallon": round(p_i.price, 4),
                "cost": round(cost, 2),
                "station": None
                if st is None
                else {
                    "id": st.id,
                    "name": st.name,
                    "address": st.address,
                    "city": st.city,
                    "state": st.state,
                    "price_per_gallon": st.price,
                    "lat": st.lat,
                    "lon": st.lon,
                },
            }
        )

    total_gallons = total_miles / mpg
    return {
        "total_cost": round(total_cost, 2),
        "total_gallons": round(total_gallons, 3),
        "stops": stops,
        "legs": [
            {
                "from_mile": stops[k]["mile_marker"],
                "to_mile": stops[k + 1]["mile_marker"]
                if k + 1 < len(stops)
                else round(total_miles, 2),
                "miles": round(
                    (stops[k + 1]["mile_marker"] if k + 1 < len(stops) else total_miles)
                    - stops[k]["mile_marker"],
                    2,
                ),
            }
            for k in range(len(stops))
        ],
    }
