"""Alternative outcome / upset watch — the honest counterweight.

Favourites lose ~1 in 3 even-ish games; draws and shootouts happen. So every
verdict carries the SECOND-most-likely outcome, its scoreline (off the same
Poisson grid), how live it is, and — where the store has the data — the concrete
reasons it could happen (underdog set-piece threat, favourite's defensive
frailty, recent wobble, knockout variance). Deterministic and anchored; an LLM
narrative is optional polish, never the source of the numbers.
"""

from __future__ import annotations

import logging

from worldcupagents.agents.schemas import AlternativeOutcome, Outcome
from worldcupagents.ensemble.baseline import most_likely_scoreline

logger = logging.getLogger(__name__)

_LIVE_THRESHOLD = 0.25     # ≥25% = a genuinely plausible alternative
_LABEL = {Outcome.HOME_WIN: "home win", Outcome.DRAW: "draw", Outcome.AWAY_WIN: "away win"}


def build_alternative(grid, p_home: float, p_draw: float, p_away: float,
                      primary: Outcome, knockout: bool) -> AlternativeOutcome:
    """The second-most-likely outcome as a structured upset watch. In knockouts
    p_draw is folded to 0, so the alternative is simply the losing side."""
    ranked = sorted(
        [(Outcome.HOME_WIN, p_home), (Outcome.DRAW, p_draw), (Outcome.AWAY_WIN, p_away)],
        key=lambda x: x[1], reverse=True,
    )
    # The runner-up that isn't the primary call (skip a 0-prob folded draw).
    alt_outcome, alt_p = next((o, p) for o, p in ranked if o != primary and p > 0)
    restrict = {Outcome.HOME_WIN: "home", Outcome.DRAW: "draw", Outcome.AWAY_WIN: "away"}[alt_outcome]
    h, a = most_likely_scoreline(grid, restrict=restrict)
    score = f"{h}-{a}" + (" (a.e.t./pens)" if knockout and alt_outcome != primary else "")
    gap = round(max(p_home, p_draw, p_away) - alt_p, 3)
    return AlternativeOutcome(
        outcome=alt_outcome,
        probability=round(alt_p, 3),
        scoreline=score,
        gap=gap,
        live=alt_p >= _LIVE_THRESHOLD,
        narrative=(f"If it doesn't go to form: {_LABEL[alt_outcome]} "
                   f"({score}) is the live alternative at {alt_p:.0%}, only "
                   f"{gap:.0%} behind the call." if alt_p >= _LIVE_THRESHOLD else
                   f"Most likely upset: {_LABEL[alt_outcome]} ({score}) at {alt_p:.0%} "
                   f"— a long shot ({gap:.0%} behind), but the path exists."),
    )


def upset_factors(config: dict, fixture, home_profile, away_profile,
                  alt: AlternativeOutcome) -> list[str]:
    """Data-backed reasons the alternative could happen. Reads set pieces, tempo,
    forte and form from the store (graceful: [] when absent). Frames the side the
    ALTERNATIVE favours as the 'underdog' and the primary call's side as the
    'favourite'."""
    factors: list[str] = []
    try:
        if alt.outcome == Outcome.HOME_WIN:
            underdog, favourite = home_profile, away_profile
        elif alt.outcome == Outcome.AWAY_WIN:
            underdog, favourite = away_profile, home_profile
        else:  # a draw alternative — frame as "tight game"
            return _draw_factors(config, fixture, home_profile, away_profile)

        sp = _set_piece_threat(config, underdog.team)
        if sp:
            factors.append(f"{underdog.team} can nick one from dead balls — {sp}")
        frailty = _defensive_frailty(config, favourite.team)
        if frailty:
            factors.append(f"{favourite.team} is gettable at the back — {frailty}")
        form = _form_streak(underdog)
        if form:
            factors.append(f"{underdog.team} arrives in form — {form}")
        if fixture.knockout:
            factors.append("one knockout tie: extra-time and a shootout are coin-flips no model can price")
        if not factors:
            factors.append(f"variance: in a single match {underdog.team} only needs one good day")
    except Exception as e:  # noqa: BLE001 — upset colour must never break a verdict
        logger.warning("upset_factors failed (%s)", e)
    return factors[:4]


# ── internals (all read the store defensively) ───────────────────────────────

def _open_store(config: dict):
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return None
    return MatchStore.from_config(config)


def _set_piece_threat(config: dict, team: str) -> str:
    store = _open_store(config)
    if store is None:
        return ""
    try:
        comp, season = config.get("fd_competition"), config.get("season")
        hit = store.situations(comp, season, team) if season else None
        if not hit:
            latest = store.latest_situations(comp or "WC", team)
            hit = (latest[0], latest[1]) if latest else None
    finally:
        store.close()
    if not hit:
        return ""
    data = hit[0]
    bits = []
    for key, label in (("FromCorner", "corners"), ("SetPiece", "set pieces"),
                       ("From Corner", "corners")):
        agg = data.get(key)
        if isinstance(agg, dict) and agg.get("goals"):
            bits.append(f"{agg['goals']} from {label}")
    return ", ".join(bits)


def _defensive_frailty(config: dict, team: str) -> str:
    store = _open_store(config)
    if store is None:
        return ""
    try:
        from datetime import date, timedelta
        since = (date.today() - timedelta(days=365 * 5)).isoformat()
        prof = store.team_stat_profile(team, comp=config.get("fd_competition"), since=since)
    finally:
        store.close()
    if not prof:
        return ""
    bits = []
    if prof.get("shots_a"):
        bits.append(f"concedes {prof['shots_a']} shots/game")
    if prof.get("corners_a"):
        bits.append(f"{prof['corners_a']} corners against/game")
    return ", ".join(bits)


def _form_streak(profile) -> str:
    if not profile.form:
        return ""
    recent = profile.form[:4]
    wdl = "".join("W" if r.goals_for > r.goals_against
                  else "L" if r.goals_for < r.goals_against else "D" for r in recent)
    wins = wdl.count("W")
    return f"{wdl} in the last {len(recent)}" if wins >= 2 else ""


def _draw_factors(config: dict, fixture, home_profile, away_profile) -> list[str]:
    # A draw is most plausible when both sides are evenly matched / low-event.
    out = ["evenly matched on the model — a low-event stalemate is in play"]
    if fixture.knockout:
        out.append("level after 90 → extra time and penalties decide it")
    return out
