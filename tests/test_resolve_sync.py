"""resolve --sync: auto-resolve pending predictions from store results (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.graph.predict import Predictor
from worldcupagents.graph.reflection import sync_pending


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def _predict(cfg, home="Brazil", away="Mexico"):
    Predictor(cfg).predict(Fixture(home=home, away=away, stage=Stage.GROUP))


def _store_result(cfg, home="Brazil", away="Mexico", hg=2, ag=0, date="2099-12-31"):
    store = MatchStore.from_config(cfg)
    store.upsert([{"date": date, "comp": "WC", "home": home, "away": away,
                   "hg": hg, "ag": ag, "source": "test"}])
    store.close()


def test_sync_resolves_pending_from_store(tmp_path):
    cfg = _cfg(tmp_path)
    _predict(cfg)                                   # writes a pending entry (today)
    _store_result(cfg, hg=2, ag=0)                  # the result lands later
    results = sync_pending(cfg)
    assert len(results) == 1
    r = results[0]
    assert r["actual"] == "HOME_WIN" and r["match_date"] == "2099-12-31"
    assert 0.0 <= r["brier"] <= 2.0
    # The log entry is rewritten to resolved with the real scoreline.
    text = (tmp_path / "memory" / "prediction_log.md").read_text(encoding="utf-8")
    assert "| pending]" not in text
    assert "resolved: HOME_WIN 2-0" in text
    # Per-team lessons were appended.
    assert (tmp_path / "memory" / "teams").exists()


def test_sync_is_a_noop_when_no_result_yet(tmp_path):
    cfg = _cfg(tmp_path)
    _predict(cfg)
    # Store has only a match BEFORE the prediction date — not the predicted fixture.
    _store_result(cfg, date="2000-01-01")
    assert sync_pending(cfg) == []
    text = (tmp_path / "memory" / "prediction_log.md").read_text(encoding="utf-8")
    assert "| pending]" in text                     # untouched


def test_sync_without_log_or_store(tmp_path):
    cfg = _cfg(tmp_path)
    assert sync_pending(cfg) == []                  # nothing exists: no crash
    _predict(cfg)
    assert sync_pending(cfg) == []                  # log but no store db


def test_sync_picks_earliest_match_after_prediction(tmp_path):
    cfg = _cfg(tmp_path)
    _predict(cfg)
    _store_result(cfg, hg=1, ag=1, date="2099-01-01")   # the predicted fixture
    _store_result(cfg, hg=3, ag=0, date="2099-06-01")   # a later rematch
    results = sync_pending(cfg)
    assert len(results) == 1
    assert results[0]["actual"] == "DRAW"               # earliest match wins
    assert results[0]["match_date"] == "2099-01-01"


def test_sync_resolves_multiple_fixtures(tmp_path):
    cfg = _cfg(tmp_path)
    _predict(cfg, "Brazil", "Mexico")
    _predict(cfg, "France", "USA")
    _store_result(cfg, "Brazil", "Mexico", 2, 0)
    _store_result(cfg, "France", "USA", 0, 1)
    results = sync_pending(cfg)
    assert {(r["home"], r["actual"]) for r in results} == {
        ("Brazil", "HOME_WIN"), ("France", "AWAY_WIN")}
