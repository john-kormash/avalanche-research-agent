"""Thin access layer over the CAIC's undocumented HTTP APIs.

Observations are fetched through the public v2 JSON endpoints, which accept
Ransack-style filters and support historical date ranges. Forecasts come from
the ``api-proxy/avid`` products endpoint.

Two behaviours of the upstream API drive the shape of this module:

1. ``/products/all`` ignores its ``datetime`` argument and always returns the
   currently published products. There is no forecast archive to backfill from,
   so forecasts have to be snapshotted daily and accumulated locally.
2. The forecast payload uses ``publicName`` and omits ``confidence``, so the
   ``AvalancheForecast`` model in ``caic-python`` 0.2.0 fails validation against
   it. We parse the forecast payload directly instead.
"""

from __future__ import annotations

import datetime as dt
import html
import time
from collections.abc import Iterator
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

    def current_forecasts(self) -> list[dict]:
        """Today's published products.

        The upstream ``datetime`` parameter is accepted but ignored, so this is
        only ever a snapshot of *now* — which is precisely why it must be
        archived on a schedule.
        """
        payload = self._get(
            f"{HOME}/api-proxy/avid",
            params={"_api_proxy_uri": "/products/all?includeExpired=true"},
        )
        if not isinstance(payload, list):
            return []
        return [p for p in payload if p.get("type") == "avalancheforecast"]
