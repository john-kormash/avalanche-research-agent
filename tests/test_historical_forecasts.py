"""Historical forecast retrieval and geometric location matching.

The `datetime` and `includeExpired` parameters are mutually exclusive upstream:
sending both returns the *current* products and silently ignores the date. That
behaviour is invisible — a 200 with plausible-looking data — so these tests pin
the request shape as well as the parsing.
"""

from __future__ import annotations

import datetime as dt

import pytest

from avalanche.caic import CaicClient, geometry_contains, resolve_forecasts_by_location
from avalanche.locations import resolve
from avalanche.profile import _summarize_rose

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-106.0, 39.0], [-105.0, 39.0], [-105.0, 40.0], [-106.0, 40.0], [-106.0, 39.0]]],
}


class FakeClient(CaicClient):
    """A client that records its request URIs instead of making them."""

    def __init__(self, products=None, areas=None):
        super().__init__()
        self.requested: list[str] = []
        self._products = products or []
        self._areas = areas or []

    def _avid(self, uri: str):
        self.requested.append(uri)
        if "/area" in uri:
            return {"type": "FeatureCollection", "features": self._areas}
        return self._products


# --------------------------------------------------------------- geometry


def test_point_inside_polygon():
    assert geometry_contains(-105.5, 39.5, SQUARE)


@pytest.mark.parametrize("lon,lat", [(-107.0, 39.5), (-105.5, 41.0), (-104.0, 38.0)])
def test_point_outside_polygon(lon, lat):
    assert not geometry_contains(lon, lat, SQUARE)


def test_hole_is_excluded():
    donut = {
        "type": "Polygon",
        "coordinates": [
            SQUARE["coordinates"][0],
            [[-105.6, 39.4], [-105.4, 39.4], [-105.4, 39.6], [-105.6, 39.6], [-105.6, 39.4]],
        ],
    }
    assert not geometry_contains(-105.5, 39.5, donut)
    assert geometry_contains(-105.9, 39.1, donut)


def test_unsupported_geometry_is_not_a_match():
    assert not geometry_contains(-105.5, 39.5, {"type": "Point", "coordinates": [-105.5, 39.5]})
    assert not geometry_contains(-105.5, 39.5, {})


# ------------------------------------------------------------ request shape


def test_historical_request_sends_datetime_without_includeexpired():
    """Sending both makes CAIC return today's products and ignore the date."""
    client = FakeClient()
    client.forecasts_on(dt.date(2026, 3, 9))
    uri = client.requested[0]
    assert "datetime=2026-03-09" in uri
    assert "includeExpired" not in uri, "includeExpired silently overrides datetime"


def test_todays_request_uses_includeexpired():
    client = FakeClient()
    client.current_forecasts()
    assert "includeExpired=true" in client.requested[0]
    assert "datetime=" not in client.requested[0]


def test_area_request_filters_to_avalanche_forecasts():
    client = FakeClient()
    client.forecast_areas_on(dt.date(2026, 3, 9))
    uri = client.requested[0]
    assert "/products/all/area" in uri
    assert "productType=avalancheforecast" in uri
    assert "datetime=2026-03-09" in uri


# --------------------------------------------------------------- resolution


def test_locations_resolve_to_the_area_that_contains_them():
    products = [{"type": "avalancheforecast", "id": "f1", "areaId": "area-1"}]
    areas = [{"id": "area-1", "properties": {"id": "area-1"}, "geometry": SQUARE}]
    client = FakeClient(products, areas)

    # Berthoud Pass is 39.798 N, -105.777 W — inside the square.
    resolved = resolve_forecasts_by_location(client, dt.date(2026, 3, 9), [resolve("Berthoud Pass")])
    assert list(resolved) == ["Berthoud Pass"]
    assert resolved["Berthoud Pass"][0]["id"] == "f1"


def test_a_location_outside_every_area_is_absent_not_empty():
    """Absent means "nothing covers here", which must not read as "no forecast issued"."""
    products = [{"type": "avalancheforecast", "id": "f1", "areaId": "area-1"}]
    areas = [{"id": "area-1", "properties": {"id": "area-1"}, "geometry": SQUARE}]
    client = FakeClient(products, areas)

    # Red Mountain Pass is 37.896 N, -107.712 W — well outside the square.
    resolved = resolve_forecasts_by_location(
        client, dt.date(2026, 3, 9), [resolve("Red Mountain Pass")]
    )
    assert resolved == {}


def test_no_products_short_circuits_before_fetching_areas():
    client = FakeClient(products=[], areas=[])
    assert resolve_forecasts_by_location(client, dt.date(2026, 3, 9), [resolve("Berthoud Pass")]) == {}
    assert not any("/area" in u for u in client.requested), "fetched areas with no forecasts to match"


# -------------------------------------------------------------------- rose


def test_rose_groups_aspects_by_elevation_band():
    rose = _summarize_rose(["n_alp", "ne_alp", "e_tln", "n_tln", "nw_btl"])
    assert rose == "above treeline: N, NE; near treeline: N, E; below treeline: NW"


def test_rose_is_empty_when_nothing_is_specified():
    assert _summarize_rose([]) == ""
    assert _summarize_rose(["garbage", "n_bogus"]) == ""
