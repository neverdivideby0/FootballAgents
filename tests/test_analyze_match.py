"""Milestone 4 tests — analyze_match pipeline (hermetic: placeholder, no network/LLM)."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from worldcupagents.agents.schemas import PHASE_LABELS, PhaseTacticalInsight
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.pipelines.analyze_match import analyze_match


def _cfg(tmp_path, use_llm=False) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["use_llm"] = use_llm
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["data_vendors"]["commentary"] = "placeholder"  # offline, deterministic
    return cfg


def test_offline_pipeline_writes_report(tmp_path):
    out = analyze_match("Argentina", "France", "2022-12-18", _cfg(tmp_path))

    # 5 phases, in canonical order, persisted as json + md.
    assert [p.phase for p in out.report.phases] == PHASE_LABELS
    assert out.report.match_id == "Argentina_vs_France_2022-12-18"
    assert out.json_path.exists() and out.md_path.exists()
    assert out.model is None and out.cost is None        # offline -> no spend

    saved = json.loads(out.json_path.read_text())
    assert saved["home"] == "Argentina" and len(saved["phases"]) == 5

    md = out.md_path.read_text()
    assert "# Tactical Report" in md and "Initial Setup" in md


def test_offline_pipeline_surfaces_goal_events_in_md(tmp_path):
    out = analyze_match("Argentina", "France", "2022-12-18", _cfg(tmp_path))
    # The bundled sample has goals; they should appear as typed events in the md.
    assert "goal:" in out.md_path.read_text()


# --- injected real analyst (FakeLLM) without touching the network ---

class _FakeStructured:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        raw = SimpleNamespace(usage_metadata={"input_tokens": 500, "output_tokens": 90})
        return {"raw": raw, "parsed": self.result, "parsing_error": None}


class FakeLLM:
    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(
            PhaseTacticalInsight(phase="x", formations_blocks=["4-4-2 mid block"], summary="Tactical read.")
        )


def test_pipeline_with_injected_llm_runs_analyst_and_tracks_cost(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    cfg["llm_provider"] = "openai"
    cfg["quick_think_llm"] = "gpt-5-nano"

    out = analyze_match("Argentina", "France", "2022-12-18", cfg, analyst_llm=FakeLLM())

    # Non-empty phases got the structured read; tokens + cost accumulated.
    analyzed = [p for p in out.report.phases if p.formations_blocks]
    assert analyzed and analyzed[0].formations_blocks == ["4-4-2 mid block"]
    assert out.usage["input"] > 0 and out.usage["output"] > 0
    assert out.model == "gpt-5-nano" and out.cost is not None and out.cost > 0


def test_pipeline_no_persist_returns_report_without_writing(tmp_path):
    out = analyze_match("Spain", "Brazil", None, _cfg(tmp_path), persist=False)
    assert out.json_path is None and out.md_path is None
    assert len(out.report.phases) == 5
