"""M-ML2 tests — per-league memory isolation + comp-filtered strengths (hermetic)."""

from __future__ import annotations

import copy
from pathlib import Path

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.ensemble.strength import load_strength_model
from worldcupagents.leagues.registry import apply_league, get_league


# ── per-league memory isolation ──────────────────────────────────────────────

def test_wc_keeps_default_memory_dir():
    cfg = {"memory_dir": "memory"}
    apply_league(cfg, get_league("WC2026"))
    assert cfg["memory_dir"] == "memory"            # unchanged for the default league


def test_non_default_league_gets_subdir():
    cfg = {"memory_dir": "memory"}
    apply_league(cfg, get_league("PL"))
    assert cfg["memory_dir"] == "memory/PL"          # isolated


def test_explicit_memory_dir_is_respected(tmp_path):
    cfg = {"memory_dir": str(tmp_path / "mem")}
    apply_league(cfg, get_league("PL"))
    assert cfg["memory_dir"] == str(tmp_path / "mem")  # not overridden (tests stay hermetic)


# ── comp-filtered strengths ──────────────────────────────────────────────────

def _seed_mixed(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert([
        # Premier League rows
        {"date": "2025-08-16", "comp": "PL", "home": "Arsenal FC", "away": "Chelsea FC",
         "hg": 2, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2025-08-23", "comp": "PL", "home": "Chelsea FC", "away": "Arsenal FC",
         "hg": 1, "ag": 1, "xg_home": None, "xg_away": None, "source": "t"},
        # A World-Cup row that must NOT leak into the PL model
        {"date": "2022-12-18", "comp": "WC", "home": "Argentina", "away": "France",
         "hg": 3, "ag": 3, "xg_home": None, "xg_away": None, "source": "t"},
    ])
    store.close()


def test_strength_model_is_competition_scoped(tmp_path):
    _seed_mixed(tmp_path)

    pl_cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL"}
    pl_model = load_strength_model(pl_cfg)
    assert pl_model is not None
    assert "arsenal fc" in pl_model.teams and "chelsea fc" in pl_model.teams
    assert "argentina" not in pl_model.teams        # WC row excluded

    wc_cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "WC"}
    wc_model = load_strength_model(wc_cfg)
    assert wc_model is not None
    assert "argentina" in wc_model.teams and "arsenal fc" not in wc_model.teams


def test_strength_model_none_when_competition_absent(tmp_path):
    _seed_mixed(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "SA"}  # no Serie A rows
    assert load_strength_model(cfg) is None


# ── apply_league via a full config (regression: WC config intact) ────────────

def test_apply_league_on_full_config_round_trips():
    # Paths are anchored to the project root (work from any cwd); leagues get a subdir.
    wc = copy.deepcopy(DEFAULT_CONFIG)
    apply_league(wc, get_league("WC2026"))
    assert wc["fd_competition"] == "WC"
    assert wc["memory_dir"] == DEFAULT_CONFIG["memory_dir"]          # unchanged for the default league

    pl = copy.deepcopy(DEFAULT_CONFIG)
    apply_league(pl, get_league("PL"))
    assert pl["fd_competition"] == "PL"
    assert pl["memory_dir"] == str(Path(DEFAULT_CONFIG["memory_dir"]) / "PL")
    assert Path(pl["memory_dir"]).is_absolute()                      # works from any cwd
