"""Data-hoard pipeline tests — hermetic, no network."""

from __future__ import annotations

import copy
import sqlite3

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines import hoard_data as hd


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    return cfg


def _fixtures(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "results.csv").write_text(
        "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
        "2024-01-01,Spain,France,2,1,Friendly,Madrid,Spain,FALSE\n"
        "2024-01-02,Argentina,Brazil,1,1,Copa America,Miami,United States,TRUE\n"
        "2026-06-01,Spain,Argentina,NA,NA,FIFA World Cup,Dallas,United States,TRUE\n",
        encoding="utf-8",
    )
    (src / "shootouts.csv").write_text(
        "date,home_team,away_team,winner,first_shooter\n"
        "2024-01-02,Argentina,Brazil,Argentina,Brazil\n",
        encoding="utf-8",
    )
    (src / "goalscorers.csv").write_text(
        "date,home_team,away_team,team,scorer,minute,own_goal,penalty\n"
        "2024-01-01,Spain,France,Spain,Alvaro Example,10,FALSE,FALSE\n"
        "2024-01-01,Spain,France,France,Own Example,20,TRUE,FALSE\n"
        "2024-01-02,Argentina,Brazil,Argentina,Leo Example,55,FALSE,TRUE\n",
        encoding="utf-8",
    )
    (src / "former_names.csv").write_text(
        "current,former,start_date,end_date\n"
        "DR Congo,Zaire,1971,1997\n",
        encoding="utf-8",
    )
    return src


