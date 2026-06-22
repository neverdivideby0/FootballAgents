"""Weighted international strength fit: recency decay, 4-year cutoff, type weights,
min-games guard (hermetic — synthetic rows, no store/network)."""

from __future__ import annotations

from worldcupagents.dataflows.names import canonical_name, normalize_key
from worldcupagents.ensemble.strength import (
    StrengthModel,
    _match_tier,
    expected_goals_from_strengths,
    fit_international_strengths,
    team_lambdas,
)

AS_OF = "2026-06-22"


def _row(home, away, hg, ag, d, tour="Friendly"):
    return {"date": d, "tournament": tour, "home_team": home, "away_team": away,
            "home_score": hg, "away_score": ag, "neutral": None}


def _att(model, team):
    return model.attack[normalize_key(canonical_name(team))]


# ── tournament-tier classifier ──────────────────────────────────────────────

def test_match_tier_classifier():
    assert _match_tier("FIFA World Cup qualification") == "qualifier"
    assert _match_tier("UEFA Euro qualifying") == "qualifier"
    assert _match_tier("Friendly") == "friendly"
    assert _match_tier("FIFA World Cup") == "tournament"
    assert _match_tier("UEFA Nations League") == "tournament"
    assert _match_tier(None) == "tournament"


# ── hard 4-year cutoff ──────────────────────────────────────────────────────

def test_games_beyond_max_age_are_ignored():
    # A team whose only game is >4 years old contributes nothing → absent from the model.
    m = fit_international_strengths([_row("OldTeam", "Opp", 5, 0, "2020-01-01")],
                                   as_of=AS_OF, max_age_years=4.0)
    assert m is None or normalize_key(canonical_name("OldTeam")) not in m.teams


# ── recency: a recent result counts more than an old one ────────────────────

def test_recency_weights_recent_more_than_old():
    recent = fit_international_strengths([_row("Reno", "Opp", 5, 0, "2026-06-01")], as_of=AS_OF)
    old = fit_international_strengths([_row("Olda", "Opp", 5, 0, "2023-06-01")], as_of=AS_OF)
    assert _att(recent, "Reno") > _att(old, "Olda")   # same 5-0, more recent → higher attack


# ── type weighting: tournament > friendly ───────────────────────────────────

def test_tournament_weighted_more_than_friendly():
    d = "2026-06-01"
    tourn = fit_international_strengths([_row("Tor", "Opp", 5, 0, d, "FIFA World Cup")], as_of=AS_OF)
    frnd = fit_international_strengths([_row("Fri", "Opp", 5, 0, d, "Friendly")], as_of=AS_OF)
    assert _att(tourn, "Tor") > _att(frnd, "Fri")     # same 5-0, tournament weighed heavier


# ── shrinkage: a one-game team is pulled toward average, not 0/extreme ───────

def test_shrinkage_pulls_small_sample_toward_average():
    m = fit_international_strengths([_row("ZeroT", "Opp", 0, 0, "2026-06-01")], as_of=AS_OF)
    # Scored 0 but shrinkage keeps attack well above 0 (not the old attack=0 → λ-floor bug).
    assert _att(m, "ZeroT") > 0.4


# ── min-games guard falls back to rank-Elo ──────────────────────────────────

def test_min_games_guard_falls_back_to_rank_elo():
    # Model: A has 3 games, B has 1. With min_games=2, B is too thin → rank-Elo.
    rows = [_row("A", "X", 2, 1, "2026-06-01"), _row("A", "Y", 1, 1, "2026-05-01"),
            _row("A", "Z", 3, 0, "2026-04-01"), _row("B", "W", 1, 0, "2026-06-02")]
    m = fit_international_strengths(rows, as_of=AS_OF)
    assert m.games[normalize_key(canonical_name("A"))] == 3
    assert m.games[normalize_key(canonical_name("B"))] == 1
    guarded = team_lambdas("A", "B", 1, 99, strength=m, min_games=2)
    from worldcupagents.ensemble.baseline import expected_goals
    assert guarded == expected_goals(1, 99)           # fell back to rank-Elo
    # Without the guard it would use the (thin) fitted strengths instead.
    assert team_lambdas("A", "B", 1, 99, strength=m, min_games=0) != guarded


# ── opponent adjustment: the iteration is what makes a goal-vs-strong-defence count ──

def _opponent_skewed_rows():
    """EliteDef concedes ~nothing (strong D); Minnow leaks (weak D). TeamA beats
    EliteDef 2-0, TeamB beats Minnow 2-0 — identical raw output, different opposition."""
    return [
        _row("EliteDef", "N1", 0, 0, "2026-06-01"), _row("N2", "EliteDef", 0, 1, "2026-05-20"),
        _row("EliteDef", "N3", 1, 0, "2026-05-10"),
        _row("Minnow", "N1", 0, 3, "2026-06-01"), _row("N2", "Minnow", 4, 0, "2026-05-20"),
        _row("Minnow", "N3", 0, 3, "2026-05-10"),
        _row("TeamA", "EliteDef", 2, 0, "2026-06-10"),
        _row("TeamB", "Minnow", 2, 0, "2026-06-10"),
    ]


def test_iteration_rewards_scoring_against_strong_defence():
    rows = _opponent_skewed_rows()
    ka = normalize_key(canonical_name("TeamA"))
    kb = normalize_key(canonical_name("TeamB"))

    one_pass = fit_international_strengths(rows, as_of=AS_OF, shrinkage_k=1.0, iters=1)
    iterated = fit_international_strengths(rows, as_of=AS_OF, shrinkage_k=1.0, iters=50)

    # One pass ignores opponent quality → identical 2-0 records read identical.
    assert abs(one_pass.attack[ka] - one_pass.attack[kb]) < 1e-6
    # Iterating discounts goals scored against a leaky minnow → TeamA rated higher.
    assert iterated.attack[ka] > iterated.attack[kb] + 0.05


def test_iteration_converges_to_a_stable_model():
    rows = _opponent_skewed_rows()
    m = fit_international_strengths(rows, as_of=AS_OF, iters=200, tol=1e-9)
    assert m is not None
    assert all(v > 0 and v < 100 for v in m.attack.values())   # bounded, finite
    assert all(v > 0 and v < 100 for v in m.defense.values())
