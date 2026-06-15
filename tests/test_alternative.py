"""Alternative outcome / upset watch — the honest counterweight (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, Outcome, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.ensemble.alternative import build_alternative
from worldcupagents.ensemble.baseline import score_grid


def test_alternative_is_the_runner_up_outcome():
    grid = score_grid(1.6, 1.1)
    alt = build_alternative(grid, 0.50, 0.27, 0.23, Outcome.HOME_WIN, knockout=False)
    assert alt.outcome == Outcome.DRAW            # second-most-likely
    assert alt.probability == 0.27
    assert alt.gap == 0.23                        # 0.50 − 0.27
    assert "-" in alt.scoreline


def test_live_flag_trips_above_threshold():
    grid = score_grid(1.4, 1.3)
    live = build_alternative(grid, 0.44, 0.26, 0.30, Outcome.HOME_WIN, knockout=False)
    assert live.outcome == Outcome.AWAY_WIN and live.live is True
    assert "live alternative" in live.narrative
    longshot = build_alternative(grid, 0.70, 0.18, 0.12, Outcome.HOME_WIN, knockout=False)
    assert longshot.live is False and "long shot" in longshot.narrative


def test_knockout_alternative_skips_folded_draw():
    grid = score_grid(1.8, 1.0)
    # knockout: p_draw folded to 0 → alternative is the losing side, with pens tag
    alt = build_alternative(grid, 0.68, 0.0, 0.32, Outcome.HOME_WIN, knockout=True)
    assert alt.outcome == Outcome.AWAY_WIN
    assert "pens" in alt.scoreline


def test_verdict_always_carries_an_alternative():
    from worldcupagents.agents.schemas import TeamProfile
    from worldcupagents.ensemble.verdict import assemble_verdict
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    home = TeamProfile(team="Brazil", fifa_rank=3)
    away = TeamProfile(team="Mexico", fifa_rank=12)
    v = assemble_verdict(cfg, Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP),
                         home, away, None, 0.6)
    assert v.alternative is not None
    assert v.alternative.outcome != v.outcome      # genuinely the OTHER result
    assert 0.0 <= v.alternative.probability <= 1.0


def test_upset_factors_explain_the_path(tmp_path):
    from worldcupagents.agents.schemas import MatchResult, TeamProfile
    from worldcupagents.dataflows.match_store import MatchStore
    from worldcupagents.dataflows.providers.football_data_couk import parse_csv
    from worldcupagents.ensemble.alternative import build_alternative, upset_factors

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["fd_competition"] = "PL"
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    store = MatchStore.from_config(cfg)
    store.upsert(parse_csv(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR\n"
        "E0,17/08/2024,Arsenal,Chelsea,2,1,10,18,4,7,9,11,4,9,1,2,0,0\n", "PL", "2425"))
    store.upsert_situations("PL", "2025-26", "Chelsea FC",
                            {"FromCorner": {"goals": 8, "shots": 40, "xG": 7.0}}, "understat")
    store.close()
    cfg["season"] = "2025-26"

    underdog = TeamProfile(team="Chelsea FC", form=[
        MatchResult(opponent="X", goals_for=2, goals_against=0, date="2026-05-01"),
        MatchResult(opponent="Y", goals_for=1, goals_against=0, date="2026-04-24")])
    favourite = TeamProfile(team="Arsenal FC")
    fx = Fixture(home="Arsenal FC", away="Chelsea FC", stage=Stage.GROUP)
    grid = score_grid(1.5, 1.2)
    alt = build_alternative(grid, 0.5, 0.25, 0.25, Outcome.HOME_WIN, knockout=False)
    # Alt is a draw here; force an away-win alt to exercise underdog framing.
    alt.outcome = Outcome.AWAY_WIN
    factors = upset_factors(cfg, fx, favourite, underdog, alt)
    blob = " ".join(factors).lower()
    assert "dead balls" in blob or "corners" in blob          # Chelsea set-piece threat
    assert "arsenal" in blob                                   # favourite frailty named
