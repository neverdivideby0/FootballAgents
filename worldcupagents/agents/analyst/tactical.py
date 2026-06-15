"""Tactical analyst (COMMENTARY_PLAN.md step C) — analog of the judge agent.

Reads one PhaseChunk (commentary + events for a phase) and returns a structured
PhaseTacticalInsight: formations/blocks, tactical adjustments, key player
matchups, and a short summary. Same patterns as pundit.py:
  * structured output via with_structured_output(..., include_raw=True)
  * token accumulation into a shared usage_acc dict
  * graceful degradation — use_llm off OR an LLM error yields a deterministic
    placeholder insight, never a crash.
"""

from __future__ import annotations

import logging

from worldcupagents.agents.schemas import (
    MatchTacticalReport,
    PhaseChunk,
    PhaseTacticalInsight,
)

logger = logging.getLogger(__name__)

_MAX_BRIEF_CHARS = 2200   # cap prompt size; Crunch phases can be large (ET + pens)
_MAX_BRIEF_LINES = 45


def make_tactical_analyzer(config: dict, llm=None, usage_acc: dict | None = None):
    """Return a callable: PhaseChunk -> PhaseTacticalInsight.

    usage_acc: optional mutable {"input": int, "output": int} for token tracking.
    """
    use_llm = bool(config.get("use_llm")) and llm is not None

    def analyze_phase(chunk: PhaseChunk) -> PhaseTacticalInsight:
        if not chunk.entries and not chunk.events:
            return PhaseTacticalInsight(phase=chunk.phase, summary="(no commentary for this phase)")
        if not use_llm:
            return _placeholder_insight(chunk)
        try:
            return _llm_insight(llm, chunk, usage_acc)
        except Exception as e:  # noqa: BLE001 — visible degrade, never crash the pipeline
            logger.warning("Tactical analyst LLM error for phase %r (%s); placeholder", chunk.phase, e)
            return _placeholder_insight(chunk)

    return analyze_phase


def build_report(
    home: str,
    away: str,
    date: str | None,
    chunks: list[PhaseChunk],
    analyze_phase,
    sources: list[str] | None = None,
) -> MatchTacticalReport:
    """Run the analyzer over every phase and assemble the full match report."""
    return MatchTacticalReport(
        match_id=make_match_id(home, away, date),
        home=home,
        away=away,
        date=date,
        phases=[analyze_phase(c) for c in chunks],
        sources=sources or [],
    )


def make_match_id(home: str, away: str, date: str | None) -> str:
    slug = lambda s: s.replace(" ", "_").replace("/", "-")  # noqa: E731
    return f"{slug(home)}_vs_{slug(away)}_{date or 'undated'}"


# ── internals ────────────────────────────────────────────────────────────────

def _phase_brief(chunk: PhaseChunk) -> str:
    """Compact the chunk into prompt text, capped to control token use."""
    lines: list[str] = []
    if chunk.events:
        evs = "; ".join(f"{e.minute}' {e.type}: {e.detail}" for e in chunk.events)
        lines.append(f"EVENTS: {evs}")
    for e in chunk.entries[:_MAX_BRIEF_LINES]:
        stamp = f"{e.minute}' " if e.minute is not None else ""
        lines.append(f"{stamp}{e.text}")
    brief = "\n".join(lines)
    return brief[:_MAX_BRIEF_CHARS]


def _llm_insight(llm, chunk: PhaseChunk, usage_acc: dict | None) -> PhaseTacticalInsight:
    prompt = f"""You are a seasoned football tactical analyst reviewing ONE phase of a match.

PHASE: {chunk.phase}

COMMENTARY + EVENTS:
{_phase_brief(chunk)}

Working ONLY from the text above (do not invent details that aren't supported by it), extract:
- formations_blocks: formations and defensive/pressing blocks in play (e.g. 'low block', '4-3-3 high press').
- adjustments: notable tactical changes (e.g. 'winger shifting inside', 'switched to a back five', a substitution that changes shape).
- key_matchups: individual player-vs-player matchups the commentary highlights.
- summary: 2-3 sentences capturing the tactical story of THIS phase.
Return an empty list for any field the commentary does not support."""

    chain = llm.with_structured_output(PhaseTacticalInsight, include_raw=True)
    result = chain.invoke(prompt)

    raw = result.get("raw") if isinstance(result, dict) else None
    if usage_acc is not None and raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta:
            usage_acc["input"] += meta.get("input_tokens", 0)
            usage_acc["output"] += meta.get("output_tokens", 0)

    insight = result.get("parsed") if isinstance(result, dict) else result
    insight.phase = chunk.phase  # authoritative — never trust the model for the label
    return insight


def _placeholder_insight(chunk: PhaseChunk) -> PhaseTacticalInsight:
    """Deterministic offline insight — visible, sourced from the chunk, no LLM."""
    goals = [f"{e.minute}'" for e in chunk.events if e.type == "goal"]
    bits = [f"{len(chunk.entries)} commentary beats"]
    if chunk.events:
        bits.append(f"{len(chunk.events)} events")
    if goals:
        bits.append(f"goals at {', '.join(goals)}")
    return PhaseTacticalInsight(
        phase=chunk.phase,
        summary=f"[placeholder] {chunk.phase}: {'; '.join(bits)}. Enable use_llm for tactical extraction.",
    )
