"""run_critic (wishlist feature D) — assemble + run the Critic Loop for one team.

Gathers the team's enriched profile (stats/xG/form) + tactical memory (recall),
runs the Critic agent, and persists to memory/critic/<team>.{json,md}.
Offline by default; --provider/--llm runs the real cross-examination.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.analyst.critic import make_critic
from worldcupagents.agents.schemas import CriticReport
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.enrich import enrich_profile
from worldcupagents.dataflows.interface import get_provider
from worldcupagents.dataflows.names import canonical_name, normalize_key
from worldcupagents.llm_clients.factory import create_llm
from worldcupagents.llm_clients.model_catalog import estimate_cost
from worldcupagents.recall import reports_for_team, top_players

logger = logging.getLogger(__name__)


@dataclass
class CriticOutcome:
    report: CriticReport
    usage: dict
    cost: float | None
    model: str | None
    json_path: Path | None
    md_path: Path | None


def run_critic(team: str, config: dict | None = None, critic_llm=None, persist: bool = True) -> CriticOutcome:
    config = dict(config or DEFAULT_CONFIG)
    usage = {"input": 0, "output": 0}
    model = config.get("deep_think_llm", "") if config.get("use_llm") else None

    if config.get("use_llm") and critic_llm is None:
        try:
            critic_llm = create_llm(config["llm_provider"], model)
        except Exception as e:  # noqa: BLE001
            logger.warning("critic LLM unavailable (%s); using placeholder critic.", e)
            critic_llm, model = None, None

    profile = enrich_profile(get_provider(config, "squads").get_team_profile(team), config)
    reports = reports_for_team(team, config)
    players = top_players(team, config)

    critic = make_critic(config, critic_llm, usage)
    report = critic(team, profile, reports, players)
    report.sources = list(profile.sources) + [r.match_id for r in reports]

    cost = estimate_cost(model, usage["input"], usage["output"]) if model else None
    json_path = md_path = None
    if persist:
        json_path, md_path = _persist(report, config)
    return CriticOutcome(report, dict(usage), cost, model, json_path, md_path)


def _persist(report: CriticReport, config: dict) -> tuple[Path, Path]:
    out_dir = Path(config.get("memory_dir", "memory")) / "critic"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = normalize_key(canonical_name(report.team))
    json_path = out_dir / f"{slug}.json"
    md_path = out_dir / f"{slug}.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _to_markdown(r: CriticReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Critic Report — {r.team}", f"\n_Generated {now}_\n", f"{r.summary or '_(no summary)_'}\n", "## Findings"]
    if r.findings:
        for f in r.findings:
            lines.append(f"- **{f.metric}** ← {f.commentary}\n  - → {f.insight}")
    else:
        lines.append("_(none)_")
    lines.append("\n## Tensions")
    lines.append("\n".join(f"- {t}" for t in r.tensions) if r.tensions else "_(none)_")
    lines.append(f"\n_Sources: {', '.join(r.sources) or '—'}_")
    return "\n".join(lines)
