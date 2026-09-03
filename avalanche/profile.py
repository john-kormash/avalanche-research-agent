"""Season-aware risk profiling for a named location.

This is the layer that answers "is Berthoud Pass safe today". It does so by
aggregation, not by similarity search: the question is really "on the aspects
and elevations that exist here, what has been failing, how recently, and what
does today's forecast say about those same aspects and elevations".

The output is a structured brief. An LLM narrates it; it does not have to
discover it.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from typing import Any

from .caic import BAND_LABELS
from .locations import Location, resolve
from .store import Store

EARTH_MILES = 3958.8


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_MILES * math.asin(math.sqrt(a))


def _parse_timestamp(value: str | None) -> dt.datetime | None:
    """Parse a CAIC timestamp, which is ISO-8601 with a trailing ``Z``."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


@dataclass
class Brief:
    location: Location
    as_of: str
    forecast: dict[str, Any] | None
    season_rose: dict[str, dict[str, int]]
    recent: list[dict[str, Any]]
    problems_by_frequency: list[tuple[str, int]]
    weak_layers: list[tuple[str, int]]
    nearby: list[dict[str, Any]]
    season_total: int = 0
    weather: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location.name,
            "zone": self.location.zone,
            "as_of": self.as_of,
            "forecast": self.forecast,
            "season_total_avalanches": self.season_total,
            "season_rose": self.season_rose,
            "problems_by_frequency": self.problems_by_frequency,
            "weak_layers": self.weak_layers,
            "weather": self.weather,
            "recent_activity": self.recent,
            "nearby_avalanches": self.nearby,
            "notes": self.notes,
        }


