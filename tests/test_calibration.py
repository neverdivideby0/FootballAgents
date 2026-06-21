"""Calibration feedback: neutral-venue bias signals, recency weighting, and the
recency-weighted/shrunk judge_weight (all hermetic — no network, no LLM)."""

from __future__ import annotations

import copy
import json

from worldcupagents.calibration import (
    calibration_note,
    calibration_summary,
    effective_judge_weight,
    fitted_weights_path,
    refit_judge_weight,
    resolved_predictions,
)
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import _ENTRY_SEP


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    return cfg


def _resolved_entry(date, home, away, predicted, actual, p, brier=0.3, score="1-0") -> str:
    """Build one resolved prediction_log entry in the real on-disk format."""
    tag = f"[{date} | {home} vs {away} | {predicted} {score} | resolved: {actual} {score} Brier={brier:.3f}]"
    body = (f"\n\nPREDICTION:\nsome rationale\n"
            f"(p_home={p[0]:.3f}, p_draw={p[1]:.3f}, p_away={p[2]:.3f})")
    return tag + body


def _write_log(cfg, entries: list[str]) -> None:
    path = cfg["prediction_log_path"]
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(_ENTRY_SEP.join(entries) + _ENTRY_SEP, encoding="utf-8")


# ── resolved_predictions ────────────────────────────────────────────────────

def test_resolved_predictions_parses(tmp_path):
    cfg = _cfg(tmp_path)
    _write_log(cfg, [
        _resolved_entry("2026-06-12", "Spain", "Croatia", "HOME_WIN", "HOME_WIN", (0.60, 0.25, 0.15)),
        _resolved_entry("2026-06-13", "Brazil", "Serbia", "HOME_WIN", "AWAY_WIN", (0.70, 0.20, 0.10)),
    ])
    rows = resolved_predictions(cfg)
    assert len(rows) == 2
    assert rows[0]["fixture"] == "Spain vs Croatia"
    assert rows[0]["predicted"] == "HOME_WIN" and rows[0]["actual"] == "HOME_WIN"
    assert rows[1]["p"] == [0.70, 0.20, 0.10]


def test_empty_log_returns_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolved_predictions(cfg) == []
    assert calibration_note(cfg) == ""  # "" only when there is no history


# ── bias signals (neutral-venue) ────────────────────────────────────────────

def _upset_log(cfg, n=6):
    """n fixtures where the favourite was backed ~70% but LOST every time."""
    entries = []
    for i in range(n):
        # favourite = home (p_home high) but actual AWAY_WIN every time
        entries.append(_resolved_entry(f"2026-06-{10+i:02d}", f"Fav{i}", f"Dog{i}",
                                        "HOME_WIN", "AWAY_WIN", (0.70, 0.18, 0.12), brier=0.9))
    _write_log(cfg, entries)


def test_favourite_overbacking_flagged(tmp_path):
    cfg = _cfg(tmp_path)
    _upset_log(cfg, n=6)
    s = calibration_summary(cfg)
    assert s["favourite"]["gap"] > 0.08          # forecast >> realized favourite wins
    note = calibration_note(cfg)
    assert "favourites" in note.lower() and "over-backed" in note.lower()
    # Neutral-venue: never mentions home-field advantage.
    assert "home" not in note.lower()


def test_no_home_signal_key(tmp_path):
    cfg = _cfg(tmp_path)
    _upset_log(cfg, n=4)
    s = calibration_summary(cfg)
    assert "home" not in s  # only confidence / favourite / draw / host


def test_draw_underforecast_flagged(tmp_path):
    cfg = _cfg(tmp_path)
    # Every game was a draw, but draw prob was always tiny.
    entries = [_resolved_entry(f"2026-06-{10+i:02d}", f"A{i}", f"B{i}",
                               "HOME_WIN", "DRAW", (0.55, 0.10, 0.35), brier=0.8)
               for i in range(5)]
    _write_log(cfg, entries)
    s = calibration_summary(cfg)
    assert s["draw"]["gap"] < -0.08              # forecast draw % << realized
    assert "draw" in calibration_note(cfg).lower()


def test_note_emits_from_n1(tmp_path):
    cfg = _cfg(tmp_path)
    _write_log(cfg, [_resolved_entry("2026-06-12", "Spain", "Croatia",
                                     "HOME_WIN", "AWAY_WIN", (0.70, 0.18, 0.12), brier=0.9)])
    note = calibration_note(cfg)
    assert note and "n=1" in note
    assert "tentatively" in note  # language is soft at tiny sample


