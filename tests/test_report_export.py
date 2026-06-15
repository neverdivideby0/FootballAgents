"""M-E tests — sectioned markdown report export (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import Predictor
from worldcupagents.pipelines.report_export import build_markdown_report, export_markdown_report


def _cfg(tmp_path, **overrides) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["exports_dir"] = str(tmp_path / "exports")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    cfg.update(overrides)
    return cfg


def test_markdown_report_has_all_sections(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    predictor = Predictor(cfg)
    final, v = predictor.predict(fx)

    md = build_markdown_report(fx, v, final, predictor, cfg)
    assert "# Brazil vs Mexico" in md
    assert "## Summary" in md and "**Call:" in md          # answer-first summary
    assert "## 1. Pre-Match Dossier" in md
    assert "### Brazil" in md and "### Mexico" in md       # dossier per-team blocks
    assert "## 2. Analyst Reports" in md and "**Form analyst**" in md
    assert "## 3. Advocate Debate" in md
    assert "## 4. Provisional Verdict (Judge)" in md
    assert "## 5. Scenario (Risk) Debate" in md      # scenario on by default
    assert "## 6. Final Verdict" in md
    assert "Probabilities: H " in md


def test_markdown_report_skips_disabled_sections(tmp_path):
    cfg = _cfg(tmp_path, enable_analyst_reports=False, enable_scenario_debate=False)
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    predictor = Predictor(cfg)
    final, v = predictor.predict(fx)

    md = build_markdown_report(fx, v, final, predictor, cfg)
    assert "## 2. Analyst Reports" not in md
    assert "## 5. Scenario (Risk) Debate" not in md
    assert "## 6. Final Verdict" in md               # always present


def test_export_writes_file(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    predictor = Predictor(cfg)
    final, v = predictor.predict(fx)

    path = export_markdown_report(fx, v, final, predictor, cfg)
    assert path.exists() and path.suffix == ".md"
    assert "Brazil_vs_Mexico" in path.name
    assert "## 6. Final Verdict" in path.read_text()
