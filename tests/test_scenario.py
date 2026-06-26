"""Scenario debate + Final Pundit tests — TA's risk-team analog (hermetic)."""

from __future__ import annotations

import copy
from types import SimpleNamespace

from worldcupagents.agents.schemas import Fixture, JudgeRead, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.conditional_logic import ConditionalLogic
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


# ── rotation logic (pure) ────────────────────────────────────────────────────

def test_scenario_rotation_and_cap():
    logic = ConditionalLogic(max_scenario_rounds=1)
    s = lambda count, speaker: {"scenario_debate_state": {"count": count, "latest_speaker": speaker}}  # noqa: E731
    assert logic.should_continue_scenario(s(0, "")) == "Upside Pundit"
    assert logic.should_continue_scenario(s(1, "Upside")) == "Downside Pundit"
    assert logic.should_continue_scenario(s(2, "Downside")) == "Neutral Pundit"
    assert logic.should_continue_scenario(s(3, "Neutral")) == "Final Pundit"   # cap = 3*1


def test_scenario_two_rounds_continues_past_first_cycle():
    logic = ConditionalLogic(max_scenario_rounds=2)
    state = {"scenario_debate_state": {"count": 3, "latest_speaker": "Neutral"}}
    assert logic.should_continue_scenario(state) == "Upside Pundit"            # round 2 starts


# ── offline end-to-end ───────────────────────────────────────────────────────

def test_offline_scenario_runs_rotation_and_passes_verdict_through(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, v = Predictor(_cfg(tmp_path)).predict(fx)

    sd = final["scenario_debate_state"]
    assert sd["count"] == 3                                    # 3 * max_scenario_rounds(1)
    h = sd["history"]
    assert h.index("Upside Pundit") < h.index("Downside Pundit") < h.index("Neutral Pundit")
    # Offline final pundit is a pass-through: final verdict IS the provisional one.
    assert final["provisional_verdict"] == v


def test_scenario_disabled_no_pundit_turns(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, _ = Predictor(_cfg(tmp_path, enable_scenario_debate=False)).predict(fx)
    assert final["scenario_debate_state"]["count"] == 0        # seeded but never advanced


# ── LLM paths (FakeLLM) ──────────────────────────────────────────────────────

class _FakeStructured:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        return {"raw": None, "parsed": self.result, "parsing_error": None}


class SequencedDeepLLM:
    """Returns reads in order: judge gets the first, final pundit the second."""

    def __init__(self, *reads):
        self.reads = list(reads)

    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(self.reads.pop(0))


class FakeQuickLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, prompt):
        return SimpleNamespace(content=self.content, usage_metadata={"input_tokens": 10, "output_tokens": 5})


def test_final_pundit_read_drives_final_verdict_with_blend(tmp_path):
    judge_read = JudgeRead(p_home=0.34, p_draw=0.33, p_away=0.33, scoreline="1-1", confidence="low")
    final_read = JudgeRead(p_home=0.7, p_draw=0.2, p_away=0.1, scoreline="2-0", confidence="high",
                           rationale="Downside pundit's depth argument held.")
    deep = SequencedDeepLLM(judge_read, final_read)
    quick = FakeQuickLLM("Punchy point. Weaknesses: none.")

    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    # Blend math is a STATS-path property; agents mode would use the read verbatim.
    cfg = _cfg(tmp_path, use_llm=True, verdict_mode="stats")
    final, v = Predictor(cfg, deep_llm=deep, quick_llm=quick).predict(fx)

    prov = final["provisional_verdict"]
    assert v != prov                                         # the final pundit moved the verdict
    assert v.p_home > prov.p_home                            # toward its more confident home read
    # Blend math: final p_home = w*read + (1-w)*base, same baseline as provisional.
    b = v.breakdown
    expected_home = b.judge_weight * b.judge_home + (1 - b.judge_weight) * b.base_home
    assert abs(v.p_home - expected_home) < 1e-6
    assert (b.base_home, b.base_draw, b.base_away) == (
        prov.breakdown.base_home, prov.breakdown.base_draw, prov.breakdown.base_away
    )
    assert v.rationale == "Downside pundit's depth argument held."


def test_final_pundit_llm_error_falls_back_to_provisional(tmp_path):
    judge_read = JudgeRead(p_home=0.5, p_draw=0.3, p_away=0.2, scoreline="2-1", confidence="medium")

    class JudgeOnlyDeep:
        """Judge call succeeds; the final pundit's call explodes."""

        def __init__(self):
            self.calls = 0

        def with_structured_output(self, schema, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _FakeStructured(judge_read)
            raise RuntimeError("rate limited")

    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, v = Predictor(_cfg(tmp_path, use_llm=True),
                         deep_llm=JudgeOnlyDeep(), quick_llm=FakeQuickLLM("x. Weaknesses: y.")).predict(fx)
    assert v == final["provisional_verdict"]                 # graceful pass-through


def test_knockout_final_verdict_has_no_draw(tmp_path):
    judge_read = JudgeRead(p_home=0.4, p_draw=0.3, p_away=0.3, scoreline="1-1", confidence="low")
    final_read = JudgeRead(p_home=0.45, p_draw=0.2, p_away=0.35, scoreline="2-1", confidence="medium")
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.QF)
    _, v = Predictor(_cfg(tmp_path, use_llm=True),
                     deep_llm=SequencedDeepLLM(judge_read, final_read),
                     quick_llm=FakeQuickLLM("x. Weaknesses: y.")).predict(fx)
    assert v.p_draw == 0.0                                   # knockout fold applied by the final pundit too
    assert abs(v.p_home + v.p_away - 1.0) < 1e-6


def test_scenario_pundits_see_provisional_verdict_in_prompt(tmp_path):
    prompts: list[str] = []

    class CapturingQuick:
        def invoke(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(content="Point. Weaknesses: none.",
                                   usage_metadata={"input_tokens": 1, "output_tokens": 1})

    judge_read = JudgeRead(p_home=0.6, p_draw=0.25, p_away=0.15, scoreline="2-0", confidence="high")
    final_read = JudgeRead(p_home=0.55, p_draw=0.27, p_away=0.18, scoreline="2-1", confidence="medium")
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    Predictor(_cfg(tmp_path, use_llm=True),
              deep_llm=SequencedDeepLLM(judge_read, final_read),
              quick_llm=CapturingQuick()).predict(fx)

    pundit_prompts = [p for p in prompts if "PROVISIONAL VERDICT" in p]
    assert len(pundit_prompts) == 3                          # one per pundit turn
    assert any("UPSIDE" in p for p in pundit_prompts)
    assert any("DOWNSIDE" in p for p in pundit_prompts)
    assert all("HOME_WIN" in p for p in pundit_prompts)      # the provisional call is visible
