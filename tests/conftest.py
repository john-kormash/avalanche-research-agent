"""Shared fixtures.

Every test runs offline against recorded CAIC and SNOTEL payloads in
``fixtures/``. Refreshing those files (``make fixtures``) is how upstream schema
drift gets caught: if CAIC renames a field, the parsing tests fail here rather
than silently returning empty briefs in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avalanche.store import Store
from avalanche.weather import derive

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def avalanche_records() -> list[dict]:
    return load("avalanche_observations")


@pytest.fixture
def report_records() -> list[dict]:
    return load("observation_reports")


@pytest.fixture
def forecast_products() -> list[dict]:
    return [p for p in load("forecast_products") if p.get("type") == "avalancheforecast"]


@pytest.fixture
def snotel_payload() -> list[dict]:
    return load("snotel_data")


@pytest.fixture
def snotel_rows(snotel_payload) -> list[dict]:
    """The SNOTEL payload flattened to one row per date, as the client returns it."""
    by_date: dict[str, dict] = {}
    for element in snotel_payload[0]["data"]:
        code = element["stationElement"]["elementCode"]
        for point in element.get("values", []):
            by_date.setdefault(point["date"], {"date": point["date"], "triplet": "335:CO:SNTL"})[code] = point.get("value")
    return derive([by_date[d] for d in sorted(by_date)])


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def populated(store, avalanche_records, report_records, snotel_rows) -> Store:
    """A store holding one real slice of the 2026 season."""
    from avalanche.weather import Station

    store.add_avalanches(avalanche_records)
    store.add_reports(report_records)
    store.add_snotel(
        Station("335:CO:SNTL", "Berthoud Summit", 39.8036, -105.7781, 11300.0, 0.4),
        snotel_rows,
    )
    return store


@pytest.fixture(scope="session")
def anyio_backend():
    """Run async tests on asyncio only; trio is not a dependency."""
    return "asyncio"
