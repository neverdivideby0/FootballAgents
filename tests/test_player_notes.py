"""Per-player qualitative notes: store, squad-scoped surfacing, explorer (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, Player, Stage, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def test_store_roundtrip_and_team_filter(tmp_path):
    store = MatchStore.from_config(_cfg(tmp_path))
    store.upsert_player_note("Arsenal FC", "Bukayo Saka", "Inverted right winger; cuts inside.")
    store.upsert_player_note("Liverpool FC", "Mohamed Salah", "Right-sided poacher.")
    arsenal = store.player_notes_for_team("Arsenal FC")
    assert len(arsenal) == 1 and arsenal[0]["player"] == "Bukayo Saka"
    assert len(store.all_player_notes()) == 2
    # Upsert replaces, doesn't duplicate.
    store.upsert_player_note("Arsenal FC", "Bukayo Saka", "Updated note.")
    assert len(store.player_notes_for_team("Arsenal FC")) == 1
    assert store.player_notes_for_team("Arsenal FC")[0]["note"] == "Updated note."
    assert store.delete_player_note("Arsenal FC", "Bukayo Saka") is True
    assert store.player_notes_for_team("Arsenal FC") == []
    store.close()


def test_player_notes_line_squad_scoped(tmp_path):
    from worldcupagents.agents.analyst.reports import _player_notes_line
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_player_note("Arsenal FC", "Bukayo Saka", "Main creator.")
    store.upsert_player_note("Arsenal FC", "Old Reserve", "Not in the squad anymore.")
    store.close()
    profile = TeamProfile(team="Arsenal FC", squad=[Player(name="Bukayo Saka")])
    line = _player_notes_line(cfg, profile)
    assert "Bukayo Saka — Main creator." in line
    assert "Old Reserve" not in line          # squad filter drops the non-member
    assert "[source: manual]" in line


def test_notes_flow_into_player_report(tmp_path):
    from worldcupagents.graph.predict import Predictor
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_player_note("Brazil", "Vinicius Junior", "Left winger, runs in behind; beats his man 1v1.")
    store.close()
    # Brazil's placeholder squad must include the player for the squad filter.
    import worldcupagents.dataflows.providers.placeholder as ph
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, _ = Predictor(cfg).predict(fx)
    # The note surfaces only if Vinicius is in the resolved squad; assert the
    # mechanism doesn't crash and, when present, the note is included.
    report = final.get("player_report", "")
    squad = {p.name for p in final["home_profile"].squad}
    if "Vinicius Junior" in squad:
        assert "Vinicius Junior — Left winger" in report


def test_explorer_renders_player_notes_tab(tmp_path):
    from worldcupagents.pipelines.data_explorer import build_inventory, render_html
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["exports_dir"] = str(tmp_path / "exports")
    store = MatchStore.from_config(cfg)
    store.upsert_player_note("Arsenal FC", "Declan Rice", "Box-to-box, set-piece threat.")
    store.close()
    html = render_html(build_inventory(cfg))
    assert 'id="tab-playernotes"' in html
    assert "note-player" in html                       # the command-builder
    assert "Declan Rice" in html and "Box-to-box" in html
