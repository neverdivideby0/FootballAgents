"""Statistical baseline + ensemble blend.

Decision (PROJECT_OUTLINE §4.2): final probabilities are a BLEND of the judge's
qualitative read and this statistical baseline, rather than a raw LLM-stated "70%".

As of Phase 0 the baseline is a **bivariate (independent) Poisson goals model**:
team strength -> expected goals (lambda_home, lambda_away) -> a full score grid.
Both the W/D/L probabilities AND the most-likely scoreline are read off the SAME
grid, so they are always mutually consistent and scorelines vary realistically
(not a hardcoded 2-1). When real attack/defense stats arrive (Phase 1) only
``expected_goals`` needs to change — callers and the grid are untouched.
Neutral venue (World Cup) so there is no home-field term.
"""

from __future__ import annotations

import math

from worldcupagents.agents.schemas import Fixture

Probs = tuple[float, float, float]  # (p_home, p_draw, p_away)
Lambdas = tuple[float, float]       # (lambda_home, lambda_away) — expected goals

# ── Tunable goal-model constants ────────────────────────────────────────────
_BASE_TOTAL_GOALS = 2.7    # avg total goals in an even men's international (~1.35 each)
_MAX_SUPREMACY = 4.5       # goal supremacy of a maximal mismatch (France–Curaçao class)
_SUPREMACY_GAMMA = 2.0     # convex: mid-table gaps stay modest, true mismatches escalate
_TOTAL_EXPANSION = 0.5     # lopsided games have MORE total goals, not the even-game 2.7
_MIN_LAMBDA = 0.18         # floor so even big underdogs can nick a goal
_GRID_MAX_GOALS = 10       # score grid covers 0..10 goals per side (blowout tails)


def _rank_to_elo(rank: int | None) -> float:
    if rank is None:
        return 1500.0  # league-average for unknown teams
    return 2100.0 - 12.0 * (rank - 1)


def _normalize(h: float, d: float, a: float) -> Probs:
    s = h + d + a
    if s <= 0:
        return (1 / 3, 1 / 3, 1 / 3)
    return (h / s, d / s, a / s)


# ── Goal-expectancy model ───────────────────────────────────────────────────

def expected_goals(home_rank: int | None, away_rank: int | None) -> Lambdas:
    """Map team strength to expected goals for each side.

    Strength comes from FIFA rank via Elo. Supremacy is CONVEX in the Elo edge
    (gamma=2): near-even and mid-table gaps stay modest, but true mismatches
    escalate the way international blowouts actually do (England 6-2 Iran,
    Spain 7-0 Costa Rica). Lopsided games also expand TOTAL goals — a rout is
    not an even 2.7-goal game redistributed.
    """
    eh, ea = _rank_to_elo(home_rank), _rank_to_elo(away_rank)
    exp_home = 1.0 / (1.0 + 10 ** ((ea - eh) / 400.0))  # Elo expected score 0..1
    edge = (exp_home - 0.5) * 2.0                        # signed, -1..1
    supremacy = math.copysign(abs(edge) ** _SUPREMACY_GAMMA * _MAX_SUPREMACY, edge)
    total = _BASE_TOTAL_GOALS + _TOTAL_EXPANSION * abs(supremacy)
    lam_home = max(_MIN_LAMBDA, (total + supremacy) / 2.0)
    lam_away = max(_MIN_LAMBDA, (total - supremacy) / 2.0)
    return lam_home, lam_away


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_grid(lam_home: float, lam_away: float, max_goals: int = _GRID_MAX_GOALS) -> list[list[float]]:
    """P(home=i, away=j) for i,j in 0..max_goals (independent Poisson)."""
    home_pmf = [_poisson_pmf(i, lam_home) for i in range(max_goals + 1)]
    away_pmf = [_poisson_pmf(j, lam_away) for j in range(max_goals + 1)]
    return [[hp * ap for ap in away_pmf] for hp in home_pmf]


def grid_outcome_probs(grid: list[list[float]]) -> Probs:
    """Sum the score grid into (p_home, p_draw, p_away)."""
    p_home = p_draw = p_away = 0.0
    for i, row in enumerate(grid):
        for j, p in enumerate(row):
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return _normalize(p_home, p_draw, p_away)


def most_likely_scoreline(
    grid: list[list[float]], restrict: str | None = None
) -> tuple[int, int]:
    """Most probable (home_goals, away_goals) cell.

    restrict: None = whole grid; 'home'/'away'/'draw' = only cells matching that
    outcome (used to keep the printed scoreline consistent with the final verdict).
    """
    best, best_p = (1, 1), -1.0
    for i, row in enumerate(grid):
        for j, p in enumerate(row):
            if restrict == "home" and not i > j:
                continue
            if restrict == "away" and not j > i:
                continue
            if restrict == "draw" and i != j:
                continue
            if p > best_p:
                best, best_p = (i, j), p
    return best


def baseline_probabilities(fixture: Fixture, home_rank: int | None, away_rank: int | None) -> Probs:
    """W/D/L probabilities from the Poisson goals model (single source of truth)."""
    lam_h, lam_a = expected_goals(home_rank, away_rank)
    return grid_outcome_probs(score_grid(lam_h, lam_a))


def shrink_to_uniform(probs: Probs, amount: float) -> Probs:
    """Pull probabilities toward (1/3,1/3,1/3). Stand-in for LLM uncertainty in M0."""
    u = 1 / 3
    return _normalize(*[(1 - amount) * p + amount * u for p in probs])


def blend(judge: Probs, base: Probs, judge_weight: float) -> Probs:
    w = max(0.0, min(1.0, judge_weight))
    return _normalize(*[w * j + (1 - w) * b for j, b in zip(judge, base)])


def clamp_to_band(probs: Probs, base: Probs, delta: float) -> Probs:
    """Bound the contextual move: scale the deviation from the Tier-1 ``base`` so NO
    probability shifts more than ±delta. Because the deviations sum to zero, scaling
    keeps the result a valid (summing-to-1) distribution — the contextual (LLM) layer
    can NUDGE the prior, never reshape it. delta<=0 disables (identity)."""
    if delta <= 0:
        return probs
    dev = [p - b for p, b in zip(probs, base)]
    worst = max(abs(d) for d in dev)
    if worst <= delta:
        return probs
    s = delta / worst
    return tuple(b + s * d for b, d in zip(base, dev))


def normalize3(h: float, d: float, a: float) -> Probs:
    """Public normalizer for an (home, draw, away) triple."""
    return _normalize(h, d, a)
