"""Shared verdict assembly — ONE pipeline from a qualitative read to a MatchVerdict.

Extracted from the judge so both decision agents (Judge / Research-Manager analog
and Final Pundit / Portfolio-Manager analog) produce verdicts the exact same way:

    read (JudgeRead | None)
      → Poisson grid (fitted-strength λ or rank-Elo)
      → baseline probs
      → blend(read, baseline, judge_weight)     ← probabilities stay ANCHORED
      → knockout draw-fold (+ penalties detection)
      → outcome + grid-consistent scoreline
      → MatchVerdict with ProbBreakdown

This is the project's key divergence from TradingAgents (whose final rating is
un-anchored): every verdict, provisional or final, is blended with the same
statistical baseline.
"""

from __future__ import annotations

import logging

from worldcupagents.agents.schemas import DecidedBy, JudgeRead, MatchVerdict, Outcome, ProbBreakdown
from worldcupagents.dataflows.world_cup_2026 import VENUE_NOTES as _VENUE_NOTES
from worldcupagents.ensemble.baseline import (
    blend,
    expected_goals,
    grid_outcome_probs,
    most_likely_scoreline,
    normalize3,
    score_grid,
    shrink_to_uniform,
)

logger = logging.getLogger(__name__)


def assemble_verdict(config: dict, fixture, home, away,
                     read: JudgeRead | None, judge_weight: float) -> MatchVerdict:
    """Build a fully blended, stage-aware MatchVerdict from an optional LLM read."""
    lam_h, lam_a = match_lambdas(config, home, away)
    grid = score_grid(lam_h, lam_a)
    base = grid_outcome_probs(grid)

    if read is not None:
        judge_read = normalize3(read.p_home, read.p_draw, read.p_away)
    else:
        judge_read = shrink_to_uniform(base, 0.3)  # placeholder read

    p_home, p_draw, p_away = blend(judge_read, base, judge_weight)

    decided_by = DecidedBy.REGULATION
    if fixture.knockout:
        p_home, p_away = _fold_draw(p_home, p_draw, p_away)
        p_draw = 0.0
        if abs(p_home - p_away) < 0.10:
            decided_by = DecidedBy.PENALTIES

    outcome = _argmax_outcome(p_home, p_draw, p_away, fixture.knockout)

    # The honest counterweight: the second-most-likely outcome, always surfaced
    # so a favourite call is never the whole story. Off the SAME grid.
    from worldcupagents.ensemble.alternative import build_alternative
    alternative = build_alternative(grid, p_home, p_draw, p_away, outcome, fixture.knockout)

    # Data-derived focus areas: battlegrounds + a player to watch. In the baseline
    # path they ARE the key/x factors; with an LLM the read's factors lead and the
    # focus tops up anything the model didn't already name.
    from worldcupagents.ensemble.focus import match_focus
    focus = match_focus(config, home, away)
    if read:
        key_factors = list(read.key_factors) + [b for b in focus["key_factors"]][:2]
        x_factors = list(read.x_factors) + [x for x in focus["x_factors"]][:2]
    else:
        key_factors = focus["key_factors"] + _baseline_key_factors(config, fixture, home, away)
        x_factors = focus["x_factors"] + _x_factors(fixture, config)

    # Scoreline is read off the SAME Poisson grid, restricted to the final
    # outcome — so it's consistent with the verdict AND varies with goal expectancy.
    return MatchVerdict(
        outcome=outcome,
        decided_by=decided_by,
        p_home=p_home, p_draw=p_draw, p_away=p_away,
        scoreline=_scoreline(grid, outcome, decided_by, lam_h, lam_a),
        confidence=(read.confidence if read else _confidence(p_home, p_draw, p_away)),
        exp_goals_home=round(lam_h, 2), exp_goals_away=round(lam_a, 2),
        key_factors=key_factors,
        x_factors=x_factors,
        rationale=(
            read.rationale if read
            else "Baseline-only verdict (use_llm off): ensemble of a softened "
                 "rank prior and the Elo baseline. Enable use_llm for a debate-driven read."
        ),
        breakdown=ProbBreakdown(
            judge_home=judge_read[0], judge_draw=judge_read[1], judge_away=judge_read[2],
            base_home=base[0], base_draw=base[1], base_away=base[2],
            judge_weight=judge_weight,
        ),
        alternative=alternative,
    )


