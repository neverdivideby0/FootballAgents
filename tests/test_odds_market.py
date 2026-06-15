"""Odds capture + de-vigged market baseline (hermetic)."""

from __future__ import annotations

from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.football_data_couk import parse_csv
from worldcupagents.pipelines.backtest import devig_odds, run_backtest

_CSV = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,AvgH,AvgD,AvgA\n"
    "E0,17/08/2024,Man United,Fulham,1,0,1.60,4.20,5.25,1.58,4.10,5.50\n"
    "E0,18/08/2024,Ipswich,Liverpool,0,2,7.50,4.50,1.45,7.20,4.40,1.47\n"
    "E0,19/08/2024,Blank,Game,,,,,,,,\n"                       # skipped
    "E0,20/08/2024,NoOdds FC,Other FC,1,1,,,,,,\n"             # no odds -> None
)


def test_parse_csv_captures_b365_with_avg_fallback():
    rows = parse_csv(_CSV, "PL", "2425")
    by = {r["home"]: r for r in rows}
    assert by["Manchester United FC"]["odds_h"] == 1.60      # B365 preferred
    no_odds = by["NoOdds FC"]
    assert no_odds["odds_h"] is None and no_odds["odds_d"] is None


def test_devig_strips_overround():
    ph, pd, pa = devig_odds(1.60, 4.20, 5.25)
    assert abs(ph + pd + pa - 1.0) < 1e-9
    assert ph > pd > pa and 0.58 < ph < 0.60                  # ~59% home


def test_store_roundtrips_odds(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert(parse_csv(_CSV, "PL", "2425"))
    rows = {r["home"]: r for r in store.all_matches()}
    store.close()
    assert rows["Manchester United FC"]["odds_h"] == 1.60
    assert rows["NoOdds FC"]["odds_h"] is None


def test_backtest_adds_market_model_only_for_rows_with_odds():
    # 3 rows with odds, 1 without -> market scored on 3, others on all 4.
    rows = [
        {"home": "A", "away": "B", "home_goals": 2, "away_goals": 0, "odds_h": 1.6, "odds_d": 4.2, "odds_a": 5.25},
        {"home": "C", "away": "D", "home_goals": 0, "away_goals": 2, "odds_h": 7.5, "odds_d": 4.5, "odds_a": 1.45},
        {"home": "E", "away": "F", "home_goals": 1, "away_goals": 1, "odds_h": 3.0, "odds_d": 3.3, "odds_a": 2.4},
        {"home": "G", "away": "H", "home_goals": 1, "away_goals": 0, "odds_h": None, "odds_d": None, "odds_a": None},
    ]
    res = run_backtest(rows, include_stats_loocv=False)
    assert "market(de-vigged odds)" in res.scores
    m = res.scores["market(de-vigged odds)"]
    assert m.n == 3                                            # the no-odds row skipped
    # Market got the two decisive favourites right (A home, C away).
    assert m.hits >= 2 and 0.0 <= m.mean_brier <= 2.0


def test_no_market_model_without_any_odds():
    rows = [{"home": "A", "away": "B", "home_goals": 1, "away_goals": 0,
             "odds_h": None, "odds_d": None, "odds_a": None}] * 3
    res = run_backtest(rows, include_stats_loocv=False)
    assert "market(de-vigged odds)" not in res.scores