class Profiler:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ bits

    def _forecast(self, zone: str, on: str, now: dt.datetime | None = None) -> dict[str, Any] | None:
        """The archived forecast for a date, labelled with how far it can be trusted.

        Avalanche.org's terms are explicit that "danger rating displays must be
        published and expired accordingly", so a rating is never returned bare.
        Three outcomes:

        ``current``     issued for this date and still inside its validity window.
        ``expired``     issued for this date but past its expiry. Legitimate for
                        retrospective questions; must be shown labelled, never as
                        today's rating.
        ``superseded``  nothing was issued for this date. Ratings are withheld
                        entirely — the nearest forecast is reported as a pointer,
                        not as a substitute.
        """
        now = now or dt.datetime.now(dt.timezone.utc)

        row = self.store.conn.execute(
            "SELECT * FROM forecast WHERE location = ? AND valid_date = ? "
            "ORDER BY captured_at DESC, day_offset ASC LIMIT 1",
            (zone, on),
        ).fetchone()

        if row is not None:
            expires = _parse_timestamp(row["expires_at"])
            status = "expired" if expires is not None and expires <= now else "current"
        else:
            row = self.store.conn.execute(
                "SELECT * FROM forecast WHERE location = ? AND valid_date < ? "
                "ORDER BY valid_date DESC, captured_at DESC LIMIT 1",
                (zone, on),
            ).fetchone()
            if row is None:
                return None
            status = "superseded"

        forecast = {
            "zone": row["zone"],
            "status": status,
            "valid_date": row["valid_date"],
            "issued_at": row["issued_at"],
            "expires_at": row["expires_at"],
            "captured_at": row["captured_at"],
        }

        # A superseded forecast says nothing about the requested date, so its
        # ratings and narrative are withheld rather than shown out of context.
        if status == "superseded":
            return forecast

        forecast.update(
            {
                "danger": {
                    "above treeline": row["danger_alp"],
                    "near treeline": row["danger_tln"],
                    "below treeline": row["danger_btl"],
                },
                "problems": json.loads(row["problems"] or "[]"),
                "snowpack_summary": row["snowpack_summary"],
                "avalanche_summary": row["avalanche_summary"],
                "travel_advice": row["travel_advice"],
            }
        )
        return forecast

    def _rose(self, loc: Location, start: str, end: str) -> tuple[dict, int]:
        rows = self.store.conn.execute(
            "SELECT aspect, band, COUNT(*) n FROM avalanche "
            "WHERE zone_slug=? AND observed_at BETWEEN ? AND ? "
            "AND aspect IS NOT NULL AND band IS NOT NULL GROUP BY aspect, band",
            (loc.zone_slug, start, end),
        ).fetchall()
        rose: dict[str, dict[str, int]] = {}
        total = 0
        for r in rows:
            # Only count terrain that actually exists at this location.
            if r["aspect"] not in loc.aspects or r["band"] not in loc.bands:
                continue
            rose.setdefault(r["aspect"], {})[r["band"]] = r["n"]
            total += r["n"]
        return rose, total

    def _weather(self, loc: Location, on: str, recent_s: str) -> dict[str, Any] | None:
        """Recent snowpack weather from the nearest ingested SNOTEL site."""
        sites = self.store.conn.execute(
            "SELECT DISTINCT triplet, station, latitude, longitude, elevation_ft FROM snotel"
        ).fetchall()
        if not sites:
            return None
        row = min(
            sites,
            key=lambda s: haversine(loc.latitude, loc.longitude, s["latitude"], s["longitude"]),
        )
        miles = round(haversine(loc.latitude, loc.longitude, row["latitude"], row["longitude"]), 1)
        series = self.store.conn.execute(
            "SELECT * FROM snotel WHERE triplet=? AND date BETWEEN ? AND ? ORDER BY date",
            (row["triplet"], recent_s[:10], on),
        ).fetchall()
        if not series:
            return None
        latest = series[-1]
        loading_days = [r["date"] for r in series if r["is_loading"]]
        warming_days = [r["date"] for r in series if r["is_warming"]]
        return {
            "station": row["station"],
            "miles_away": miles,
            "elevation_ft": row["elevation_ft"],
            "as_of": latest["date"],
            "snow_depth_in": latest["depth_in"],
            "swe_in": latest["swe_in"],
            "swe_gain_24h": latest["swe_24h"],
            "swe_gain_72h": latest["swe_72h"],
            "loading_days": loading_days,
            "warming_days": warming_days,
        }

    def _loading_response(self, loc: Location, recent: list[dict], loading_days: list[str]) -> int:
        """Count recent avalanches that ran within 48h of a loading day."""
        loads = [dt.date.fromisoformat(d) for d in loading_days]
        hits = 0
        for a in recent:
            when = (a.get("observed_at") or "")[:10]
            if not when:
                continue
            day = dt.date.fromisoformat(when)
            if any(0 <= (day - load).days <= 2 for load in loads):
                hits += 1
        return hits

    # ----------------------------------------------------------------- brief

    def brief(self, query: str, on: dt.date | None = None, season_start: dt.date | None = None,
              recent_days: int = 14, radius_miles: float = 15.0) -> Brief:
        loc = resolve(query)
        if loc is None:
            raise ValueError(f"Unknown location: {query!r}")

        on = on or dt.date.today()
        # A CAIC water year runs October through September.
        season_start = season_start or dt.date(on.year if on.month >= 10 else on.year - 1, 10, 1)
        start_s, end_s = season_start.isoformat(), on.isoformat() + "T23:59:59Z"
        recent_s = (on - dt.timedelta(days=recent_days)).isoformat()

        rose, total = self._rose(loc, start_s, end_s)

        problems = self.store.conn.execute(
            "SELECT problem_type, COUNT(*) n FROM avalanche WHERE zone_slug=? "
            "AND observed_at BETWEEN ? AND ? AND problem_type IS NOT NULL "
            "GROUP BY problem_type ORDER BY n DESC",
            (loc.zone_slug, start_s, end_s),
        ).fetchall()

        layers = self.store.conn.execute(
            "SELECT weak_layer, COUNT(*) n FROM avalanche WHERE zone_slug=? "
            "AND observed_at BETWEEN ? AND ? AND weak_layer IS NOT NULL "
            "AND weak_layer NOT IN ('Unknown','') GROUP BY weak_layer ORDER BY n DESC LIMIT 8",
            (loc.zone_slug, start_s, end_s),
        ).fetchall()

        recent_rows = self.store.conn.execute(
            "SELECT * FROM avalanche WHERE zone_slug=? AND observed_at BETWEEN ? AND ? "
            "ORDER BY observed_at DESC LIMIT 40",
            (loc.zone_slug, recent_s, end_s),
        ).fetchall()

        recent, nearby = [], []
        for r in recent_rows:
            item = {
                "observed_at": r["observed_at"],
                "aspect": r["aspect"],
                "band": BAND_LABELS.get(r["band"] or "", r["band"]),
                "d_size": r["d_size"],
                "problem_type": r["problem_type"],
                "trigger": r["primary_trigger"],
                "note": r["detail"] or r["comments"] or "",
                "url": r["url"],
            }
            recent.append(item)
            if r["latitude"] and r["longitude"]:
                miles = haversine(loc.latitude, loc.longitude, r["latitude"], r["longitude"])
                if miles <= radius_miles:
                    nearby.append({**item, "miles_away": round(miles, 1)})

        weather = self._weather(loc, on.isoformat(), recent_s)

        notes = []
        if weather:
            if weather["swe_gain_72h"] is not None:
                notes.append(
                    f"{weather['station']} SNOTEL ({weather['miles_away']} mi): "
                    f"{weather['snow_depth_in']}\" on the ground, "
                    f"{weather['swe_gain_72h']}\" of water in the last 72 hours."
                )
            responded = self._loading_response(loc, recent, weather["loading_days"])
            if responded:
                notes.append(
                    f"{responded} of the recent avalanches ran within 48 hours of a "
                    "loading day at the nearest SNOTEL site."
                )
            if weather["warming_days"]:
                notes.append(
                    f"Above-freezing temperatures on {len(weather['warming_days'])} of the "
                    "last 14 days — watch for wet activity on sunny aspects."
                )
        on_terrain = [
            a for a in recent
            if a["aspect"] in loc.aspects
            and any(BAND_LABELS.get(b) == a["band"] for b in loc.bands)
        ]
        if on_terrain:
            notes.append(
                f"{len(on_terrain)} of the last {len(recent)} avalanches in {loc.zone} "
                f"were on aspects and elevations that exist at {loc.name}."
            )
        if nearby:
            notes.append(f"{len(nearby)} occurred within {radius_miles:g} miles of {loc.name}.")
        persistent = [p["problem_type"] for p in recent if p["problem_type"] == "persistent"]
        if persistent:
            notes.append(
                f"{len(persistent)} recent avalanche(s) ran on a persistent problem — "
                "these do not stabilize on a normal timescale."
            )

        return Brief(
            location=loc,
            as_of=on.isoformat(),
            forecast=self._forecast(loc.name, on.isoformat()),
            season_rose=rose,
            season_total=total,
            recent=recent,
            problems_by_frequency=[(p["problem_type"], p["n"]) for p in problems],
            weak_layers=[(layer["weak_layer"], layer["n"]) for layer in layers],
            nearby=nearby,
            weather=weather,
            notes=notes,
        )


