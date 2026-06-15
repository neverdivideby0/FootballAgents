"""Senior Scout agent — the "Contextual Performance Report" (wishlist feature E).

Blends a team's hard profile (squad, rank, form, xG) with the qualitative
tactical tendencies mined from analysed matches (memory/matches via recall).
Same patterns as the judge/analyst: structured output + token accounting +
graceful degradation (offline placeholder when use_llm is off or the LLM errors).
"""

from __future__ import annotations

import logging

from worldcupagents.agents.briefs import profile_brief
from worldcupagents.agents.schemas import MatchTacticalReport, PlayerStat, ScoutReport, TeamProfile

logger = logging.getLogger(__name__)


def _players_line(players: list[PlayerStat] | None) -> str:
    if not players:
        return ""
    from worldcupagents.recall import players_digest
    return "PLAYER METRICS (this competition):\n" + players_digest(players)


def make_senior_scout(config: dict, llm=None, usage_acc: dict | None = None):
    """Return a callable: (team, TeamProfile, [MatchTacticalReport], [PlayerStat]) -> ScoutReport."""
    use_llm = bool(config.get("use_llm")) and llm is not None

    def scout(team, profile, reports, players=None) -> ScoutReport:
        if not use_llm:
            return _placeholder_scout(team, profile, reports, players)
        try:
            return _llm_scout(llm, team, profile, reports, players, usage_acc)
        except Exception as e:  # noqa: BLE001 — visible degrade, never crash
            logger.warning("Senior scout LLM error for %r (%s); placeholder", team, e)
            return _placeholder_scout(team, profile, reports, players)

    return scout


def _tactical_digest(reports: list[MatchTacticalReport]) -> str:
    if not reports:
        return "(no analysed matches yet)"
    lines = []
    for r in reports:
        bits = []
        for p in r.phases:
            seg = p.formations_blocks[:1] + p.adjustments[:1]
            if seg:
                bits.append(f"{p.phase.split(' ', 1)[0]}: {', '.join(seg)}")
        lines.append(f"vs {r.away if r.home != r.away else '?'} ({r.date or 'undated'}): "
                     + ("; ".join(bits) if bits else "analysed"))
    return "\n".join(lines)


def _llm_scout(llm, team, profile, reports, players, usage_acc) -> ScoutReport:
    prompt = f"""You are a Senior Technical Scout writing a concise Contextual Performance Report on {team}.

HARD PROFILE:
{profile_brief(profile)}

{_players_line(players)}

TACTICAL TENDENCIES (from analysed matches — qualitative, may be sparse):
{_tactical_digest(reports)}

Blend the hard profile, player metrics and tactical evidence into a scouting report. Be
concrete and grounded — do NOT invent stats or matches not shown above. Provide: a short
summary, key strengths, exploitable weaknesses, recurring tactical tendencies, and the key
players (use the PLAYER METRICS above for the key players when present)."""

    chain = llm.with_structured_output(ScoutReport, include_raw=True)
    result = chain.invoke(prompt)
    raw = result.get("raw") if isinstance(result, dict) else None
    if usage_acc is not None and raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta:
            usage_acc["input"] += meta.get("input_tokens", 0)
            usage_acc["output"] += meta.get("output_tokens", 0)
    report = result.get("parsed") if isinstance(result, dict) else result
    report.team = team  # authoritative
    return report


def _placeholder_scout(team, profile, reports, players=None) -> ScoutReport:
    tendencies = []
    for r in reports:
        for p in r.phases:
            tendencies.extend(p.formations_blocks)
            tendencies.extend(p.adjustments)
    rank = f"FIFA #{profile.fifa_rank}" if profile.fifa_rank else "unranked"
    # Prefer real goal-contribution leaders over a raw squad slice.
    if players:
        key_players = [f"{p.player} ({p.goals}G/{p.assists}A)" for p in players]
    else:
        key_players = [pl.name for pl in profile.squad[:5]]
    return ScoutReport(
        team=team,
        summary=f"[placeholder] {team} ({rank}); {len(reports)} analysed match(es). "
                "Enable use_llm for a synthesised scouting report.",
        tactical_tendencies=list(dict.fromkeys(tendencies))[:8],  # de-duped, capped
        key_players=key_players,
    )
