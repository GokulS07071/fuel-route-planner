"""Setup-only enrichment using the same shared geocoder controls as the API."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from fuelapp.services import cache, geo, stations

OUT = Path(__file__).resolve().parents[3] / "data" / "custom_places.json"


class Command(BaseCommand):
    help = "Geocode unmatched fuel-stop towns into data/custom_places.json"

    def handle(self, *args, **opts):
        _, stats = stations.load_stations()
        existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
        todo = [city for city in stats["unmatched_cities"] if city not in existing]
        self.stdout.write(
            f"{len(stats['unmatched_cities'])} unmatched, {len(todo)} to fetch"
        )
        for i, city in enumerate(todo):
            try:
                location = geo.locate(city)
            except (geo.GeocodeError, cache.Busy) as exc:
                self.stderr.write(f"[{i + 1}/{len(todo)}] {city}: {exc}")
                continue
            existing[city] = [location["lat"], location["lon"]]
            temporary = OUT.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(existing, indent=1, sort_keys=True, allow_nan=False),
                encoding="utf-8",
            )
            temporary.replace(OUT)
            self.stdout.write(f"[{i + 1}/{len(todo)}] {city}: saved")
        self.stdout.write(self.style.SUCCESS(f"done, {len(existing)} custom places"))
