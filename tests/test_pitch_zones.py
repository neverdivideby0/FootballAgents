"""B2 — pitch zone labels (StatsBomb 120×80 frame → coaching grid)."""

from __future__ import annotations

import pytest

from worldcupagents.dataflows.pitch_zones import lane, third, zone_label


@pytest.mark.parametrize("x,y,expected", [
    (105, 30, "final third, left half-space"),       # the canonical plan example
    (15, 78, "defensive third, right wing"),
    (60, 40, "middle third, central"),
    (0, 0, "defensive third, left wing"),            # corner of own box area
    (119.5, 79.9, "final third, right wing"),        # attacking right corner
    (80, 16, "final third, left half-space"),        # boundary: x=80 starts final third
    (40, 48, "middle third, right half-space"),      # boundary: x=40 starts middle
])
def test_zone_label_table(x, y, expected):
    assert zone_label(x, y) == expected


def test_clamping_out_of_range():
    assert third(-5) == "defensive third" and third(500) == "final third"
    assert lane(-1) == "left wing" and lane(99) == "right wing"


def test_missing_coordinates():
    assert zone_label(None, 40) == "" and zone_label(60, None) == ""
