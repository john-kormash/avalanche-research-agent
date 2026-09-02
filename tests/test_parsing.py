"""Parsing tests — these are the schema-drift alarm.

CAIC's APIs are undocumented and have already drifted once (the forecast payload
moved from `title` to `publicName`, which silently broke caic-python's model).
These assertions fail loudly when a field this project depends on disappears.
"""

from __future__ import annotations

from avalanche.caic import DANGER_SCALE, normalize_band


def test_elevation_bands_are_html_escaped():
    """CAIC sends `&#62;TL` rather than `>TL`; unescaping is load-bearing."""
    assert normalize_band("&#62;TL") == "alp"
    assert normalize_band("&#60;TL") == "btl"
    assert normalize_band("TL") == "tln"
    assert normalize_band(None) is None
    assert normalize_band("nonsense") is None


def test_avalanche_records_carry_the_fields_the_rose_needs(avalanche_records):
    assert avalanche_records, "fixture is empty"
    for r in avalanche_records:
        assert "observed_at" in r
        assert "backcountry_zone" in r
        assert "aspect" in r
        assert "elevation" in r
        assert "destructive_size" in r

    # At least some records must be usable, or the aspect/elevation rose is empty.
    usable = [r for r in avalanche_records if r.get("aspect") and normalize_band(r.get("elevation"))]
    assert usable, "no record had both an aspect and a parseable elevation band"


def test_forecast_products_use_publicname_not_title(forecast_products):
    """The drift that breaks caic-python 0.2.0's AvalancheForecast model."""
    assert forecast_products, "no avalancheforecast products in fixture"
    product = forecast_products[0]
    assert "publicName" in product
    assert "dangerRatings" in product
    assert "avalancheProblems" in product


def test_forecast_danger_ratings_are_per_band_and_per_day(forecast_products):
    days = forecast_products[0]["dangerRatings"]["days"]
    assert days, "forecast carried no danger rating days"
    for day in days:
        assert {"alp", "tln", "btl"} <= set(day)
        for band in ("alp", "tln", "btl"):
            assert day[band] in DANGER_SCALE, f"unknown danger value {day[band]!r}"


def test_field_reports_nest_their_observations(report_records):
    assert report_records
    keys = set(report_records[0])
    assert {"snowpack_observations", "weather_observations", "avalanche_observations"} <= keys


def test_snotel_payload_carries_every_element(snotel_payload):
    codes = {e["stationElement"]["elementCode"] for e in snotel_payload[0]["data"]}
    assert {"WTEQ", "SNWD", "TMAX", "TMIN"} <= codes
