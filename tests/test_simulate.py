"""D1 — tournament simulator (hermetic: synthetic fixtures, injected λ)."""

from __future__ import annotations

from worldcupagents.pipelines.simulate import (
    _bracket_order, _qualifier_target, simulate_tournament)

# Two groups of four — the generalized format gives top-2 each → a 4-team KO.
_TEAMS = {"A": ["Alpha", "Beta", "Gamma", "Delta"],
          "B": ["Epsilon", "Zeta", "Eta", "Theta"]}


def _fixtures(played: list[tuple] = ()):  # all round-robin group games
    fx = []
    done = {(h, a): (hg, ag) for h, a, hg, ag in played}
    for g, ts in _TEAMS.items():
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                h, a = ts[i], ts[j]
                hg, ag = done.get((h, a), (None, None))
                fx.append({"stage": "GROUP_STAGE", "group": g,
                           "status": "FINISHED" if hg is not None else "TIMED",
                           "home": h, "away": a, "hg": hg, "ag": ag, "date": "2026-06-12"})
    return fx


def _strong_alpha(home: str, away: str):
    """Alpha is far stronger than everyone; all else even."""
    lh = 3.0 if home == "Alpha" else 1.2
    la = 3.0 if away == "Alpha" else 1.2
    return lh, la


def test_qualifier_target_matches_2026_format():
    assert _qualifier_target(12) == 32           # 24 direct + 8 thirds
    assert _qualifier_target(2) == 4             # tests: top-2 only
    assert _qualifier_target(8) == 16            # historic 32-team format


def test_bracket_order_keeps_top_seeds_apart():
    order = _bracket_order(4)
    assert order == [0, 3, 1, 2]                 # 1v4 and 2v3; 1 meets 2 in the final
    o32 = _bracket_order(32)
    assert sorted(o32) == list(range(32))
    # seeds 0 and 1 land in opposite halves of the draw
    assert (o32.index(0) < 16) != (o32.index(1) < 16)


def test_dominant_team_wins_most_tournaments():
    res = simulate_tournament(fixtures=_fixtures(), lambdas_fn=_strong_alpha,
                              n=400, seed=7)
    assert res.n == 400 and res.played == 0 and res.remaining == 12
    champ_share = res.share("Alpha", "champion")
    assert champ_share > 0.5                     # clearly the favourite
    assert sum(res.share(t, "champion") for ts in _TEAMS.values() for t in ts) == 1.0


def test_locked_results_are_respected():
    # Lock all three of Alpha's group games as heavy LOSSES — facts beat strength.
    played = [("Alpha", "Beta", 0, 9), ("Alpha", "Gamma", 0, 9), ("Alpha", "Delta", 0, 9)]
    res = simulate_tournament(fixtures=_fixtures(played), lambdas_fn=_strong_alpha,
                              n=200, seed=7)
    assert res.played == 3
    # Alpha lost all three group games as fact -> can never top the group.
    assert res.share("Alpha", "group_win") == 0.0
    assert res.share("Alpha", "champion") == 0.0


def test_deterministic_with_seed_and_empty_without_fixtures():
    a = simulate_tournament(fixtures=_fixtures(), lambdas_fn=_strong_alpha, n=50, seed=3)
    b = simulate_tournament(fixtures=_fixtures(), lambdas_fn=_strong_alpha, n=50, seed=3)
    assert a.teams == b.teams
    assert simulate_tournament(fixtures=[], lambdas_fn=_strong_alpha, n=10).n == 0


def test_real_r32_pairings_used_when_present():
    fx = _fixtures() + [{"stage": "LAST_32", "group": None, "status": "TIMED",
                         "home": "Alpha", "away": "Zeta", "hg": None, "ag": None,
                         "date": "2026-06-29"},
                        {"stage": "LAST_32", "group": None, "status": "TIMED",
                         "home": "Epsilon", "away": "Beta", "hg": None, "ag": None,
                         "date": "2026-06-29"}]
    res = simulate_tournament(fixtures=fx, lambdas_fn=_strong_alpha, n=50, seed=3)
    assert res.bracket_source.startswith("official LAST_32")
