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
from dataclasses import dataclass, field
from datetime import date

from worldcupagents.dataflows.match_store import MatchStore, db_path
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

_MIN_LAMBDA = 0.18
_MAX_LAMBDA = 4.5

# International match-type weights (tournament > qualifier > friendly) — overridable
# via config['intl_strength_type_weights'].
_DEFAULT_TYPE_WEIGHTS = {"tournament": 1.0, "qualifier": 0.7, "friendly": 0.4}


def _match_tier(tournament: str | None) -> str:
    """Classify an international fixture's competitiveness from its tournament name."""
    t = (tournament or "").lower()
    if "qualif" in t:                       # qualification / qualifier / qualifying
        return "qualifier"
    if "friendly" in t:
        return "friendly"
    return "tournament"                     # World Cup, Euro, Copa, AFCON, Nations League…


def _parse_iso(d: str | None) -> date | None:
    try:
        return date.fromisoformat((d or "")[:10])
    except ValueError:
        return None


@dataclass
class StrengthModel:
    attack: dict[str, float]
    defense: dict[str, float]
    mu: float          # league mean goals per team per match
    home_adv: float    # multiplicative home-goal advantage
    teams: set[str]
    games: dict[str, int] = field(default_factory=dict)  # per-team sample size (for the min-games guard)


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
    return StrengthModel(attack, defense, mu, home_adv, set(played), dict(played))


def fit_international_strengths(
    rows: list[dict],
    *,
    as_of: str | date | None = None,
    half_life_years: float = 2.0,
    max_age_years: float = 4.0,
    type_weights: dict[str, float] | None = None,
    shrinkage_k: float = 4.0,
    iters: int = 50,
    tol: float = 1e-4,
) -> StrengthModel | None:
    """Weighted, **opponent-adjusted** attack/defense fit for NATIONAL teams from
    international history (``wh_matches`` rows).

    Each match is weighted by **recency** (exponential decay, ``half_life_years``)
    × **type** (tournament > qualifier > friendly); games older than ``max_age_years``
    count zero (hard cutoff). Strengths are then solved by a **Dixon–Coles–style
    fixed-point iteration** (closed-form Poisson coordinate updates — no scipy), so a
    rating reflects the quality of the opponents actually faced: scoring against a
    strong defence counts for more than padding stats against minnows. A shrinkage
    pseudo-count (``shrinkage_k``) pulls thin samples toward average; neutral-venue
    (``home_adv = 1.0``); team keys canonicalized to align with the prediction side.
    """
    type_weights = type_weights or _DEFAULT_TYPE_WEIGHTS
    as_of_d = as_of if isinstance(as_of, date) else (_parse_iso(as_of) or date.today())
    per_year = 0.5 ** (1.0 / half_life_years) if half_life_years > 0 else 1.0

    w_scored: dict[str, float] = defaultdict(float)
    w_conceded: dict[str, float] = defaultdict(float)
    games: dict[str, int] = defaultdict(int)
    # Per-team opponent log for the iteration: (weight, opponent_key).
    log: dict[str, list[tuple[float, str]]] = defaultdict(list)
    tot_w_goals = tot_w = 0.0

    for m in rows:
        d = _parse_iso(m.get("date"))
        if d is None:
            continue
        age = (as_of_d - d).days / 365.25
        if age < 0 or age > max_age_years:        # future or beyond the hard cutoff
            continue
        w = (per_year ** age) * type_weights.get(_match_tier(m.get("tournament")), 1.0)
        if w <= 0:
            continue
        h = normalize_key(canonical_name(m["home_team"]))
        a = normalize_key(canonical_name(m["away_team"]))
        hg, ag = int(m["home_score"]), int(m["away_score"])
        w_scored[h] += w * hg; w_conceded[h] += w * ag; games[h] += 1; log[h].append((w, a))
        w_scored[a] += w * ag; w_conceded[a] += w * hg; games[a] += 1; log[a].append((w, h))
        tot_w_goals += w * (hg + ag); tot_w += w * 2

    if tot_w <= 0:
        return None
    mu = (tot_w_goals / tot_w) or 1.0  # weighted mean goals per team-match (guard all-0-0)
    k = shrinkage_k
    teams = list(log)
    attack = {t: 1.0 for t in teams}
    defense = {t: 1.0 for t in teams}

    # Coordinate descent: attack[t] = (weighted goals for + shrink) / (mu · Σ w·defense[opp] + shrink).
    # With every defense=1 the first step reduces to the one-pass shrunk ratio; further
    # steps discount goals scored against weak defences (low defense[opp]).
    for _ in range(max(1, iters)):
        new_att, new_def = {}, {}
        for t in teams:
            opp_def = sum(w * defense[o] for w, o in log[t])
            opp_att = sum(w * attack[o] for w, o in log[t])
            new_att[t] = (w_scored[t] + k * mu) / (mu * (opp_def + k))
            new_def[t] = (w_conceded[t] + k * mu) / (mu * (opp_att + k))
        # Identifiability: rescale so mean(attack) == 1 (λ products are invariant to this).
        scale = (sum(new_att.values()) / len(new_att)) or 1.0
        new_att = {t: v / scale for t, v in new_att.items()}
        new_def = {t: v * scale for t, v in new_def.items()}
        delta = max(max(abs(new_att[t] - attack[t]), abs(new_def[t] - defense[t])) for t in teams)
        attack, defense = new_att, new_def
        if delta < tol:
            break

    return StrengthModel(attack, defense, mu, home_adv=1.0, teams=set(teams), games=dict(games))


