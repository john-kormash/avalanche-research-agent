"""Gazetteer of named backcountry locations.

CAIC publishes at zone granularity — "Front Range", "Vail & Summit County" —
but people ask about places: Berthoud Pass, Loveland Pass, Red Mountain Pass.
Bridging that gap is the single highest-leverage piece of this system, because
it is what lets a question name a trailhead and a query name a polygon.

Each entry carries the terrain that actually decides relevance: which aspects
exist there, and which elevation bands the skiing spans. A persistent slab
problem on north-facing terrain above treeline is a different answer at
Berthoud Pass (which has plenty of both) than at a low-angle, below-treeline
tour.
"""

from __future__ import annotations

from dataclasses import dataclass

ALL_ASPECTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


@dataclass(frozen=True)
class Location:
    name: str
    zone_slug: str
    zone: str
    latitude: float
    longitude: float
    aspects: tuple[str, ...]
    bands: tuple[str, ...]
    summit_ft: int
    notes: str = ""


# A starter set. In production this should be backed by a DEM-derived terrain
# analysis per named zone rather than hand curation.
LOCATIONS: dict[str, Location] = {
    loc.name.lower(): loc
    for loc in [
        Location(
            "Berthoud Pass", "front-range", "Front Range", 39.7981, -105.7772,
            tuple(ALL_ASPECTS), ("alp", "tln", "btl"), 11307,
            "Roadside terrain on both sides of US-40; skiing runs roughly "
            "10,700-12,400 ft, so all three elevation bands are in play.",
        ),
        Location(
            "Loveland Pass", "front-range", "Front Range", 39.6636, -105.8794,
            tuple(ALL_ASPECTS), ("alp", "tln"), 11990,
            "Predominantly above and near treeline; very little below-treeline terrain.",
        ),
        Location(
            "Jones Pass", "front-range", "Front Range", 39.7642, -105.9089,
            ("N", "NE", "E", "SE", "S"), ("alp", "tln", "btl"), 12451,
        ),
        Location(
            "Vail Pass", "vail-and-summit-county", "Vail & Summit County",
            39.5308, -106.2178, tuple(ALL_ASPECTS), ("tln", "btl"), 10666,
        ),
        Location(
            "Red Mountain Pass", "northern-san-juan", "Northern San Juan",
            37.8961, -107.7117, tuple(ALL_ASPECTS), ("alp", "tln", "btl"), 11018,
        ),
        Location(
            "Cameron Pass", "front-range", "Front Range", 40.5197, -105.8919,
            tuple(ALL_ASPECTS), ("tln", "btl"), 10276,
        ),
    ]
}


def resolve(query: str) -> Location | None:
    """Look up a named location, tolerating partial and case-insensitive input."""
    q = query.strip().lower()
    if q in LOCATIONS:
        return LOCATIONS[q]
    matches = [loc for name, loc in LOCATIONS.items() if q in name or name in q]
    return matches[0] if len(matches) == 1 else None
