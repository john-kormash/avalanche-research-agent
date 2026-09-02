"""The risk profile — the aggregation that actually answers the question."""

from __future__ import annotations

import datetime as dt

import pytest

from avalanche.locations import resolve
from avalanche.profile import Profiler, haversine, render

ON = dt.date(2026, 3, 10)
SEASON = dt.date(2025, 10, 1)


def test_haversine_matches_a_known_distance():
    # Berthoud Pass to Loveland Pass: 0.1345 deg of latitude (9.29 mi) and
    # 0.1022 deg of longitude at 39.7 N (5.42 mi), so ~10.8 mi great-circle.
    miles = haversine(39.7981, -105.7772, 39.6636, -105.8794)
    assert 10.5 < miles < 11.0


def test_unknown_location_is_rejected(populated):
    with pytest.raises(ValueError, match="Unknown location"):
        Profiler(populated).brief("Nonexistent Pass")


def test_brief_only_counts_terrain_that_exists_at_the_location(populated):
    """Vail Pass has no above-treeline terrain, so alpine slides must not count."""
    brief = Profiler(populated).brief("Vail Pass", on=ON, season_start=SEASON)
    loc = resolve("Vail Pass")
    assert "alp" not in loc.bands
    for aspect, bands in brief.season_rose.items():
        assert "alp" not in bands, "counted alpine activity at a location with no alpine terrain"
        assert aspect in loc.aspects


def test_brief_aggregates_a_rose_for_berthoud(populated):
    brief = Profiler(populated).brief("Berthoud Pass", on=ON, season_start=SEASON)
    assert brief.season_total == sum(
        n for bands in brief.season_rose.values() for n in bands.values()
    )
    for aspect in brief.season_rose:
        assert aspect in resolve("Berthoud Pass").aspects


def test_brief_picks_the_nearest_snotel_station(populated):
    brief = Profiler(populated).brief("Berthoud Pass", on=ON, season_start=SEASON)
    assert brief.weather is not None
    assert brief.weather["station"] == "Berthoud Summit"
    assert brief.weather["miles_away"] < 1.0


def test_weather_is_absent_rather_than_wrong_when_nothing_is_ingested(store, avalanche_records):
    store.add_avalanches(avalanche_records)
    brief = Profiler(store).brief("Berthoud Pass", on=ON, season_start=SEASON)
    assert brief.weather is None


def test_render_states_missing_forecast_instead_of_implying_safety(populated):
    """An empty archive must read as absent data, never as a low rating."""
    text = render(Profiler(populated).brief("Berthoud Pass", on=ON, season_start=SEASON))
    assert "No archived forecast" in text
    assert "# Berthoud Pass" in text


def test_render_is_markdown_with_the_expected_sections(populated):
    text = render(Profiler(populated).brief("Berthoud Pass", on=ON, season_start=SEASON))
    for heading in ("## Forecast", "## Season to date", "## Recent activity"):
        assert heading in text
