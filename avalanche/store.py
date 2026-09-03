"""Normalized local store for a season of CAIC data.

The store is deliberately structured-first. Almost everything that answers a
real question — aspect, elevation band, destructive size, problem type, trigger,
weak layer — arrives from CAIC as an enumerated field, not as prose. Those
columns are what the risk profile aggregates over. Free text is kept alongside
as supporting evidence and rendered into per-day Markdown digests.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .caic import BAND_LABELS, normalize_band

SCHEMA = """
CREATE TABLE IF NOT EXISTS avalanche (
    id TEXT PRIMARY KEY,
    observed_at TEXT,
    water_year INTEGER,
    zone TEXT,
    zone_slug TEXT,
    latitude REAL,
    longitude REAL,
    aspect TEXT,
    band TEXT,
    elevation_feet INTEGER,
    problem_type TEXT,
    type_code TEXT,
    d_size TEXT,
    r_size TEXT,
    primary_trigger TEXT,
    weak_layer TEXT,
    grain_type TEXT,
    is_incident INTEGER,
    comments TEXT,
    detail TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS avalanche_zone_date ON avalanche(zone_slug, observed_at);
CREATE INDEX IF NOT EXISTS avalanche_rose ON avalanche(zone_slug, aspect, band);

CREATE TABLE IF NOT EXISTS report (
    id TEXT PRIMARY KEY,
    observed_at TEXT,
    zone TEXT,
    zone_slug TEXT,
    latitude REAL,
    longitude REAL,
    area TEXT,
    route TEXT,
    description TEXT,
    snowpack_text TEXT,
    weather_text TEXT,
    saw_avalanche INTEGER,
    triggered_avalanche INTEGER,
    url TEXT
);
CREATE INDEX IF NOT EXISTS report_zone_date ON report(zone_slug, observed_at);

-- One row per location per forecast day. CAIC groups its zones dynamically, so
-- the location is resolved geometrically at ingest rather than stored as a name.
CREATE TABLE IF NOT EXISTS forecast (
    id TEXT,
    location TEXT,
    area_id TEXT,
    captured_at TEXT,
    issued_at TEXT,
    expires_at TEXT,
    zone TEXT,
    day_offset INTEGER,
    valid_date TEXT,
    danger_alp TEXT,
    danger_tln TEXT,
    danger_btl TEXT,
    weather_summary TEXT,
    snowpack_summary TEXT,
    avalanche_summary TEXT,
    travel_advice TEXT,
    problems TEXT,
    PRIMARY KEY (location, valid_date, id, day_offset)
);
CREATE INDEX IF NOT EXISTS forecast_location_date ON forecast(location, valid_date);

-- Daily SNOTEL series, one row per station per day, with loading/warming flags.
CREATE TABLE IF NOT EXISTS snotel (
    triplet TEXT,
    station TEXT,
    latitude REAL,
    longitude REAL,
    elevation_ft REAL,
    date TEXT,
    swe_in REAL,
    depth_in REAL,
    precip_in REAL,
    t_obs REAL,
    t_max REAL,
    t_min REAL,
    swe_24h REAL,
    swe_72h REAL,
    depth_24h REAL,
    is_loading INTEGER,
    is_warming INTEGER,
    PRIMARY KEY (triplet, date)
);
CREATE INDEX IF NOT EXISTS snotel_date ON snotel(date);
"""


def _text(value: Any) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _insert(table: str, rows: list[tuple]) -> str:
    """Build an INSERT whose placeholder count comes from the data.

    Hard-coding the count lets the SQL and the tuple drift apart silently as
    columns are added; deriving it makes that impossible.
    """
    return f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * len(rows[0]))})"


def _zone(record: dict) -> tuple[str, str]:
    zone = record.get("backcountry_zone") or {}
    return _text(zone.get("title")), _text(zone.get("slug"))


class Store:
    def __init__(self, path: str | Path = "caic.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- ingest

    def add_avalanches(self, records: Iterable[dict]) -> int:
        rows = []
        for r in records:
            zone, slug = _zone(r)
            detail = (r.get("avalanche_detail") or {}).get("description")
            rows.append(
                (
                    r.get("id"),
                    r.get("observed_at"),
                    r.get("water_year"),
                    zone,
                    slug,
                    r.get("latitude"),
                    r.get("longitude"),
                    _text(r.get("aspect")) or None,
                    normalize_band(r.get("elevation")),
                    r.get("elevation_feet"),
                    _text(r.get("problem_type")) or None,
                    _text(r.get("type_code")) or None,
                    _text(r.get("destructive_size")) or None,
                    _text(r.get("relative_size")) or None,
                    _text(r.get("primary_trigger")) or None,
                    _text(r.get("weak_layer")) or None,
                    _text(r.get("weak_layer_grain_type")) or _text(r.get("grain_type")) or None,
                    1 if r.get("is_incident") else 0,
                    _text(r.get("comments")),
                    _text(detail),
                    (r.get("observation_report") or {}).get("external_canonical_report"),
                )
            )
        if rows:
            self.conn.executemany(_insert("avalanche", rows), rows)
        self.conn.commit()
        return len(rows)

    def add_reports(self, records: Iterable[dict]) -> int:
        rows = []
        for r in records:
            zone, slug = _zone(r)
            snow = [_text(o.get("comments")) for o in (r.get("snowpack_observations") or [])]
            snow.append(_text((r.get("snowpack_detail") or {}).get("description")))
            wx = [_text(o.get("comments")) for o in (r.get("weather_observations") or [])]
            wx.append(_text((r.get("weather_detail") or {}).get("description")))
            rows.append(
                (
                    r.get("id"),
                    r.get("observed_at"),
                    zone,
                    slug,
                    r.get("latitude"),
                    r.get("longitude"),
                    _text(r.get("area")),
                    _text(r.get("route")),
                    _text(r.get("description")),
                    "\n".join(t for t in snow if t),
                    "\n".join(t for t in wx if t),
                    1 if r.get("saw_avalanche") else 0,
                    1 if r.get("triggered_avalanche") else 0,
                    r.get("url"),
                )
            )
        if rows:
            self.conn.executemany(_insert("report", rows), rows)
        self.conn.commit()
        return len(rows)

    def add_forecasts(
        self,
        products: Iterable[dict],
        location: str,
        captured_at: str | None = None,
    ) -> int:
        """Store the forecast days of products already resolved to one location."""
        captured_at = captured_at or dt.datetime.now(dt.timezone.utc).isoformat()
        rows = []
        for p in products:
            ratings = (p.get("dangerRatings") or {}).get("days") or []
            problems = (p.get("avalancheProblems") or {}).get("days") or []
            for i, day in enumerate(ratings):
                valid = _text(day.get("date"))[:10]
                rows.append(
                    (
                        p.get("id"),
                        location,
                        p.get("areaId"),
                        captured_at,
                        p.get("issueDateTime"),
                        p.get("expiryDateTime"),
                        _text(p.get("publicName")) or _text(p.get("title")),
                        day.get("position", i + 1),
                        valid,
                        day.get("alp"),
                        day.get("tln"),
                        day.get("btl"),
                        _text(p.get("weatherSummary")),
                        _text(p.get("snowpackSummary")),
                        _text(p.get("avalancheSummary")),
                        _text(p.get("terrainAndTravelAdvice")),
                        json.dumps(problems[i] if i < len(problems) else []),
                    )
                )
        if rows:
            self.conn.executemany(_insert("forecast", rows), rows)
        self.conn.commit()
        return len(rows)

    def add_snotel(self, station: Any, rows: Iterable[dict]) -> int:
        payload = [
            (
                r.get("triplet"), station.name,
                station.latitude, station.longitude, station.elevation_ft,
                r.get("date"),
                r.get("WTEQ"), r.get("SNWD"), r.get("PREC"),
                r.get("TOBS"), r.get("TMAX"), r.get("TMIN"),
                r.get("swe_24h"), r.get("swe_72h"), r.get("depth_24h"),
                1 if r.get("is_loading") else 0,
                1 if r.get("is_warming") else 0,
            )
            for r in rows
        ]
        if payload:
            self.conn.executemany(_insert("snotel", payload), payload)
        self.conn.commit()
        return len(payload)

    # ------------------------------------------------------------- readable

    def write_digests(self, out_dir: str | Path) -> int:
        """Render one Markdown digest per zone per day.

        This is the human-readable mirror of the database: greppable, diffable,
        reviewable by a forecaster, and ready to serve as a retrieval corpus if
        semantic search is layered on later.
        """
        out = Path(out_dir)
        rows = self.conn.execute(
            "SELECT DISTINCT zone_slug, zone, substr(observed_at,1,10) AS day "
            "FROM avalanche WHERE zone_slug != '' ORDER BY day DESC"
        ).fetchall()
        written = 0
        for row in rows:
            slug, zone, day = row["zone_slug"], row["zone"], row["day"]
            avs = self.conn.execute(
                "SELECT * FROM avalanche WHERE zone_slug=? AND substr(observed_at,1,10)=? "
                "ORDER BY d_size DESC",
                (slug, day),
            ).fetchall()
            reports = self.conn.execute(
                "SELECT * FROM report WHERE zone_slug=? AND substr(observed_at,1,10)=?",
                (slug, day),
            ).fetchall()

            lines = [f"# {zone} — {day}", ""]
            lines += [f"**Avalanches recorded:** {len(avs)}  ", f"**Field reports:** {len(reports)}", ""]

            if avs:
                lines += ["## Avalanches", "", "| Aspect | Band | Size | Type | Problem | Trigger |", "|---|---|---|---|---|---|"]
                for a in avs:
                    band = BAND_LABELS.get(a["band"] or "", a["band"] or "—")
                    lines.append(
                        f"| {a['aspect'] or '—'} | {band} | {a['d_size'] or '—'} "
                        f"| {a['type_code'] or '—'} | {a['problem_type'] or '—'} "
                        f"| {a['primary_trigger'] or '—'} |"
                    )
                lines.append("")
                notes = [f"- {a['detail'] or a['comments']}" for a in avs if a["detail"] or a["comments"]]
                if notes:
                    lines += ["### Observer notes", "", *notes, ""]

            snow = [r["snowpack_text"] for r in reports if r["snowpack_text"]]
            if snow:
                lines += ["## Snowpack", "", *(f"- {s}" for s in snow), ""]
            wx = [r["weather_text"] for r in reports if r["weather_text"]]
            if wx:
                lines += ["## Weather", "", *(f"- {w}" for w in wx), ""]

            path = out / slug / f"{day}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines))
            written += 1
        return written