def test_hoard_international_results_populates_warehouse_and_summaries(tmp_path, monkeypatch):
    fixtures = _fixtures(tmp_path)

    def fake_fetch(url, dest, refresh):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((fixtures / dest.name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(hd, "_fetch_file", fake_fetch)
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260611")

    res = hd.hoard_international_results(_cfg(tmp_path), refresh=True)

    assert res.counts["wh_matches"] == 2              # NA future row skipped
    assert res.counts["wh_goals"] == 3
    assert res.counts["summary_matches"] == 2
    assert res.counts["summary_player_stats"] == 2    # own goal excluded

    store = MatchStore.from_config(_cfg(tmp_path))
    counts = store.warehouse_counts()
    players = store.players("INT")
    aliases = store.conn.execute("SELECT alias FROM wh_team_aliases WHERE alias = 'Zaire'").fetchall()
    raw = store.raw_snapshots()
    store.close()

    assert counts["wh_matches"] == 2
    assert {p["player"] for p in players} == {"Alvaro Example", "Leo Example"}
    assert aliases
    assert raw[0]["source_id"] == "international_results" and raw[0]["files"] == 3


def test_hoard_international_results_is_idempotent(tmp_path, monkeypatch):
    fixtures = _fixtures(tmp_path)

    def fake_fetch(url, dest, refresh):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((fixtures / dest.name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(hd, "_fetch_file", fake_fetch)
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260611")

    cfg = _cfg(tmp_path)
    hd.hoard_international_results(cfg, refresh=True)
    hd.hoard_international_results(cfg, refresh=False)

    con = sqlite3.connect(str(tmp_path / "data" / "football.db"))
    try:
        assert con.execute("SELECT COUNT(*) FROM wh_matches").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM wh_match_sources").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM matches WHERE comp = 'INT'").fetchone()[0] == 2
    finally:
        con.close()


def test_hoard_summary_population_can_be_disabled(tmp_path, monkeypatch):
    fixtures = _fixtures(tmp_path)

    def fake_fetch(url, dest, refresh):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((fixtures / dest.name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(hd, "_fetch_file", fake_fetch)
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260611")

    res = hd.hoard_international_results(_cfg(tmp_path), refresh=True, populate_summary=False)

    assert res.counts["wh_matches"] == 2
    assert res.counts["summary_matches"] == 0
    store = MatchStore.from_config(_cfg(tmp_path))
    try:
        assert store.count() == 0
    finally:
        store.close()


def test_hoard_summary_preserves_existing_richer_match_fields(tmp_path, monkeypatch):
    fixtures = _fixtures(tmp_path)

    def fake_fetch(url, dest, refresh):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((fixtures / dest.name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(hd, "_fetch_file", fake_fetch)
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260611")

    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert([{
        "date": "2024-01-01", "comp": "WC", "home": "Spain", "away": "France",
        "hg": 2, "ag": 1, "xg_home": 1.7, "xg_away": 0.8,
        "odds_h": 2.1, "odds_d": 3.2, "odds_a": 3.8, "source": "demo-xg",
    }])
    store.close()

    hd.hoard_international_results(cfg, refresh=True)

    store = MatchStore.from_config(cfg)
    try:
        row = [r for r in store.all_matches() if r["date"] == "2024-01-01"][0]
    finally:
        store.close()
    assert row["comp"] == "WC"
    assert row["xg_home"] == 1.7 and row["odds_h"] == 2.1
    assert row["source"] == "demo-xg; martj42/international_results:results.csv"


def test_hoard_wikipedia_player_totals_adds_career_rows(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_players([{
        "comp": "INT", "player": "Lionel Messi", "team": "Argentina",
        "goals": 63, "assists": 0, "penalties": 14, "matches": 48,
        "source": "martj42/international_results:goalscorers.csv",
    }])
    store.close()
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260611")
    monkeypatch.setattr(hd, "_fetch_json", lambda url: {
        "query": {"pages": [{
            "title": "Lionel Messi",
            "revisions": [{"slots": {"main": {"content": (
                "| nationalyears1 = 2005-\n"
                "| nationalteam1 = [[Argentina national football team|Argentina]]\n"
                "| nationalcaps1 = 199\n"
                "| nationalgoals1 = 117\n"
            )}}}],
        }]}
    })

    res = hd.hoard_wikipedia_player_totals(cfg, refresh=True, limit_source=1)

    store = MatchStore.from_config(cfg)
    try:
        totals = store.conn.execute("SELECT * FROM wh_player_career_totals").fetchall()
        career = store.players("INT_CAREER")
    finally:
        store.close()
    assert res.counts["wh_player_career_totals"] == 1
    assert totals[0]["caps"] == 199 and totals[0]["goals"] == 117
    assert career[0]["matches"] == 199 and career[0]["goals"] == 117


def test_hoard_statsbomb_open_data_populates_wc_situations(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260611")

    def fake_json(url):
        if url.endswith("competitions.json"):
            return [{"competition_id": 43, "season_id": 106, "competition_name": "FIFA World Cup"}]
        if "/matches/43/106.json" in url:
            return [{
                "match_id": 1,
                "match_date": "2022-12-18",
                "home_team": {"home_team_name": "Argentina"},
                "away_team": {"away_team_name": "France"},
                "home_score": 3,
                "away_score": 3,
                "season": {"season_name": "2022"},
                "stadium": {"name": "Lusail", "country": {"name": "Qatar"}},
            }]
        if "/events/1.json" in url:
            return [{
                "id": "shot-1",
                "type": {"name": "Shot"},
                "team": {"name": "Argentina"},
                "player": {"name": "Lionel Messi"},
                "minute": 23,
                "play_pattern": {"name": "From Corner"},
                "shot": {
                    "statsbomb_xg": 0.12,
                    "outcome": {"name": "Goal"},
                    "body_part": {"name": "Left Foot"},
                },
            }]
        if "/lineups/1.json" in url:
            return [{"team_name": "Argentina", "lineup": [{"player_id": 10, "player_name": "Lionel Messi"}]}]
        raise AssertionError(url)

    monkeypatch.setattr(hd, "_fetch_json", fake_json)

    res = hd.hoard_statsbomb_open_data(cfg, refresh=True, limit_source=1)

    store = MatchStore.from_config(cfg)
    try:
        sit = store.situations("WC", "2022", "Argentina")
        counts = store.warehouse_counts()
    finally:
        store.close()
    assert res.counts["wh_events"] == 1
    assert counts["wh_lineups"] == 1
    assert sit is not None
    assert sit[0]["From Corner"]["goals"] == 1


def test_statsbomb_event_aggregation_and_style_fingerprint(tmp_path, monkeypatch):
    """B3: Pass/Carry events → wh_player_match_stats / wh_team_match_stats
    aggregates, style fingerprint in team_situations, zones on shots — and the
    analyst lines read it all back."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(hd, "_snapshot_id", lambda: "20260612")

    def fake_json(url):
        if url.endswith("competitions.json"):
            return [{"competition_id": 43, "season_id": 106, "competition_name": "FIFA World Cup"}]
        if "/matches/43/106.json" in url:
            return [{
                "match_id": 1, "match_date": "2022-12-18",
                "home_team": {"home_team_name": "Argentina"},
                "away_team": {"away_team_name": "France"},
                "home_score": 3, "away_score": 3,
                "season": {"season_name": "2022"},
                "stadium": {"name": "Lusail", "country": {"name": "Qatar"}},
            }]
        if "/events/1.json" in url:
            return [
                # progressive completed pass into the final third
                {"type": {"name": "Pass"}, "team": {"name": "Argentina"},
                 "player": {"name": "Lionel Messi"}, "location": [40, 40],
                 "pass": {"end_location": [90, 40], "recipient": {"name": "Rodrigo De Paul"}}},
                # short completed pass (not progressive)
                {"type": {"name": "Pass"}, "team": {"name": "Argentina"},
                 "player": {"name": "Lionel Messi"}, "location": [50, 40],
                 "pass": {"end_location": [55, 40], "recipient": {"name": "Rodrigo De Paul"}}},
                # incomplete pass (outcome present)
                {"type": {"name": "Pass"}, "team": {"name": "Argentina"},
                 "player": {"name": "Lionel Messi"}, "location": [30, 70],
                 "pass": {"end_location": [70, 60], "outcome": {"name": "Incomplete"}}},
                # France keeps one completed pass (for possession share)
                {"type": {"name": "Pass"}, "team": {"name": "France"},
                 "player": {"name": "Antoine Griezmann"}, "location": [60, 40],
                 "pass": {"end_location": [62, 41], "recipient": {"name": "Kylian Mbappé"}}},
                # progressive carry into the final third
                {"type": {"name": "Carry"}, "team": {"name": "Argentina"},
                 "player": {"name": "Lionel Messi"}, "location": [60, 20],
                 "carry": {"end_location": [85, 30]}},
                # the shot (with location -> zone)
                {"id": "shot-1", "type": {"name": "Shot"}, "team": {"name": "Argentina"},
                 "player": {"name": "Lionel Messi"}, "minute": 23,
                 "play_pattern": {"name": "From Corner"}, "location": [108, 36],
                 "shot": {"statsbomb_xg": 0.12, "outcome": {"name": "Goal"},
                          "body_part": {"name": "Left Foot"}}},
            ]
        if "/lineups/1.json" in url:
            return [{"team_name": "Argentina", "lineup": [{"player_id": 10, "player_name": "Lionel Messi"}]}]
        raise AssertionError(url)

    monkeypatch.setattr(hd, "_fetch_json", fake_json)
    res = hd.hoard_statsbomb_open_data(cfg, refresh=True, limit_source=1)
    assert res.counts["wh_player_match_stats"] > 0
    assert res.counts["wh_team_match_stats"] > 0

    store = MatchStore.from_config(cfg)
    try:
        aggs = store.wc_player_aggregates(hd._statsbomb_team_id("Argentina"))
        sit = store.situations("WC", "2022", "Argentina")
        events = [dict(r) for r in store.conn.execute(
            "SELECT data_json FROM wh_events").fetchall()]
    finally:
        store.close()

    # Per-player aggregates: Messi 3 passes (2 completed), 1 prog pass, 1 prog carry.
    messi = next(a for a in aggs if a["player"] == "Lionel Messi")
    assert messi["passes"] == 3 and messi["passes_completed"] == 2
    assert messi["progressive_passes"] == 1 and messi["progressive_carries"] == 1
    assert messi["goals"] == 1 and abs(messi["xg"] - 0.12) < 1e-6

    # Style fingerprint rides in the situations JSON.
    style = sit[0]["style"]
    assert style["possession_share"] == 0.75            # 3 of 4 passes
    assert style["top_pass_pairs"][0].startswith("Lionel Messi → Rodrigo De Paul (2")
    assert any("middle third, central" in z for z in style["build_up_zones"])
    assert style["directness"] > 0

    # Shot rows now carry the semantic zone, not just raw coordinates.
    import json as _json
    shot = _json.loads(events[0]["data_json"])
    assert shot["zone"] == "final third, central"

    # And the analyst lines read it back (offline, sourced).
    from worldcupagents.agents.analyst.reports import _style_line, _wc_player_metrics_line
    sl = _style_line(cfg, "Argentina")
    assert "Argentina style (WC 2022)" in sl and "possession 75%" in sl
    assert "Lionel Messi → Rodrigo De Paul" in sl
    pm = _wc_player_metrics_line(cfg, "Argentina")
    assert "Lionel Messi" in pm and "progressive actions" in pm
    assert "[source: StatsBomb open data]" in pm
