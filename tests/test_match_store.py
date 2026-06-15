"""M1.1 tests — SQLite match store + ingesters (hermetic, no network)."""

from __future__ import annotations

from worldcupagents.dataflows.match_store import MatchStore, db_path
from worldcupagents.pipelines.backtest import _SAMPLE
from worldcupagents.pipelines.fetch_data import fetch_data, rows_from_csv, rows_from_football_data_org


def test_store_upsert_count_and_read(tmp_path):
    store = MatchStore(tmp_path / "football.db")
    rows = [
        {"date": "2022-12-18", "comp": "WC", "home": "Argentina", "away": "France",
         "hg": 3, "ag": 3, "xg_home": 2.4, "xg_away": 1.1, "source": "test"},
        {"date": "2022-12-14", "comp": "WC", "home": "France", "away": "Morocco",
         "hg": 2, "ag": 0, "xg_home": None, "xg_away": None, "source": "test"},
    ]
    assert store.upsert(rows) == 2
    assert store.count() == 2
    got = store.all_matches()
    assert {r["home"] for r in got} == {"Argentina", "France"}
    assert got[0]["date"] <= got[1]["date"]  # ordered by date
    store.close()


def test_store_upsert_is_idempotent(tmp_path):
    store = MatchStore(tmp_path / "football.db")
    row = [{"date": "2022-12-18", "comp": "WC", "home": "Argentina", "away": "France",
            "hg": 3, "ag": 3, "xg_home": None, "xg_away": None, "source": "t"}]
    store.upsert(row)
    store.upsert(row)  # same key -> replace, not duplicate
    assert store.count() == 1
    store.close()


def test_rows_from_csv_reads_backtest_sample():
    rows = rows_from_csv(_SAMPLE)
    assert len(rows) == 10
    assert rows[0]["home"] == "France" and rows[0]["hg"] == 4
    assert rows[0]["source"] == "csv"


def test_fetch_data_csv_seed_then_idempotent(tmp_path):
    cfg = {"data_dir": str(tmp_path)}
    res = fetch_data(cfg, csv_path=_SAMPLE)
    assert res["added"] == 10 and res["total"] == 10 and res["source"] == "csv"
    # re-running the same seed must not duplicate
    res2 = fetch_data(cfg, csv_path=_SAMPLE)
    assert res2["total"] == 10
    assert db_path(cfg).exists()


def test_fetch_data_api_football_national_history_dedupes(tmp_path, monkeypatch):
    class FakeApiFootball:
        @classmethod
        def from_config(cls, config):
            return cls()

        def get_recent_national_results(self, team, limit=5):
            return [{
                "date": "2026-03-26",
                "comp": "INT",
                "home": "United States",
                "away": "Japan",
                "hg": 2,
                "ag": 1,
                "xg_home": None,
                "xg_away": None,
                "source": f"api_football:national:{team}:last{limit}:Friendlies",
            }]

    monkeypatch.setattr("worldcupagents.dataflows.world_cup_2026.WC2026_TEAMS", ["United States", "Japan"])
    monkeypatch.setattr("worldcupagents.dataflows.providers.api_football.ApiFootballProvider", FakeApiFootball)

    cfg = {"data_dir": str(tmp_path)}
    res = fetch_data(cfg, national_history=True, national_limit=1)

    assert res["source"] == "api_football_national"
    assert res["added"] == 1 and res["total"] == 1
    res2 = fetch_data(cfg, national_history=True, national_limit=1)
    assert res2["added"] == 0 and res2["total"] == 1


# --- football-data.org ingest with mocked HTTP ---

class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, headers=None, ttl=None):
        return self.payload


def test_football_data_org_ingest_parses_finished_matches(monkeypatch):
    from worldcupagents.dataflows.providers import football_data_org as fdo

    payload = {"matches": [
        {"utcDate": "2026-06-12T19:00:00Z", "homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "Poland"},
         "score": {"fullTime": {"home": 2, "away": 1}}},
        {"utcDate": "2026-06-12T22:00:00Z", "homeTeam": {"name": "Qatar"}, "awayTeam": {"name": "Ecuador"},
         "score": {"fullTime": {"home": None, "away": None}}},  # not finished -> skipped
    ]}
    provider = fdo.FootballDataOrgProvider(token="x", competition="WC", http=FakeHTTP(payload))
    monkeypatch.setattr(
        "worldcupagents.pipelines.fetch_data.FootballDataOrgProvider.from_config",
        classmethod(lambda cls, config: provider),
    )
    rows = rows_from_football_data_org({})
    assert len(rows) == 1                       # the unfinished match is skipped
    assert rows[0]["home"] == "Mexico" and rows[0]["hg"] == 2
    assert rows[0]["source"] == "football_data_org:WC"
