"""M-D tests — the closed memory loop: reflections written at resolve, lessons
read back into the next prediction's prompts (hermetic)."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from worldcupagents.agents.schemas import Fixture, JudgeRead, Outcome, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import _ENTRY_SEP, Predictor
from worldcupagents.graph.reflection import resolve_prediction
from worldcupagents.recall import past_context_for, prediction_lessons


def _cfg(tmp_path, **overrides) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    cfg.update(overrides)
    return cfg


def _seed_log(cfg, home="Argentina", away="Brazil"):
    p = Path(cfg["prediction_log_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"[2026-06-20 | {home} vs {away} | HOME_WIN 2-1 | pending]\n\n"
        "PREDICTION:\nArgentina edge a tight one.\n"
        "(p_home=0.600, p_draw=0.250, p_away=0.150)"
    )
    p.write_text(entry + _ENTRY_SEP, encoding="utf-8")
    return p


class ReflectLLM:
    def invoke(self, prompt):
        return SimpleNamespace(
            content="The home call was right but over-confident. Lesson: trust the favourite, trim the edge.",
            usage_metadata={"input_tokens": 100, "output_tokens": 30},
        )


class BoomLLM:
    def invoke(self, prompt):
        raise RuntimeError("401")


# ── reflection writing ───────────────────────────────────────────────────────

def test_llm_reflection_written_to_log_and_dossier(tmp_path):
    cfg = _cfg(tmp_path)
    log = _seed_log(cfg)
    res = resolve_prediction("Argentina", "Brazil", Outcome.HOME_WIN, cfg, "2-1",
                             reflect_llm=ReflectLLM())
    assert "trim the edge" in res["reflection"]
    text = log.read_text()
    assert "REFLECTION: The home call was right" in text
    teams = Path(cfg["memory_dir"]) / "teams"
    assert "trim the edge" in (teams / "argentina.md").read_text()


def test_reflection_llm_error_degrades_gracefully(tmp_path):
    cfg = _cfg(tmp_path)
    log = _seed_log(cfg)
    res = resolve_prediction("Argentina", "Brazil", Outcome.DRAW, cfg, "1-1",
                             reflect_llm=BoomLLM())
    assert res["found"] is True and res["reflection"] is None
    assert "REFLECTION:" not in log.read_text()   # resolved, just without a reflection


# ── lesson recall ────────────────────────────────────────────────────────────

def _resolve_with_reflection(cfg, home, away, actual=Outcome.HOME_WIN, score="2-1"):
    resolve_prediction(home, away, actual, cfg, score, reflect_llm=ReflectLLM())


def test_prediction_lessons_same_and_cross_team(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_log(cfg, "Argentina", "Brazil")
    _resolve_with_reflection(cfg, "Argentina", "Brazil")
    # a second, unrelated resolved match -> cross-team lesson
    p = Path(cfg["prediction_log_path"])
    p.write_text(p.read_text() + (
        "[2026-06-21 | France vs Spain | DRAW 1-1 | pending]\n\n"
        "PREDICTION:\nTight.\n(p_home=0.300, p_draw=0.400, p_away=0.300)"
    ) + _ENTRY_SEP, encoding="utf-8")
    _resolve_with_reflection(cfg, "France", "Spain", Outcome.DRAW, "0-0")

    lessons = prediction_lessons("Argentina", "Mexico", cfg)
    assert "LESSONS FROM PAST PREDICTIONS" in lessons
    assert "These teams:" in lessons and "Argentina vs Brazil" in lessons
    assert "Other matches" in lessons and "France vs Spain" in lessons
    assert "Lesson: The home call was right" in lessons


def test_lessons_empty_without_resolved_entries(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_log(cfg)   # pending only — nothing resolved
    assert prediction_lessons("Argentina", "Brazil", cfg) == ""


def test_past_context_combines_brief_and_lessons(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_log(cfg)
    _resolve_with_reflection(cfg, "Argentina", "Brazil")
    ctx = past_context_for("Argentina", "Brazil", cfg)
    assert "LESSONS FROM PAST PREDICTIONS" in ctx   # lessons present even with no tactical brief


# ── read-back into the next prediction's prompts ────────────────────────────

def test_lessons_reach_judge_and_final_pundit_prompts(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    _seed_log(cfg)
    _resolve_with_reflection(cfg, "Argentina", "Brazil")

    prompts: list[str] = []

    class _FakeStructured:
        def __init__(self, result):
            self.result = result

        def invoke(self, prompt):
            prompts.append(prompt)
            return {"raw": None, "parsed": self.result, "parsing_error": None}

    class DeepLLM:
        def with_structured_output(self, schema, **kwargs):
            return _FakeStructured(
                JudgeRead(p_home=0.5, p_draw=0.3, p_away=0.2, scoreline="2-1", confidence="medium")
            )

    class QuickLLM:
        def invoke(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(content="Case. Weaknesses: none.",
                                   usage_metadata={"input_tokens": 1, "output_tokens": 1})

    fx = Fixture(home="Argentina", away="Brazil", stage=Stage.GROUP)
    Predictor(cfg, deep_llm=DeepLLM(), quick_llm=QuickLLM()).predict(fx)

    judge_prompts = [p for p in prompts if "neutral football pundit" in p]
    final_prompts = [p for p in prompts if "FINAL pundit" in p]
    assert judge_prompts and "LESSONS FROM PAST PREDICTIONS" in judge_prompts[0]
    assert final_prompts and "LESSONS FROM PAST PREDICTIONS" in final_prompts[0]
    assert "trim the edge" in judge_prompts[0]   # the reflection itself is visible
