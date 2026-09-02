"""Command line entry point.

    python -m avalanche ingest --start 2026-01-01 --end 2026-02-15
    python -m avalanche snapshot          # archive today's forecasts
    python -m avalanche digests --out digests/
    python -m avalanche ask "is berthoud pass safe today"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

from .caic import CaicClient
from .locations import LOCATIONS, resolve
from .profile import Profiler, render
from .store import Store
from .weather import SnotelClient, derive


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def cmd_ingest(args, store: Store) -> int:
    client = CaicClient()
    avs = list(client.avalanche_observations(args.start, args.end))
    n_av = store.add_avalanches(avs)
    reports = list(client.observation_reports(args.start, args.end))
    n_rp = store.add_reports(reports)
    print(f"ingested {n_av} avalanche observations, {n_rp} field reports")
    return 0


def cmd_weather(args, store: Store) -> int:
    """Ingest SNOTEL daily series for the sites nearest each known location."""
    client = SnotelClient()
    seen: set[str] = set()
    total = 0
    targets = [resolve(args.location)] if args.location else list(LOCATIONS.values())
    for loc in targets:
        if loc is None:
            print(f"unknown location: {args.location}", file=sys.stderr)
            return 1
        for station in client.nearest(loc.latitude, loc.longitude, limit=args.stations):
            if station.triplet in seen:
                continue
            seen.add(station.triplet)
            rows = derive(client.daily(station.triplet, args.start, args.end))
            total += store.add_snotel(station, rows)
            print(f"  {station.name:<24} {station.miles_away:>5} mi  {len(rows)} days")
    print(f"ingested {total} station-days across {len(seen)} sites")
    return 0


def cmd_snapshot(args, store: Store) -> int:
    n = store.add_forecast_snapshot(CaicClient().current_forecasts())
    print(f"archived {n} forecast day-rows")
    return 0


def cmd_digests(args, store: Store) -> int:
    print(f"wrote {store.write_digests(args.out)} digest files to {args.out}")
    return 0


def cmd_ask(args, store: Store) -> int:
    """Resolve a natural-language question to a location and emit its brief."""
    question = " ".join(args.question)
    location = args.location or _guess_location(question)
    if not location:
        print(f"Could not identify a location in: {question!r}", file=sys.stderr)
        print("Known: " + ", ".join(sorted(place.name for place in LOCATIONS.values())), file=sys.stderr)
        return 1

    brief = Profiler(store).brief(location, on=args.on)
    print(json.dumps(brief.to_dict(), indent=2) if args.json else render(brief))
    return 0


def _guess_location(question: str) -> str | None:
    """Pull a known location out of a free-text question.

    Tries whole names first ("... berthoud pass ..."), then falls back to word
    n-grams so a bare "berthoud" still resolves.
    """
    q = question.lower()
    hits = [loc.name for name, loc in LOCATIONS.items() if name in q]
    if hits:
        return max(hits, key=len)

    words = re.findall(r"[a-z]+", q)
    for size in (2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i : i + size])
            if len(phrase) < 4:
                continue
            match = resolve(phrase)
            if match:
                return match.name
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="avalanche", description=__doc__)
    parser.add_argument("--db", default="caic.db")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="backfill observations for a date range")
    p.add_argument("--start", type=_date, required=True)
    p.add_argument("--end", type=_date, required=True)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("snapshot", help="archive today's forecasts (run daily)")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("weather", help="ingest SNOTEL snowpack weather")
    p.add_argument("--start", type=_date, required=True)
    p.add_argument("--end", type=_date, required=True)
    p.add_argument("--location", help="limit to one known location")
    p.add_argument("--stations", type=int, default=2)
    p.set_defaults(func=cmd_weather)

    p = sub.add_parser("digests", help="render human-readable Markdown digests")
    p.add_argument("--out", default="digests")
    p.set_defaults(func=cmd_digests)

    p = sub.add_parser("ask", help="produce a grounded brief for a question")
    p.add_argument("question", nargs="+")
    p.add_argument("--location")
    p.add_argument("--on", type=_date)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ask)

    args = parser.parse_args(argv)
    store = Store(args.db)
    try:
        return args.func(args, store)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
