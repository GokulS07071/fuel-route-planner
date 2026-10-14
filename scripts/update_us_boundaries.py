"""Refresh the bundled public-domain Census state polygons (setup only)."""

import gzip
import io
import json
from pathlib import Path
from urllib.request import urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile

URL = "https://www2.census.gov/geo/tiger/GENZ2025/kml/cb_2025_us_state_500k.zip"
NS = {"k": "http://www.opengis.net/kml/2.2"}


def main():
    with urlopen(URL, timeout=30) as response:
        archive = ZipFile(io.BytesIO(response.read()))
    filename = next(n for n in archive.namelist() if n.endswith(".kml"))
    root = ET.fromstring(archive.read(filename))
    polygons = []
    for polygon in root.findall(".//k:Polygon", NS):
        rings = []
        for coords in polygon.findall(".//k:LinearRing/k:coordinates", NS):
            rings.append(
                [
                    [float(v) for v in point.split(",")[:2]]
                    for point in coords.text.split()
                ]
            )
        polygons.append(rings)
    if len(polygons) < 50:
        raise ValueError("Unexpected Census boundary dataset")
    target = Path(__file__).resolve().parents[1] / "data" / "us-boundaries.json.gz"
    payload = json.dumps(
        {"source": URL, "polygons": polygons}, separators=(",", ":")
    ).encode()
    target.write_bytes(gzip.compress(payload, mtime=0))
    print(f"Saved {len(polygons)} polygons to {target}")


if __name__ == "__main__":
    main()
