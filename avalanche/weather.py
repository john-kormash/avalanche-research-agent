"""Snowpack weather from the NRCS SNOTEL network.

Avalanche-relevant weather is not general weather. What matters is how much
water arrived, how fast, and whether the surface warmed — loading and warming
are what turn a weak layer into an avalanche. SNOTEL measures exactly that,
free and without a key, at sites that in Colorado sit remarkably close to the
terrain people actually ski: the Berthoud Summit site is 0.4 miles from the
pass at 11,300 ft.

This is a batch ingest rather than a request-time lookup. The value is in the
joined season history — "the layer got loaded on Feb 10 and eight avalanches
ran within 48 hours" — which is a time series operation, not a point query.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

import requests

AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"

# WTEQ snow water equivalent, SNWD snow depth, PREC accumulated precipitation.
ELEMENTS = ["WTEQ", "SNWD", "PREC", "TOBS", "TMAX", "TMIN"]

# A 72-hour water gain at or above this many inches is the classic threshold for
# "the snowpack just got a load it has to adjust to".
LOADING_INCHES = 1.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Station:
    triplet: str
    name: str
    latitude: float
    longitude: float
    elevation_ft: float
    miles_away: float = 0.0


class SnotelClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self._stations: list[dict] | None = None

    def _get(self, path: str, params: Any = None) -> Any:
        resp = self.session.get(f"{AWDB}/{path}", params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def stations(self, state: str = "CO") -> list[dict]:
        """All active SNOTEL sites in a state.

        The API ignores its own ``stateCds``/``networkCds`` filters, so the
        whole station list comes back and is narrowed here.
        """
        if self._stations is None:
            self._stations = self._get("stations", {"activeOnly": "true"})
        return [
            s for s in self._stations
            if s.get("stateCode") == state and s.get("networkCode") == "SNTL"
            and s.get("latitude") and s.get("longitude")
        ]

    def nearest(self, lat: float, lon: float, limit: int = 3, state: str = "CO") -> list[Station]:
        scored = [
            (haversine(lat, lon, s["latitude"], s["longitude"]), s)
            for s in self.stations(state)
        ]
        scored.sort(key=lambda x: x[0])
        return [
            Station(
                triplet=s["stationTriplet"], name=s["name"], latitude=s["latitude"],
                longitude=s["longitude"], elevation_ft=s.get("elevation") or 0.0,
                miles_away=round(miles, 1),
            )
            for miles, s in scored[:limit]
        ]

    def daily(self, triplet: str, start: dt.date, end: dt.date) -> list[dict]:
        """Daily observations for one station, one row per date."""
        payload = self._get(
            "data",
            {
                "stationTriplets": triplet,
                "elements": ",".join(ELEMENTS),
                "duration": "DAILY",
                "beginDate": start.isoformat(),
                "endDate": end.isoformat(),
            },
        )
        if not payload:
            return []

        by_date: dict[str, dict] = {}
        for element in payload[0].get("data", []):
            code = (element.get("stationElement") or {}).get("elementCode")
            if code not in ELEMENTS:
                continue
            for point in element.get("values", []):
                date = point.get("date")
                if date:
                    by_date.setdefault(date, {"date": date, "triplet": triplet})[code] = point.get("value")
        return [by_date[d] for d in sorted(by_date)]


def derive(rows: list[dict]) -> list[dict]:
    """Add loading and warming signals to a station's daily series.

    Water-equivalent gain is the loading signal; a max temperature above
    freezing is the warming signal that drives wet activity.
    """
    out = []
    for i, row in enumerate(rows):
        swe = row.get("WTEQ")
        prev = rows[i - 1].get("WTEQ") if i >= 1 else None
        prev3 = rows[i - 3].get("WTEQ") if i >= 3 else None
        swe_24 = round(swe - prev, 2) if swe is not None and prev is not None else None
        swe_72 = round(swe - prev3, 2) if swe is not None and prev3 is not None else None
        depth = row.get("SNWD")
        prev_depth = rows[i - 1].get("SNWD") if i >= 1 else None
        tmax = row.get("TMAX")
        out.append(
            {
                **row,
                "swe_24h": swe_24,
                "swe_72h": swe_72,
                "depth_24h": (depth - prev_depth) if depth is not None and prev_depth is not None else None,
                "is_loading": bool(swe_72 is not None and swe_72 >= LOADING_INCHES),
                "is_warming": bool(tmax is not None and tmax > 32),
            }
        )
    return out
