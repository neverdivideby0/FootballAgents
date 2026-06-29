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
import re

from worldcupagents.agents.schemas import (
    AlternativeOutcome,
    DecidedBy,
    JudgeRead,
    MatchVerdict,
    Outcome,
    ProbBreakdown,
)
from worldcupagents.dataflows.world_cup_2026 import VENUE_NOTES as _VENUE_NOTES
from worldcupagents.ensemble.baseline import (
    blend,
    clamp_to_band,
    expected_goals,
    grid_outcome_probs,
    most_likely_scoreline,
    normalize3,
    score_grid,
    shrink_to_uniform,
)

logger = logging.getLogger(__name__)


def assemble_verdict(config: dict, fixture, home, away,
                     read: JudgeRead | None, judge_weight: float,
                     debate_state: dict | None = None) -> MatchVerdict:
    """Build a stage-aware MatchVerdict from an optional LLM read.

    Two modes (config['verdict_mode']):
      * "agents" (default) — when a real ``read`` exists, the judge's stated scoreline +
        probabilities ARE the verdict (no Poisson/blend); the upset watch is built from the
        advocates' black-swan calls in ``debate_state``.
      * "stats" — the statistical path below (fitted-strength λ → grid → blend → clamp).
    Agents-mode automatically falls back to the stats path whenever ``read`` is None
    (offline / missing key / judge error), so predict never crashes.
    """
    mode = (config.get("verdict_mode") or "agents").lower()
    if mode == "agents" and read is not None:
        return _agents_verdict(config, fixture, home, away, read, debate_state)

    lam_h, lam_a = match_lambdas(config, home, away)
    grid = score_grid(lam_h, lam_a)
    base = grid_outcome_probs(grid)

    # Draw calibration: nudge P(draw) up for close, cagey GROUP games (the Poisson
    # base under-forecasts draws). Knockouts fold the draw away below, so skip them.
    if not fixture.knockout:
        from worldcupagents.ensemble.draw import draw_uplift
        base = draw_uplift(base, lam_h, lam_a, home, away, config)

    if read is not None:
        judge_read = normalize3(read.p_home, read.p_draw, read.p_away)
    else:
        judge_read = shrink_to_uniform(base, 0.3)  # placeholder read

    # Contextual factors (the judge read) may only NUDGE the Tier-1 base, never
    # reshape it: clamp the blended result to within ±delta of base, then renormalize.
    p_home, p_draw, p_away = blend(judge_read, base, judge_weight)
    p_home, p_draw, p_away = clamp_to_band(
        (p_home, p_draw, p_away), base, float(config.get("max_contextual_delta", 0.15)))

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


# ── Agents mode: the verdict IS the judge's read (no Poisson) ────────────────

_SCORE_RE = re.compile(r"(\d+)\s*[-–]\s*(\d+)")
_ALT_LIVE = 0.25


