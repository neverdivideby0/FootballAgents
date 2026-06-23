"""Phase 3 — injury/fitness overlay (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Player, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.injuries import apply_injuries, injury_summary
from worldcupagents.dataflows.match_store import MatchStore


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["harvest_punditry_injuries"] = False    # isolate the manual overlay
    return cfg


def _profile():
    return TeamProfile(
        team="Spain",
        squad=[Player(name="Víctor Muñoz"), Player(name="Pedri"), Player(name="Lamine Yamal")],
        probable_xi=["Pedri", "Víctor Muñoz", "Lamine Yamal"],
    )


def test_overlay_sets_status_and_drops_from_xi(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_injury("Spain", "Víctor Muñoz", "injured", note="tournament-ending")
    store.close()

    prof = apply_injuries(_profile(), cfg)
    by_name = {p.name: p.status for p in prof.squad}
    assert by_name["Víctor Muñoz"] == "injured"
    assert by_name["Pedri"] == "fit"
    assert "Víctor Muñoz" not in prof.probable_xi          # dropped from the XI
    assert "Pedri" in prof.probable_xi                      # available player kept


def test_doubt_is_flagged_but_kept_in_xi(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_injury("Spain", "Pedri", "doubt")
    store.close()
    prof = apply_injuries(_profile(), cfg)
    assert {p.name: p.status for p in prof.squad}["Pedri"] == "doubt"
    assert "Pedri" in prof.probable_xi                      # doubt → kept, just flagged


def test_injury_summary_and_manual_wins(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_injury("Spain", "Víctor Muñoz", "injured", source="manual")
    # an auto row must NOT overwrite the manual one
    wrote = store.upsert_injury("Spain", "Víctor Muñoz", "doubt",
                                source="guardian:punditry", overwrite=False)
    store.close()
    assert wrote is False
    s = injury_summary("Spain", cfg)
    assert "Víctor Muñoz" in s and "injured" in s and "manual" in s
