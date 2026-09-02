"""SNOTEL derivation — loading and warming signals."""

from __future__ import annotations

from avalanche.weather import LOADING_INCHES, derive


def test_derive_computes_water_gain_windows(snotel_rows):
    assert snotel_rows
    # The first rows have no prior days to difference against.
    assert snotel_rows[0]["swe_24h"] is None
    assert snotel_rows[0]["swe_72h"] is None
    later = [r for r in snotel_rows if r["swe_72h"] is not None]
    assert later, "no row had a 72-hour window"


def test_loading_flag_matches_the_threshold(snotel_rows):
    for row in snotel_rows:
        if row["swe_72h"] is None:
            assert row["is_loading"] is False
        else:
            assert row["is_loading"] == (row["swe_72h"] >= LOADING_INCHES)


def test_warming_flag_tracks_above_freezing(snotel_rows):
    for row in snotel_rows:
        if row.get("TMAX") is not None:
            assert row["is_warming"] == (row["TMAX"] > 32)


def test_derive_tolerates_missing_values():
    rows = [
        {"date": "2026-03-01", "WTEQ": 10.0, "TMAX": 20.0},
        {"date": "2026-03-02", "WTEQ": None, "TMAX": None},
        {"date": "2026-03-03", "WTEQ": 11.5, "TMAX": 35.0},
        {"date": "2026-03-04", "WTEQ": 12.0, "TMAX": 30.0},
    ]
    out = derive(rows)
    assert out[1]["swe_24h"] is None
    assert out[1]["is_warming"] is False
    assert out[2]["is_warming"] is True
    assert out[3]["swe_72h"] == 2.0
