"""MCP server exposing the CAIC layer to any MCP client.

The tool surface deliberately does **not** mirror the CAIC API. A faithful
wrapper would expose paginated observation endpoints, and an agent asking "is
Berthoud Pass safe" would have to pull hundreds of records into its context and
aggregate them itself — slow, expensive, and exactly where a model starts
inventing numbers.

Instead each tool returns an *answer*: the aggregation happens in SQL and only
the result crosses into the context window. `risk_brief` is the primary tool and
answers the whole question in one call; the rest exist for follow-ups.

All tools are read-only, which is declared through annotations so a host can
run them in parallel without gating.

Run it:
    python -m avalanche.mcp_server                  # stdio
    MCP_TRANSPORT=streamable-http python -m avalanche.mcp_server
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .caic import BAND_LABELS, CaicClient
from .locations import LOCATIONS, resolve
from .profile import Profiler, render
from .store import Store

DB_PATH = os.environ.get("CAIC_DB", "caic.db")

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
LIVE = ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=True)

mcp = MCPServer(
    name="caic",
    title="Colorado Avalanche Information Center",
    version="0.1.0",
    instructions=(
        "Avalanche conditions for Colorado backcountry locations, backed by CAIC "
        "observations, archived forecasts and SNOTEL snowpack weather.\n\n"
        "Call `risk_brief` first for any question about whether a place is safe or "
        "what conditions are like — it returns danger ratings, a season-long "
        "aspect/elevation breakdown of avalanche activity, recent weather loading "
        "and recent slides in one response. Use the other tools only to drill into "
        "something the brief surfaced.\n\n"
        "Always report the season context alongside the current rating: a moderate "
        "day on a persistent weak layer is not the same as a moderate day on a "
        "settled snowpack. Never present this as a substitute for the official "
        "CAIC forecast — link the reader to avalanche.state.co.us."
    ),
)


def _store() -> Store:
    return Store(DB_PATH)


@mcp.tool(
    description=(
        "Primary tool. A grounded avalanche brief for a named Colorado backcountry "
        "location: current danger ratings by elevation band, the season's avalanche "
        "activity broken down by aspect and elevation, dominant avalanche problems, "
        "recent snowpack loading from the nearest SNOTEL site, and recent slides "
        "nearby. Answers 'is <place> safe today' in a single call."
    ),
    annotations=READ_ONLY,
)
def risk_brief(location: str, date: str | None = None) -> str:
    """
    Args:
        location: A named location, e.g. "Berthoud Pass". Call `list_locations` for the set.
        date: ISO date (YYYY-MM-DD). Defaults to today.
    """
    on = dt.date.fromisoformat(date) if date else None
    store = _store()
    try:
        return render(Profiler(store).brief(location, on=on))
    except ValueError as err:
        known = ", ".join(sorted(place.name for place in LOCATIONS.values()))
        return f"{err}\n\nKnown locations: {known}"
    finally:
        store.close()


@mcp.tool(
    description="List the backcountry locations this server can report on, with their CAIC zone and terrain.",
    annotations=READ_ONLY,
)
def list_locations() -> list[dict[str, Any]]:
    return [
        {
            "name": loc.name,
            "zone": loc.zone,
            "summit_ft": loc.summit_ft,
            "aspects": list(loc.aspects),
            "elevation_bands": [BAND_LABELS[b] for b in loc.bands],
            "notes": loc.notes,
        }
        for loc in sorted(LOCATIONS.values(), key=lambda p: p.name)
    ]


@mcp.tool(
    description=(
        "Count avalanches in a CAIC zone grouped by aspect, elevation band, problem "
        "type or destructive size. Returns counts, not individual records, so it "
        "stays cheap over a whole season. Use it to test a hypothesis the brief "
        "raised, e.g. 'has anything run on north aspects below treeline this year'."
    ),
    annotations=READ_ONLY,
)
def avalanche_counts(
    zone_slug: str,
    group_by: str = "aspect",
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """
    Args:
        zone_slug: CAIC zone slug, e.g. "front-range". `list_locations` reports each location's zone.
        group_by: One of aspect, band, problem_type, d_size, primary_trigger, weak_layer.
        since: ISO date lower bound. Defaults to the start of the current water year.
        until: ISO date upper bound. Defaults to today.
    """
    allowed = {"aspect", "band", "problem_type", "d_size", "primary_trigger", "weak_layer"}
    if group_by not in allowed:
        return {"error": f"group_by must be one of {sorted(allowed)}"}

    today = dt.date.today()
    start = since or dt.date(today.year if today.month >= 10 else today.year - 1, 10, 1).isoformat()
    end = (until or today.isoformat()) + "T23:59:59Z"

    store = _store()
    try:
        rows = store.conn.execute(
            f"SELECT {group_by} AS key, COUNT(*) n FROM avalanche "
            "WHERE zone_slug=? AND observed_at BETWEEN ? AND ? AND "
            f"{group_by} IS NOT NULL GROUP BY {group_by} ORDER BY n DESC",
            (zone_slug, start, end),
        ).fetchall()
        return {
            "zone_slug": zone_slug,
            "grouped_by": group_by,
            "window": {"since": start[:10], "until": end[:10]},
            "total": sum(r["n"] for r in rows),
            "counts": {(r["key"] or "unknown"): r["n"] for r in rows},
        }
    finally:
        store.close()


@mcp.tool(
    description=(
        "Daily snowpack weather from the SNOTEL site nearest a location: snow depth, "
        "water equivalent, 24h and 72h water gain, and flags for loading and "
        "above-freezing days. Loading is what turns a weak layer into an avalanche."
    ),
    annotations=READ_ONLY,
)
def snowpack_weather(location: str, days: int = 14, end: str | None = None) -> dict[str, Any]:
    """
    Args:
        location: A named location, e.g. "Berthoud Pass".
        days: How many days back to return. Defaults to 14.
        end: ISO end date. Defaults to today.
    """
    loc = resolve(location)
    if loc is None:
        return {"error": f"unknown location: {location}"}

    last = dt.date.fromisoformat(end) if end else dt.date.today()
    first = last - dt.timedelta(days=days)

    store = _store()
    try:
        sites = store.conn.execute(
            "SELECT DISTINCT triplet, station, latitude, longitude, elevation_ft FROM snotel"
        ).fetchall()
        if not sites:
            return {"error": "no SNOTEL data ingested; run `avalanche weather` first"}

        from .weather import haversine

        site = min(sites, key=lambda s: haversine(loc.latitude, loc.longitude, s["latitude"], s["longitude"]))
        rows = store.conn.execute(
            "SELECT date, depth_in, swe_in, swe_24h, swe_72h, t_max, t_min, is_loading, is_warming "
            "FROM snotel WHERE triplet=? AND date BETWEEN ? AND ? ORDER BY date",
            (site["triplet"], first.isoformat(), last.isoformat()),
        ).fetchall()
        return {
            "location": loc.name,
            "station": site["station"],
            "station_elevation_ft": site["elevation_ft"],
            "miles_from_location": round(
                haversine(loc.latitude, loc.longitude, site["latitude"], site["longitude"]), 1
            ),
            "days": [dict(r) for r in rows],
        }
    finally:
        store.close()


@mcp.tool(
    description=(
        "The human-readable field digest for one CAIC zone on one day: every "
        "avalanche recorded with aspect, size and problem, plus observer notes on "
        "snowpack and weather. Use this when the brief points at a specific day and "
        "you want the narrative behind the numbers."
    ),
    annotations=READ_ONLY,
)
def zone_digest(zone_slug: str, date: str) -> str:
    """
    Args:
        zone_slug: CAIC zone slug, e.g. "front-range".
        date: ISO date (YYYY-MM-DD).
    """
    store = _store()
    try:
        avs = store.conn.execute(
            "SELECT aspect, band, d_size, type_code, problem_type, primary_trigger, "
            "comments, detail FROM avalanche WHERE zone_slug=? AND substr(observed_at,1,10)=?",
            (zone_slug, date),
        ).fetchall()
        reports = store.conn.execute(
            "SELECT snowpack_text, weather_text, description FROM report "
            "WHERE zone_slug=? AND substr(observed_at,1,10)=?",
            (zone_slug, date),
        ).fetchall()

        if not avs and not reports:
            return f"No observations recorded for {zone_slug} on {date}."

        lines = [f"# {zone_slug} — {date}", "", f"{len(avs)} avalanches, {len(reports)} field reports", ""]
        for a in avs:
            band = BAND_LABELS.get(a["band"] or "", a["band"] or "?")
            lines.append(
                f"- {a['aspect'] or '?'} {band}, {a['d_size'] or '?'}, "
                f"{a['problem_type'] or 'unclassified'} — {a['detail'] or a['comments'] or 'no notes'}"
            )
        notes = [r["snowpack_text"] for r in reports if r["snowpack_text"]]
        if notes:
            lines += ["", "## Snowpack notes", *(f"- {n}" for n in notes)]
        return "\n".join(lines)
    finally:
        store.close()


@mcp.tool(
    description=(
        "Fetch the avalanche forecast CAIC is publishing right now, live from their "
        "API. Use this for today's official danger ratings; the other tools read the "
        "local archive, which is only as current as the last snapshot."
    ),
    annotations=LIVE,
)
def current_forecast(zone: str | None = None) -> list[dict[str, Any]]:
    """
    Args:
        zone: Optional zone name filter, e.g. "Front Range". Omit for every zone.
    """
    products = CaicClient().current_forecasts()
    out = []
    for p in products:
        name = p.get("publicName") or p.get("title") or ""
        if zone and zone.lower() not in name.lower():
            continue
        days = (p.get("dangerRatings") or {}).get("days") or []
        out.append(
            {
                "zone": name,
                "issued": p.get("issueDateTime"),
                "expires": p.get("expiryDateTime"),
                "danger_by_day": [
                    {
                        "date": d.get("date"),
                        "above_treeline": d.get("alp"),
                        "near_treeline": d.get("tln"),
                        "below_treeline": d.get("btl"),
                    }
                    for d in days
                ],
                "snowpack_summary": p.get("snowpackSummary"),
                "avalanche_summary": p.get("avalancheSummary"),
                "travel_advice": p.get("terrainAndTravelAdvice"),
            }
        )
    return out


@mcp.resource(
    "caic://coverage",
    name="Data coverage",
    description="What this server currently holds: date ranges, record counts and known gaps.",
    mime_type="text/markdown",
)
def coverage() -> str:
    if not Path(DB_PATH).exists():
        return "No database yet. Run `avalanche ingest` and `avalanche weather` to populate it."
    store = _store()
    try:
        av = store.conn.execute(
            "SELECT COUNT(*) n, MIN(observed_at) lo, MAX(observed_at) hi FROM avalanche"
        ).fetchone()
        sn = store.conn.execute(
            "SELECT COUNT(*) n, MIN(date) lo, MAX(date) hi FROM snotel"
        ).fetchone()
        fc = store.conn.execute("SELECT COUNT(*) n FROM forecast").fetchone()
        return "\n".join(
            [
                "# CAIC data coverage",
                "",
                f"- Avalanche observations: {av['n']} ({(av['lo'] or '—')[:10]} to {(av['hi'] or '—')[:10]})",
                f"- SNOTEL station-days: {sn['n']} ({sn['lo'] or '—'} to {sn['hi'] or '—'})",
                f"- Archived forecast day-rows: {fc['n']}",
                "",
                "## Known gaps",
                "- CAIC publishes no forecast archive, so forecast history only goes back",
                "  to the first local snapshot. Run `avalanche snapshot` daily.",
                "- Ridgetop wind is not covered; SNOTEL does not measure it.",
            ]
        )
    finally:
        store.close()


def main() -> None:
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