def expected_goals_from_strengths(model: StrengthModel | None, home: str, away: str,
                                  min_games: int = 0):
    """(λ_home, λ_away) from fitted strengths, or None if either team is unseen OR
    has fewer than ``min_games`` fitted games (too little data to trust → caller
    falls back to rank-Elo)."""
    if model is None:
        return None
    h, a = normalize_key(canonical_name(home)), normalize_key(canonical_name(away))
    if h not in model.teams or a not in model.teams:
        return None
    if min_games > 0 and (model.games.get(h, 0) < min_games or model.games.get(a, 0) < min_games):
        return None
    lam_h = model.mu * model.attack[h] * model.defense[a] * model.home_adv
    lam_a = model.mu * model.attack[a] * model.defense[h] / model.home_adv
    clamp = lambda x: max(_MIN_LAMBDA, min(_MAX_LAMBDA, x))  # noqa: E731
    return clamp(lam_h), clamp(lam_a)


def team_lambdas(home: str, away: str, rank_home, rank_away,
                 strength: StrengthModel | None = None, min_games: int = 0):
    """The single conditional: fitted strengths when available (and both teams have
    ≥ min_games), else rank-Elo."""
    if strength is not None:
        lam = expected_goals_from_strengths(strength, home, away, min_games=min_games)
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

    # NATIONAL teams are fitted on weighted INTERNATIONAL history (wh_matches) —
    # recency + tournament/qualifier/friendly weighting — never on the thin
    # per-tournament results that made elite sides collapse to the λ floor. Club
    # fixtures keep the per-competition fit on `matches` (no club↔intl mixing).
    if _is_international(config):
        return _load_international_model(config)

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


def _is_international(config: dict) -> bool:
    """National-team competition? Explicit kind wins; else infer from the WC code.
    A directly-built club config (fd_competition='PL', no league_kind) stays club."""
    kind = config.get("league_kind")
    if kind == "league":
        return False
    if kind == "tournament":
        return True
    return config.get("fd_competition") == "WC"


def _load_international_model(config: dict) -> StrengthModel | None:
    """Weighted national-team fit from `wh_matches` (international results only)."""
    from datetime import timedelta

    max_age = float(config.get("intl_strength_max_age_years", 4.0))
    as_of_s = config.get("strength_as_of")  # eval/replay may pin the as-of date
    as_of_d = _parse_iso(as_of_s) or date.today()
    since = (as_of_d - timedelta(days=int(max_age * 365.25) + 1)).isoformat()

    store = MatchStore.from_config(config)
    try:
        rows = store.international_results(since=since)
    finally:
        store.close()
    if not rows:
        return None
    return fit_international_strengths(
        rows,
        as_of=as_of_d,
        half_life_years=float(config.get("intl_strength_half_life_years", 2.0)),
        max_age_years=max_age,
        type_weights=config.get("intl_strength_type_weights"),
        shrinkage_k=float(config.get("intl_strength_shrinkage_k", 4.0)),
        iters=int(config.get("intl_strength_iters", 50)),
        tol=float(config.get("intl_strength_tol", 1e-4)),
    )
