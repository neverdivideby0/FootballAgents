"""Calibration harness (DATA_PLAN M1.0) — the yardstick for the stats tier.

Scores probabilistic models against historical results with the **Brier score**
(reusing graph.reflection.brier_score) plus hit-rate. The point: establish how
good the current rank-Elo Poisson baseline is, so that when the stats tier (M1.2)
swaps in fitted λ we can re-run and *prove* the improvement — not assume it.

Models compared (all no-spend):
  * rank-poisson   — the current baseline (expected_goals from FIFA rank)
  * uniform        — 1/3 each (a probabilistic floor)
  * favorite       — naive: higher-ranked team heavily favored
Optionally `predictor` (the full ensemble) when you pass an explicit config with
use_llm — that one costs tokens, so it's opt-in.

Caveat (PROJECT_OUTLINE §11): backtesting pre-2026 matches leaks training data
and uses present-day ranks. Read the *relative* gaps between models, not the
absolute Brier, and weight live 2026 results far more once they exist.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from worldcupagents.agents.schemas import Fixture, Outcome, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows import fifa_rankings
from worldcupagents.ensemble.baseline import baseline_probabilities, expected_goals, grid_outcome_probs, score_grid
from worldcupagents.ensemble.strength import expected_goals_from_strengths, fit_strengths
from worldcupagents.graph.reflection import brier_score, outcome_from_score

logger = logging.getLogger(__name__)

_SAMPLE = Path(__file__).parent / "data" / "backtest_sample.csv"
Probs = tuple[float, float, float]


@dataclass
class ModelScore:
    name: str
    n: int = 0
    brier_sum: float = 0.0
    hits: int = 0

    @property
    def mean_brier(self) -> float:
        return self.brier_sum / self.n if self.n else 0.0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n if self.n else 0.0


@dataclass
class BacktestResult:
    scores: dict[str, ModelScore] = field(default_factory=dict)
    n_matches: int = 0


def load_fixtures(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else _SAMPLE
    with open(p, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["home_goals"] = int(r["home_goals"])
        r["away_goals"] = int(r["away_goals"])
    return rows


def rows_from_store(config: dict, comp: str | None = None) -> list[dict]:
    """Backtest rows from the SQLite match store, optionally filtered to one
    competition (e.g. comp='PL'). Maps store hg/ag -> home_goals/away_goals."""
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        rows = store.all_matches()
    finally:
        store.close()
    return [
        {"home": r["home"], "away": r["away"], "home_goals": r["hg"], "away_goals": r["ag"],
         "odds_h": r.get("odds_h"), "odds_d": r.get("odds_d"), "odds_a": r.get("odds_a")}
        for r in rows
        if comp is None or r.get("comp") == comp
    ]


def devig_odds(oh: float, od: float, oa: float) -> Probs:
    """1X2 decimal odds -> de-vigged probabilities (strip the bookmaker overround)."""
    inv = [1.0 / oh, 1.0 / od, 1.0 / oa]
    s = sum(inv)
    return (inv[0] / s, inv[1] / s, inv[2] / s)


# ── models ───────────────────────────────────────────────────────────────────

def _rank_poisson(home: str, away: str) -> Probs:
    fx = Fixture(home=home, away=away, stage=Stage.GROUP)
    return baseline_probabilities(fx, fifa_rankings.get_rank(home), fifa_rankings.get_rank(away))


def _uniform(home: str, away: str) -> Probs:
    return (1 / 3, 1 / 3, 1 / 3)


def _favorite(home: str, away: str) -> Probs:
    """Naive reference: back whichever side is higher-ranked; unranked = mid."""
    rh = fifa_rankings.get_rank(home) or 50
    ra = fifa_rankings.get_rank(away) or 50
    if rh < ra:      # home stronger (lower rank number)
        return (0.60, 0.25, 0.15)
    if ra < rh:
        return (0.15, 0.25, 0.60)
    return (0.40, 0.20, 0.40)


def _home_field(home: str, away: str) -> Probs:
    """Club base rate: home advantage with no team info (meaningful for leagues)."""
    return (0.45, 0.26, 0.29)


_MODELS = {
    "rank-poisson": _rank_poisson,
    "uniform": _uniform,
    "favorite": _favorite,
    "home-field": _home_field,
}


def run_backtest(rows: list[dict], extra_models: dict | None = None,
                 include_stats_loocv: bool = True) -> BacktestResult:
    models = {**_MODELS, **(extra_models or {})}
    result = BacktestResult(scores={name: ModelScore(name) for name in models})

    for r in rows:
        actual = outcome_from_score(r["home_goals"], r["away_goals"])
        result.n_matches += 1
        for name, fn in models.items():
            ph, pd, pa = fn(r["home"], r["away"])
            s = result.scores[name]
            s.n += 1
            s.brier_sum += brier_score(ph, pd, pa, actual)
            if _argmax_outcome(ph, pd, pa) == actual:
                s.hits += 1

    if include_stats_loocv and len(rows) >= 3:
        result.scores["stats-poisson(LOOCV)"] = _loocv_stats(rows)

    market = _market_score(rows)
    if market is not None:
        result.scores["market(de-vigged odds)"] = market
    return result


def _market_score(rows: list[dict]) -> ModelScore | None:
    """The bookmaker baseline — the hardest line to beat. Scored only on rows that
    carry odds (others are skipped), so n may be < total matches."""
    s = ModelScore("market(de-vigged odds)")
    for r in rows:
        oh, od, oa = r.get("odds_h"), r.get("odds_d"), r.get("odds_a")
        if not (oh and od and oa):
            continue
        ph, pd, pa = devig_odds(oh, od, oa)
        actual = outcome_from_score(r["home_goals"], r["away_goals"])
        s.n += 1
        s.brier_sum += brier_score(ph, pd, pa, actual)
        if _argmax_outcome(ph, pd, pa) == actual:
            s.hits += 1
    return s if s.n else None


def _loocv_stats(rows: list[dict]) -> ModelScore:
    """Honest out-of-sample score for the fitted-strength model: for each match,
    fit strengths on the OTHER matches and predict the held-out one (rank-Elo
    fallback when a team is unseen in the training fold)."""
    s = ModelScore("stats-poisson(LOOCV)")
    for i, r in enumerate(rows):
        train = [
            {"home": x["home"], "away": x["away"], "hg": x["home_goals"], "ag": x["away_goals"]}
            for j, x in enumerate(rows) if j != i
        ]
        model = fit_strengths(train)
        lam = expected_goals_from_strengths(model, r["home"], r["away"])
        if lam is None:  # team unseen in the fold -> rank-Elo fallback
            lam = expected_goals(fifa_rankings.get_rank(r["home"]), fifa_rankings.get_rank(r["away"]))
        ph, pd, pa = grid_outcome_probs(score_grid(*lam))
        actual = outcome_from_score(r["home_goals"], r["away_goals"])
        s.n += 1
        s.brier_sum += brier_score(ph, pd, pa, actual)
        if _argmax_outcome(ph, pd, pa) == actual:
            s.hits += 1
    return s


def _argmax_outcome(ph: float, pd: float, pa: float) -> Outcome:
    return max(
        [(ph, Outcome.HOME_WIN), (pd, Outcome.DRAW), (pa, Outcome.AWAY_WIN)],
        key=lambda x: x[0],
    )[1]


def backtest(path: str | Path | None = None, extra_models: dict | None = None,
             config: dict | None = None, from_store: bool = False, comp: str | None = None) -> BacktestResult:
    rows = rows_from_store(config or {}, comp=comp) if from_store else load_fixtures(path)
    return run_backtest(rows, extra_models)
