"""Senior-Scout report tests (hermetic: placeholder provider, no network/LLM)."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from worldcupagents.agents.schemas import (
    MatchTacticalReport,
    PhaseTacticalInsight,
    ScoutReport,
)
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.pipelines.scout_report import generate_scout_report


def _cfg(tmp_path, use_llm=False):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["use_llm"] = use_llm
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["data_dir"] = str(tmp_path / "data")   # isolate match + player store
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}  # offline profiles
    return cfg


def _write_report(tmp_path, home, away, date, insights):
    rep = MatchTacticalReport(match_id=f"{home}_vs_{away}_{date}", home=home, away=away,
                              date=date, phases=insights, sources=["test"])
    d = tmp_path / "memory" / "matches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rep.match_id}.json").write_text(json.dumps(rep.model_dump(mode="json")), encoding="utf-8")


def test_offline_scout_pulls_tactical_tendencies(tmp_path):
    cfg = _cfg(tmp_path)
    _write_report(tmp_path, "Argentina", "France", "2022-12-18", [
        PhaseTacticalInsight(phase="15-45 First-Half Shift",
                             formations_blocks=["4-3-3 high press"], adjustments=["fullbacks high"]),
    ])
    out = generate_scout_report("Argentina", cfg)
    assert out.report.team == "Argentina"
    assert "placeholder" in out.report.summary.lower()
    assert "4-3-3 high press" in out.report.tactical_tendencies
    assert out.model is None and out.cost is None
    assert out.json_path.exists() and out.md_path.exists()
    assert "# Scouting Report — Argentina" in out.md_path.read_text()


# --- injected real scout (FakeLLM), no network ---

class _FakeStructured:
    def invoke(self, prompt):
        raw = SimpleNamespace(usage_metadata={"input_tokens": 600, "output_tokens": 150})
        rep = ScoutReport(team="x", summary="Elite, Messi-led.", strengths=["press resistance"],
                          weaknesses=["high line"], tactical_tendencies=["controls tempo"],
                          key_players=["Messi"])
        return {"raw": raw, "parsed": rep, "parsing_error": None}


class FakeLLM:
    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured()


def test_llm_scout_synthesises_and_tracks_cost(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    cfg["llm_provider"] = "openai"
    cfg["deep_think_llm"] = "gpt-5.4-mini"
    out = generate_scout_report("Argentina", cfg, scout_llm=FakeLLM())

    assert out.report.team == "Argentina"            # overwritten from "x"
    assert out.report.strengths == ["press resistance"]
    assert out.usage["input"] == 600 and out.cost is not None and out.cost > 0
    assert out.model == "gpt-5.4-mini"


def test_scout_no_persist(tmp_path):
    out = generate_scout_report("Brazil", _cfg(tmp_path), persist=False)
    assert out.json_path is None and out.report.team == "Brazil"
