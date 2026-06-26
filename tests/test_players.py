"""Per-player metrics — store, provider parse, recall (hermetic)."""

from __future__ import annotations

from worldcupagents.agents.schemas import PlayerStat
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.recall import players_digest, top_players


def _seed_players(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert_players([
        {"comp": "PL", "player": "Erling Haaland", "team": "Manchester City FC",
         "goals": 27, "assists": 8, "penalties": 3, "matches": 36, "source": "t"},
        {"comp": "PL", "player": "Phil Foden", "team": "Manchester City FC",
         "goals": 9, "assists": 6, "penalties": 0, "matches": 34, "source": "t"},
        {"comp": "PL", "player": "Cole Palmer", "team": "Chelsea FC",
         "goals": 15, "assists": 9, "penalties": 5, "matches": 35, "source": "t"},
        {"comp": "SA", "player": "Someone Else", "team": "Manchester City FC",  # wrong comp
         "goals": 99, "assists": 0, "penalties": 0, "matches": 1, "source": "t"},
    ])
    store.close()


def test_store_player_upsert_and_query(tmp_path):
    _seed_players(tmp_path)
    store = MatchStore(tmp_path / "data" / "football.db")
    pl = store.players(comp="PL")
    store.close()
    assert len(pl) == 3 and {r["player"] for r in pl} >= {"Erling Haaland", "Cole Palmer"}


def test_player_upsert_idempotent(tmp_path):
    _seed_players(tmp_path)
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert_players([{"comp": "PL", "player": "Erling Haaland", "team": "Manchester City FC",
                           "goals": 28, "assists": 8, "penalties": 3, "matches": 37, "source": "t"}])
    pl = [r for r in store.players(comp="PL") if r["player"] == "Erling Haaland"]
    store.close()
    assert len(pl) == 1 and pl[0]["goals"] == 28   # replaced, not duplicated


def test_top_players_ranks_by_goal_contribution_and_scopes_team(tmp_path):
    _seed_players(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL"}
    top = top_players("Manchester City FC", cfg)
    assert [p.player for p in top] == ["Erling Haaland", "Phil Foden"]   # City only, sorted
    assert top[0].goal_contributions == 35
    assert all(isinstance(p, PlayerStat) for p in top)


def test_top_players_competition_scoped(tmp_path):
    _seed_players(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL"}
    names = {p.player for p in top_players("Manchester City FC", cfg)}
    assert "Someone Else" not in names   # the SA row is excluded


def test_top_players_empty_without_store(tmp_path):
    assert top_players("X", {"data_dir": str(tmp_path / "nope"), "fd_competition": "PL"}) == []


def test_players_digest_formats():
    ps = [PlayerStat(player="Haaland", team="City", goals=27, assists=8, matches=36)]
    assert "Haaland: 27G/8A in 36" in players_digest(ps)


# provider scorers parsing (mocked HTTP)

class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, headers=None, ttl=None):
        return self.payload


def test_provider_get_scorers_parses(tmp_path):
    from worldcupagents.dataflows.providers.football_data_org import FootballDataOrgProvider
    payload = {"scorers": [
        {"player": {"name": "Erling Haaland"}, "team": {"name": "Manchester City FC"},
         "goals": 27, "assists": 8, "penalties": 3, "playedMatches": 36},
        {"player": {"name": "Ollie Watkins"}, "team": {"name": "Aston Villa FC"},
         "goals": 16, "assists": 3, "penalties": None, "playedMatches": 37},
    ]}
    prov = FootballDataOrgProvider(token="x", competition="PL", http=FakeHTTP(payload))
    rows = prov.get_scorers()
    assert rows[0]["player"] == "Erling Haaland" and rows[0]["goals"] == 27
    assert rows[1]["penalties"] == 0   # None coerced to 0
    assert rows[0]["source"] == "football_data_org:PL/scorers"
