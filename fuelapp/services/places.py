"""Load US place coordinates from the Census gazetteer (public domain).

The fuel-price CSV has no coordinates — only City/State — so we resolve
city names offline against the gazetteer. No API calls at runtime.
"""

from __future__ import annotations

import csv
import functools
import math
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parents[2] / "data"

_SUFFIX_RE = re.compile(
    r"\s+(CITY|TOWN|VILLAGE|CDP|BURough|BOROUGH|MUNICIPALITY|PLANTATION|UT|CCD)+$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")


def canonical(name: str) -> str:
    s = _NON_ALNUM.sub(" ", name.upper())
    s = re.sub(r"\s+", " ", s).strip()
    while True:
        m = _SUFFIX_RE.search(s)
        if not m:
            break
        s = s[: m.start()].strip()
    return s


def _aliases(key: str) -> list[str]:
    out = [key]
    if "SAINT " in key:
        out.append(key.replace("SAINT ", "ST ", 1))
    if "ST " in key:
        out.append(key.replace("ST ", "SAINT ", 1))
    if "FORT " in key:
        out.append(key.replace("FORT ", "FT ", 1))
    if "FT " in key:
        out.append(key.replace("FT ", "FORT ", 1))
    if "BEACH" in key:
        out.append(key.replace("BEACH", "BCH", 1))
    return list(dict.fromkeys(out))


@functools.lru_cache(maxsize=1)
def load_places() -> dict[tuple[str, str], tuple[float, float]]:
    """(canonical city, state) -> (lat, lon)."""
    table: dict[tuple[str, str], tuple[float, float]] = {}
    path = _DATA / "places.txt"
    if not path.exists():
        return table
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            state = (row["USPS"] or "").strip()
            lat = (row["INTPTLAT"] or "").strip()
            lon = (row["INTPTLONG"] or "").strip()
            if not state or not lat or not lon:
                continue
            try:
                coords = (float(lat), float(lon))
            except ValueError:
                continue
            if not all(math.isfinite(v) for v in coords) or not (
                -90 <= coords[0] <= 90 and -180 <= coords[1] <= 180
            ):
                continue
            key = canonical(row["NAME"])
            for alias in _aliases(key):
                table.setdefault((alias, state), coords)
    return table


def lookup(city: str, state: str) -> tuple[float, float] | None:
    table = load_places()
    state = state.strip().upper()
    for alias in _aliases(canonical(city)):
        hit = table.get((alias, state))
        if hit:
            return hit
    return None


# ---------------------------------------------------------------------------
# A small hand-curated alias layer for cities whose CSV spelling differs
# from the Census gazetteer (populated at build time from unmatched rows).
# ---------------------------------------------------------------------------
_CUSTOM: dict[tuple[str, str], tuple[float, float]] | None = None


def _custom() -> dict[tuple[str, str], tuple[float, float]]:
    global _CUSTOM
    if _CUSTOM is None:
        custom = {}
        path = _DATA / "custom_places.json"
        if path.exists():
            import json

            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Custom places must be a JSON object")
            for key, coords in raw.items():  # key == "City, ST"
                if not isinstance(coords, list) or len(coords) != 2:
                    raise ValueError(f"Invalid coordinates for {key}")
                if not all(
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                    for v in coords
                ):
                    raise ValueError(f"Invalid coordinates for {key}")
                if not (-90 <= coords[0] <= 90 and -180 <= coords[1] <= 180):
                    raise ValueError(f"Invalid coordinates for {key}")
                city, _, state = key.partition(",")
                custom[(canonical(city), state.strip().upper())] = tuple(coords)
        _CUSTOM = custom
    return _CUSTOM


def lookup_with_fallback(city: str, state: str) -> tuple[float, float] | None:
    state = state.strip().upper()
    for alias in _aliases(canonical(city)):
        hit = _custom().get((alias, state))
        if hit:
            return hit
    return lookup(city, state)
