"""analyze_match — the post-game commentary pipeline (COMMENTARY_PLAN.md step F).

Linear orchestration (no LangGraph needed for a straight line):

    A. ingest   get_commentary_provider(config).fetch_match(home, away, date)
    B. chunk    chunk_commentary(lines, events)               -> 5 PhaseChunks
    C. analyze  make_tactical_analyzer(config, llm)           -> PhaseTacticalInsight per phase
    store       memory/matches/<id>.json  (+ .md for humans)

Offline by default (placeholder analyst, no spend). Set use_llm in config — e.g.
``worldcupagents analyze-match ... --provider openai`` — to run the real analyst.
Degrades gracefully at every step: a missing key, a failed fetch, or an LLM error
never crashes; you get a placeholder-grade report instead.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.analyst.tactical import build_report, make_tactical_analyzer
from worldcupagents.agents.schemas import MatchTacticalReport, PhaseChunk
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.commentary.chunker import chunk_commentary
from worldcupagents.dataflows.commentary.registry import get_commentary_provider
from worldcupagents.llm_clients.factory import create_llm
from worldcupagents.llm_clients.model_catalog import estimate_cost

logger = logging.getLogger(__name__)


@dataclass
class AnalyzeOutcome:
    report: MatchTacticalReport
    chunks: list[PhaseChunk]
    usage: dict
    cost: float | None
    model: str | None          # analyst model used (None when offline)
    json_path: Path | None
    md_path: Path | None


def analyze_match(
    home: str,
    away: str,
    date: str | None = None,
    config: dict | None = None,
    analyst_llm=None,
    persist: bool = True,
    force: bool = False,
) -> AnalyzeOutcome:
    """Run the post-game commentary pipeline for one fixture.

    force=False (default): if a JSON report already exists AND has populated
    tactical data (formations_blocks / adjustments / key_matchups), skip the
    LLM analysis and load the existing report instead of overwriting it. This
    prevents an accidental offline re-run from clobbering a good LLM report.
    force=True: always re-run and overwrite.
    """
    config = dict(config or DEFAULT_CONFIG)
    usage = {"input": 0, "output": 0}
    model = config.get("quick_think_llm", "") if config.get("use_llm") else None

    # Guard: if a populated report already exists and force=False, return it directly.
    if persist and not force:
        existing = _load_existing(home, away, date, config)
        if existing is not None:
            logger.info("analyze_match: loaded existing report for %s vs %s (use force=True to overwrite)", home, away)
            json_path, md_path = _persist_paths(home, away, date, config)
            return AnalyzeOutcome(existing, [], {}, None, None, json_path, md_path)

    # Build the analyst LLM (single-pass extraction -> the quick model). Injected
    # llm wins (tests); missing key degrades to the placeholder analyst.
    if config.get("use_llm") and analyst_llm is None:
        try:
            analyst_llm = create_llm(config["llm_provider"], model)
        except Exception as e:  # noqa: BLE001
            logger.warning("analyst LLM unavailable (%s); using placeholder analyst.", e)
            analyst_llm = None
            model = None

    # A. ingest  ->  B. chunk  ->  C. analyze
    feed = get_commentary_provider(config).fetch_match(home, away, date)
    chunks = chunk_commentary(feed.lines, feed.events)
    analyze = make_tactical_analyzer(config, analyst_llm, usage)
    report = build_report(home, away, date, chunks, analyze, sources=feed.sources)

    cost = estimate_cost(model, usage["input"], usage["output"]) if model else None
    json_path = md_path = None
    if persist:
        json_path, md_path = _persist(report, chunks, config)

    return AnalyzeOutcome(report, chunks, dict(usage), cost, model, json_path, md_path)


# ── persistence & guard helpers ──────────────────────────────────────────────

def _persist_paths(home: str, away: str, date: str | None, config: dict) -> tuple[Path, Path]:
    from worldcupagents.agents.analyst.tactical import make_match_id
    mid = make_match_id(home, away, date)
    out_dir = Path(config.get("memory_dir", "memory")) / "matches"
    return out_dir / f"{mid}.json", out_dir / f"{mid}.md"


def _load_existing(home: str, away: str, date: str | None, config: dict) -> MatchTacticalReport | None:
    """Return an existing report if it has real tactical content; else None."""
    json_path, _ = _persist_paths(home, away, date, config)
    if not json_path.exists():
        return None
    try:
        rep = MatchTacticalReport.model_validate_json(json_path.read_text(encoding="utf-8"))
        # Only protect reports that actually have LLM-extracted tactical data.
        has_content = any(
            p.formations_blocks or p.adjustments or p.key_matchups
            for p in rep.phases
        )
        return rep if has_content else None
    except Exception:  # noqa: BLE001
        return None


def _persist(report: MatchTacticalReport, chunks: list[PhaseChunk], config: dict) -> tuple[Path, Path]:
    out_dir = Path(config.get("memory_dir", "memory")) / "matches"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report.match_id}.json"
    md_path = out_dir / f"{report.match_id}.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(report, chunks), encoding="utf-8")
    return json_path, md_path


def _to_markdown(report: MatchTacticalReport, chunks: list[PhaseChunk]) -> str:
    events_by_phase = {c.phase: c.events for c in chunks}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Tactical Report — {report.home} vs {report.away} ({report.date or 'undated'})",
        "",
        f"_Generated {now}_  ",
        f"_Sources: {', '.join(report.sources) or '—'}_",
        "",
    ]
    for p in report.phases:
        lines.append(f"## {p.phase}")
        lines.append(p.summary or "_(no summary)_")
        if p.formations_blocks:
            lines.append(f"- **Formations / blocks:** {'; '.join(p.formations_blocks)}")
        if p.adjustments:
            lines.append(f"- **Adjustments:** {'; '.join(p.adjustments)}")
        if p.key_matchups:
            lines.append(f"- **Key matchups:** {'; '.join(p.key_matchups)}")
        evs = events_by_phase.get(p.phase) or []
        if evs:
            lines.append(f"- **Events:** {'; '.join(f'{e.minute}′ {e.type}: {e.detail}' for e in evs)}")
        lines.append("")
    return "\n".join(lines)
