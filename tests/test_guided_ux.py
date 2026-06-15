"""M-ML3 tests — league-aware team sourcing for the guided flow (hermetic)."""

from __future__ import annotations

from worldcupagents.cli import _teams_for_league
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.leagues.registry import get_league


def test_world_cup_teams_are_confederation_grouped():
    groups = _teams_for_league(get_league("WC2026"))
    assert "UEFA (Europe)" in groups
    total = sum(len(v) for v in groups.values())
    assert total == 48


def test_club_league_teams_pulled_from_store(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert([
        {"date": "2025-08-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC", "hg": 2, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2025-08-08", "comp": "PL", "home": "Arsenal FC", "away": "Chelsea FC", "hg": 1, "ag": 1, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2024-01-01", "comp": "SA", "home": "Juventus", "away": "Napoli", "hg": 1, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},  # other comp
    ])
    store.close()

    groups = _teams_for_league(get_league("PL"), config={"data_dir": str(tmp_path / "data")})
    teams = [t for ts in groups.values() for t in ts]
    assert set(teams) == {"Arsenal FC", "Chelsea FC", "Leeds United FC"}   # PL only, no Juventus
    assert "Premier League 2025-26" in groups                              # single section, the league name


def test_club_league_without_store_is_empty(tmp_path):
    groups = _teams_for_league(get_league("PL"), config={"data_dir": str(tmp_path / "nope")})
    assert groups == {"Premier League 2025-26": []}
