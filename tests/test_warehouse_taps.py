"""B1 — warehouse taps into the debate: intl form/H2H, career caps, WC shot
profiles flow into analyst reports for tournament fixtures (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.analyst.reports import (
    _career_totals_line, _intl_form_line, _intl_h2h_line, _wc_situations_line)
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.entities import normalize_entity_key
from worldcupagents.dataflows.match_store import MatchStore


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def _seed_warehouse(cfg) -> None:
    store = MatchStore.from_config(cfg)
    for tid, name in [("national:argentina", "Argentina"), ("national:france", "France"),
                      ("national:chile", "Chile")]:
        store.upsert_wh_team(tid, name, kind="national", source_id="seed")
        store.upsert_wh_team_alias(tid, name, "seed", normalize_entity_key(name),
                                   confidence=1.0, status="active")
    store.upsert_wh_rows("wh_matches", [
        {"wh_match_id": "m1", "date": "2026-03-24", "tournament": "Friendly",
         "home_team_id": "national:argentina", "away_team_id": "national:chile",
         "home_team": "Argentina", "away_team": "Chile",
         "home_score": 3, "away_score": 0, "source_id": "international_results"},
        {"wh_match_id": "m2", "date": "2022-12-18", "tournament": "FIFA World Cup",
         "home_team_id": "national:argentina", "away_team_id": "national:france",
         "home_team": "Argentina", "away_team": "France",
         "home_score": 3, "away_score": 3, "source_id": "international_results"},
        {"wh_match_id": "m3", "date": "2025-06-05", "tournament": "Friendly",
         "home_team_id": "national:france", "away_team_id": "national:argentina",
         "home_team": "France", "away_team": "Argentina",
         "home_score": 0, "away_score": 1, "source_id": "international_results"},
    ])
    store.upsert_wh_rows("wh_player_career_totals", [
        {"total_id": "t1", "player_id": "p:messi", "player": "Lionel Messi",
         "team_id": "national:argentina", "team": "Argentina",
         "scope": "national_team_infobox", "caps": 180, "goals": 106,
         "start_year": 2005, "end_year": None, "source_id": "wikipedia",
         "source_url": "https://en.wikipedia.org/wiki/Lionel_Messi"},
        {"total_id": "t2", "player_id": "p:martinez", "player": "Lautaro Martínez",
         "team_id": "national:argentina", "team": "Argentina",
         "scope": "national_team_infobox", "caps": 70, "goals": 32,
         "start_year": 2018, "end_year": None, "source_id": "wikipedia",
         "source_url": "https://en.wikipedia.org/wiki/Lautaro_Martinez"},
    ])
    store.upsert_situations("WC", "2022", "Argentina",
                            {"From Corner": {"shots": 14, "goals": 2, "xG": 1.7},
                             "Regular Play": {"shots": 80, "goals": 9, "xG": 10.2}},
                            "statsbomb/open-data:events")
    store.close()


def test_intl_form_line_formats_from_team_perspective(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    line = _intl_form_line(cfg, "Argentina")
    assert line.startswith("Argentina recent internationals:")
    assert "W 3-0 v Chile (2026-03-24, Friendly)" in line
    assert "W 1-0 v France (2025-06-05, Friendly)" in line   # away win, team perspective
    assert "[source: international_results]" in line


def test_intl_h2h_line_finds_meetings_either_venue(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    line = _intl_h2h_line(cfg, "Argentina", "France")
    assert "France 0-1 Argentina (2025-06-05" in line
    assert "Argentina 3-3 France (2022-12-18, FIFA World Cup)" in line
    assert "Chile" not in line                                # H2H only


def test_career_totals_line_orders_by_goals(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    line = _career_totals_line(cfg, "Argentina")
    assert "Lionel Messi 180 caps, 106 intl goals (2005–present)" in line
    assert line.index("Messi") < line.index("Lautaro")        # goals desc
    assert "[source: https://en.wikipedia.org/wiki/Lionel_Messi]" in line


def test_career_totals_squad_filter_drops_retired_legends(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    # Add a retired legend with MORE goals than any current player.
    store = MatchStore.from_config(cfg)
    store.upsert_wh_rows("wh_player_career_totals", [
        {"total_id": "leg", "player_id": "p:batistuta", "player": "Gabriel Batistuta",
         "team_id": "national:argentina", "team": "Argentina", "scope": "national_team_infobox",
         "caps": 78, "goals": 200, "start_year": 1991, "end_year": 2005,
         "source_id": "wikipedia", "source_url": "https://en.wikipedia.org/wiki/Gabriel_Batistuta"}])
    store.close()
    # No squad → the legend (most goals) leads.
    assert "Gabriel Batistuta" in _career_totals_line(cfg, "Argentina")
    # Squad-scoped → only the named current players, legend excluded.
    line = _career_totals_line(cfg, "Argentina", ["Lionel Messi", "Lautaro Martínez"])
    assert "Lionel Messi" in line and "Batistuta" not in line


def test_wc_situations_line_uses_latest_season(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    line = _wc_situations_line(cfg, "Argentina")
    assert "Argentina shot profile (WC 2022):" in line
    assert "From Corner: 2g/14sh, xG 1.7" in line
    assert "[source: statsbomb/open-data:events]" in line


def test_taps_empty_without_warehouse(tmp_path):
    cfg = _cfg(tmp_path)                                      # no store at all
    assert _intl_form_line(cfg, "Argentina") == ""
    assert _intl_h2h_line(cfg, "Argentina", "France") == ""
    assert _career_totals_line(cfg, "Argentina") == ""
    assert _wc_situations_line(cfg, "Argentina") == ""


def test_form_report_carries_warehouse_lines_for_wc_fixture(tmp_path):
    from worldcupagents.agents.schemas import Fixture, Stage
    from worldcupagents.graph.predict import Predictor

    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    final, _ = Predictor(cfg).predict(Fixture(home="Argentina", away="France", stage=Stage.GROUP))
    form = final.get("form_report", "")
    assert "Argentina recent internationals:" in form
    assert "H2H (international):" in form
    assert "shot profile (WC 2022)" in form
    player = final.get("player_report", "")
    assert "Lionel Messi 180 caps" in player


def test_league_fixtures_skip_warehouse_taps(tmp_path):
    """Club matches keep their Understat lines; the national-team taps stay out."""
    from worldcupagents.agents.schemas import Fixture, Stage
    from worldcupagents.graph.predict import Predictor
    from worldcupagents.leagues.registry import apply_league, get_league

    cfg = _cfg(tmp_path)
    _seed_warehouse(cfg)
    apply_league(cfg, get_league("PL"))
    final, _ = Predictor(cfg).predict(Fixture(home="Arsenal FC", away="Chelsea FC", stage=Stage.GROUP))
    assert "recent internationals" not in final.get("form_report", "")
    assert "career totals" not in final.get("player_report", "")
