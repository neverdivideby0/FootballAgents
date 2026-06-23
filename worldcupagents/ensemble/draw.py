"""Deterministic draw-probability calibration.

The independent-Poisson base under-forecasts draws for evenly-matched, low-event
games. This nudges P(draw) UP (taking the mass proportionally from home/away) when
the Tier-1 read says the game is close — scaled by how close — with an extra bump
when a low-directness favourite meets a defensively-solid opponent (the classic
cagey, hard-to-break-down stalemate). Bounded by ``draw_calibration_max`` and applied
ONLY to group-stage games (knockouts fold the draw away). Pure function of λ + the
already-fitted style/forte data — no LLM.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

Probs = tuple[float, float, float]

_LOW_DIRECTNESS = 0.40   # favourite below this plays a slow, possession game
_SOLID = 1.05            # underdog solidity (1/defense) above this is hard to break down


def draw_uplift(base: Probs, lam_h: float, lam_a: float, home, away, config: dict) -> Probs:
    """Return (p_home, p_draw, p_away) with P(draw) calibrated up for close, cagey
    group games. Identity when the cap is 0 or the game is lopsided."""
    p_h, p_d, p_a = base
    cap = float(config.get("draw_calibration_max", 0.08))
    if cap <= 0 or (p_h + p_a) <= 0:
        return base

    total = max(lam_h + lam_a, 0.1)
    closeness = max(0.0, 1.0 - abs(lam_h - lam_a) / total)   # 1 even, →0 lopsided
    factor = closeness ** 2                                  # mid-table gaps stay modest
    try:
        if _cagey_matchup(config, home, away, lam_h, lam_a):
            factor = min(1.0, factor + 0.4)                 # tactical bump
    except Exception as e:  # noqa: BLE001 — style/forte best-effort
        logger.debug("draw calibration: cagey check skipped (%s)", e)

    uplift = min(cap * factor, p_h + p_a)
    if uplift <= 0:
        return base
    take_h = uplift * p_h / (p_h + p_a)
    out = (p_h - take_h, p_d + uplift, p_a - (uplift - take_h))
    s = sum(out)
    return (out[0] / s, out[1] / s, out[2] / s) if s > 0 else base


def _cagey_matchup(config: dict, home, away, lam_h: float, lam_a: float) -> bool:
    """A low-directness FAVOURITE meeting a defensively-solid underdog — the matchup
    most prone to a frustrating 0-0/1-1. Reuses the fitted style + forte data."""
    from worldcupagents.ensemble.focus import _style
    from worldcupagents.ensemble.strength import load_strength_model, team_forte

    fav, dog = (home, away) if lam_h >= lam_a else (away, home)
    style = _style(config, fav.team)
    direct = (style or {}).get("directness")
    if direct is None or direct >= _LOW_DIRECTNESS:        # favourite isn't slow/patient
        return False
    forte = team_forte(load_strength_model(config), dog.team)
    return bool(forte and forte.get("solidity", 1.0) >= _SOLID)
