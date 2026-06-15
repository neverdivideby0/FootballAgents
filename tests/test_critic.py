"""M1.4 tests — Critic Loop (hermetic: placeholder provider, no network/LLM)."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from worldcupagents.agents.schemas import (
    CriticFinding,
    CriticReport,
    MatchTacticalReport,
    PhaseTacticalInsight,
)
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.pipelines.critic import run_critic


def _cfg(tmp_path, use_llm=False):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["use_llm"] = use_llm
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def _write_report(tmp_path, home, away, date, insights):
    rep = MatchTacticalReport(match_id=f"{home}_vs_{away}_{date}", home=home, away=away,
                              date=date, phases=insights, sources=["test"])
    d = tmp_path / "memory" / "matches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rep.match_id}.json").write_text(json.dumps(rep.model_dump(mode="json")), encoding="utf-8")


def test_offline_critic_runs_and_persists(tmp_path):
    cfg = _cfg(tmp_path)
    _write_report(tmp_path, "Argentina", "France", "2022-12-18", [
        PhaseTacticalInsight(phase="75-90+ Crunch Time", formations_blocks=["low block"],
                             adjustments=["dropped deep to protect the lead"]),
    ])
    out = run_critic("Argentina", cfg)
    assert out.report.team == "Argentina"
    assert "placeholder" in out.report.summary.lower()
    assert out.model is None and out.cost is None
    assert out.json_path.exists() and out.md_path.exists()
    assert "# Critic Report — Argentina" in out.md_path.read_text()


# --- injected real critic (FakeLLM) ---

class _FakeStructured:
    def invoke(self, prompt):
        raw = SimpleNamespace(usage_metadata={"input_tokens": 700, "output_tokens": 160})
        rep = CriticReport(
            team="x", summary="Strong but fades late.",
            findings=[CriticFinding(metric="xG against 1.4/game",
                                    commentary="drops into a low block in Crunch Time",
                                    insight="late passivity invites pressure")],
            tensions=["scores high but commentary stresses defensive frailty"],
        )
        return {"raw": raw, "parsed": rep, "parsing_error": None}


class FakeLLM:
    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured()


def test_llm_critic_cross_examines_and_tracks_cost(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    cfg["llm_provider"] = "openai"
    cfg["deep_think_llm"] = "gpt-5.4-mini"
    out = run_critic("Argentina", cfg, critic_llm=FakeLLM())

    assert out.report.team == "Argentina"           # overwritten from "x"
    assert out.report.findings[0].metric == "xG against 1.4/game"
    assert out.report.tensions
    assert out.usage["input"] == 700 and out.cost is not None and out.cost > 0
    assert out.model == "gpt-5.4-mini"


def test_critic_no_persist(tmp_path):
    out = run_critic("Brazil", _cfg(tmp_path), persist=False)
    assert out.json_path is None and out.report.team == "Brazil"
