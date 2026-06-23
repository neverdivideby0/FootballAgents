"""Phase 2 — bilateral data-parity note (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import MatchResult, Player, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.ensemble.parity import coverage, parity_note


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")   # empty store → players/weaknesses thin
    return cfg


def _rich(team="Spain"):
    return TeamProfile(
        team=team, fifa_rank=3,
        squad=[Player(name=f"P{i}") for i in range(23)],
        probable_xi=[f"P{i}" for i in range(11)],
        coach="Luis de la Fuente",
        xg_for=1.8, xg_against=0.7,
        form=[MatchResult(opponent="X", goals_for=2, goals_against=0)],
    )


def _thin(team="Minnowland"):
    return TeamProfile(team=team, fifa_rank=120, squad=[Player(name="A")])


def test_coverage_counts_signals(tmp_path):
    cfg = _cfg(tmp_path)
    cr, ct = sum(coverage(_rich(), cfg).values()), sum(coverage(_thin(), cfg).values())
    assert cr > ct                                  # rich side scores higher
    assert ct <= 2                                  # thin side: ~squad only


def test_parity_note_fires_when_lopsided(tmp_path):
    note = parity_note(_rich("Spain"), _thin("Minnowland"), _cfg(tmp_path))
    assert note and "DATA PARITY" in note
    assert "Minnowland" in note                      # the thin side is named
    assert "not on what we happen to be missing" in note


def test_parity_quiet_when_balanced(tmp_path):
    cfg = _cfg(tmp_path)
    assert parity_note(_thin("A"), _thin("B"), cfg) == ""        # both thin → balanced
    assert parity_note(_rich("A"), _rich("B"), cfg) == ""        # both rich → balanced
