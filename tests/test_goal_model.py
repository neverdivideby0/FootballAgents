"""F3 regression tests — mismatch-aware goals model + complete WC2026 ranks."""

from __future__ import annotations

from worldcupagents.dataflows import fifa_rankings
from worldcupagents.dataflows.world_cup_2026 import WC2026_TEAMS
from worldcupagents.ensemble.baseline import (
    expected_goals,
    grid_outcome_probs,
    most_likely_scoreline,
    score_grid,
)


def test_every_wc2026_team_has_a_rank():
    missing = [t for t in WC2026_TEAMS if fifa_rankings.get_rank(t) is None]
    assert missing == [], f"unranked WC2026 teams: {missing}"


def test_turkey_alias_resolves_to_turkiye_rank():
    assert fifa_rankings.get_rank("Turkey") == fifa_rankings.get_rank("Türkiye") == 50


def test_curacao_is_a_minnow_not_midtable():
    assert fifa_rankings.get_rank("Curaçao") > 75


def test_extreme_mismatch_produces_blowout_scoreline():
    # France (#2) vs Curaçao (#82): the modal scoreline must reflect a rout.
    lam_h, lam_a = expected_goals(2, fifa_rankings.get_rank("Curaçao"))
    assert lam_h >= 4.0                                   # ~4-5 goal expectation
    h, a = most_likely_scoreline(score_grid(lam_h, lam_a))
    assert h >= 4 and a == 0


def test_even_game_unchanged_by_recalibration():
    lam_h, lam_a = expected_goals(10, 11)
    assert abs(lam_h - 1.35) < 0.05 and abs(lam_a - 1.35) < 0.05   # base 2.7 split
    h, a = most_likely_scoreline(score_grid(lam_h, lam_a))
    assert (h, a) == (1, 1)


def test_mid_gap_stays_moderate():
    # Convex supremacy: a #5 vs #20 gap must NOT balloon like a true mismatch.
    lam_h, lam_a = expected_goals(5, 20)
    assert 1.7 <= lam_h <= 2.6 and 0.8 <= lam_a <= 1.4


def test_supremacy_is_symmetric():
    h1, a1 = expected_goals(2, 82)
    h2, a2 = expected_goals(82, 2)
    assert abs(h1 - a2) < 1e-9 and abs(a1 - h2) < 1e-9


def test_blowout_probability_is_decisive():
    lam = expected_goals(2, 82)
    p_home, p_draw, p_away = grid_outcome_probs(score_grid(*lam))
    assert p_home > 0.95
