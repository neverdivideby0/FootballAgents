"""Guardian WC2026 player-guide ingester (hermetic — injected fetch_json)."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines.guardian_guide import (
    TEAMS_SHEET, _age, _player_note, ingest_guardian_player_guide)


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


_TEAMS = {"sheets": {"Teams": [
    {"Team": "Argentina", "Coach": "Lionel Scaloni", "Group": "J",
     "Bio": "Holders travel with familiar faces.", "strengths": "Messi still here.",
     "weaknesses": "<a>Injury</a> setbacks.", "player_pick": "Lionel Messi",
     "spreadsheet": "ARG_SHEET"},
]}}
_PLAYERS = {"sheets": {"Players": [
    {"team": "Argentina", "name": "Lionel Messi ", "position": "Forward", "club": "Inter Miami ",
     "caps": "190", "goals for country": "112", "date of birth": "24/06/1987",
     "special player? (eg. key player, promising talent, etc) OPTIONAL": "Key player",
     "bio": "<p>The greatest of all time, still pulling the strings.</p>"},
    {"team": "Argentina", "name": "Emiliano Martínez", "position": "Goalkeeper",
     "club": "Aston Villa", "caps": "50", "goals for country": "0",
     "date of birth": "02/09/1992", "bio": "Penalty-shootout specialist."},
    {"team": "Argentina", "name": "", "bio": "no name row — skipped"},
]}}


def _fake_fetch(url: str):
    if TEAMS_SHEET in url:
        return _TEAMS
    if "ARG_SHEET" in url:
        return _PLAYERS
    raise AssertionError(url)


def test_age_parsing():
    assert _age("24/06/1987") >= 38           # born 1987
    assert _age("2026-01-01") in (0, 1) or _age("2026-01-01") is not None
    assert _age("") is None and _age("garbage") is None


def test_player_note_formats_head_and_bio():
    note = _player_note(_PLAYERS["sheets"]["Players"][0])
    assert "Forward" in note and "Inter Miami" in note and "190 caps/112 gls" in note
    assert "Key player" in note
    assert "greatest of all time" in note and "<p>" not in note   # HTML stripped


def test_ingest_populates_players_and_team_note(tmp_path):
    cfg = _cfg(tmp_path)
    res = ingest_guardian_player_guide(cfg, fetch_json=_fake_fetch)
    assert res.players == 2 and res.teams == 1     # blank-name row skipped

    store = MatchStore.from_config(cfg)
    notes = {n["player"]: n["note"] for n in store.player_notes_for_team("Argentina")}
    store.close()
    assert "Lionel Messi" in notes and "greatest of all time" in notes["Lionel Messi"]
    assert "Emiliano Martínez" in notes

    # Team note reached the qualitative warehouse, with avg age computed from DOBs.
    from worldcupagents.recall import qualitative_brief
    brief = qualitative_brief("Argentina", "Argentina", cfg)
    assert "Messi still here" in brief or "familiar faces" in brief
    assert "Average age" in brief


def test_ingest_graceful_on_feed_failure(tmp_path):
    def boom(url):
        raise RuntimeError("network down")
    res = ingest_guardian_player_guide(_cfg(tmp_path), fetch_json=boom)
    assert res.players == 0 and res.teams == 0    # no crash
