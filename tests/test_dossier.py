"""Pre-match dossier: fdcouk stat columns, tempo profile, forte, squad-scoping,
5-year recency (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.football_data_couk import parse_csv


_CSV = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A\n"
    "E0,17/08/2024,Arsenal,Chelsea,3,0,18,6,8,2,9,12,7,3,1,2,0,1,1.60,4.20,5.25\n"
    "E0,24/08/2024,Chelsea,Arsenal,1,2,10,15,3,6,11,8,4,8,2,1,0,0,3.10,3.40,2.30\n"
)


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["fd_competition"] = "PL"
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def test_parse_csv_captures_stat_columns():
    rows = {(r["home"], r["away"]): r for r in parse_csv(_CSV, "PL", "2425")}
    a = rows[("Arsenal FC", "Chelsea FC")]
    assert a["sh_home"] == 18 and a["sot_home"] == 8 and a["fouls_home"] == 9
    assert a["corners_home"] == 7 and a["yellow_away"] == 2
    assert a["red_home"] == 0 and a["red_away"] == 1     # HR=0, AR=1


def test_store_roundtrips_stats_and_profiles(tmp_path):
    store = MatchStore.from_config(_cfg(tmp_path))
    store.upsert(parse_csv(_CSV, "PL", "2425"))
    # Arsenal: home game (18 shots, 8 SoT, 9 fouls, 7 corners) + away game
    # (15 shots, 6 SoT, 8 fouls, 8 corners) → averages over 2.
    prof = store.team_stat_profile("Arsenal FC", comp="PL")
    store.close()
    assert prof["n"] == 2
    assert prof["shots"] == 16.5 and prof["sot"] == 7.0
    assert prof["corners"] == 7.5 and prof["fouls"] == 8.5
    assert prof["shots_a"] == 8.0          # conceded: 6 (home) + 10 (away)


def test_stat_profile_recency_filter(tmp_path):
    csv_old = _CSV.replace("17/08/2024", "17/08/2015").replace("24/08/2024", "24/08/2015")
    store = MatchStore.from_config(_cfg(tmp_path))
    store.upsert(parse_csv(csv_old, "PL", "1516"))
    assert store.team_stat_profile("Arsenal FC", comp="PL", since="2021-01-01") is None
    assert store.team_stat_profile("Arsenal FC", comp="PL")["n"] == 2   # no filter
    store.close()


def test_team_forte_reads_attack_defense():
    from worldcupagents.ensemble.strength import fit_strengths, team_forte
    # Build a tiny league where 'Wall FC' concedes nothing and 'Cannon FC' scores freely.
    matches = [
        {"home": "Cannon FC", "away": "Mid FC", "hg": 4, "ag": 1},
        {"home": "Mid FC", "away": "Cannon FC", "hg": 1, "ag": 3},
        {"home": "Wall FC", "away": "Mid FC", "hg": 1, "ag": 0},
        {"home": "Mid FC", "away": "Wall FC", "hg": 0, "ag": 1},
    ]
    model = fit_strengths(matches)
    cannon = team_forte(model, "Cannon FC")
    wall = team_forte(model, "Wall FC")
    assert cannon["attack"] > 1.5                         # scores well above average
    assert cannon["attack"] > wall["attack"]
    assert wall["solidity"] > 1.0 and "defense-leaning" in wall["label"]
    assert team_forte(model, "Nonexistent FC") is None


def test_top_players_squad_scoping(tmp_path):
    store = MatchStore.from_config(_cfg(tmp_path))
    store.upsert_players([
        {"comp": "PL", "player": "Bukayo Saka", "team": "Arsenal FC", "goals": 7, "assists": 5},
        {"comp": "PL", "player": "Old Striker", "team": "Arsenal FC", "goals": 20, "assists": 0},
    ])
    store.close()
    from worldcupagents.recall import top_players
    cfg = _cfg(tmp_path)
    # No squad → both, top goals first.
    assert [p.player for p in top_players("Arsenal FC", cfg)][0] == "Old Striker"
    # Squad-scoped → only the squad member survives even with fewer goals.
    scoped = top_players("Arsenal FC", cfg, squad=["Bukayo Saka"])
    assert [p.player for p in scoped] == ["Bukayo Saka"]


def test_recent_team_matches_per_match_stats(tmp_path):
    store = MatchStore.from_config(_cfg(tmp_path))
    store.upsert(parse_csv(_CSV, "PL", "2425"))     # Arsenal home v Chelsea, then away
    rows = store.recent_team_matches("Arsenal FC", comp="PL", limit=6)
    store.close()
    assert len(rows) == 2
    newest = rows[0]                                # 24/08 Chelsea (home) v Arsenal (away)
    assert newest["venue"] == "A" and newest["opponent"] == "Chelsea FC"
    assert newest["result"] == "W" and newest["gf"] == 2 and newest["ga"] == 1
    assert newest["shots"] == 15 and newest["sot"] == 6        # Arsenal's away figures
    assert newest["shots_against"] == 10 and newest["corners"] == 8


def test_squad_club_stats_matches_by_name_across_leagues(tmp_path):
    from worldcupagents.recall import squad_club_stats
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    # A national squad player whose CLUB row lives in a different league (BL1),
    # plus an INT goalscorer row that must NOT be preferred.
    store.upsert_players([
        {"comp": "BL1", "player": "Marcel Sabitzer", "team": "Borussia Dortmund FC",
         "goals": 6, "assists": 4, "xg": 5.1, "xa": 3.2, "key_passes": 40, "minutes": 2400},
        {"comp": "INT", "player": "Marcel Sabitzer", "team": "Austria", "goals": 3},
        {"comp": "PL", "player": "Someone Else", "team": "Arsenal FC", "goals": 9},
    ])
    store.close()
    out = squad_club_stats(cfg, ["Marcel Sabitzer", "Konrad Laimer"])
    assert len(out) == 1
    p = out[0]
    assert p.player == "Marcel Sabitzer" and p.team == "Borussia Dortmund FC"  # club, not Austria
    assert p.xg == 5.1                                    # richest (club) row won


def test_build_dossier_assembles_blocks(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert(parse_csv(_CSV, "PL", "2425"))
    store.close()
    from worldcupagents.pipelines.prematch import build_dossier
    doss = build_dossier("Arsenal FC", "Chelsea FC", cfg)
    assert doss["home"]["team"] == "Arsenal FC"
    assert doss["home"]["tempo"]["n"] == 2          # fdcouk stats reached the block
    assert "since" in doss and doss["since"] < "2024-01-01"
