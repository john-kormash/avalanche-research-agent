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


def test_forecast_snapshots_accumulate_rather_than_replace(store, forecast_products):
    """CAIC publishes no archive, so each snapshot must be kept, not overwritten."""
    store.add_forecast_snapshot(forecast_products, captured_at="2026-03-09T12:00:00Z")
    store.add_forecast_snapshot(forecast_products, captured_at="2026-03-10T12:00:00Z")
    captures = store.conn.execute("SELECT DISTINCT captured_at FROM forecast").fetchall()
    assert len(captures) == 2


def test_digests_render_one_file_per_zone_day(populated, tmp_path):
    out = tmp_path / "digests"
    written = populated.write_digests(out)
    assert written > 0
    files = list(out.rglob("*.md"))
    assert len(files) == written
    body = files[0].read_text()
    assert body.startswith("# ")
    assert "Avalanches recorded:" in body
