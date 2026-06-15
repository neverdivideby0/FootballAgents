"""M3 tests — Brier scoring + prediction-log resolution (hermetic, no network)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Outcome
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import _ENTRY_SEP
from worldcupagents.graph.reflection import (
    brier_score,
    outcome_from_score,
    quality_label,
    resolve_prediction,
)


# ── pure scoring ─────────────────────────────────────────────────────────────

def test_brier_confident_correct_is_zero():
    assert brier_score(1.0, 0.0, 0.0, Outcome.HOME_WIN) == 0.0


def test_brier_confident_wrong_is_two():
    assert brier_score(1.0, 0.0, 0.0, Outcome.AWAY_WIN) == 2.0


def test_brier_uniform_beats_nothing_but_below_confident_wrong():
    b = brier_score(1 / 3, 1 / 3, 1 / 3, Outcome.HOME_WIN)
    assert round(b, 3) == 0.667


def test_outcome_from_score():
    assert outcome_from_score(2, 1) == Outcome.HOME_WIN
    assert outcome_from_score(1, 1) == Outcome.DRAW
    assert outcome_from_score(0, 2) == Outcome.AWAY_WIN


def test_quality_label_bands():
    assert quality_label(0.1) == "strong"
    assert quality_label(0.4) == "decent"
    assert quality_label(0.6) == "beats coin-flip"
    assert quality_label(1.2) == "poor"


# ── log resolution ───────────────────────────────────────────────────────────

def _cfg(tmp_path):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["memory_dir"] = str(tmp_path / "memory")
    return cfg


def _seed_log(cfg, home="Argentina", away="Brazil"):
    from pathlib import Path
    p = Path(cfg["prediction_log_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"[2026-06-20 | {home} vs {away} | HOME_WIN 2-1 | pending]\n\n"
        "PREDICTION:\nArgentina edge a tight one.\n"
        "(p_home=0.600, p_draw=0.250, p_away=0.150)"
    )
    p.write_text(entry + _ENTRY_SEP, encoding="utf-8")
    return p


def test_resolve_marks_entry_and_scores(tmp_path):
    cfg = _cfg(tmp_path)
    log = _seed_log(cfg)

    res = resolve_prediction("Argentina", "Brazil", Outcome.HOME_WIN, cfg, actual_scoreline="3-1")
    assert res["found"] is True
    assert res["predicted"] == "HOME_WIN"
    # Brier for (.6,.25,.15) vs HOME_WIN = .16 + .0625 + .0225 = .245
    assert round(res["brier"], 3) == 0.245

    text = log.read_text()
    assert "| pending]" not in text
    assert "resolved: HOME_WIN 3-1 Brier=0.245" in text
    assert "RESULT: HOME_WIN 3-1" in text


def test_resolve_writes_team_lessons(tmp_path):
    from pathlib import Path
    cfg = _cfg(tmp_path)
    _seed_log(cfg)
    resolve_prediction("Argentina", "Brazil", Outcome.AWAY_WIN, cfg, actual_scoreline="0-1")
    teams = Path(cfg["memory_dir"]) / "teams"
    assert (teams / "argentina.md").exists() and (teams / "brazil.md").exists()
    assert "we predicted HOME_WIN" in (teams / "argentina.md").read_text()


def test_resolve_missing_prediction_is_graceful(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_log(cfg)
    res = resolve_prediction("France", "Spain", Outcome.DRAW, cfg)
    assert res["found"] is False     # no matching pending entry, no crash


def test_resolve_no_log_file_is_graceful(tmp_path):
    res = resolve_prediction("A", "B", Outcome.DRAW, _cfg(tmp_path))
    assert res["found"] is False
