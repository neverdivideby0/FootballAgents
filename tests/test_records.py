"""Home + head-to-head records, and that they reach the debate prompts (hermetic)."""

from __future__ import annotations

import copy
from types import SimpleNamespace

from worldcupagents.agents.schemas import Fixture, JudgeRead, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.records import h2h_home_record, home_record, records_summary
from worldcupagents.graph.predict import Predictor


def _seed(tmp_path, rows):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert(rows)
    store.close()


def _cfg(tmp_path):
    return {"data_dir": str(tmp_path / "data"), "fd_competition": "PL"}


def test_home_record_counts_only_home_games(tmp_path):
    _seed(tmp_path, [
        {"date": "2025-08-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC", "hg": 3, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2025-08-08", "comp": "PL", "home": "Chelsea FC", "away": "Everton FC",      "hg": 1, "ag": 1, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2025-08-15", "comp": "PL", "home": "Arsenal FC", "away": "Chelsea FC",      "hg": 2, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},  # Chelsea away — ignored
    ])
    assert home_record("Chelsea FC", _cfg(tmp_path)) == (1, 1, 0)


def test_chelsea_unbeaten_at_home_vs_leeds_example(tmp_path):
    # The user's example: Chelsea unbeaten at home vs Leeds across many meetings.
    rows = []
    for i in range(20):
        hg, ag = (2, 0) if i % 4 else (1, 1)  # mix of wins and draws, never a loss
        rows.append({"date": f"20{i:02d}-01-01", "comp": "PL", "home": "Chelsea FC",
                     "away": "Leeds United FC", "hg": hg, "ag": ag, "xg_home": None, "xg_away": None, "source": "t"})
    _seed(tmp_path, rows)

    w, d, loss, n = h2h_home_record("Chelsea FC", "Leeds United FC", _cfg(tmp_path))
    assert n == 20 and loss == 0

    summary = records_summary("Chelsea FC", "Leeds United FC", _cfg(tmp_path))
    assert "UNBEATEN at home vs Leeds United FC" in summary
    assert "in last 20" in summary


def test_records_summary_empty_without_data(tmp_path):
    assert records_summary("A", "B", _cfg(tmp_path)) == ""


def test_competition_scoping(tmp_path):
    _seed(tmp_path, [
        {"date": "2025-08-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC", "hg": 3, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2024-01-01", "comp": "FA",  "home": "Chelsea FC", "away": "Leeds United FC", "hg": 0, "ag": 2, "xg_home": None, "xg_away": None, "source": "t"},  # other comp — excluded
    ])
    w, d, loss, n = h2h_home_record("Chelsea FC", "Leeds United FC", _cfg(tmp_path))  # cfg pins PL
    assert (w, d, loss, n) == (1, 0, 0, 1)


# ── the record actually reaches the debate prompts ───────────────────────────

_PROMPTS: list[str] = []


class _FakeStructured:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        _PROMPTS.append(prompt)
        return {"raw": None, "parsed": self.result, "parsing_error": None}


class FakeLLM:
    def __init__(self, content, read=None):
        self.content, self.read = content, read

    def invoke(self, prompt):
        _PROMPTS.append(prompt)
        return SimpleNamespace(content=self.content, usage_metadata={"input_tokens": 10, "output_tokens": 5})

    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(self.read)


def test_record_reaches_advocate_and_judge_prompts(tmp_path):
    _PROMPTS.clear()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["use_llm"] = True
    cfg["fd_competition"] = "PL"
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "log.md")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    _seed(tmp_path, [
        {"date": "2025-08-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC", "hg": 3, "ag": 0, "xg_home": None, "xg_away": None, "source": "t"},
        {"date": "2025-09-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC", "hg": 2, "ag": 1, "xg_home": None, "xg_away": None, "source": "t"},
    ])
    read = JudgeRead(p_home=0.6, p_draw=0.25, p_away=0.15, scoreline="2-0", confidence="high")
    Predictor(cfg, deep_llm=FakeLLM("", read=read), quick_llm=FakeLLM("Case. Weaknesses: none.")).predict(
        Fixture(home="Chelsea FC", away="Leeds United FC", stage=Stage.GROUP)
    )
    assert any("HOME & HEAD-TO-HEAD RECORD" in p and "UNBEATEN at home vs Leeds United FC" in p for p in _PROMPTS)
