"""Phase 1 — reliable stage detection + draw calibration + contextual clamp (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, JudgeRead, Stage, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.ensemble.baseline import blend, clamp_to_band, grid_outcome_probs, score_grid
from worldcupagents.ensemble.draw import draw_uplift
from worldcupagents.ensemble.verdict import assemble_verdict


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")   # no store → rank-Elo, hermetic
    cfg["verdict_mode"] = "stats"              # this file tests the statistical path
    return cfg


def _P(team, rank=None):
    return TeamProfile(team=team, fifa_rank=rank)


# ── stage detection ─────────────────────────────────────────────────────────

def test_map_feed_stage():
    from worldcupagents.dataflows.fixtures import map_feed_stage
    assert map_feed_stage("GROUP_STAGE") == Stage.GROUP
    assert map_feed_stage("LAST_16") == Stage.R16
    assert map_feed_stage("QUARTER_FINALS") == Stage.QF
    assert map_feed_stage("THIRD_PLACE") == Stage.FINAL
    assert map_feed_stage("nonsense") is None


def test_resolve_stage_from_feed(monkeypatch):
    import worldcupagents.pipelines.simulate as sim
    feed = [{"home": "Spain", "away": "Germany", "date": "2026-07-10", "stage": "LAST_16"}]
    monkeypatch.setattr(sim, "load_wc_fixtures", lambda config: feed)
    from worldcupagents.dataflows.fixtures import resolve_stage
    assert resolve_stage("Spain", "Germany", None, DEFAULT_CONFIG) == (Stage.R16, "feed")
    assert resolve_stage("Germany", "Spain", None, DEFAULT_CONFIG) == (Stage.R16, "feed")  # orientation
    assert resolve_stage("Spain", "Brazil", None, DEFAULT_CONFIG) == (None, "absent")       # not in feed


# ── draw calibration ────────────────────────────────────────────────────────

def _base(lh, la):
    return grid_outcome_probs(score_grid(lh, la))


def test_draw_uplift_scales_with_closeness(tmp_path):
    cfg = _cfg(tmp_path)
    even = draw_uplift(_base(1.4, 1.4), 1.4, 1.4, _P("X"), _P("Y"), cfg)
    close = draw_uplift(_base(1.5, 1.3), 1.5, 1.3, _P("X"), _P("Y"), cfg)
    lop = draw_uplift(_base(2.5, 0.5), 2.5, 0.5, _P("X"), _P("Y"), cfg)
    d_even = even[1] - _base(1.4, 1.4)[1]
    d_close = close[1] - _base(1.5, 1.3)[1]
    d_lop = lop[1] - _base(2.5, 0.5)[1]
    assert d_even > d_close > d_lop >= 0          # closer → bigger draw boost
    assert d_even <= cfg["draw_calibration_max"] + 1e-9   # bounded
    assert abs(sum(even) - 1.0) < 1e-9


def test_draw_uplift_disabled_when_cap_zero(tmp_path):
    cfg = _cfg(tmp_path); cfg["draw_calibration_max"] = 0.0
    b = _base(1.4, 1.4)
    assert draw_uplift(b, 1.4, 1.4, _P("X"), _P("Y"), cfg) == b


# ── contextual clamp ────────────────────────────────────────────────────────

def test_clamp_bounds_the_contextual_move():
    base = (0.40, 0.30, 0.30)
    blended = blend((0.90, 0.05, 0.05), base, 0.886)
    clamped = clamp_to_band(blended, base, 0.15)
    assert abs(sum(clamped) - 1.0) < 1e-9
    assert all(abs(c - b) <= 0.15 + 1e-9 for c, b in zip(clamped, base))   # nothing moves > δ
    assert clamped[0] < blended[0]                                          # the big jump is reined in
    # within-band reads pass through untouched
    small = blend((0.45, 0.30, 0.25), base, 0.6)
    assert clamp_to_band(small, base, 0.15) == small


# ── integration: assemble_verdict ───────────────────────────────────────────

def test_group_verdict_applies_draw_uplift(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="A", away="B", stage=Stage.GROUP)
    v = assemble_verdict(cfg, fx, _P("A", 20), _P("B", 22), None, 0.6)   # near-even ranks
    raw_base_draw = grid_outcome_probs(score_grid(v.exp_goals_home, v.exp_goals_away))[1]
    assert v.p_draw >= raw_base_draw                                      # draw nudged up (or equal)


def test_knockout_verdict_has_no_draw(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="A", away="B", stage=Stage.QF)
    v = assemble_verdict(cfg, fx, _P("A", 5), _P("B", 30), None, 0.6)
    assert v.p_draw == 0.0                                                # knockouts fold the draw


def test_clamp_reins_in_extreme_judge(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="A", away="B", stage=Stage.GROUP)
    read = JudgeRead(p_home=0.97, p_draw=0.02, p_away=0.01, scoreline="3-0")
    v = assemble_verdict(cfg, fx, _P("A", 18), _P("B", 20), read, 0.886)
    # The judge wanted 0.97; with the clamp it can't run away from a near-even base.
    assert v.p_home < 0.80
