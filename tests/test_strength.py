"""M1.2 tests — attack/defense strength model + LOOCV (hermetic, no network)."""

from __future__ import annotations

from worldcupagents.ensemble.strength import (
    expected_goals_from_strengths,
    fit_strengths,
    load_strength_model,
    team_lambdas,
)
from worldcupagents.pipelines.backtest import backtest

# A tiny synthetic league: "Strong" scores a lot, "Weak" concedes a lot.
_MATCHES = [
    {"home": "Strong", "away": "Weak", "hg": 4, "ag": 0},
    {"home": "Mid", "away": "Weak", "hg": 3, "ag": 1},
    {"home": "Strong", "away": "Mid", "hg": 2, "ag": 1},
    {"home": "Weak", "away": "Mid", "hg": 0, "ag": 2},
]


def test_fit_returns_none_on_empty():
    assert fit_strengths([]) is None


def test_strong_team_has_higher_attack_than_weak():
    m = fit_strengths(_MATCHES)
    assert m is not None
    sa = m.attack["strong"]
    wa = m.attack["weak"]
    assert sa > wa
    # Weak concedes a lot -> high defense ratio (worse defense = higher number).
    assert m.defense["weak"] > m.defense["strong"]


def test_expected_goals_none_for_unseen_team():
    m = fit_strengths(_MATCHES)
    assert expected_goals_from_strengths(m, "Strong", "Atlantis") is None  # away unseen


def test_expected_goals_strong_vs_weak_is_high():
    m = fit_strengths(_MATCHES)
    lam = expected_goals_from_strengths(m, "Strong", "Weak")
    assert lam is not None
    lam_h, lam_a = lam
    assert lam_h > lam_a              # strong home favoured
    assert lam_h > 1.5               # expected to score plenty vs a leaky defense


def test_team_lambdas_falls_back_to_rank_elo_without_strength():
    # No strength model -> uses rank-Elo (Argentina #1 should out-score a minnow).
    lam_h, lam_a = team_lambdas("Argentina", "Some Minnow", 1, None, strength=None)
    assert lam_h > lam_a


def test_load_strength_model_absent_store_returns_none(tmp_path):
    assert load_strength_model({"data_dir": str(tmp_path)}) is None


def test_backtest_includes_loocv_stats_model():
    res = backtest()  # bundled 10-match sample
    assert "stats-poisson(LOOCV)" in res.scores
    s = res.scores["stats-poisson(LOOCV)"]
    assert s.n == res.n_matches
    assert 0.0 <= s.mean_brier <= 2.0
