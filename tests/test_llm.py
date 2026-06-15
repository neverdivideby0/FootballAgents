"""M2 tests — LLM advocates + judge, exercised with a fake LLM (no key/network)."""

from __future__ import annotations

import copy
from types import SimpleNamespace

from worldcupagents.agents.schemas import Fixture, JudgeRead, Outcome, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import Predictor


class _FakeStructured:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        # Mirror the include_raw=True dict that the real judge expects.
        return {"raw": None, "parsed": self.result, "parsing_error": None}


class FakeLLM:
    """Mimics the bits of a LangChain chat model we use."""

    def __init__(self, content: str, read: JudgeRead | None = None):
        self.content = content
        self.read = read
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)

    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(self.read)


class BoomLLM:
    def invoke(self, prompt):
        raise RuntimeError("401 no key")

    def with_structured_output(self, schema):
        return self

    # _FakeStructured-style .invoke is the same method above -> raises


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["use_llm"] = True
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")  # isolate the match store
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def test_llm_advocates_and_judge_drive_verdict(tmp_path):
    read = JudgeRead(
        p_home=0.2, p_draw=0.2, p_away=0.6, scoreline="0-2", confidence="high",
        key_factors=["away midfield control"], x_factors=["altitude"],
        rationale="Away side's press wins it.",
    )
    quick = FakeLLM(content="Spain are sharp. Weaknesses: shaky at the back.")
    deep = FakeLLM(content="", read=read)

    fx = Fixture(home="Spain", away="Brazil", stage=Stage.GROUP)
    final, v = Predictor(_cfg(tmp_path), deep_llm=deep, quick_llm=quick).predict(fx)

    # Advocate text came from the quick LLM.
    assert "Weaknesses:" in final["debate_state"]["history"]
    assert quick.prompts, "advocate LLM should have been invoked"
    # Judge read flowed through: away-leaning read + away-leaning baseline -> AWAY_WIN.
    assert v.outcome == Outcome.AWAY_WIN
    # Scoreline comes from the Poisson grid, restricted to the outcome -> must be
    # an away win (home goals < away goals), and free-form (not a fixed template).
    hg, ag = (int(x) for x in v.scoreline.split()[0].split("-"))
    assert ag > hg, f"AWAY_WIN scoreline should have away > home, got {v.scoreline}"
    assert v.rationale == "Away side's press wins it."
    assert abs(v.p_home + v.p_draw + v.p_away - 1.0) < 1e-6

    # Ensemble transparency: breakdown captured, and the judge read matches what we fed.
    b = v.breakdown
    assert b is not None and b.judge_weight == 0.6
    assert (round(b.judge_home, 2), round(b.judge_draw, 2), round(b.judge_away, 2)) == (0.2, 0.2, 0.6)
    # Group stage (no draw-fold): final home prob is the weighted blend of judge & baseline.
    expected_home = b.judge_weight * b.judge_home + (1 - b.judge_weight) * b.base_home
    assert abs(v.p_home - expected_home) < 1e-6


def test_judge_degrades_to_baseline_on_llm_error(tmp_path):
    fx = Fixture(home="Argentina", away="USA", stage=Stage.GROUP)
    final, v = Predictor(_cfg(tmp_path), deep_llm=BoomLLM(), quick_llm=BoomLLM()).predict(fx)
    # Both advocate and judge LLMs raise -> visible degradation, no crash.
    assert "[LLM unavailable]" in final["debate_state"]["history"]
    assert "Baseline-only verdict" in v.rationale
    assert v.outcome in Outcome