def _parse_score(s: str | None) -> tuple[int, int] | None:
    m = _SCORE_RE.search(s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _agents_verdict(config: dict, fixture, home, away, read: JudgeRead,
                    debate_state: dict | None) -> MatchVerdict:
    """The judge's stated score + probabilities become the verdict directly. The
    statistical engine is bypassed; only the knockout no-draw rule is enforced."""
    from worldcupagents.ensemble.focus import match_focus

    p_home, p_draw, p_away = normalize3(read.p_home, read.p_draw, read.p_away)
    decided_by = read.decided_by or DecidedBy.REGULATION
    ft = _parse_score(read.scoreline)

    if fixture.knockout:
        # A knockout must resolve to a winner. Fold the draw mass onto the sides,
        # and if the judge's full-time score is level, settle it (honour the judge's
        # decided_by; else penalties when it's tight, extra time otherwise).
        p_home, p_away = _fold_draw(p_home, p_draw, p_away)
        p_draw = 0.0
        level = ft is not None and ft[0] == ft[1]
        if level and decided_by == DecidedBy.REGULATION:
            decided_by = DecidedBy.PENALTIES if abs(p_home - p_away) < 0.10 else DecidedBy.EXTRA_TIME
        elif not level:
            decided_by = DecidedBy.REGULATION

    outcome = _argmax_outcome(p_home, p_draw, p_away, fixture.knockout)
    scoreline = _agents_scoreline(read.scoreline, decided_by, fixture, outcome)
    alternative = _agents_alternative(debate_state, outcome, fixture,
                                      (p_home, p_draw, p_away))

    focus = match_focus(config, home, away)
    key_factors = list(read.key_factors) + list(focus["key_factors"])[:2]
    x_factors = list(read.x_factors) + list(focus["x_factors"])[:2]

    return MatchVerdict(
        outcome=outcome, decided_by=decided_by,
        p_home=p_home, p_draw=p_draw, p_away=p_away,
        scoreline=scoreline,
        confidence=read.confidence,
        exp_goals_home=None, exp_goals_away=None,  # a Poisson concept — n/a in agents mode
        key_factors=key_factors, x_factors=x_factors,
        rationale=read.rationale,
        breakdown=None,  # no blend happened — the judge's numbers stand
        alternative=alternative,
    )


def _agents_scoreline(raw: str | None, decided_by: DecidedBy, fixture, outcome: Outcome) -> str:
    """Normalise the judge's scoreline to 'H-A' (+ a.e.t./pens suffix) and make sure a
    knockout reads as a decisive result for whoever advances."""
    sc = _parse_score(raw)
    if sc is None:
        sc = {"HOME_WIN": (1, 0), "AWAY_WIN": (0, 1), "DRAW": (1, 1)}[outcome.value]
    h, a = sc
    if not fixture.knockout:
        return f"{h}-{a}"
    if decided_by == DecidedBy.PENALTIES:
        if h != a:                       # pens imply a level full-time score
            a = h
        return f"{h}-{a} (a.e.t., pens)"
    if decided_by == DecidedBy.EXTRA_TIME:
        if outcome == Outcome.HOME_WIN and h <= a:
            h = a + 1
        elif outcome == Outcome.AWAY_WIN and a <= h:
            a = h + 1
        return f"{h}-{a} (a.e.t.)"
    # decisive in regulation — keep the score on the right side of the result
    if outcome == Outcome.HOME_WIN and h <= a:
        h = a + 1
    elif outcome == Outcome.AWAY_WIN and a <= h:
        a = h + 1
    return f"{h}-{a}"


def _agents_alternative(debate_state: dict | None, primary: Outcome, fixture,
                        probs: tuple[float, float, float]) -> AlternativeOutcome:
    """The upset watch, agent-sourced: pick the advocates' black-swan scoreline whose
    outcome differs from the final call (prefer the surprising/losing side). Falls back
    to the second-most-likely outcome when no usable black swan was captured."""
    ds = debate_state or {}
    idx = {Outcome.HOME_WIN: 0, Outcome.DRAW: 1, Outcome.AWAY_WIN: 2}
    primary_p = probs[idx[primary]]

    for side in ("away", "home"):  # the away/underdog swan first
        raw = ds.get(f"{side}_black_swan")
        sc = _parse_score(raw)
        if not raw or sc is None:
            continue
        h, a = sc
        oc = Outcome.HOME_WIN if h > a else Outcome.AWAY_WIN if a > h else Outcome.DRAW
        if fixture.knockout and oc == Outcome.DRAW:  # a level knockout swan = pens upset
            oc = Outcome.AWAY_WIN if primary == Outcome.HOME_WIN else Outcome.HOME_WIN
        if oc == primary:
            continue
        alt_p = probs[idx[oc]]
        score = f"{h}-{a}" + (" (a.e.t., pens)" if fixture.knockout and h == a else "")
        how = _how_clause(raw)
        return AlternativeOutcome(
            outcome=oc, probability=round(alt_p, 3), scoreline=score,
            gap=round(primary_p - alt_p, 3), live=alt_p >= _ALT_LIVE,
            swing_factors=[how] if how else [],
            narrative=(f"Black swan ({score}): {how}" if how else f"Black swan: {score}"),
        )

    # Fallback: second-most-likely outcome (no usable swan).
    ranked = sorted(((o, probs[i]) for o, i in idx.items()), key=lambda x: x[1], reverse=True)
    alt_oc, alt_p = next((o, p) for o, p in ranked if o != primary and p > 0)
    score = {"HOME_WIN": "2-1", "AWAY_WIN": "1-2", "DRAW": "1-1"}[alt_oc.value]
    if fixture.knockout and alt_oc != Outcome.DRAW:
        score += " (a.e.t., pens)"
    return AlternativeOutcome(
        outcome=alt_oc, probability=round(alt_p, 3), scoreline=score,
        gap=round(primary_p - alt_p, 3), live=alt_p >= _ALT_LIVE,
        narrative=f"Second-most-likely outcome at {alt_p:.0%}.",
    )


def _how_clause(raw: str) -> str:
    """The advocate's '(if …)' explanation after a black-swan scoreline, if present."""
    m = re.search(r"\(([^)]+)\)", raw or "")
    if m:
        return m.group(1).strip()
    # else any prose trailing the scoreline
    tail = _SCORE_RE.sub("", raw or "", count=1).strip(" -–—:;,.")
    return tail[:140]


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
            min_games = int(config.get("strength_min_games", 2))
            return team_lambdas(home.team, away.team, home.fifa_rank, away.fifa_rank,
                                strength, min_games=min_games)
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
