"""Entity identity resolver tests."""

from __future__ import annotations

import copy
import sqlite3

from typer.testing import CliRunner

from worldcupagents.cli import app
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.entities import (
    normalize_entity_key,
    resolve_team,
    seed_identity_registry,
    stable_team_id,
)
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.recall import top_players


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["fd_competition"] = "INT"
    return cfg


def test_national_aliases_resolve_to_stable_ids(tmp_path):
    cfg = _cfg(tmp_path)
    seed_identity_registry(cfg)

    assert resolve_team("USA", kind="national", config=cfg).team_id == "national:united_states"
    assert resolve_team("United States", kind="national", config=cfg).team_id == "national:united_states"
    assert resolve_team("United States of America", kind="national", config=cfg).team_id == "national:united_states"
    assert resolve_team("South Korea", kind="national", config=cfg).team_id == "national:korea_republic"
    assert resolve_team("Korea Republic", kind="national", config=cfg).team_id == "national:korea_republic"


def test_club_aliases_resolve_to_stable_ids(tmp_path):
    cfg = _cfg(tmp_path)
    seed_identity_registry(cfg)

    expected = "club:manchester_city_fc"
    assert resolve_team("Man City", kind="club", config=cfg).team_id == expected
    assert resolve_team("Manchester City", kind="club", config=cfg).team_id == expected
    assert resolve_team("Manchester City FC", kind="club", config=cfg).team_id == expected


def test_congo_resolution_is_explicit(tmp_path):
    cfg = _cfg(tmp_path)
    seed_identity_registry(cfg)

    assert resolve_team("DR Congo", kind="national", config=cfg).team_id == "national:dr_congo"
    assert resolve_team("Congo DR", kind="national", config=cfg).team_id == "national:dr_congo"
    assert resolve_team("Congo", kind="national", config=cfg).team_id == "national:congo"


def test_source_specific_alias_wins_over_global_alias(tmp_path):
    cfg = _cfg(tmp_path)
    seed_identity_registry(cfg)
    store = MatchStore.from_config(cfg)
    try:
        store.upsert_wh_team("national:testland", "Testland", kind="national", source_id="test")
        store.upsert_wh_team_alias(
            "national:testland", "USA", "weird_source", normalize_entity_key("USA"),
            confidence=1.0, status="active", notes="source override",
        )
    finally:
        store.close()

    assert resolve_team("USA", kind="national", config=cfg).team_id == "national:united_states"
    assert resolve_team("USA", kind="national", source_id="weird_source", config=cfg).team_id == "national:testland"


def test_ambiguous_alias_records_unresolved(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    try:
        for tid, name in (("national:a", "A"), ("national:b", "B")):
            store.upsert_wh_team(tid, name, kind="national")
            store.upsert_wh_team_alias(tid, "Shared", "test", normalize_entity_key("Shared"))
    finally:
        store.close()

    res = resolve_team(
        "Shared", kind="national", source_id="test", config=cfg,
        record_unresolved=True, context="unit",
    )
    assert res.status == "ambiguous"

    store = MatchStore.from_config(cfg)
    try:
        unresolved = store.unresolved_names()
    finally:
        store.close()
    assert unresolved and unresolved[0]["raw_name"] == "Shared"


def test_legacy_alias_rows_migrate_and_still_resolve(tmp_path):
    db = tmp_path / "data" / "football.db"
    db.parent.mkdir()
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE wh_teams (team_id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'national', source_id TEXT, source_name TEXT);"
        "CREATE TABLE wh_team_aliases (alias_key TEXT PRIMARY KEY, team_id TEXT NOT NULL, alias TEXT NOT NULL, source_id TEXT);"
        "INSERT INTO wh_teams VALUES ('national:legacy','Legacy','national','old','Legacy');"
        "INSERT INTO wh_team_aliases VALUES ('legacy-key','national:legacy','Legacy Alias','old');"
    )
    con.commit(); con.close()
    cfg = _cfg(tmp_path)

    store = MatchStore.from_config(cfg)
    store.close()

    assert resolve_team("Legacy Alias", kind="national", source_id="old", config=cfg).team_id == "national:legacy"


def test_top_players_uses_entity_aliases(tmp_path):
    cfg = _cfg(tmp_path)
    seed_identity_registry(cfg)
    store = MatchStore.from_config(cfg)
    try:
        store.upsert_players([{
            "comp": "INT", "player": "Example", "team": "United States",
            "goals": 3, "assists": 0, "penalties": 0, "matches": 2, "source": "t",
        }])
    finally:
        store.close()

    assert top_players("USA", cfg)[0].player == "Example"


def test_resolve_name_cli(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("worldcupagents.cli.DEFAULT_CONFIG", cfg)
    result = CliRunner().invoke(app, ["resolve-name", "USA", "--kind", "national"])

    assert result.exit_code == 0
    assert '"team_id": "national:united_states"' in result.output
