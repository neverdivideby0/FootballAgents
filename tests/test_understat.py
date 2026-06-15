"""Understat provider tests — situations punditry + xG fill (hermetic, mocked HTTP)."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.club_aliases import understat_name
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.understat import UnderstatProvider, situations_digest
from worldcupagents.pipelines.fetch_data import fetch_understat_xg

_PAYLOAD = {
    "dates": [
        {"datetime": "2025-08-17 15:30:00",
         "h": {"title": "Manchester United"}, "a": {"title": "Arsenal"},
         "xG": {"h": "1.37", "a": "1.33"}},
        {"datetime": "2026-09-01 15:00:00",   # unplayed: empty xG -> skipped
         "h": {"title": "Arsenal"}, "a": {"title": "Chelsea"}, "xG": {"h": "", "a": ""}},
    ],
    "players": [],
    "statistics": {
        "situation": {
            "OpenPlay": {"shots": 395, "goals": 44, "xG": 53.9, "against": {"shots": 240, "goals": 20, "xG": 26.7}},
            "FromCorner": {"shots": 110, "goals": 19, "xG": 20.5, "against": {"shots": 43, "goals": 4, "xG": 5.7}},
            "SetPiece": {"shots": 40, "goals": 4, "xG": 6.2, "against": {"shots": 21, "goals": 2, "xG": 2.6}},
            "Penalty": {"shots": 4, "goals": 4, "xG": 3.0, "against": {"shots": 0, "goals": 0, "xG": 0}},
        },
    },
}


class FakeHTTP:
    def __init__(self, payload=_PAYLOAD):
        self.payload = payload
        self.urls: list[str] = []

    def get_json(self, url, headers=None, ttl=None):
        self.urls.append(url)
        return self.payload


def test_understat_name_mapping():
    assert understat_name("Arsenal FC") == "Arsenal"
    assert understat_name("Manchester United FC") == "Manchester United"
    assert understat_name("Wolverhampton Wanderers FC") == "Wolverhampton Wanderers"
    assert understat_name("AFC Bournemouth") == "Bournemouth"


def test_situations_and_digest():
    prov = UnderstatProvider(http=FakeHTTP())
    sit, url = prov.situations("Arsenal FC", "2025-26")
    assert sit["FromCorner"]["goals"] == 19
    assert url.endswith("/team/Arsenal/2025")
    line = situations_digest(sit, "Arsenal FC")
    assert "19 from corners (xG 20.5)" in line
    assert "4 from set pieces" in line
    assert "Conceded:" in line and "20 from open play" in line


def test_match_xg_rows_canonical_names_and_skips_unplayed():
    rows = UnderstatProvider(http=FakeHTTP()).match_xg_rows("Arsenal FC", "2025-26")
    assert len(rows) == 1                                   # the unplayed one skipped
    r = rows[0]
    assert r["home"] == "Manchester United FC" and r["away"] == "Arsenal FC"
    assert r == {"date": "2025-08-17", "home": "Manchester United FC", "away": "Arsenal FC",
                 "xg_home": 1.37, "xg_away": 1.33}


def test_fetch_understat_xg_updates_store(tmp_path, monkeypatch):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["fd_competition"] = "PL"
    cfg["season"] = "2025-26"

    store = MatchStore.from_config(cfg)
    store.upsert([{"date": "2025-08-17", "comp": "PL", "home": "Manchester United FC",
                   "away": "Arsenal FC", "hg": 1, "ag": 2, "xg_home": None, "xg_away": None,
                   "source": "t"}])
    store.close()

    monkeypatch.setattr(
        "worldcupagents.dataflows.providers.understat.UnderstatProvider.from_config",
        classmethod(lambda cls, config: UnderstatProvider(http=FakeHTTP())),
    )
    res = fetch_understat_xg(cfg)
    assert res["teams"] == 2                                # both PL teams got situations
    assert res["xg_rows"] >= 1                              # the stored row got xG

    store = MatchStore.from_config(cfg)
    row = store.all_matches()[0]
    sit = store.situations("PL", "2025-26", "Arsenal FC")
    store.close()
    assert row["xg_home"] == 1.37 and row["xg_away"] == 1.33
    assert sit is not None and sit[0]["FromCorner"]["goals"] == 19


def test_update_xg_never_creates_rows(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    assert store.update_xg("2025-01-01", "A", "B", 1.0, 2.0) is False
    assert store.count() == 0
    store.close()
