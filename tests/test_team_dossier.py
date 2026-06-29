"""recall.team_dossier_brief — the per-team living dossier feeds the debate (hermetic)."""

from __future__ import annotations

import copy
from pathlib import Path

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.recall import past_context_for, team_dossier_brief


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    return cfg


def _write_dossier(cfg, team, text):
    from worldcupagents.dataflows.names import canonical_name, normalize_key
    d = Path(cfg["memory_dir"]) / "teams"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{normalize_key(canonical_name(team))}.md").write_text(text, encoding="utf-8")


def test_dossier_dedupes_auto_lines_and_surfaces_manual(tmp_path):
    cfg = _cfg(tmp_path)
    _write_dossier(cfg, "South Africa", (
        "# South Africa — prediction lessons\n\n"
        "- 2026-06-12: Mexico vs South Africa → HOME_WIN 2-0 (we predicted HOME_WIN, Brier 0.060)\n"
        "- 2026-06-12: Mexico vs South Africa → HOME_WIN 2-0 (we predicted HOME_WIN, Brier 0.192)\n"
        "- 2026-06-12: Mexico vs South Africa → HOME_WIN 2-0 (we predicted HOME_WIN, Brier 0.267)\n"
        "- Defensively organised but blunt up front; concede late.\n"   # a manual note
    ))
    brief = team_dossier_brief("South Africa", "Mexico", cfg)
    # The repeated Mexico fixture collapses to ONE line.
    assert brief.count("Mexico vs South Africa") == 1
    # The hand-written note is surfaced verbatim.
    assert "concede late" in brief
    assert "TEAM DOSSIER" in brief


def test_flows_into_past_context(tmp_path):
    cfg = _cfg(tmp_path)
    _write_dossier(cfg, "Spain", "# Spain\n\n- Struggle to break down a deep low block.\n")
    assert "TEAM DOSSIER" in past_context_for("Spain", "Germany", cfg)


def test_empty_when_no_dossier(tmp_path):
    assert team_dossier_brief("Brazil", "Argentina", _cfg(tmp_path)) == ""
