"""M0 smoke tests — the graph wires up and runs end-to-end on the placeholder path."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, Outcome, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.predict import Predictor


def _isolated_config(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path / "runs")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_dir"] = str(tmp_path / "data")  # isolate the match store (no real data/)
    # Pin the offline provider so these tests never depend on a token/network.
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def test_group_stage_runs_and_probs_sum_to_one(tmp_path):
    fx = Fixture(home="Brazil", away="Mexico", stage=Stage.GROUP)
    _, v = Predictor(_isolated_config(tmp_path)).predict(fx)
    assert v.outcome in Outcome
    assert abs(v.p_home + v.p_draw + v.p_away - 1.0) < 1e-6


def test_knockout_has_no_draw(tmp_path):
    fx = Fixture(home="France", away="USA", stage=Stage.QF)
    _, v = Predictor(_isolated_config(tmp_path)).predict(fx)
    assert v.outcome != Outcome.DRAW
    assert v.p_draw == 0.0


def test_debate_runs_full_round_cap(tmp_path):
    fx = Fixture(home="Spain", away="Germany", stage=Stage.GROUP)
    final, _ = Predictor(_isolated_config(tmp_path)).predict(fx)
    # max_debate_rounds=2 -> 4 alternating turns.
    assert final["debate_state"]["count"] == 4


def test_prediction_log_written(tmp_path):
    fx = Fixture(home="Argentina", away="Croatia", stage=Stage.SF)
    cfg = _isolated_config(tmp_path)
    Predictor(cfg).predict(fx)
    log_text = (tmp_path / "memory" / "prediction_log.md").read_text()
    assert "Argentina vs Croatia" in log_text
    assert "pending" in log_text
