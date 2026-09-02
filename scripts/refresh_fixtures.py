#!/usr/bin/env python3
"""Re-record the API payloads the tests run against.

Run this when you suspect CAIC has changed something, then run the suite: the
parsing tests are written to fail loudly on a field this project depends on
disappearing, which is the early warning the undocumented APIs don't give you.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
WINDOW = ("2026-03-06T00:00:00.000Z", "2026-03-10T00:00:00.000Z")


def write(name: str, payload) -> None:
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1))
    print(f"  {name:<26} {path.stat().st_size / 1024:7.1f} KB")


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    print("Refreshing fixtures…")

    for name, endpoint, per in [
        ("avalanche_observations", "avalanche_observations", 8),
        ("observation_reports", "observation_reports", 4),
    ]:
        write(name, s.get(
            f"https://api.avalanche.state.co.us/api/v2/{endpoint}",
            params=[("page", "1"), ("per", str(per)),
                    ("r[observed_at_gteq]", WINDOW[0]), ("r[observed_at_lteq]", WINDOW[1])],
            headers={"Accept": "application/json"}, timeout=60).json())

    write("forecast_products", s.get(
        "https://avalanche.state.co.us/api-proxy/avid",
        params={"_api_proxy_uri": "/products/all?includeExpired=true"}, timeout=60).json())

    write("snotel_data", s.get(
        "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data",
        params={"stationTriplets": "335:CO:SNTL", "elements": "WTEQ,SNWD,PREC,TOBS,TMAX,TMIN",
                "duration": "DAILY", "beginDate": "2026-03-01", "endDate": "2026-03-12"},
        timeout=60).json())

    stations = s.get("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations",
                     params={"activeOnly": "true"}, timeout=120).json()
    write("snotel_stations", [x for x in stations
                             if x.get("stateCode") == "CO" and x.get("networkCode") == "SNTL"][:25])
    print("\nNow run: make test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
