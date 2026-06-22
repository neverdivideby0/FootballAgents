"""Bilateral data-parity check.

The pipeline can act like a single-team scout: a data-rich side (Spain) gets pages of
players/weaknesses/style while a thin side (Saudi Arabia) comes back near-empty — and the
LLM can mistake "more data about A" for "A is better". This measures each team's data
coverage and, when it's lopsided, emits a `DATA PARITY` note for the judge + advocates:
weigh the thin side on its merits, not on what's missing. Deterministic; never fabricates
data (it flags the gap, it doesn't fill it with invented facts).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_GAP = 3  # coverage difference (out of ~7 signals) that counts as materially lopsided


def coverage(profile, config: dict) -> dict[str, bool]:
    """Which data signals are populated for one team (cheap + robust)."""
    sig = {
        "squad": bool(profile.squad),
        "form": bool(profile.form),
        "xg": profile.xg_for is not None,
        "probable_xi": bool(profile.probable_xi),
        "coach": bool(profile.coach),
    }
    squad_names = [p.name for p in profile.squad]
    try:
        from worldcupagents.recall import squad_club_stats, top_players
        players = top_players(profile.team, config, squad=squad_names)
        if not players and config.get("league_kind") != "league" and squad_names:
            players = squad_club_stats(config, squad_names)
        sig["players"] = len(players) >= 3
    except Exception:  # noqa: BLE001
        sig["players"] = False
    try:
        from worldcupagents.dataflows.weaknesses import find_weaknesses
        sig["weaknesses"] = bool(find_weaknesses(config, profile, None))
    except Exception:  # noqa: BLE001
        sig["weaknesses"] = False
    return sig


def parity_note(home, away, config: dict) -> str:
    """A DATA PARITY note when coverage is materially lopsided; "" when balanced."""
    try:
        ch, ca = coverage(home, config), coverage(away, config)
    except Exception as e:  # noqa: BLE001 — parity is best-effort
        logger.debug("parity: coverage failed (%s)", e)
        return ""
    sh, sa = sum(ch.values()), sum(ca.values())
    if abs(sh - sa) < _GAP:
        return ""
    (thin, thin_cov), rich = ((away, ca), home) if sa < sh else ((home, ch), away)
    missing = [k for k, v in thin_cov.items() if not v]
    return (f"DATA PARITY: coverage is uneven — we hold richer data on {rich.team} than on "
            f"{thin.team} (thin/absent for {thin.team}: {', '.join(missing)}). Do NOT mistake "
            f"thinner data for lower quality; judge {thin.team} on its merits (rank, coach, "
            f"squad pedigree), not on what we happen to be missing.")