# ── recency (EWMA) ──────────────────────────────────────────────────────────

def test_ewma_latest_match_dominates(tmp_path):
    """Same results, different order: when the upsets are the MOST RECENT games,
    the favourite-over-backing signal is stronger than when they're the oldest."""
    cfg = _cfg(tmp_path)
    favs_won = [_resolved_entry(f"2026-06-{1+i:02d}", f"A{i}", f"B{i}",
                                "HOME_WIN", "HOME_WIN", (0.70, 0.18, 0.12), brier=0.1)
                for i in range(5)]
    upsets = [_resolved_entry(f"2026-06-{20+i:02d}", f"C{i}", f"D{i}",
                              "HOME_WIN", "AWAY_WIN", (0.70, 0.18, 0.12), brier=0.9)
              for i in range(3)]

    _write_log(cfg, favs_won + upsets)           # upsets are most recent
    gap_recent = calibration_summary(cfg)["favourite"]["gap"]
    _write_log(cfg, upsets + favs_won)           # upsets are oldest
    gap_old = calibration_summary(cfg)["favourite"]["gap"]

    assert gap_recent > gap_old                  # recent upsets weigh more


# ── host carve-out (gated, neutral-venue exception) ─────────────────────────

def test_host_carveout_gated(tmp_path):
    cfg = _cfg(tmp_path)
    # Two host games only → below the min-3 gate → suppressed.
    _write_log(cfg, [
        _resolved_entry("2026-06-12", "Mexico", "Croatia", "HOME_WIN", "AWAY_WIN", (0.6, 0.2, 0.2)),
        _resolved_entry("2026-06-13", "Canada", "Serbia", "HOME_WIN", "AWAY_WIN", (0.6, 0.2, 0.2)),
    ])
    assert calibration_summary(cfg)["host"] is None


def test_host_carveout_appears(tmp_path):
    cfg = _cfg(tmp_path)
    entries = [_resolved_entry(f"2026-06-{10+i:02d}", "United States", f"Opp{i}",
                               "HOME_WIN", "AWAY_WIN", (0.6, 0.2, 0.2)) for i in range(4)]
    _write_log(cfg, entries)
    host = calibration_summary(cfg)["host"]
    assert host is not None and host["n"] == 4


# ── adaptive judge_weight ───────────────────────────────────────────────────

def _eval_record(i, judge, base, hg, ag):
    return {"ts": f"2026-06-{1+i:02d}T00:00:00", "date": f"2026-06-{1+i:02d}",
            "home": f"H{i}", "away": f"A{i}", "hg": hg, "ag": ag,
            "judge": judge, "base": base, "llm": True, "provider": "test",
            "blend": judge, "judge_weight": 0.6, "odds": None}


def _write_eval(cfg, records):
    from pathlib import Path
    log = Path(cfg["data_dir"]) / "eval_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_effective_weight_prior_without_file(tmp_path):
    cfg = _cfg(tmp_path)
    assert effective_judge_weight(cfg) == 0.6          # no fitted file → prior


def test_refit_shrinks_small_sample_and_loads(tmp_path):
    cfg = _cfg(tmp_path)
    # Judge nails every result (home win), baseline says away — pure judge (w=1) wins.
    recs = [_eval_record(i, judge=[0.9, 0.05, 0.05], base=[0.05, 0.05, 0.9], hg=2, ag=0)
            for i in range(4)]
    _write_eval(cfg, recs)
    info = refit_judge_weight(cfg)
    assert info["w_fit"] == 1.0                        # fit points at pure judge
    assert 0.6 <= info["weight"] < 0.75                # but shrunk toward 0.6 (n small)
    assert fitted_weights_path(cfg).exists()
    assert effective_judge_weight(cfg) == info["weight"]  # predictor would load it


def test_refit_leans_in_with_more_reads(tmp_path):
    cfg = _cfg(tmp_path)
    small = [_eval_record(i, [0.9, 0.05, 0.05], [0.05, 0.05, 0.9], 2, 0) for i in range(4)]
    _write_eval(cfg, small)
    w_small = refit_judge_weight(cfg)["weight"]
    big = [_eval_record(i, [0.9, 0.05, 0.05], [0.05, 0.05, 0.9], 2, 0) for i in range(80)]
    _write_eval(cfg, big)
    w_big = refit_judge_weight(cfg)["weight"]
    assert w_big > w_small                             # more reads → leans into the fit


def test_refit_none_without_reads(tmp_path):
    cfg = _cfg(tmp_path)
    assert refit_judge_weight(cfg) is None
