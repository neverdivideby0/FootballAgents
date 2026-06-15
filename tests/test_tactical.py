"""Milestone 3 tests — tactical analyst agent (FakeLLM, no key/network)."""

from __future__ import annotations

from types import SimpleNamespace

from worldcupagents.agents.analyst.tactical import (
    build_report,
    make_match_id,
    make_tactical_analyzer,
)
from worldcupagents.agents.schemas import (
    PHASE_CRUNCH,
    PHASE_FIRST_HALF,
    CommentaryEntry,
    MatchEvent,
    PhaseChunk,
    PhaseTacticalInsight,
)


class _FakeStructured:
    def __init__(self, result, usage=None):
        self.result = result
        self.usage = usage

    def invoke(self, prompt):
        raw = SimpleNamespace(usage_metadata=self.usage) if self.usage else None
        return {"raw": raw, "parsed": self.result, "parsing_error": None}


class FakeLLM:
    def __init__(self, insight: PhaseTacticalInsight, usage=None):
        self.insight = insight
        self.usage = usage
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        self.calls += 1
        return _FakeStructured(self.insight, self.usage)


class BoomLLM:
    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, prompt):
        raise RuntimeError("401 no key")


def _chunk() -> PhaseChunk:
    return PhaseChunk(
        phase=PHASE_FIRST_HALF,
        entries=[CommentaryEntry(minute=23, text="23 min: Messi converts the penalty.")],
        events=[MatchEvent(minute=23, type="goal", detail="Messi (pen)")],
    )


def _cfg(use_llm=True):
    return {"use_llm": use_llm}


def test_analyst_returns_structured_insight_and_counts_tokens():
    model_out = PhaseTacticalInsight(
        phase="WRONG",  # the agent must overwrite this with the chunk's phase
        formations_blocks=["4-3-3 high press"],
        adjustments=["fullbacks pushing on"],
        key_matchups=["Messi vs Upamecano"],
        summary="Argentina dominant.",
    )
    usage = {"input_tokens": 800, "output_tokens": 120}
    llm = FakeLLM(model_out, usage=usage)
    acc = {"input": 0, "output": 0}

    analyze = make_tactical_analyzer(_cfg(), llm=llm, usage_acc=acc)
    insight = analyze(_chunk())

    assert llm.calls == 1
    assert insight.phase == PHASE_FIRST_HALF              # overwritten, not "WRONG"
    assert insight.formations_blocks == ["4-3-3 high press"]
    assert acc == {"input": 800, "output": 120}           # tokens accumulated


def test_analyst_placeholder_when_llm_off():
    analyze = make_tactical_analyzer(_cfg(use_llm=False))
    insight = analyze(_chunk())
    assert insight.phase == PHASE_FIRST_HALF
    assert "placeholder" in insight.summary.lower()
    assert "goals at 23'" in insight.summary


def test_analyst_degrades_on_llm_error():
    analyze = make_tactical_analyzer(_cfg(), llm=BoomLLM(), usage_acc={"input": 0, "output": 0})
    insight = analyze(_chunk())
    assert insight.phase == PHASE_FIRST_HALF
    assert "placeholder" in insight.summary.lower()        # visible degrade, no crash


def test_analyst_skips_empty_phase_without_calling_llm():
    llm = FakeLLM(PhaseTacticalInsight(phase="x"))
    analyze = make_tactical_analyzer(_cfg(), llm=llm)
    insight = analyze(PhaseChunk(phase=PHASE_CRUNCH))       # no entries, no events
    assert llm.calls == 0
    assert insight.phase == PHASE_CRUNCH
    assert "no commentary" in insight.summary.lower()


def test_build_report_assembles_all_phases():
    chunks = [
        PhaseChunk(phase=PHASE_FIRST_HALF, entries=[CommentaryEntry(minute=10, text="early pressure")]),
        PhaseChunk(phase=PHASE_CRUNCH),  # empty
    ]
    analyze = make_tactical_analyzer(_cfg(use_llm=False))
    report = build_report("Argentina", "France", "2022-12-18", chunks, analyze,
                          sources=["https://g/final"])
    assert report.match_id == "Argentina_vs_France_2022-12-18"
    assert [p.phase for p in report.phases] == [PHASE_FIRST_HALF, PHASE_CRUNCH]
    assert report.sources == ["https://g/final"]


def test_make_match_id_slugs_spaces_and_slashes():
    assert make_match_id("United States", "Korea/Republic", None) == "United_States_vs_Korea-Republic_undated"
