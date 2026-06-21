"""watch tick: hermetic, idempotent matchday autopilot (placeholder vendors, no net)."""

from __future__ import annotations

import copy
from pathlib import Path

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.cli import _watch_tick
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.graph.predict import Predictor


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    cfg["fd_competition"] = "WC"
    return cfg


def _seed_finished(cfg, home="Brazil", away="Mexico", hg=2, ag=0, date="2099-12-31"):
    store = MatchStore.from_config(cfg)
    store.upsert([{"date": date, "comp": "WC", "home": home, "away": away,
                   "hg": hg, "ag": ag, "source": "test"}])
    store.close()


def test_tick_resolves_pending_and_is_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    Predictor(cfg).predict(Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP))  # pending entry
    _seed_finished(cfg)                                            # result lands in the store

    _watch_tick(cfg, "WC2026", reflect_llm=None)

    # Punditry digest (placeholder) + tactical report written for the finished match.
    mid = "Brazil_vs_Mexico_2099-12-31"
    assert (Path(cfg["memory_dir"]) / "punditry" / f"{mid}.json").exists()
    assert (Path(cfg["memory_dir"]) / "matches" / f"{mid}.json").exists()
    # The pending prediction was auto-resolved.
    log = Path(cfg["prediction_log_path"]).read_text(encoding="utf-8")
    assert "| resolved:" in log

    # Second tick is a no-op: the digest already exists, so nothing is reprocessed
    # and it must not crash.
    digest_path = Path(cfg["memory_dir"]) / "punditry" / f"{mid}.json"
    before = digest_path.read_text(encoding="utf-8")
    _watch_tick(cfg, "WC2026", reflect_llm=None)
    assert digest_path.read_text(encoding="utf-8") == before
