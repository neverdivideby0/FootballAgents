"""Analyst report stage tests — TA's analyst-team analog (hermetic)."""

from __future__ import annotations

import copy
from types import SimpleNamespace

from worldcupagents.agents.schemas import Fixture, JudgeRead, Stage
from worldcupagents.config import DEFAULT_CONFIG
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


def test_offline_run_populates_all_three_reports(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, _ = Predictor(_cfg(tmp_path)).predict(fx)
    assert "Brazil" in final["form_report"] and "Expected goals" in final["form_report"]
    assert final["tactical_report"]      # no history -> explicit "(no analysed...)" text
    assert "no player stats" in final["player_report"]  # empty store digest


def test_reports_disabled_reduces_topology(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, v = Predictor(_cfg(tmp_path, enable_analyst_reports=False)).predict(fx)
    assert "form_report" not in final     # nodes never ran
    assert v.outcome is not None          # prediction still works


# --- reports reach the debate prompts ---

_PROMPTS: list[str] = []


class _FakeStructured:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        _PROMPTS.append(prompt)
        return {"raw": None, "parsed": self.result, "parsing_error": None}


class FakeLLM:
    def __init__(self, content="", read=None):
        self.content, self.read = content, read

    def invoke(self, prompt):
        _PROMPTS.append(prompt)
        return SimpleNamespace(content=self.content, usage_metadata={"input_tokens": 10, "output_tokens": 5})

    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(self.read)


def test_reports_reach_advocate_and_judge_prompts(tmp_path):
    _PROMPTS.clear()
    cfg = _cfg(tmp_path, use_llm=True)
    read = JudgeRead(p_home=0.5, p_draw=0.25, p_away=0.25, scoreline="1-0", confidence="medium")
    quick = FakeLLM(content="Case. Weaknesses: none.")
    deep = FakeLLM(read=read)
    Predictor(cfg, deep_llm=deep, quick_llm=quick).predict(
        Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    )
    advocate_prompts = [p for p in _PROMPTS if "Team Advocate" in p]
    judge_prompts = [p for p in _PROMPTS if "neutral football pundit" in p]
    assert advocate_prompts and all("FORM REPORT" in p for p in advocate_prompts)
    assert judge_prompts and "FORM REPORT" in judge_prompts[0] and "PLAYER REPORT" in judge_prompts[0]


def test_llm_polish_and_fallback(tmp_path):
    # analyst_reports_llm=True routes digests through the quick LLM...
    cfg = _cfg(tmp_path, use_llm=True, analyst_reports_llm=True, enable_scenario_debate=False)
    quick = FakeLLM(content="Polished analyst prose.")
    deep = FakeLLM(read=JudgeRead(p_home=0.4, p_draw=0.3, p_away=0.3, scoreline="1-1", confidence="low"))
    final, _ = Predictor(cfg, deep_llm=deep, quick_llm=quick).predict(
        Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    )
    assert final["form_report"] == "Polished analyst prose."

    # ...and a crashing LLM degrades to the raw digest, visibly.
    class BoomLLM:
        def invoke(self, prompt):
            raise RuntimeError("401")

        def with_structured_output(self, schema, **kwargs):
            return self

    final2, _ = Predictor(_cfg(tmp_path, use_llm=True, analyst_reports_llm=True,
                                enable_scenario_debate=False),
                          deep_llm=BoomLLM(), quick_llm=BoomLLM()).predict(
        Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    )
    assert final2["form_report"].startswith("[LLM unavailable]")
    assert "Expected goals" in final2["form_report"]   # digest preserved
