"""Per-signal credit: with/without Brier scoreboard from SIGNALS-tagged predictions."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.credit import credit_report, signal_credit
from worldcupagents.graph.predict import _ENTRY_SEP, _signals_present


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    return cfg


def _entry(brier: float, signals: str) -> str:
    tag = f"[2026-06-20 | A vs B | HOME_WIN 1-0 | resolved: HOME_WIN 1-0 Brier={brier:.3f}]"
    return f"{tag}\n\nPREDICTION:\nx\n(p_home=0.5, p_draw=0.3, p_away=0.2)\nSIGNALS: {signals}"


def _write(cfg, entries):
    from pathlib import Path
    p = Path(cfg["prediction_log_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_ENTRY_SEP.join(entries) + _ENTRY_SEP, encoding="utf-8")


def test_signals_present_detects_state():
    final = {
        "past_context": "PUNDITRY SIGNALS ...\n\nLESSONS FROM PAST PREDICTIONS ...",
        "matchup_context": {"market": {"home": 0.5}},
        "calibration_note": "Calibration check ...",
    }
    assert _signals_present(final) == ["calibration", "lessons", "market", "punditry"]
    assert _signals_present({}) == []  # baseline-only prediction


def test_credit_splits_with_without(tmp_path):
    cfg = _cfg(tmp_path)
    # punditry present → good Brier; punditry absent → bad Brier (4 each, > MIN).
    entries = [_entry(0.20, "punditry, market") for _ in range(4)]
    entries += [_entry(0.50, "market") for _ in range(4)]
    _write(cfg, entries)

    c = signal_credit(cfg)
    assert c["n"] == 8
    pund = c["signals"]["punditry"]
    assert pund["with_brier"] == 0.20 and pund["with_n"] == 4
    assert pund["without_brier"] == 0.50 and pund["without_n"] == 4
    assert pund["delta"] == -0.30                      # punditry group scored better
    # market was on every prediction → no "without" group → insufficient.
    assert c["signals"]["market"] is None
    assert "helped" in credit_report(cfg)


def test_empty_and_untagged(tmp_path):
    cfg = _cfg(tmp_path)
    assert "No signal-tagged" in credit_report(cfg)            # no log at all
    # An old entry without a SIGNALS line is skipped (not counted).
    old = "[2026-01-01 | A vs B | HOME_WIN 1-0 | resolved: HOME_WIN 1-0 Brier=0.300]\n\nPREDICTION:\nx"
    _write(cfg, [old])
    assert signal_credit(cfg)["n"] == 0
