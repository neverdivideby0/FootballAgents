"""Explorer calibration tab: resolved-prediction parsing + reliability bins (hermetic)."""

from __future__ import annotations

import copy
import json

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.pipelines.data_explorer import _calibration, build_inventory, render_html


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


_LOG = (
    "[2026-06-01 | Brazil vs Mexico | HOME_WIN 2-0 | resolved: HOME_WIN 2-1 Brier=0.210]\n\n"
    "PREDICTION:\nsolid favourite\n"
    "(p_home=0.620, p_draw=0.230, p_away=0.150)\n"
    "RESULT: HOME_WIN 2-1. Predicted HOME_WIN → Brier=0.210 (strong).\n"
    "\n\n<!-- ENTRY_END -->\n\n"
    "[2026-06-02 | France vs USA | HOME_WIN 2-1 | resolved: AWAY_WIN 0-1 Brier=1.310]\n\n"
    "PREDICTION:\nchalk\n"
    "(p_home=0.700, p_draw=0.200, p_away=0.100)\n"
    "\n\n<!-- ENTRY_END -->\n\n"
    "[2026-06-03 | Spain vs Japan | HOME_WIN 1-0 | pending]\n\n"
    "PREDICTION:\nopen\n"
    "(p_home=0.500, p_draw=0.280, p_away=0.220)\n"
    "\n\n<!-- ENTRY_END -->\n\n"
)


def test_calibration_parses_resolved_only(tmp_path):
    cfg = _cfg(tmp_path)
    log = tmp_path / "memory" / "prediction_log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_LOG, encoding="utf-8")

    cal = _calibration(cfg)
    assert len(cal["resolved"]) == 2                    # pending entry excluded
    assert cal["mean_brier"] == round((0.210 + 1.310) / 2, 3)
    assert cal["hit_rate"] == 0.5                       # one right, one wrong
    first = cal["resolved"][0]
    assert first["fixture"] == "Brazil vs Mexico" and first["score"] == "2-1"
    assert first["p"] == [0.620, 0.230, 0.150]
    # Reliability bins: 6 forecasts total (3 per resolved match), all binned.
    assert sum(b["n"] for b in cal["bins"]) == 6
    # The 60–70% bin holds the 0.62 home forecast, which came true.
    b6 = cal["bins"][6]
    assert b6["n"] >= 1 and b6["realized"] is not None


def test_calibration_includes_eval_log_summary(tmp_path):
    cfg = _cfg(tmp_path)
    p = tmp_path / "data" / "eval_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hg": 2, "ag": 0, "llm": True,
                             "blend": [0.6, 0.25, 0.15], "judge": [0.7, 0.2, 0.1],
                             "base": [0.5, 0.3, 0.2], "odds": None}) + "\n", encoding="utf-8")
    cal = _calibration(cfg)
    assert cal["n_with_eval_log"] == 1
    models = {s["model"] for s in cal["eval_scores"]}
    assert {"baseline(no LLM)", "llm-judge(raw)", "llm-blend(final)"} <= models


def test_explorer_renders_calibration_tab(tmp_path):
    cfg = _cfg(tmp_path)
    log = tmp_path / "memory" / "prediction_log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_LOG, encoding="utf-8")
    html = render_html(build_inventory(cfg))
    assert 'id="tab-calibration"' in html
    assert "Brazil vs Mexico" in html
    assert "coin-flip = 0.667" in html


def test_calibration_empty_is_graceful(tmp_path):
    cal = _calibration(_cfg(tmp_path))                  # no log at all
    assert cal["resolved"] == [] and cal["mean_brier"] is None
    html = render_html(build_inventory(_cfg(tmp_path)))
    assert "no resolved predictions yet" in html
