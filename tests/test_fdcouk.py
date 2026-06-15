"""football-data.co.uk ingester + club aliases (hermetic, mocked HTTP)."""

from __future__ import annotations

from worldcupagents.dataflows.club_aliases import canon_club
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.football_data_couk import (
    fetch_season_rows,
    parse_csv,
    season_url,
)
from worldcupagents.pipelines.fetch_data import fetch_data

_CSV = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
    "E0,11/08/2023,20:00,Burnley,Man City,0,3,A\n"
    "E0,12/08/2023,15:00,Chelsea,Leeds,2,0,H\n"
    "E0,19/08/2023,15:00,Postponed,Game,,,\n"            # blank goals -> skipped
)


def test_club_aliases_map_to_canonical():
    assert canon_club("Man City") == "Manchester City FC"
    assert canon_club("Chelsea") == "Chelsea FC"
    assert canon_club("Leeds") == "Leeds United FC"
    assert canon_club("Some Unknown FC") == "Some Unknown FC"   # passthrough


def test_season_url_uses_division_code():
    assert season_url("PL", "2324").endswith("/2324/E0.csv")
    assert season_url("PD", "2324").endswith("/SP1.csv")


def test_parse_csv_normalises_names_dates_and_skips_blanks():
    rows = parse_csv(_CSV, "PL", "2324")
    assert len(rows) == 2                                  # postponed row skipped
    assert rows[0]["home"] == "Burnley FC" and rows[0]["away"] == "Manchester City FC"
    assert rows[0]["date"] == "2023-08-11"
    assert rows[1]["home"] == "Chelsea FC" and rows[1]["hg"] == 2
    assert rows[1]["source"] == "fdcouk:PL:2324"


def test_fetch_season_rows_with_injected_http():
    rows = fetch_season_rows("PL", "2324", http_get=lambda url: _CSV)
    assert len(rows) == 2 and rows[1]["away"] == "Leeds United FC"


def test_fetch_season_rows_unsupported_comp_is_empty():
    assert fetch_season_rows("WC", "2324", http_get=lambda url: _CSV) == []


def test_fetch_data_seasons_merges_into_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "worldcupagents.dataflows.providers.football_data_couk.fetch_season_rows",
        lambda comp, season, http_get=None: parse_csv(_CSV, comp, season),
    )
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL"}
    res = fetch_data(cfg, seasons=["2223", "2324"])
    assert res["source"] == "fdcouk"
    # 2 valid rows per season, same fixtures -> idempotent by date|home|away
    store = MatchStore.from_config(cfg)
    teams = {r["home"] for r in store.all_matches()} | {r["away"] for r in store.all_matches()}
    store.close()
    assert "Chelsea FC" in teams and "Manchester City FC" in teams
