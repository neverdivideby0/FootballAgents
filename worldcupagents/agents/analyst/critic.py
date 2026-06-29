"""Critic Loop agent (wishlist feature D) — quant vs qual cross-examination.

Compares a team's QUANTITATIVE metrics (xG, form, goals — from the enriched
profile / match store) against its QUALITATIVE tactical insights (formations,
adjustments, matchups — from memory/matches via the analyst) to surface deep
context: why the numbers look the way they do, and where stats and narrative
disagree. Team-level for now (per-player metrics await the club-stats source).

Same patterns as the other agents: structured output + token accounting +
graceful degradation (offline placeholder / LLM-error fallback).
"""

from __future__ import annotations

import logging

from worldcupagents.agents.schemas import (
    CriticFinding,
    CriticReport,
    MatchTacticalReport,
    TeamProfile,
)

logger = logging.getLogger(__name__)


def make_critic(config: dict, llm=None, usage_acc: dict | None = None):
    """Return a callable: (team, TeamProfile, [MatchTacticalReport], [PlayerStat]) -> CriticReport."""
    use_llm = bool(config.get("use_llm")) and llm is not None

    def critic(team, profile, reports, players=None) -> CriticReport:
        if not use_llm:
            return _placeholder_critic(team, profile, reports, players)
        try:
            return _llm_critic(llm, team, profile, reports, players, usage_acc)
        except Exception as e:  # noqa: BLE001
            logger.warning("Critic LLM error for %r (%s); placeholder", team, e)
            return _placeholder_critic(team, profile, reports, players)

    return critic


def _players_line(players) -> str:
    if not players:
        return ""
    from worldcupagents.recall import players_digest
    return "PLAYER METRICS:\n" + players_digest(players)


# ── digests shared by both paths ─────────────────────────────────────────────

def quant_digest(profile: TeamProfile) -> str:
    bits: list[str] = []
    if profile.fifa_rank:
        bits.append(f"FIFA rank #{profile.fifa_rank}")
    if profile.form:
        w = sum(1 for r in profile.form if r.goals_for > r.goals_against)
        d = sum(1 for r in profile.form if r.goals_for == r.goals_against)
        loss = len(profile.form) - w - d
        gf = sum(r.goals_for for r in profile.form)
        ga = sum(r.goals_against for r in profile.form)
        bits.append(f"last {len(profile.form)}: {w}W-{d}D-{loss}L, {gf} scored / {ga} conceded")
    if profile.xg_for is not None or profile.xg_against is not None:
        bits.append(f"xG {profile.xg_for or 0:.1f} for / {profile.xg_against or 0:.1f} against per game")
    return "; ".join(bits) or "(no quantitative data on record)"


def tactical_digest(reports: list[MatchTacticalReport]) -> str:
    if not reports:
        return "(no analysed matches yet)"
    lines = []
    for r in reports:
        for p in r.phases:
            seg = p.formations_blocks[:1] + p.adjustments[:1] + p.key_matchups[:1]
            if seg:
                lines.append(f"[{p.phase.split(' ', 1)[0]}] {', '.join(seg)}")
    return "; ".join(lines[:12]) if lines else "(matches analysed, no tactical flags recorded)"


# ── LLM + placeholder ────────────────────────────────────────────────────────

def _llm_critic(llm, team, profile, reports, players, usage_acc) -> CriticReport:
    prompt = f"""You are a performance analyst running a CRITIC LOOP on {team}: cross-examine the
QUANTITATIVE metrics against the QUALITATIVE tactical commentary to surface deep context
(why the numbers look the way they do).

QUANTITATIVE (hard numbers):
{quant_digest(profile)}
{_players_line(players)}

QUALITATIVE (tactical insights from analysed matches):
{tactical_digest(reports)}

For each notable metric, find the tactical evidence that explains it and state the deep
insight (findings: metric / commentary / insight). Where the numbers and the commentary
DISAGREE or cannot be reconciled, list it under tensions. Do NOT invent data not shown above.
Finish with a short summary."""

    chain = llm.with_structured_output(CriticReport, include_raw=True)
    result = chain.invoke(prompt)
    raw = result.get("raw") if isinstance(result, dict) else None
    if usage_acc is not None and raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta:
            usage_acc["input"] += meta.get("input_tokens", 0)
            usage_acc["output"] += meta.get("output_tokens", 0)
    report = result.get("parsed") if isinstance(result, dict) else result
    report.team = team
    return report


def _placeholder_critic(team, profile, reports, players=None) -> CriticReport:
    findings: list[CriticFinding] = []
    if players:
        top = players[0]
        findings.append(CriticFinding(
            metric=f"{top.player}: {top.goals}G/{top.assists}A in {top.matches}",
            commentary="leading goal contributor in the analysed competition",
            insight="[placeholder] attacking output concentrated in this player — "
                    "enable use_llm to cross-examine against the commentary",
        ))
    # A naive, deterministic cross-ref: pair the xG-against signal with any
    # defensive-shape tactical flag, if both are present.
    tac = tactical_digest(reports)
    if profile.xg_against is not None and ("block" in tac.lower() or "deep" in tac.lower()):
        findings.append(CriticFinding(
            metric=f"xG against {profile.xg_against:.1f}/game",
            commentary="defensive-block tendencies recorded in the analysed matches",
            insight="[placeholder] possible link between deep defending and chances conceded — "
                    "enable use_llm for a real cross-examination",
        ))
    return CriticReport(
        team=team,
        summary=f"[placeholder] {team}: {quant_digest(profile)}. Tactical: {tac}. "
                "Enable use_llm for a synthesised critic loop.",
        findings=findings,
    )
