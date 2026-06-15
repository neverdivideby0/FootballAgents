"""Attack/defense strength model (DATA_PLAN M1.2) — real λ from real results.

A dependency-free Dixon–Coles-style ratio model fitted on the match store:
  attack[t]  = (avg goals t scores)   / league mean
  defense[t] = (avg goals t concedes) / league mean
  λ_home = mu · attack[home] · defense[away] · home_adv
  λ_away = mu · attack[away] · defense[home] / home_adv

These λ feed the SAME Poisson score grid as the rank-Elo baseline — the model is
swappable behind ``team_lambdas`` (strengths when both teams are known, else the
rank-Elo fallback). No new dependencies (no scipy/numpy); fits on small data.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass

from worldcupagents.dataflows.match_store import MatchStore, db_path
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

_MIN_LAMBDA = 0.18
_MAX_LAMBDA = 4.5


@dataclass
class StrengthModel:
    attack: dict[str, float]
    defense: dict[str, float]
    mu: float          # league mean goals per team per match
    home_adv: float    # multiplicative home-goal advantage
    teams: set[str]


def fit_strengths(matches: list[dict]) -> StrengthModel | None:
    """Fit from match-store rows (keys: home, away, hg, ag). None if no data."""
    scored: dict[str, float] = defaultdict(float)
    conceded: dict[str, float] = defaultdict(float)
    played: dict[str, int] = defaultdict(int)
    total_home = total_away = 0
    n = 0

    for m in matches:
        h, a = normalize_key(m["home"]), normalize_key(m["away"])
        hg, ag = int(m["hg"]), int(m["ag"])
        scored[h] += hg; conceded[h] += ag; played[h] += 1
        scored[a] += ag; conceded[a] += hg; played[a] += 1
        total_home += hg; total_away += ag; n += 1

    if n == 0:
        return None

    home_avg = total_home / n
    away_avg = total_away / n
    mu = (total_home + total_away) / (2 * n) or 1.0  # avg goals per team-match

    attack, defense = {}, {}
    for t, games in played.items():
        attack[t] = (scored[t] / games) / mu if mu else 1.0
        defense[t] = (conceded[t] / games) / mu if mu else 1.0

    home_adv = math.sqrt(home_avg / away_avg) if away_avg > 0 else 1.0
    return StrengthModel(attack, defense, mu, home_adv, set(played))


def expected_goals_from_strengths(model: StrengthModel | None, home: str, away: str):
    """(λ_home, λ_away) from fitted strengths, or None if either team is unseen."""
    if model is None:
        return None
    h, a = normalize_key(canonical_name(home)), normalize_key(canonical_name(away))
    if h not in model.teams or a not in model.teams:
        return None
    lam_h = model.mu * model.attack[h] * model.defense[a] * model.home_adv
    lam_a = model.mu * model.attack[a] * model.defense[h] / model.home_adv
    clamp = lambda x: max(_MIN_LAMBDA, min(_MAX_LAMBDA, x))  # noqa: E731
    return clamp(lam_h), clamp(lam_a)


def team_lambdas(home: str, away: str, rank_home, rank_away, strength: StrengthModel | None = None):
    """The single conditional: fitted strengths when available, else rank-Elo."""
    if strength is not None:
        lam = expected_goals_from_strengths(strength, home, away)
        if lam is not None:
            return lam
    from worldcupagents.ensemble.baseline import expected_goals
    return expected_goals(rank_home, rank_away)


def team_forte(model: StrengthModel | None, team: str) -> dict | None:
    """A team's attack vs defense leaning from fitted strengths. attack > 1 =
    scores more than league average; defense > 1 = CONCEDES more than average
    (so lower is better defensively). Returns ratings + a plain-language label,
    or None if the team is unseen."""
    if model is None:
        return None
    t = normalize_key(canonical_name(team))
    if t not in model.teams:
        return None
    att, dfn = model.attack.get(t, 1.0), model.defense.get(t, 1.0)
    # Defensive solidity reads better as (1/defense): >1 means concedes less.
    # Floor the divisor so a zero-concede team reads as MAX solidity, not 1.0.
    solidity = 1.0 / max(dfn, 0.25)
    if att >= 1.05 and solidity >= 1.05:
        label = "complete (strong both ends)"
    elif att - solidity > 0.15:
        label = "attack-leaning (outscores rather than shuts out)"
    elif solidity - att > 0.15:
        label = "defense-leaning (grinds low-scoring games)"
    else:
        label = "balanced"
    return {"attack": round(att, 2), "defense": round(dfn, 2),
            "solidity": round(solidity, 2), "label": label}


def load_strength_model(config: dict) -> StrengthModel | None:
    """Fit a model from the configured match store, filtered to the active
    competition (config['fd_competition']) so leagues never cross-contaminate.
    None if the store is absent or has no matches for that competition."""
    if not db_path(config).exists():
        return None
    store = MatchStore.from_config(config)
    try:
        matches = store.all_matches()
    finally:
        store.close()
    comp = config.get("fd_competition")
    if comp is not None:
        matches = [m for m in matches if m.get("comp") == comp]
    season = config.get("season")
    if season:  # fit only on matches up to the season's end — no future leakage
        from worldcupagents.seasons import season_cutoff
        hi = season_cutoff(season)
        matches = [m for m in matches if not m.get("date") or m["date"] <= hi]
    return fit_strengths(matches)
