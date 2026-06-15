"""LLM-lift evaluation harness (hermetic — offline predictor + synthetic reads)."""

from __future__ import annotations

import copy
import json

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines.evaluate import (
    eval_log_path, fit_judge_weight, load_eval_log, pick_rows, run_eval, score_records)


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def _seed_store(cfg, n=5):
    store = MatchStore.from_config(cfg)
    rows = [{"date": f"2026-05-{10 + i:02d}", "comp": "WC", "home": "Brazil", "away": "Mexico",
             "hg": 2, "ag": i % 2, "source": "test"} for i in range(n)]
    store.upsert(rows)
    store.close()


def test_pick_rows_takes_newest(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_store(cfg, n=5)
    rows = pick_rows(cfg, comp="WC", last_n=3)
    assert len(rows) == 3
    assert rows[0]["date"] < rows[-1]["date"]          # chronological
    assert rows[-1]["date"] == "2026-05-14"            # newest included


def test_run_eval_logs_records_without_polluting_prediction_log(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_store(cfg, n=2)
    rows = pick_rows(cfg, comp="WC", last_n=2)
    records = run_eval(cfg, rows)
    assert len(records) == 2
    # Records carry the blend + baseline breakdown and the actual result.
    rec = records[0]
    assert abs(sum(rec["blend"]) - 1.0) < 1e-6
    assert rec["base"] is not None and rec["hg"] == 2
    assert rec["llm"] is False                          # offline run flagged honestly
    # Crash-safe JSONL log written…
    assert len(load_eval_log(cfg)) == 2
    # …and the learning loop untouched (no fake pending predictions).
    from pathlib import Path
    log = Path(cfg["prediction_log_path"])
    assert not log.exists() or "pending" not in log.read_text(encoding="utf-8")


def test_score_records_separates_llm_and_baseline_models():
    records = [
        # An LLM record where the judge nailed it and the baseline was meh.
        {"date": "2026-05-24", "home": "A", "away": "B", "provider": "openai",
         "hg": 2, "ag": 0, "llm": True,
         "blend": [0.7, 0.2, 0.1], "judge": [0.9, 0.07, 0.03], "base": [0.45, 0.3, 0.25],
         "odds": [1.5, 4.0, 6.0]},
        # A no-LLM record: only the baseline should be scored.
        {"date": "2026-05-24", "home": "C", "away": "D", "provider": None,
         "hg": 0, "ag": 1, "llm": False,
         "blend": [0.4, 0.3, 0.3], "judge": None, "base": [0.4, 0.3, 0.3], "odds": None},
    ]
    scores = score_records(records)
    assert scores["baseline(no LLM)"].n == 2
    assert scores["llm-judge(raw)"].n == 1              # judge only on LLM rows
    assert scores["llm-blend(final)"].n == 1
    assert scores["market(de-vigged odds)"].n == 1      # market only where odds exist
    assert scores["llm-judge(raw)"].mean_brier < scores["baseline(no LLM)"].mean_brier


def test_fit_judge_weight_prefers_the_better_signal():
    # Judge is consistently sharp, baseline consistently flat -> best w near 1.
    records = [
        {"hg": 2, "ag": 0, "llm": True, "judge": [0.9, 0.07, 0.03], "base": [0.34, 0.33, 0.33]}
        for _ in range(4)
    ]
    best_w, curve = fit_judge_weight(records)
    assert best_w >= 0.9
    assert curve[0][0] == 0.0 and curve[-1][0] == 1.0
    # And the inverse: judge consistently wrong -> best w = 0.
    bad = [{"hg": 0, "ag": 2, "llm": True, "judge": [0.9, 0.07, 0.03], "base": [0.2, 0.3, 0.5]}
           for _ in range(4)]
    best_w_bad, _ = fit_judge_weight(bad)
    assert best_w_bad == 0.0


def test_fit_judge_weight_empty_log():
    assert fit_judge_weight([]) == (0.0, [])
    assert fit_judge_weight([{"hg": 1, "ag": 0, "llm": False, "judge": None, "base": [0.4, 0.3, 0.3]}]) == (0.0, [])


def test_dedupe_keeps_latest_per_fixture_and_provider():
    from worldcupagents.pipelines.evaluate import dedupe_records
    a1 = {"date": "2026-05-24", "home": "A", "away": "B", "provider": "openai", "ts": "1"}
    a2 = {"date": "2026-05-24", "home": "A", "away": "B", "provider": "openai", "ts": "2"}
    b = {"date": "2026-05-24", "home": "A", "away": "B", "provider": "deepseek", "ts": "1"}
    out = dedupe_records([a1, a2, b])
    assert len(out) == 2                                # rerun replaced, provider kept
    assert {r["ts"] for r in out if r["provider"] == "openai"} == {"2"}


def test_score_records_dedupes_reruns():
    rec = {"date": "2026-05-24", "home": "A", "away": "B", "provider": "openai",
           "hg": 2, "ag": 0, "llm": True,
           "blend": [0.7, 0.2, 0.1], "judge": [0.7, 0.2, 0.1], "base": [0.5, 0.3, 0.2],
           "odds": None}
    scores = score_records([rec, dict(rec)])            # same fixture evaluated twice
    assert scores["llm-blend(final)"].n == 1


def test_run_eval_marks_failed_llm_honestly(tmp_path):
    """use_llm set but the client can't be built -> zero output tokens -> the
    placeholder judge read must be recorded llm=False (not a real read)."""
    cfg = _cfg(tmp_path)
    cfg["use_llm"] = True
    cfg["llm_provider"] = "no-such-provider"            # factory raises; degrades to baseline
    _seed_store(cfg, n=1)
    records = run_eval(cfg, pick_rows(cfg, comp="WC", last_n=1))
    assert len(records) == 1
    assert records[0]["llm"] is False and records[0]["provider"] is None


def test_pick_rows_excludes_already_evaluated(tmp_path):
    from worldcupagents.pipelines.evaluate import evaluated_keys
    cfg = _cfg(tmp_path)
    _seed_store(cfg, n=3)
    run_eval(cfg, pick_rows(cfg, comp="WC", last_n=2))      # evaluate the newest 2
    # Offline records are llm=False, so evaluated_keys (per-provider) ignores them…
    assert evaluated_keys(cfg, "openai") == set()
    # …but with explicit exclusion the older fixture is the only one left.
    log = load_eval_log(cfg)
    done = {(r["date"], r["home"], r["away"]) for r in log}
    remaining = pick_rows(cfg, comp="WC", last_n=10, exclude=done)
    assert len(remaining) == 1 and remaining[0]["date"] == "2026-05-10"


def test_eval_log_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    p = eval_log_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hg": 1, "ag": 1}) + "\nnot-json\n", encoding="utf-8")
    out = load_eval_log(cfg)                            # bad lines skipped quietly
    assert out == [{"hg": 1, "ag": 1}]
