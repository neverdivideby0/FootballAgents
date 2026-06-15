"""M-C tests — research-depth presets + streaming predictor (hermetic)."""

from __future__ import annotations

import copy

import pytest

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.config import DEFAULT_CONFIG, RESEARCH_DEPTH_PRESETS, apply_research_depth
from worldcupagents.graph.predict import Predictor


def _cfg(tmp_path, **overrides) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    cfg.update(overrides)
    return cfg


# ── depth presets ────────────────────────────────────────────────────────────

def test_presets_cover_three_depths():
    assert set(RESEARCH_DEPTH_PRESETS) == {"shallow", "medium", "deep"}


def test_apply_depth_shallow_disables_scenario():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    apply_research_depth(cfg, "shallow")
    assert cfg["max_debate_rounds"] == 1 and cfg["enable_scenario_debate"] is False


def test_apply_depth_deep_maxes_out():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    apply_research_depth(cfg, "DEEP")  # case-insensitive
    assert cfg["max_debate_rounds"] == 3
    assert cfg["enable_scenario_debate"] is True and cfg["max_scenario_rounds"] == 2
    assert cfg["analyst_reports_llm"] is True


def test_apply_depth_unknown_raises():
    with pytest.raises(ValueError):
        apply_research_depth({}, "abyssal")


# ── streaming predictor ──────────────────────────────────────────────────────

def test_predict_stream_matches_invoke_and_emits_events(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    events: list[str] = []

    p = Predictor(_cfg(tmp_path))
    final_s, v_s = p.predict_stream(fx, on_event=lambda node, delta: events.append(node))
    final_i, v_i = Predictor(_cfg(tmp_path)).predict(fx)

    # Same final verdict as the non-streaming path (offline = deterministic).
    assert v_s.outcome == v_i.outcome and v_s.scoreline == v_i.scoreline
    assert abs(v_s.p_home - v_i.p_home) < 1e-9

    # Events cover the whole topology in order.
    assert events[0] == "Build Dossiers"
    assert "Judge" in events and "Final Pundit" in events
    assert events.index("Judge") < events.index("Upside Pundit") < events.index("Final Pundit")
    assert events.count("Home Advocate") == 2          # max_debate_rounds=2


def test_predict_stream_callback_error_does_not_break_run(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)

    def explode(node, delta):
        raise RuntimeError("UI crashed")

    final, v = Predictor(_cfg(tmp_path)).predict_stream(fx, on_event=explode)
    assert v.outcome is not None                       # prediction survived the UI