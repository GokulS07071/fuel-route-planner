"""Offline country checks using Census 2025 1:500,000 state polygons."""

import functools
import gzip
import json
import math
from pathlib import Path


@functools.lru_cache(maxsize=1)
def _polygons():
    path = Path(__file__).resolve().parents[2] / "data" / "us-boundaries.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        polygons = json.load(f)["polygons"]
    return [
        (
            (
                min(p[0] for p in rings[0]),
                min(p[1] for p in rings[0]),
                max(p[0] for p in rings[0]),
                max(p[1] for p in rings[0]),
            ),
            rings,
        )
        for rings in polygons
    ]


def _inside(x, y, ring):
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        # Include points exactly on the simplified boundary.
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if (
            abs(cross) < 1e-10
            and min(ax, bx) <= x <= max(ax, bx)
            and min(ay, by) <= y <= max(ay, by)
        ):
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def within_usa(lat, lon):
    if (
        not math.isfinite(lat)
        or not math.isfinite(lon)
        or not (-90 <= lat <= 90 and -180 <= lon <= 180)
    ):
        return False
    for (left, bottom, right, top), rings in _polygons():
        if left <= lon <= right and bottom <= lat <= top:
            if _inside(lon, lat, rings[0]) and not any(
                _inside(lon, lat, hole) for hole in rings[1:]
            ):
                return True
    return False
