"""CLI argument handling and location guessing."""

from __future__ import annotations

import pytest

from avalanche.cli import _guess_location


@pytest.mark.parametrize(
    "question,expected",
    [
        ("is berthoud pass safe today", "Berthoud Pass"),
        ("berthoud", "Berthoud Pass"),
        ("what about Red Mountain Pass", "Red Mountain Pass"),
        ("how is loveland looking", "Loveland Pass"),
        ("is it safe at vail pass tomorrow", "Vail Pass"),
    ],
)
def test_location_is_recovered_from_free_text(question, expected):
    assert _guess_location(question) == expected


def test_unrecognized_question_returns_nothing():
    assert _guess_location("what about somewhere unknown") is None
