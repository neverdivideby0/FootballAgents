"""WS-A tests — provenance + citations: dated/sourced evidence in reports,
Guardian URLs in the tactical brief, citation instructions in prompts (hermetic)."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from worldcupagents.agents.schemas import (
    Fixture,
    JudgeRead,
    MatchTacticalReport,
    PhaseTacticalInsight,
    Stage,
)
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.graph.predict import Predictor
from worldcupagents.recall import predictive_brief


def _cfg(tmp_path, **overrides) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    cfg.update(overrides)
    return cfg


def _seed_store(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert([{
        "date": "2026-05-24", "comp": "WC", "home": "Brazil", "away": "Mexico",
        "hg": 2, "ag": 1, "xg_home": None, "xg_away": None, "source": "fdcouk:PL:2425",
    }])
    store.close()


def _seed_tactical_memory(tmp_path, sources):
    rep = MatchTacticalReport(
        match_id="Brazil_vs_Mexico_2026-05-24", home="Brazil", away="Mexico",
        date="2026-05-24",
        phases=[PhaseTacticalInsight(phase="15-45 First-Half Shift",
                                     formations_blocks=["4-3-3 high press"])],
        sources=sources,
    )
    d = tmp_path / "memory" / "matches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rep.match_id}.json").write_text(json.dumps(rep.model_dump(mode="json")), encoding="utf-8")


def test_form_report_carries_dates_and_source_tags(tmp_path):
    _seed_store(tmp_path)
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    final, _ = Predictor(_cfg(tmp_path)).predict(fx)
    assert "2-1 v Mexico (2026-05-24)" in final["form_report"]     # dated evidence
    assert "Sources: fdcouk:PL:2425" in final["form_report"]       # provenance footnote
    assert "[source: match store]" in final["form_report"]         # records tag


def test_tactical_brief_includes_clickable_guardian_url(tmp_path):
    url = "https://www.theguardian.com/football/live/2026/may/24/brazil-mexico"
    _seed_tactical_memory(tmp_path, sources=[url])
    brief = predictive_brief("Brazil", "Mexico", _cfg(tmp_path))
    assert f"[source: {url}]" in brief


def test_tactical_brief_falls_back_to_non_url_source(tmp_path):
    _seed_tactical_memory(tmp_path, sources=["placeholder:bundled-sample"])
    brief = predictive_brief("Brazil", "Mexico", _cfg(tmp_path))
    assert "[source: placeholder:bundled-sample]" in brief


def test_citation_instructions_reach_all_llm_prompts(tmp_path):
    prompts: list[str] = []

    class _FakeStructured:
        def __init__(self, result):
            self.result = result

        def invoke(self, prompt):
            prompts.append(prompt)
            return {"raw": None, "parsed": self.result, "parsing_error": None}

    class DeepLLM:
        def with_structured_output(self, schema, **kwargs):
            return _FakeStructured(JudgeRead(p_home=0.4, p_draw=0.3, p_away=0.3,
                                             scoreline="1-1", confidence="low"))

    class QuickLLM:
        def invoke(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(content="Case. Weaknesses: none.",
                                   usage_metadata={"input_tokens": 1, "output_tokens": 1})

    _seed_store(tmp_path)
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    Predictor(_cfg(tmp_path, use_llm=True), deep_llm=DeepLLM(), quick_llm=QuickLLM()).predict(fx)

    advocate = [p for p in prompts if "Team Advocate" in p]
    judge = [p for p in prompts if "neutral football pundit" in p]
    pundits = [p for p in prompts if "PROVISIONAL VERDICT" in p]
    assert advocate and all("CITE your evidence" in p for p in advocate)
    assert judge and "CITE the evidence" in judge[0]
    assert pundits and all("CITE evidence" in p for p in pundits)
    # The citable evidence itself is in the prompts too.
    assert any("2-1 v Mexico (2026-05-24)" in p for p in advocate)
