"""API-Football player-stats provider + richer-stats plumbing (hermetic)."""

from __future__ import annotations

import pytest

from worldcupagents.agents.schemas import PlayerStat
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.api_football import ApiFootballProvider
from worldcupagents.recall import players_digest

_RESPONSE = {
    "response": [
        {
            "player": {"name": "Mohamed Salah"},
            "statistics": [{
                "team": {"name": "Liverpool"},
                "games": {"appearences": 38, "minutes": 3200, "rating": "7.85"},
                "goals": {"total": 29, "assists": 18},
                "passes": {"accuracy": 78, "key": 92},
                "penalty": {"scored": 9},
            }],
        },
    ]
}


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.last_headers = None

    def get_json(self, url, headers=None, ttl=None):
        self.last_headers = headers
        return self.payload


class MappingHTTP:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls: list[str] = []
        self.last_headers = None

    def get_json(self, url, headers=None, ttl=None):
        self.calls.append(url)
        self.last_headers = headers
        for frag, payload in self.mapping.items():
            if frag in url:
                return payload
        raise KeyError(url)


def test_requires_key():
    with pytest.raises(ValueError):
        ApiFootballProvider(api_key="", competition="PL", season="2025")


def test_parses_rich_player_stats_and_aliases_team():
    http = FakeHTTP(_RESPONSE)
    prov = ApiFootballProvider(api_key="k", competition="PL", season="2025", http=http)
    rows = prov.get_scorers()

    assert http.last_headers == {"x-apisports-key": "k"}     # auth header sent
    r = rows[0]
    assert r["player"] == "Mohamed Salah"
    assert r["team"] == "Liverpool FC"                       # club alias applied
    assert r["goals"] == 29 and r["assists"] == 18 and r["penalties"] == 9
    assert r["pass_accuracy"] == 78.0 and r["key_passes"] == 92
    assert r["minutes"] == 3200 and r["rating"] == 7.85
    assert r["source"] == "api_football:PL/topscorers"


def test_unsupported_competition_is_empty():
    prov = ApiFootballProvider(api_key="k", competition="ZZ", season="2025", http=FakeHTTP(_RESPONSE))
    assert prov.get_scorers() == []


def test_store_roundtrips_rich_columns(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert_players(ApiFootballProvider(api_key="k", competition="PL", season="2025",
                                             http=FakeHTTP(_RESPONSE)).get_scorers())
    rows = store.players(comp="PL")
    store.close()
    assert rows[0]["pass_accuracy"] == 78.0 and rows[0]["rating"] == 7.85


def test_players_digest_includes_pass_accuracy_and_rating():
    ps = [PlayerStat(player="Salah", goals=29, assists=18, matches=38, pass_accuracy=78.0, rating=7.85)]
    d = players_digest(ps)
    assert "78% pass" in d and "7.85 rating" in d


def test_migration_adds_columns_to_legacy_db(tmp_path):
    # Simulate a pre-rich DB (old 8-column player_stats), then open with MatchStore.
    import sqlite3
    p = tmp_path / "data" / "football.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.executescript(
        "CREATE TABLE player_stats (pkey TEXT PRIMARY KEY, comp TEXT, player TEXT, team TEXT,"
        " goals INTEGER, assists INTEGER, penalties INTEGER, matches INTEGER, source TEXT);"
    )
    con.execute("INSERT INTO player_stats VALUES ('PL|X|Y','PL','X','Y',1,0,0,1,'old')")
    con.commit(); con.close()

    store = MatchStore(p)                       # __init__ runs the migration
    store.upsert_players([{"comp": "PL", "player": "Salah", "team": "Liverpool FC",
                           "goals": 29, "assists": 18, "penalties": 9, "matches": 38,
                           "pass_accuracy": 78.0, "key_passes": 92, "minutes": 3200,
                           "rating": 7.85, "source": "api_football"}])
    rows = {r["player"]: r for r in store.players(comp="PL")}
    store.close()
    assert rows["Salah"]["pass_accuracy"] == 78.0   # new column works on the migrated DB


def test_recent_national_results_parse_into_match_rows():
    http = MappingHTTP({
        "teams?name=United+States": {"response": [{
            "team": {"id": 2384, "name": "USA", "national": True},
            "country": "United States",
        }]},
        "fixtures?team=2384&season=2026&from=": {"response": [{
            "fixture": {"date": "2026-03-26T01:00:00+00:00"},
            "league": {"name": "Friendlies"},
            "teams": {"home": {"name": "USA"}, "away": {"name": "Japan"}},
            "goals": {"home": 2, "away": 1},
        }, {
            "fixture": {"date": "2025-11-18T01:00:00+00:00"},
            "league": {"name": "Friendlies"},
            "teams": {"home": {"name": "USA"}, "away": {"name": "Mexico"}},
            "goals": {"home": 0, "away": 0},
        }]},
    })
    prov = ApiFootballProvider(api_key="k", competition="WC", season="2026", http=http)

    rows = prov.get_recent_national_results("United States", limit=5)

    assert http.last_headers == {"x-apisports-key": "k"}
    assert rows == [{
        "date": "2026-03-26",
        "comp": "INT",
        "home": "United States",
        "away": "Japan",
        "hg": 2,
        "ag": 1,
        "xg_home": None,
        "xg_away": None,
        "source": "api_football:national:USA:last5:Friendlies",
    }, {
        "date": "2025-11-18",
        "comp": "INT",
        "home": "United States",
        "away": "Mexico",
        "hg": 0,
        "ag": 0,
        "xg_home": None,
        "xg_away": None,
        "source": "api_football:national:USA:last5:Friendlies",
    }]