def _baseline_key_factors(config: dict, fixture, home, away) -> list[str]:
    """Offline fallback factors, phrased per competition kind (no 'stage: group'
    or 'FIFA rank #None' for club fixtures)."""
    if config.get("league_kind") == "league":
        return [
            f"model strength read: {home.team} vs {away.team} (fitted/home-advantage baseline)",
            "league match",
        ]
    return [
        f"FIFA rank: {home.team} #{home.fifa_rank} vs {away.team} #{away.fifa_rank}",
        f"stage: {fixture.stage.value}",
    ]


def match_lambdas(config: dict, home, away):
    """Expected goals: fitted strengths (when use_stats_lambda is on AND the match
    store has the teams) else the rank-Elo baseline. Off by default — stats-λ
    only ships once the backtest validates it on out-of-sample data."""
    if config.get("use_stats_lambda"):
        try:
            from worldcupagents.ensemble.strength import load_strength_model, team_lambdas
            strength = load_strength_model(config)
            return team_lambdas(home.team, away.team, home.fifa_rank, away.fifa_rank, strength)
        except Exception as e:  # noqa: BLE001
            logger.warning("stats-lambda unavailable (%s); rank-Elo baseline", e)
    return expected_goals(home.fifa_rank, away.fifa_rank)


def _fold_draw(h: float, d: float, a: float) -> tuple[float, float]:
    tot = h + a
    if tot <= 0:
        return 0.5, 0.5
    return h + d * h / tot, a + d * a / tot


def _round(x: float) -> int:
    return int(x + 0.5)


def _argmax_outcome(h: float, d: float, a: float, knockout: bool) -> Outcome:
    if knockout:
        return Outcome.HOME_WIN if h >= a else Outcome.AWAY_WIN
    return max(
        [(h, Outcome.HOME_WIN), (d, Outcome.DRAW), (a, Outcome.AWAY_WIN)],
        key=lambda x: x[0],
    )[1]


def _scoreline(grid: list[list[float]], outcome: Outcome, decided_by: DecidedBy,
               lam_h: float | None = None, lam_a: float | None = None) -> str:
    """A representative scoreline consistent with the outcome.

    Normal games use the most-likely EXACT score (the grid mode). But the mode
    systematically undersells a blowout — a team with λ=4.6 is shown 4-0 even
    though P(5) ≈ P(4) and the expectation is ~4.6 — so for clearly lopsided wins
    we use the ROUNDED EXPECTED goals (closer to the mean), e.g. 5-0."""
    if decided_by == DecidedBy.PENALTIES:
        # Regulation finished level; the most likely level scoreline + shootout.
        h, a = most_likely_scoreline(grid, restrict="draw")
        return f"{h}-{a} (a.e.t., pens)"

    if (decided_by == DecidedBy.REGULATION and lam_h is not None and lam_a is not None
            and max(lam_h, lam_a) >= 2.5 and abs(lam_h - lam_a) >= 1.5):
        h, a = _round(lam_h), _round(lam_a)
        if outcome == Outcome.HOME_WIN and h <= a:
            h = a + 1
        elif outcome == Outcome.AWAY_WIN and a <= h:
            a = h + 1
        return f"{h}-{a}"

    restrict = {"HOME_WIN": "home", "DRAW": "draw", "AWAY_WIN": "away"}[outcome.value]
    h, a = most_likely_scoreline(grid, restrict=restrict)
    if decided_by == DecidedBy.EXTRA_TIME:
        return f"{h}-{a} (a.e.t.)"
    return f"{h}-{a}"


def _confidence(h: float, d: float, a: float) -> str:
    m = max(h, d, a)
    return "high" if m >= 0.55 else "medium" if m >= 0.42 else "low"


def _x_factors(fx, config: dict | None = None) -> list[str]:
    config = config or {}
    if config.get("league_kind") == "league":
        out = [f"home advantage at {fx.home}'s ground" if not fx.venue else f"venue: {fx.venue}",
               "fixture congestion & squad rotation",
               "league table stakes (motivation can be asymmetric)"]
    else:
        out = ["neutral venue (no traditional home advantage)"]
        if fx.venue and fx.venue in _VENUE_NOTES:
            out.append(_VENUE_NOTES[fx.venue])
        out.append("inter-city travel & recovery (US/Can/Mex distances)")
    if fx.knockout:
        out.append("knockout pressure — penalty-shootout variance")
    return out
