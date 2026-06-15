"""WS-B tests — data explorer inventory + HTML (hermetic)."""

from __future__ import annotations

import copy
import json
import re

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.entities import normalize_entity_key
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines.data_explorer import build_inventory, export_data_explorer


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["exports_dir"] = str(tmp_path / "exports")
    return cfg


def _seed(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert([
        {"date": "2025-08-16", "comp": "PL", "home": "Arsenal FC", "away": "Chelsea FC",
         "hg": 2, "ag": 0, "xg_home": None, "xg_away": None, "source": "fdcouk:PL:2526"},
        {"date": "2022-12-18", "comp": "WC", "home": "Argentina", "away": "France",
         "hg": 3, "ag": 3, "xg_home": 2.4, "xg_away": 2.1, "source": "demo-xg"},
    ])
    store.upsert_players([{"comp": "PL", "player": "Haaland", "team": "Manchester City FC",
                           "goals": 27, "assists": 8, "penalties": 3, "matches": 36, "source": "t"}])
    store.upsert_wh_team("national:united_states", "United States", kind="national", source_id="seed")
    store.upsert_wh_team_alias(
        "national:united_states", "USA", "seed", normalize_entity_key("USA"),
        confidence=1.0, status="active",
    )
    store.record_unresolved_name({
        "unresolved_id": "test|national|shared",
        "raw_name": "Shared", "name_norm": "shared", "kind": "national",
        "source_id": "test", "context": "unit", "reason": "ambiguous",
        "first_seen": "2026-06-11T00:00:00+00:00",
        "last_seen": "2026-06-11T00:00:00+00:00",
    })
    store.close()
    log = tmp_path / "memory" / "prediction_log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("[2026-06-20 | A vs B | HOME_WIN 2-1 | resolved: HOME_WIN 2-1 Brier=0.245]\n"
                   "RESULT: x\nREFLECTION: lesson here\n\n<!-- ENTRY_END -->\n"
                   "[2026-06-21 | C vs D | DRAW 1-1 | pending]\n", encoding="utf-8")


def test_inventory_counts(tmp_path):
    _seed(tmp_path)
    inv = build_inventory(_cfg(tmp_path))

    comps = {c["comp"]: c for c in inv["store"]["competitions"]}
    assert comps["PL"]["matches"] == 1 and comps["PL"]["xg_rows"] == 0
    assert comps["WC"]["xg_rows"] == 1
    assert inv["store"]["players"][0]["players"] == 1
    assert inv["memory"]["log"]["resolved"] == 1 and inv["memory"]["log"]["pending"] == 1
    assert inv["memory"]["log"]["avg_brier"] == 0.245
    assert inv["memory"]["log"]["with_reflection"] == 1
    assert inv["rankings"]["count"] >= 48
    wc = {r["team"]: r for r in inv["store"]["wc_coverage"]}
    assert wc["Argentina"]["matches"] == 1
    assert wc["Argentina"]["missing"] == 4


def test_gaps_panel_flags_missing_sources(tmp_path):
    _seed(tmp_path)
    inv = build_inventory(_cfg(tmp_path))
    gap_text = json.dumps(inv["gaps"]).lower()
    assert "line-ups" in gap_text or "lineups" in gap_text   # always-on suggestion
    assert "odds" in gap_text                                 # market baseline gap


def test_html_export_contains_sections_and_data(tmp_path):
    _seed(tmp_path)
    path = export_data_explorer(_cfg(tmp_path))
    html = path.read_text(encoding="utf-8")
    assert path.name == "data_explorer.html"
    for section in ("Data gaps", "Data sources", "Match store", "Player stats", "Memory",
                    "WC2026 recent-result coverage", "Entity Resolution", "Qualitative warehouse",
                    "not verified career totals", "include pre-1988 INT", "show all low-signal rows",
                    "Manual analysis note", "--delete-document"):
        assert section in html
    assert "Arsenal FC" in html            # embedded match rows
    assert "football-data.co.uk" in html   # source listed
    assert "Shared" in html                # unresolved names listed

    matches_literal = re.search(r"const MATCHES = (.*?);\nconst PLAYERS", html, re.S)
    assert matches_literal is not None
    assert any(r["home"] == "Arsenal FC" for r in json.loads(matches_literal.group(1)))
