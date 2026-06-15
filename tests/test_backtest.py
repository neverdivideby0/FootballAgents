"""M1.0 tests — calibration harness (hermetic, no network/LLM)."""

from __future__ import annotations

from worldcupagents.agents.schemas import Outcome
from worldcupagents.pipelines.backtest import backtest, load_fixtures, run_backtest


def test_bundled_sample_loads():
    rows = load_fixtures()
    assert len(rows) >= 8
    assert rows[0]["home"] == "France" and rows[0]["home_goals"] == 4


def test_backtest_scores_all_models():
    res = backtest()
    assert res.n_matches >= 8
    # base reference models always present (LOOCV stats model added on top)
    assert {"rank-poisson", "uniform", "favorite"} <= set(res.scores)
    for s in res.scores.values():
        assert s.n == res.n_matches
        assert 0.0 <= s.mean_brier <= 2.0
        assert 0.0 <= s.hit_rate <= 1.0


def test_uniform_brier_is_known_constant():
    # Uniform (1/3 each) scores 0.667 on every match regardless of outcome.
    res = backtest()
    assert round(res.scores["uniform"].mean_brier, 3) == 0.667


def test_perfect_model_scores_zero_brier():
    rows = [{"home": "A", "away": "B", "home_goals": 2, "away_goals": 0}]
    perfect = {"oracle": lambda h, a: (1.0, 0.0, 0.0)}  # always predicts HOME_WIN
    res = run_backtest(rows, extra_models=perfect)
    assert res.scores["oracle"].mean_brier == 0.0
    assert res.scores["oracle"].hit_rate == 1.0


def test_extra_model_can_be_compared():
    # A custom model plugs in alongside the built-ins (this is how M1.2's stats-λ
    # model will be benchmarked against the rank baseline).
    res = backtest(extra_models={"always_draw": lambda h, a: (0.0, 1.0, 0.0)})
    assert "always_draw" in res.scores
    assert "rank-poisson" in res.scores
