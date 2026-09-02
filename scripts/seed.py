#!/usr/bin/env python3
"""Populate a local database with a season of CAIC and SNOTEL data.

Usage:
    python scripts/seed.py                     # current water year to date
    python scripts/seed.py --season 2026       # the 2025-26 water year
    python scripts/seed.py --start 2026-01-01 --end 2026-03-31
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from avalanche.caic import CaicClient  # noqa: E402
from avalanche.locations import LOCATIONS  # noqa: E402
from avalanche.store import Store  # noqa: E402
from avalanche.weather import SnotelClient, derive  # noqa: E402


def water_year(year: int) -> tuple[dt.date, dt.date]:
    """A CAIC water year runs 1 October of the prior year to 30 September."""
    return dt.date(year - 1, 10, 1), dt.date(year, 9, 30)


def main() -> int:
    today = dt.date.today()
    current = today.year + 1 if today.month >= 10 else today.year

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.environ.get("CAIC_DB", "caic.db"))
    ap.add_argument("--season", type=int, help="water year, e.g. 2026 for 2025-26")
    ap.add_argument("--start", type=dt.date.fromisoformat)
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--skip-weather", action="store_true")
    args = ap.parse_args()

    if args.start and args.end:
        start, end = args.start, args.end
    else:
        start, end = water_year(args.season or current)
        end = min(end, today)

    print(f"Seeding {args.db} from {start} to {end}\n")
    store = Store(args.db)
    caic = CaicClient()

    print("Avalanche observations…")
    n = store.add_avalanches(caic.avalanche_observations(start, end))
    print(f"  {n} records")

    print("Field reports…")
    n = store.add_reports(caic.observation_reports(start, end))
    print(f"  {n} records")

    print("Forecast snapshot (today's products; CAIC publishes no archive)…")
    print(f"  {store.add_forecast_snapshot(caic.current_forecasts())} day-rows")

    if not args.skip_weather:
        print("SNOTEL snowpack weather…")
        snotel = SnotelClient()
        seen: set[str] = set()
        total = 0
        for loc in LOCATIONS.values():
            for station in snotel.nearest(loc.latitude, loc.longitude, limit=2):
                if station.triplet in seen:
                    continue
                seen.add(station.triplet)
                total += store.add_snotel(station, derive(snotel.daily(station.triplet, start, end)))
                print(f"  {station.name:<26} {station.miles_away:>5} mi")
        print(f"  {total} station-days across {len(seen)} sites")

    store.close()
    print(f"\nDone. Point the MCP server at it with CAIC_DB={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
