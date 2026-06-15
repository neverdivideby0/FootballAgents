"""Most-used XI from Understat playersData → store → report (hermetic)."""

from __future__ import annotations

from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.understat import parse_xi, xi_digest

# Minimal Understat-shaped playersData (time = minutes).
_PLAYERS = [
    {"player_name": "Raya", "position": "GK", "time": "3330", "goals": "0", "assists": "0"},
    {"player_name": "BackupKeeper", "position": "GK", "time": "90", "goals": "0", "assists": "0"},
    {"player_name": "Saliba", "position": "D S", "time": "2609", "goals": "1", "assists": "0"},
    {"player_name": "Gabriel", "position": "D S", "time": "2748", "goals": "3", "assists": "4"},
    {"player_name": "Timber", "position": "D S", "time": "2475", "goals": "3", "assists": "5"},
    {"player_name": "Calafiori", "position": "D S", "time": "1755", "goals": "1", "assists": "2"},
    {"player_name": "Rice", "position": "D M S", "time": "3111", "goals": "4", "assists": "5"},
    {"player_name": "Zubimendi", "position": "M S", "time": "3040", "goals": "5", "assists": "1"},
    {"player_name": "Odegaard", "position": "M S", "time": "1347", "goals": "1", "assists": "6"},
    {"player_name": "Saka", "position": "F M S", "time": "2239", "goals": "7", "assists": "5"},
    {"player_name": "Trossard", "position": "F M S", "time": "2026", "goals": "6", "assists": "6"},
    {"player_name": "Gyokeres", "position": "F S", "time": "2255", "goals": "14", "assists": "1"},
    {"player_name": "Eze", "position": "F M S", "time": "1841", "goals": "7", "assists": "2"},
    {"player_name": "BenchGuy", "position": "F S", "time": "120", "goals": "0", "assists": "0"},
]


def test_parse_xi_picks_one_keeper_and_top_ten_outfield():
    xi = parse_xi(_PLAYERS)
    assert len(xi) == 11
    keepers = [p for p in xi if p["pos"] == "GK"]
    assert len(keepers) == 1 and keepers[0]["name"] == "Raya"     # most minutes GK
    names = {p["name"] for p in xi}
    assert "BackupKeeper" not in names and "BenchGuy" not in names  # low minutes dropped
    assert {"Rice", "Zubimendi", "Gabriel", "Gyokeres"} <= names    # high-minute starters in


def test_xi_digest_groups_by_role():
    d = xi_digest(parse_xi(_PLAYERS))
    assert d.startswith("GK Raya")
    assert "DEF" in d and "MID" in d and "FWD" in d
    assert d.index("DEF") < d.index("FWD")                          # role order preserved


def test_store_roundtrips_xi(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    xi = parse_xi(_PLAYERS)
    store.upsert_situations("PL", "2025-26", "Arsenal FC", {"x": 1}, "understat.com/x", xi=xi)
    got = store.team_xi("PL", "2025-26", "Arsenal FC")
    cov = store.situation_coverage()
    store.close()
    assert got is not None
    rows, src = got
    assert rows[0]["name"] == "Raya" and src == "understat.com/x"
    assert cov["PL"]["situations"] == 1 and cov["PL"]["xis"] == 1


def test_store_xi_absent_when_not_written(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert_situations("PL", "2025-26", "Spurs FC", {"x": 1}, "understat.com/y")
    assert store.team_xi("PL", "2025-26", "Spurs FC") is None
    assert store.situation_coverage()["PL"]["xis"] == 0
    store.close()


def test_player_rows_maps_understat_metrics(tmp_path, monkeypatch):
    """B4: per-player season metrics (shots, key passes, xG/xA, xGBuildup) flow
    from the cached getTeamData call into player_stats rows."""
    from worldcupagents.dataflows.providers.understat import UnderstatProvider

    prov = UnderstatProvider(cache_dir=str(tmp_path / "cache"))
    monkeypatch.setattr(prov, "get_team_data", lambda team, season: {
        "statistics": {}, "players": [{
            "player_name": "Declan Rice", "games": "36", "time": "3111",
            "goals": "4", "npg": "3", "assists": "5", "shots": "40",
            "key_passes": "63", "xG": "3.72", "xA": "8.16",
            "xGBuildup": "12.96", "position": "D M S",
        }],
    })
    rows = prov.player_rows("Arsenal FC", "2025-26", "PL")
    assert len(rows) == 1
    r = rows[0]
    assert r["player"] == "Declan Rice" and r["comp"] == "PL"
    assert r["key_passes"] == 63 and r["shots"] == 40 and r["minutes"] == 3111
    assert r["penalties"] == 1                          # goals - npg
    assert abs(r["xg_buildup"] - 12.96) < 1e-6
    assert r["source"].startswith("https://understat.com/team/Arsenal")

    # And they round-trip through the store with the new columns.
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert_players(rows)
    got = store.players("PL")
    store.close()
    assert got[0]["xa"] == 8.16 and got[0]["xg_buildup"] == 12.96


def test_parse_xi_handles_short_squads():
    xi = parse_xi(_PLAYERS[:4])  # 2 GK + 2 DEF
    assert 1 <= len(xi) <= 4
    assert sum(1 for p in xi if p["pos"] == "GK") == 1
