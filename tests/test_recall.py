"""predictive_brief / recall tests + predict integration (hermetic, no network/LLM)."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from worldcupagents.agents.schemas import (
    Fixture,
    JudgeRead,
    MatchTacticalReport,
    PhaseTacticalInsight,
    Stage,
)
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import Predictor
from worldcupagents.pipelines.qualitative_data import ingest_manual_note
from worldcupagents.recall import past_context_for, predictive_brief, reports_for_team


def _write_report(tmp_path, home, away, date, insights):
    rep = MatchTacticalReport(
        match_id=f"{home}_vs_{away}_{date}", home=home, away=away, date=date,
        phases=insights, sources=["test"],
    )
    d = tmp_path / "memory" / "matches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rep.match_id}.json").write_text(json.dumps(rep.model_dump(mode="json")), encoding="utf-8")


def _cfg(tmp_path, use_llm=False):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["use_llm"] = use_llm
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")  # isolate the match store
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


# ── recall layer ─────────────────────────────────────────────────────────────

def test_predictive_brief_empty_when_no_memory(tmp_path):
    assert predictive_brief("Argentina", "France", _cfg(tmp_path)) == ""


def test_reports_for_team_matches_home_or_away_with_aliases(tmp_path):
    cfg = _cfg(tmp_path)
    _write_report(tmp_path, "United States", "Mexico", "2026-06-12",
                  [PhaseTacticalInsight(phase="15-45 First-Half Shift", summary="s")])
    # "USA" should resolve to "United States" via canonical_name.
    assert len(reports_for_team("USA", cfg)) == 1
    assert len(reports_for_team("Mexico", cfg)) == 1
    assert len(reports_for_team("Brazil", cfg)) == 0


def test_predictive_brief_includes_tactical_digest(tmp_path):
    cfg = _cfg(tmp_path)
    _write_report(tmp_path, "Argentina", "France", "2022-12-18", [
        PhaseTacticalInsight(phase="15-45 First-Half Shift",
                             formations_blocks=["4-3-3 high press"],
                             key_matchups=["Messi vs Upamecano"], summary="dominant"),
        PhaseTacticalInsight(phase="75-90+ Crunch Time",
                             adjustments=["switched to a back five"], summary="held on"),
    ])
    brief = predictive_brief("Argentina", "Brazil", cfg)
    assert "PRE-MATCH TACTICAL BRIEF" in brief
    assert "Argentina — 1 prior match" in brief
    assert "4-3-3 high press" in brief and "back five" in brief
    assert "Brazil: no analysed match history yet." in brief   # one side empty is fine


def test_past_context_includes_qualitative_warehouse_notes(tmp_path):
    cfg = _cfg(tmp_path)
    ingest_manual_note(
        "Mexico press high but can leave transition space behind the fullbacks.",
        config=cfg,
        teams=["Mexico"],
        title="Mexico manual note",
        date="2026-06-11",
    )
    ctx = past_context_for("Mexico", "South Africa", cfg)
    assert "QUALITATIVE BRIEF" in ctx
    assert "transition space" in ctx
    assert "Mexico manual note" in ctx


# ── predict integration ──────────────────────────────────────────────────────

def test_predict_injects_past_context_offline(tmp_path):
    cfg = _cfg(tmp_path)
    _write_report(tmp_path, "Argentina", "France", "2022-12-18",
                  [PhaseTacticalInsight(phase="75-90+ Crunch Time",
                                        adjustments=["dropped deep"], summary="late siege")])
    fx = Fixture(home="Argentina", away="France", stage=Stage.GROUP)
    final, _ = Predictor(cfg).predict(fx)
    assert "PRE-MATCH TACTICAL BRIEF" in final["past_context"]
    assert "dropped deep" in final["past_context"]


# advocate + judge prompts must actually CARRY the brief to the model
class _FakeStructured:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        _PROMPTS.append(prompt)
        return {"raw": None, "parsed": self.result, "parsing_error": None}


_PROMPTS: list[str] = []


class FakeLLM:
    def __init__(self, content, read=None):
        self.content, self.read = content, read

    def invoke(self, prompt):
        _PROMPTS.append(prompt)
        return SimpleNamespace(content=self.content, usage_metadata={"input_tokens": 10, "output_tokens": 5})

    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(self.read)


def test_past_context_reaches_advocate_and_judge_prompts(tmp_path):
    _PROMPTS.clear()
    cfg = _cfg(tmp_path, use_llm=True)
    _write_report(tmp_path, "Argentina", "France", "2022-12-18",
                  [PhaseTacticalInsight(phase="15-45 First-Half Shift",
                                        formations_blocks=["4-4-2 mid block"], summary="x")])
    read = JudgeRead(p_home=0.5, p_draw=0.25, p_away=0.25, scoreline="1-0", confidence="medium")
    quick = FakeLLM(content="Case. Weaknesses: none.")
    deep = FakeLLM(content="", read=read)

    fx = Fixture(home="Argentina", away="France", stage=Stage.GROUP)
    Predictor(cfg, deep_llm=deep, quick_llm=quick).predict(fx)

    # At least one advocate prompt and the judge prompt carried the brief.
    assert any("TACTICAL HISTORY" in p and "4-4-2 mid block" in p for p in _PROMPTS)
