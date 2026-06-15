"""Match focus — where the game will be won or lost.

Turns the dossier data into concrete tactical focal points: a player to watch per
side, the decisive battleground (attack vs defence, the flanks, set pieces), and
the stylistic clash (possession vs directness/pace, experience). Used two ways:

  * shown to the judge / advocates / pundits so the debate argues the *area* of the
    game, not just who's better;
  * folded into the verdict's key_factors (battlegrounds) and x_factors (player to
    watch, intangibles) — including the no-LLM baseline.

All deterministic and sourced; degrades to [] when the data is thin.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def match_focus(config: dict, home, away) -> dict:
    """{key_factors: [battlegrounds…], x_factors: [watch players + intangibles…]}."""
    key: list[str] = []
    xf: list[str] = []
    try:
        for p in (home, away):
            w = _watch_player(config, p)
            if w:
                xf.append(w)
        for fn in (_forte_battleground, _set_piece_battleground, _style_battleground):
            line = fn(config, home, away)
            if line:
                key.append(line)
    except Exception as e:  # noqa: BLE001 — focus is colour, never fatal
        logger.warning("match_focus failed (%s)", e)
    return {"key_factors": key, "x_factors": xf}


def focus_digest(focus: dict) -> str:
    """One block for an LLM prompt."""
    parts = []
    if focus.get("key_factors"):
        parts.append("Battlegrounds: " + "; ".join(focus["key_factors"]))
    if focus.get("x_factors"):
        parts.append("Players to watch / intangibles: " + "; ".join(focus["x_factors"]))
    return "\n".join(parts)


# ── derivations ───────────────────────────────────────────────────────────────

def _top_player(config: dict, profile):
    from worldcupagents.recall import squad_club_stats, top_players
    squad = [p.name for p in profile.squad]
    ps = top_players(profile.team, config, squad=squad)
    if not ps and config.get("league_kind") != "league":
        ps = squad_club_stats(config, squad)
    if not ps:
        return None
    return max(ps, key=lambda p: (p.goals or 0) + (p.assists or 0) + (p.xg or 0) + (p.xa or 0))


def _watch_player(config: dict, profile) -> str:
    p = _top_player(config, profile)
    if not p:
        return ""
    bits = [f"{p.goals or 0}G/{p.assists or 0}A"]
    if p.xa is not None and p.xa >= 3:
        bits.append(f"{p.xa:.1f} xA — the creator")
    elif p.xg is not None and p.xg >= 3:
        bits.append(f"{p.xg:.1f} xG — the goal threat")
    club = f", {p.team}" if config.get("league_kind") != "league" and p.team else ""
    return f"Watch {p.player}{club} ({profile.team}): " + ", ".join(bits)


def _forte_battleground(config: dict, home, away) -> str:
    from worldcupagents.ensemble.strength import load_strength_model, team_forte
    try:
        model = load_strength_model(config)
    except Exception:  # noqa: BLE001
        return ""
    fh, fa = team_forte(model, home.team), team_forte(model, away.team)
    if not fh or not fa:
        return ""
    # The clearest clash: one side's attack against the other's defensive solidity.
    if fh["attack"] - fa["solidity"] > 0.2:
        return (f"{home.team}'s attack ({fh['attack']}) vs {away.team}'s "
                f"{'solid' if fa['solidity'] >= 1.1 else 'leaky'} defence (solidity {fa['solidity']})")
    if fa["attack"] - fh["solidity"] > 0.2:
        return (f"{away.team}'s attack ({fa['attack']}) vs {home.team}'s "
                f"{'solid' if fh['solidity'] >= 1.1 else 'leaky'} defence (solidity {fh['solidity']})")
    return f"finely balanced — {home.team} {fh['label']} vs {away.team} {fa['label']}"


def _situations(config: dict, team: str):
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return None
    store = MatchStore.from_config(config)
    try:
        season, comp = config.get("season"), config.get("fd_competition")
        hit = store.situations(comp, season, team) if season else None
        if not hit:
            latest = store.latest_situations(comp or "WC", team)
            hit = (latest[0], latest[1]) if latest else None
    finally:
        store.close()
    return hit[0] if hit else None


def _sp_goals(data: dict) -> int:
    n = 0
    for k in ("FromCorner", "SetPiece", "From Corner"):
        agg = data.get(k)
        if isinstance(agg, dict) and agg.get("goals"):
            n += agg["goals"]
    return n


def _sp_conceded(data: dict) -> int:
    n = 0
    for k in ("FromCorner", "SetPiece", "From Corner"):
        agg = data.get(k)
        if isinstance(agg, dict):
            n += (agg.get("against") or {}).get("goals") or 0
    return n


def _set_piece_battleground(config: dict, home, away) -> str:
    dh, da = _situations(config, home.team), _situations(config, away.team)
    if not dh or not da:
        return ""
    for atk, dfn, name_a, name_d in ((dh, da, home.team, away.team), (da, dh, away.team, home.team)):
        scored, conceded = _sp_goals(atk), _sp_conceded(dfn)
        if scored >= 8 and conceded >= 6:
            return (f"set pieces — {name_a} scores from dead balls ({scored}), "
                    f"{name_d} vulnerable there (conceded {conceded})")
    return ""


def _style(config: dict, team: str) -> dict | None:
    data = _situations(config, team)
    s = (data or {}).get("style")
    return s if isinstance(s, dict) else None


def _style_battleground(config: dict, home, away) -> str:
    sh, sa = _style(config, home.team), _style(config, away.team)
    if not sh or not sa:
        return ""
    ph, pa = sh.get("possession_share"), sa.get("possession_share")
    dh, da = sh.get("directness"), sa.get("directness")
    if ph and pa and abs(ph - pa) >= 0.06:
        keeper, runner = (home.team, away.team) if ph > pa else (away.team, home.team)
        return f"tempo — {keeper} wants the ball ({max(ph, pa):.0%}), {runner} hits on transition"
    if dh and da and abs(dh - da) >= 0.05:
        direct = home.team if dh > da else away.team
        return f"{direct} plays the more direct, vertical game"
    return ""
