"""Thin access layer over the CAIC's undocumented HTTP APIs.

Observations are fetched through the public v2 JSON endpoints, which accept
Ransack-style filters and support historical date ranges. Forecasts come from
the ``api-proxy/avid`` products endpoint.

Two behaviours of the upstream API drive the shape of this module:

1. ``/products/all`` honours ``datetime`` only when ``includeExpired`` is absent.
   Sending both returns the currently published products and silently drops the
   date, so historical and current forecasts take different request shapes.
2. The forecast payload uses ``publicName`` and omits ``confidence``, so the
   ``AvalancheForecast`` model in ``caic-python`` 0.2.0 fails validation against
   it. We parse the forecast payload directly instead.
"""

from __future__ import annotations

import datetime as dt
import html
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import requests

API = "https://api.avalanche.state.co.us"
HOME = "https://avalanche.state.co.us"

# CAIC reports elevation as an HTML-escaped band rather than a number.
_BANDS = {">TL": "alp", "TL": "tln", "<TL": "btl"}

BAND_LABELS = {
    "alp": "above treeline",
    "tln": "near treeline",
    "btl": "below treeline",
}

DANGER_SCALE = {
    "noRating": 0,
    "noForecast": 0,
    "low": 1,
    "moderate": 2,
    "considerable": 3,
    "high": 4,
    "extreme": 5,
}


def normalize_band(raw: str | None) -> str | None:
    """Map CAIC's escaped elevation band (``&#62;TL``) onto ``alp``/``tln``/``btl``."""
    if not raw:
        return None
    return _BANDS.get(html.unescape(raw).strip().upper())


@dataclass
class CaicClient:
    """Synchronous CAIC reader with polite pagination."""

    session: requests.Session = field(default_factory=requests.Session)
    per_page: int = 250
    pause: float = 0.4

    def _get(self, url: str, params: Any = None) -> Any:
        resp = self.session.get(
            url, params=params, headers={"Accept": "application/json"}, timeout=60
        )
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, endpoint: str, filters: list[tuple[str, str]]) -> Iterator[dict]:
        """Walk a v2 collection endpoint.

        These endpoints return a bare JSON list with no pagination metadata, so
        we advance until a short page comes back.
        """
        page = 1
        while True:
            params = [("page", str(page)), ("per", str(self.per_page)), *filters]
            batch = self._get(f"{API}{endpoint}", params=params)
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            if len(batch) < self.per_page:
                return
            page += 1
            time.sleep(self.pause)

    @staticmethod
    def _window(start: dt.date, end: dt.date) -> list[tuple[str, str]]:
        return [
            ("r[observed_at_gteq]", f"{start.isoformat()}T00:00:00.000Z"),
            ("r[observed_at_lteq]", f"{end.isoformat()}T23:59:59.999Z"),
            ("r[sorts][]", "observed_at desc"),
        ]

    def avalanche_observations(self, start: dt.date, end: dt.date) -> Iterator[dict]:
        """Every recorded avalanche observed in the window."""
        yield from self._paginate("/api/v2/avalanche_observations", self._window(start, end))

    def observation_reports(self, start: dt.date, end: dt.date) -> Iterator[dict]:
        """Field reports, including their nested snowpack and weather observations."""
        yield from self._paginate("/api/v2/observation_reports", self._window(start, end))

    def zones(self) -> list[dict]:
        return self._get(f"{API}/api/v2/zones.json")

    def _avid(self, uri: str) -> Any:
        """Call the AVID forecast service through the site's proxy."""
        return self._get(f"{HOME}/api-proxy/avid", params={"_api_proxy_uri": uri})

    def current_forecasts(self) -> list[dict]:
        """Today's published products, expired ones included."""
        payload = self._avid("/products/all?includeExpired=true")
        if not isinstance(payload, list):
            return []
        return [p for p in payload if p.get("type") == "avalancheforecast"]

    def forecasts_on(self, day: dt.date) -> list[dict]:
        """The forecasts that were published for a past date.

        ``datetime`` and ``includeExpired`` are mutually exclusive upstream:
        sending both returns the current products and silently ignores the date,
        which is what the website's own bundle avoids by branching on whether the
        requested day is today.
        """
        stamp = f"{day.isoformat()}T19:00:00.000Z"
        payload = self._avid(f"/products/all?datetime={stamp}")
        if not isinstance(payload, list):
            return []
        return [p for p in payload if p.get("type") == "avalancheforecast"]

    def forecast_areas_on(self, day: dt.date) -> list[dict]:
        """GeoJSON footprints for a date's forecasts, keyed by ``areaId``.

        CAIC groups its zones dynamically — the same terrain belongs to a
        differently-shaped forecast area from one day to the next — so a
        location has to be matched geometrically per date rather than by name.
        """
        stamp = f"{day.isoformat()}T19:00:00.000Z"
        payload = self._avid(
            f"/products/all/area?productType=avalancheforecast&datetime={stamp}"
        )
        if not isinstance(payload, dict):
            return []
        return payload.get("features") or []


def _ring_contains(point: tuple[float, float], ring: list) -> bool:
    """Ray-casting test for a single linear ring."""
    x, y = point
    inside = False
    for i in range(len(ring)):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i - 1][0], ring[i - 1][1]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
    return inside


def geometry_contains(longitude: float, latitude: float, geometry: dict) -> bool:
    """Whether a coordinate falls inside a GeoJSON Polygon or MultiPolygon.

    Implemented directly rather than pulling in a geometry library: the only
    operation this project needs is point-in-polygon against a handful of
    forecast footprints.
    """
    if not geometry:
        return False
    kind = geometry.get("type")
    if kind == "MultiPolygon":
        polygons = geometry.get("coordinates") or []
    elif kind == "Polygon":
        polygons = [geometry.get("coordinates") or []]
    else:
        return False

    point = (longitude, latitude)
    for polygon in polygons:
        if not polygon:
            continue
        outer, *holes = polygon
        if _ring_contains(point, outer) and not any(_ring_contains(point, h) for h in holes):
            return True
    return False


def resolve_forecasts_by_location(
    client: CaicClient, day: dt.date, locations: Iterable[Any]
) -> dict[str, list[dict]]:
    """Match a date's forecasts to the locations they cover.

    Returns ``{location name: [products]}``. A location with no covering
    forecast is absent from the result rather than mapped to an empty list, so
    callers can tell "nothing was issued here" from "nothing was issued at all".
    """
    products = client.forecasts_on(day)
    if not products:
        return {}

    areas = client.forecast_areas_on(day)
    by_area: dict[str, list[dict]] = {}
    for product in products:
        by_area.setdefault(product.get("areaId"), []).append(product)

    resolved: dict[str, list[dict]] = {}
    for area in areas:
        area_id = (area.get("properties") or {}).get("id") or area.get("id")
        covering = by_area.get(area_id)
        if not covering:
            continue
        geometry = area.get("geometry") or {}
        for loc in locations:
            if geometry_contains(loc.longitude, loc.latitude, geometry):
                resolved.setdefault(loc.name, []).extend(covering)
    return resolved
