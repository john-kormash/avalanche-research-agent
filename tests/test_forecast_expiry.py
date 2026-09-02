"""Forecast expiry.

Avalanche.org's published terms of use require that "danger rating displays must
be published and expired accordingly". These tests pin that behaviour down: a
rating must never be presented as current once it isn't, and a gap in the
archive must never be filled with a neighbouring day's numbers.
"""

from __future__ import annotations

import datetime as dt

import pytest

from avalanche.profile import Profiler, _parse_timestamp, render

ZONE = "Front Range"


def make_forecast(valid_date: str, expires_at: str, danger: str = "considerable") -> list[dict]:
    """One forecast product covering a single day."""
    return [
        {
            "id": f"forecast-{valid_date}",
            "publicName": ZONE,
            "issueDateTime": f"{valid_date}T12:00:00.000Z",
            "expiryDateTime": expires_at,
            "weatherSummary": "",
            "snowpackSummary": "A persistent slab sits on faceted snow near the ground.",
            "avalancheSummary": "",
            "terrainAndTravelAdvice": "",
            "dangerRatings": {
                "days": [
                    {"position": 1, "date": f"{valid_date}T12:00:00Z",
                     "alp": danger, "tln": danger, "btl": "moderate"}
                ]
            },
            "avalancheProblems": {"days": [[{"type": "persistent", "likelihood": "likely"}]]},
        }
    ]


@pytest.fixture
def brief_for(populated):
    def build(on: str):
        return Profiler(populated).brief(
            "Berthoud Pass", on=dt.date.fromisoformat(on), season_start=dt.date(2025, 10, 1)
        )
    return build


# ----------------------------------------------------------------- parsing


@pytest.mark.parametrize("value", ["2026-03-09T16:30:00.000Z", "2026-03-09T16:30:00Z"])
def test_timestamps_parse_as_utc(value):
    parsed = _parse_timestamp(value)
    assert parsed is not None and parsed.tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "not a date"])
def test_unparseable_timestamps_are_none_rather_than_raising(value):
    assert _parse_timestamp(value) is None


# ------------------------------------------------------------- selection


def test_a_live_forecast_is_current(populated, brief_for):
    """Expiry in the future — the rating stands."""
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    populated.add_forecast_snapshot(make_forecast("2026-03-09", future))
    fc = brief_for("2026-03-09").forecast
    assert fc["status"] == "current"
    assert fc["danger"]["above treeline"] == "considerable"


def test_a_past_forecast_is_marked_expired_but_still_readable(populated, brief_for):
    """Retrospective questions are legitimate; the label carries the caveat."""
    populated.add_forecast_snapshot(make_forecast("2026-03-09", "2026-03-10T04:30:00.000Z"))
    fc = brief_for("2026-03-09").forecast
    assert fc["status"] == "expired"
    assert fc["danger"]["above treeline"] == "considerable"


def test_a_forecast_from_another_day_is_superseded_and_withholds_ratings(populated, brief_for):
    """The core fix: yesterday's numbers must not stand in for today's."""
    populated.add_forecast_snapshot(make_forecast("2026-03-09", "2026-03-10T04:30:00.000Z"))
    fc = brief_for("2026-03-12").forecast
    assert fc["status"] == "superseded"
    assert fc["valid_date"] == "2026-03-09"
    assert "danger" not in fc, "ratings leaked from a day the forecast never covered"
    assert "problems" not in fc
    assert "snowpack_summary" not in fc


def test_no_forecast_at_all_returns_none(populated, brief_for):
    assert brief_for("2026-03-09").forecast is None


def test_the_newest_snapshot_of_a_day_wins(populated, brief_for):
    """Forecasts are reissued; the latest capture for that date is authoritative."""
    populated.add_forecast_snapshot(
        make_forecast("2026-03-09", "2026-03-10T04:30:00.000Z", danger="moderate"),
        captured_at="2026-03-09T06:00:00Z",
    )
    populated.add_forecast_snapshot(
        make_forecast("2026-03-09", "2026-03-10T04:30:00.000Z", danger="high"),
        captured_at="2026-03-09T15:00:00Z",
    )
    assert brief_for("2026-03-09").forecast["danger"]["above treeline"] == "high"


# ---------------------------------------------------------------- render


def test_expired_render_says_so_before_showing_numbers(populated, brief_for):
    populated.add_forecast_snapshot(make_forecast("2026-03-09", "2026-03-10T04:30:00.000Z"))
    text = render(brief_for("2026-03-09"))
    assert "EXPIRED" in text
    assert "not a current rating" in text
    assert text.index("EXPIRED") < text.index("above treeline"), "caveat must precede the rating"
    assert "avalanche.state.co.us" in text


def test_superseded_render_shows_no_rating_at_all(populated, brief_for):
    populated.add_forecast_snapshot(make_forecast("2026-03-09", "2026-03-10T04:30:00.000Z"))
    text = render(brief_for("2026-03-12"))
    assert "No forecast was issued for 2026-03-12" in text
    for rating in ("considerable", "moderate"):
        assert rating not in text.lower().split("## Season")[0], "a rating leaked into the forecast section"


def test_missing_forecast_reads_as_missing_data_not_as_safety(populated, brief_for):
    text = render(brief_for("2026-03-09"))
    assert "No archived forecast covers this date" in text
    assert "not a low danger rating" in text


def test_current_render_states_the_validity_window(populated, brief_for):
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    populated.add_forecast_snapshot(make_forecast("2026-03-09", future))
    text = render(brief_for("2026-03-09"))
    assert "EXPIRED" not in text
    assert "expires" in text
