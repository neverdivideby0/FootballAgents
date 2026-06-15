"""Shared TeamProfile -> compact prompt summary. Keeps token use sane and avoids
duplicating the briefing logic across advocates and the judge."""

from __future__ import annotations

from worldcupagents.agents.schemas import TeamProfile


def profile_brief(p: TeamProfile) -> str:
    rank = f"#{p.fifa_rank}" if p.fifa_rank else "unranked"
    style = p.style or "style unknown"
    if p.squad:
        names = ", ".join(pl.name for pl in p.squad[:6])
        squad = f"squad {len(p.squad)} incl. {names}"
    else:
        squad = "squad data unavailable"
    if p.form:
        form = "; ".join(f"{r.goals_for}-{r.goals_against} v {r.opponent}" for r in p.form[:5])
    else:
        form = "no completed matches on record yet"
    xg = ""
    if p.xg_for is not None or p.xg_against is not None:
        xg = f" xG: {p.xg_for or 0:.1f} for / {p.xg_against or 0:.1f} against."
    return f"{p.team} (FIFA {rank}; {style}). {squad}. Recent: {form}.{xg}"