def _summarize_rose(aspect_elevations: list[str]) -> str:
    """Turn ``["n_alp", "ne_alp", ...]`` into "above treeline: N, NE".

    This is the forecaster's own aspect/elevation rose, and it shares its shape
    with the observed rose computed from avalanche records — which is what makes
    the two directly comparable.
    """
    bands: dict[str, list[str]] = {}
    for token in aspect_elevations:
        aspect, _, band = token.rpartition("_")
        if aspect and band in BAND_LABELS:
            bands.setdefault(band, []).append(aspect.upper())
    order = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    parts = []
    for band in ("alp", "tln", "btl"):
        if band in bands:
            aspects = sorted(set(bands[band]), key=lambda a: order.index(a) if a in order else 99)
            parts.append(f"{BAND_LABELS[band]}: {', '.join(aspects)}")
    return "; ".join(parts)


def _format_timestamp(value: str | None) -> str:
    parsed = _parse_timestamp(value)
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed else "unknown"


def render(brief: Brief) -> str:
    """Render a brief as the Markdown an assistant would ground its answer in."""
    loc = brief.location
    header = f"{loc.zone} zone." + (f" {loc.notes}" if loc.notes else "")
    out = [f"# {loc.name} — {brief.as_of}", "", f"*{header}*", ""]

    fc = brief.forecast
    if fc is None:
        out += [
            "## Forecast",
            "",
            "**No archived forecast covers this date.** This is missing data, not a low "
            "danger rating. Check the official forecast at avalanche.state.co.us.",
            "",
        ]
    elif fc["status"] == "superseded":
        # Ratings are deliberately withheld: showing a rating issued for another
        # day as though it applied here is exactly what the terms of use forbid.
        out += [
            "## Forecast",
            "",
            f"**No forecast was issued for {brief.as_of}.** The nearest archived one is "
            f"from {fc['valid_date']}; its ratings are withheld because avalanche danger "
            "changes day to day and they say nothing about this date. Check the official "
            "forecast at avalanche.state.co.us.",
            "",
        ]
    else:
        expired = fc["status"] == "expired"
        heading = "## Forecast — EXPIRED" if expired else "## Forecast"
        out += [heading, ""]
        if expired:
            out += [
                f"> Issued for {fc['valid_date']}, expired {_format_timestamp(fc['expires_at'])}. "
                "Shown for historical context — **not a current rating.** For today's "
                "conditions see avalanche.state.co.us.",
                "",
            ]
        else:
            out += [f"*Valid {fc['valid_date']}, expires {_format_timestamp(fc['expires_at'])}.*", ""]

        for band, rating in fc["danger"].items():
            out.append(f"- **{band}:** {rating or 'no rating'}")
        out.append("")
        for p in fc["problems"]:
            line = f"- Problem: {p.get('type')} — {p.get('likelihood')} likelihood"
            size = p.get("expectedSize") or {}
            if size.get("min") and size.get("max"):
                line += f", D{size['min']}–D{size['max']}"
            out.append(line)
            rose = _summarize_rose(p.get("aspectElevations") or [])
            if rose:
                out.append(f"    on {rose}")
        if fc.get("snowpack_summary"):
            out += ["", "**Snowpack:** " + fc["snowpack_summary"], ""]

    out += [f"## Season to date — {brief.season_total} avalanches on comparable terrain", ""]
    if brief.season_rose:
        bands = ["alp", "tln", "btl"]
        out += ["| Aspect | " + " | ".join(BAND_LABELS[b] for b in bands) + " |",
                "|---|" + "---|" * len(bands)]
        for aspect in [a for a in loc.aspects if a in brief.season_rose]:
            cells = [str(brief.season_rose[aspect].get(b, 0)) for b in bands]
            out.append(f"| {aspect} | " + " | ".join(cells) + " |")
        out.append("")

    if brief.problems_by_frequency:
        out += ["**Dominant problems this season:** " + ", ".join(
            f"{p} ({n})" for p, n in brief.problems_by_frequency[:5]), ""]
    if brief.weak_layers:
        out += ["**Weak layers cited:** " + ", ".join(
            f"{w} ({n})" for w, n in brief.weak_layers[:5]), ""]

    w = brief.weather
    if w:
        out += [f"## Snowpack weather — {w['station']} SNOTEL "
                f"({w['miles_away']} mi away, {w['elevation_ft']:.0f} ft, as of {w['as_of']})", "",
                f"- Snow depth: {w['snow_depth_in']} in / water equivalent: {w['swe_in']} in",
                f"- Water gain: {w['swe_gain_24h']} in (24h), {w['swe_gain_72h']} in (72h)"]
        if w["loading_days"]:
            out.append(f"- Loading days in window: {', '.join(w['loading_days'])}")
        out.append("")

    if brief.recent:
        out += [f"## Recent activity ({len(brief.recent)} avalanches)", ""]
        for a in brief.recent[:12]:
            line = (f"- {a['observed_at'][:10]} — {a['aspect'] or '?'} {a['band'] or '?'}, "
                    f"{a['d_size'] or '?'}, {a['problem_type'] or 'unclassified'}")
            if a.get("miles_away"):
                line += f" ({a['miles_away']} mi)"
            out.append(line)
        out.append("")

    if brief.notes:
        out += ["## Signals", ""] + [f"- {n}" for n in brief.notes] + [""]
    return "\n".join(out)
