"""Normalization and storage."""

from __future__ import annotations


def test_avalanches_normalize_into_queryable_columns(store, avalanche_records):
    n = store.add_avalanches(avalanche_records)
    assert n == len(avalanche_records)

    rows = store.conn.execute("SELECT * FROM avalanche").fetchall()
    assert len(rows) == n
    for row in rows:
        assert row["band"] in {"alp", "tln", "btl", None}
        assert row["zone_slug"], "zone slug is the primary query key and must be populated"


def test_ingest_is_idempotent(store, avalanche_records):
    store.add_avalanches(avalanche_records)
    store.add_avalanches(avalanche_records)
    count = store.conn.execute("SELECT COUNT(*) c FROM avalanche").fetchone()["c"]
    assert count == len(avalanche_records), "re-ingesting duplicated rows"


def test_snotel_rows_land_with_station_coordinates(populated):
    row = populated.conn.execute("SELECT * FROM snotel LIMIT 1").fetchone()
    assert row["latitude"] and row["longitude"]
    assert row["station"] == "Berthoud Summit"


def test_recapturing_a_forecast_day_updates_it_in_place(store, forecast_products):
    """Forecasts are keyed by location and valid date, so a re-fetch refreshes.

    Historical forecasts are retrievable from CAIC on demand, so the archive is
    a cache rather than the only copy: re-running a backfill must converge on one
    authoritative row per location per day instead of piling up duplicates.
    """
    store.add_forecasts(forecast_products, "Berthoud Pass", captured_at="2026-03-09T12:00:00Z")
    first = store.conn.execute("SELECT COUNT(*) c FROM forecast").fetchone()["c"]

    store.add_forecasts(forecast_products, "Berthoud Pass", captured_at="2026-03-10T12:00:00Z")
    second = store.conn.execute("SELECT COUNT(*) c FROM forecast").fetchone()["c"]

    assert second == first, "re-fetching the same forecast day duplicated rows"
    latest = store.conn.execute("SELECT DISTINCT captured_at FROM forecast").fetchall()
    assert [r["captured_at"] for r in latest] == ["2026-03-10T12:00:00Z"]


def test_forecasts_are_stored_per_location(store, forecast_products):
    """One CAIC forecast area can cover several named locations."""
    store.add_forecasts(forecast_products, "Berthoud Pass")
    store.add_forecasts(forecast_products, "Loveland Pass")
    locations = store.conn.execute("SELECT DISTINCT location FROM forecast").fetchall()
    assert {r["location"] for r in locations} == {"Berthoud Pass", "Loveland Pass"}


def test_digests_render_one_file_per_zone_day(populated, tmp_path):
    out = tmp_path / "digests"
    written = populated.write_digests(out)
    assert written > 0
    files = list(out.rglob("*.md"))
    assert len(files) == written
    body = files[0].read_text()
    assert body.startswith("# ")
    assert "Avalanches recorded:" in body
