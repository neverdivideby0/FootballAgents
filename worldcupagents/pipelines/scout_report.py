"""generate_scout_report — assemble a Senior-Scout report for one team.

Gathers the team's hard profile (data vendor) + tactical memory (recall), runs
the Senior Scout agent, and persists to memory/scouting/<team>.{json,md}.
Offline by default; --provider/--llm runs the real synthesis. Degrades
gracefully at every step.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.analyst.scout import make_senior_scout
from worldcupagents.agents.schemas import ScoutReport
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.enrich import enrich_profile
from worldcupagents.dataflows.interface import get_provider
from worldcupagents.dataflows.names import canonical_name, normalize_key
from worldcupagents.llm_clients.factory import create_llm
from worldcupagents.llm_clients.model_catalog import estimate_cost
from worldcupagents.recall import reports_for_team, top_players

logger = logging.getLogger(__name__)


@dataclass
class ScoutOutcome:
    report: ScoutReport
    usage: dict
    cost: float | None
    model: str | None
    json_path: Path | None
    md_path: Path | None


def generate_scout_report(
    team: str,
    config: dict | None = None,
    scout_llm=None,
    persist: bool = True,
) -> ScoutOutcome:
    config = dict(config or DEFAULT_CONFIG)
    usage = {"input": 0, "output": 0}
    model = config.get("deep_think_llm", "") if config.get("use_llm") else None

    if config.get("use_llm") and scout_llm is None:
        try:
            scout_llm = create_llm(config["llm_provider"], model)
        except Exception as e:  # noqa: BLE001
            logger.warning("scout LLM unavailable (%s); using placeholder scout.", e)
            scout_llm, model = None, None

    profile = enrich_profile(get_provider(config, "squads").get_team_profile(team), config)
    reports = reports_for_team(team, config)
    players = top_players(team, config)

    scout = make_senior_scout(config, scout_llm, usage)
    report = scout(team, profile, reports, players)
    report.sources = list(profile.sources) + [r.match_id for r in reports]

    cost = estimate_cost(model, usage["input"], usage["output"]) if model else None
    json_path = md_path = None
    if persist:
        json_path, md_path = _persist(report, config)
    return ScoutOutcome(report, dict(usage), cost, model, json_path, md_path)


def _persist(report: ScoutReport, config: dict) -> tuple[Path, Path]:
    out_dir = Path(config.get("memory_dir", "memory")) / "scouting"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = normalize_key(canonical_name(report.team))
    json_path = out_dir / f"{slug}.json"
    md_path = out_dir / f"{slug}.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _to_markdown(r: ScoutReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def section(title, items):
        return f"## {title}\n" + ("\n".join(f"- {i}" for i in items) if items else "_(none recorded)_") + "\n"

    return "\n".join([
        f"# Scouting Report — {r.team}",
        f"\n_Generated {now}_\n",
        f"{r.summary or '_(no summary)_'}\n",
        section("Strengths", r.strengths),
        section("Weaknesses", r.weaknesses),
        section("Tactical tendencies", r.tactical_tendencies),
        section("Key players", r.key_players),
        f"\n_Sources: {', '.join(r.sources) or '—'}_",
    ])
