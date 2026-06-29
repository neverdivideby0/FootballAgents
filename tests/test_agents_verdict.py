"""Agent-driven verdict mode — the judge's score/probabilities ARE the verdict
(no Poisson), with the upset watch sourced from the advocates' black swans, and a
clean fallback to the statistical path when there's no LLM read (all hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.advocates.advocate import _parse_scorelines
from worldcupagents.agents.schemas import DecidedBy, Fixture, JudgeRead, Outcome, Stage, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.ensemble.verdict import assemble_verdict


def _cfg(tmp_path, **over) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")  # no store → hermetic
    cfg.update(over)
    return cfg


def _P(team, rank=None):
    return TeamProfile(team=team, fifa_rank=rank)


def _read(**over):
    base = dict(p_home=0.7, p_draw=0.2, p_away=0.1, scoreline="2-0", confidence="high",
                key_factors=["midfield control"], x_factors=["heat"], rationale="stronger side")
    base.update(over)
    return JudgeRead(**base)


_DS = {
    "home_scorelines": ["2-0", "2-1", "3-0"], "home_black_swan": "0-1 (a counter on the break)",
    "away_scorelines": ["0-1", "1-1", "0-0"], "away_black_swan": "1-2 (set-piece upset)",
}


# ── advocate scoreline parsing ───────────────────────────────────────────────

def test_parse_scorelines_and_black_swan():
    likely, swan = _parse_scorelines(
        "…my case. Scorelines: 2-1, 1-1, 2-0 | Black swan: 0-2 (if our press is bypassed)\n"
        "Weaknesses: shaky at the back."
    )
    assert likely == ["2-1", "1-1", "2-0"]
    assert swan.startswith("0-2")
    assert "press is bypassed" in swan


def test_parse_scorelines_absent():
    assert _parse_scorelines("just prose, no scorelines line") == ([], "")


# ── agents mode ──────────────────────────────────────────────────────────────

def test_agents_mode_uses_judge_read_directly(tmp_path):
    cfg = _cfg(tmp_path)  # default verdict_mode == "agents"
    fx = Fixture(home="Spain", away="Saudi Arabia", stage=Stage.GROUP)
    v = assemble_verdict(cfg, fx, _P("Spain", 2), _P("Saudi Arabia", 60), _read(), 0.6, debate_state=_DS)

    assert v.outcome == Outcome.HOME_WIN
    assert (round(v.p_home, 2), round(v.p_draw, 2), round(v.p_away, 2)) == (0.7, 0.2, 0.1)
    assert v.scoreline == "2-0"
    assert v.breakdown is None              # no blend happened
    assert v.exp_goals_home is None         # a Poisson concept — n/a here


def test_agents_alternative_from_black_swan(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="Spain", away="Saudi Arabia", stage=Stage.GROUP)
    v = assemble_verdict(cfg, fx, _P("Spain", 2), _P("Saudi Arabia", 60), _read(), 0.6, debate_state=_DS)
    alt = v.alternative
    assert alt is not None and alt.outcome != v.outcome      # a genuine upset
    assert alt.scoreline == "1-2"                            # the away black-swan score
    assert any("set-piece" in s for s in alt.swing_factors)  # the 'how it happens' clause


def test_group_draw_stays_a_draw(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="A", away="B", stage=Stage.GROUP)
    read = _read(p_home=0.25, p_draw=0.5, p_away=0.25, scoreline="1-1")
    v = assemble_verdict(cfg, fx, _P("A"), _P("B"), read, 0.6, debate_state=_DS)
    assert v.outcome == Outcome.DRAW and v.p_draw == 0.5


def test_knockout_level_score_resolves_to_winner(tmp_path):
    cfg = _cfg(tmp_path)
    fx = Fixture(home="Spain", away="Saudi Arabia", stage=Stage.R16)
    read = _read(p_home=0.52, p_draw=0.28, p_away=0.20, scoreline="1-1", decided_by=DecidedBy.PENALTIES)
    v = assemble_verdict(cfg, fx, _P("Spain", 2), _P("Saudi Arabia", 60), read, 0.6, debate_state=_DS)

    assert v.p_draw == 0.0                               # a knockout can't end level
    assert v.outcome == Outcome.HOME_WIN                 # the favoured side advances
    assert v.decided_by == DecidedBy.PENALTIES
    assert "a.e.t., pens" in v.scoreline


# ── fallback + explicit stats mode ───────────────────────────────────────────

def test_offline_falls_back_to_stats(tmp_path):
    cfg = _cfg(tmp_path)  # agents mode, but no read available
    fx = Fixture(home="Spain", away="Saudi Arabia", stage=Stage.GROUP)
    v = assemble_verdict(cfg, fx, _P("Spain", 2), _P("Saudi Arabia", 60), None, 0.6)
    assert v.breakdown is not None                        # the statistical path ran
    assert v.exp_goals_home is not None                  # λ present on the stats path


def test_explicit_stats_mode_blends(tmp_path):
    cfg = _cfg(tmp_path, verdict_mode="stats")
    fx = Fixture(home="Spain", away="Saudi Arabia", stage=Stage.GROUP)
    v = assemble_verdict(cfg, fx, _P("Spain", 2), _P("Saudi Arabia", 60), _read(), 0.6, debate_state=_DS)
    assert v.breakdown is not None                        # blended, not verbatim
