"""Final Pundit — analog of TradingAgents' Portfolio Manager.

Reads the judge's provisional verdict plus the scenario (risk) debate and issues
the FINAL MatchVerdict. Its structured read is re-blended against the SAME
Poisson baseline via the shared assembly pipeline, so the final probabilities
stay anchored — the scenario debate can shift the read, never unmoor it.

Degradation: use_llm off, no LLM, or an LLM error ⇒ pass the provisional verdict
through unchanged (offline behavior identical to the judge-only topology).
"""

from __future__ import annotations

import logging

from worldcupagents.agents.schemas import JudgeRead
from worldcupagents.ensemble.verdict import assemble_verdict
from worldcupagents.graph.state import MatchState

logger = logging.getLogger(__name__)


def make_final_pundit(config: dict, llm=None, usage_acc: dict | None = None):
    """usage_acc: optional mutable dict {"input": int, "output": int} for token tracking."""
    from worldcupagents.calibration import effective_judge_weight
    judge_weight = effective_judge_weight(config)
    use_llm = bool(config.get("use_llm")) and llm is not None

    def final_pundit(state: MatchState) -> dict:
        provisional = state.get("provisional_verdict") or state.get("verdict")

        if not use_llm:
            return {"verdict": provisional}  # pass-through: offline == today's behavior

        try:
            read = _llm_final_read(llm, state, usage_acc, config)
        except Exception as e:  # noqa: BLE001 — fall back to the provisional verdict
            logger.warning("Final Pundit LLM error (%s); provisional verdict stands", e)
            return {"verdict": provisional}

        fx = state["fixture"]
        verdict = assemble_verdict(
            config, fx, state["home_profile"], state["away_profile"], read, judge_weight
        )
        return {"verdict": verdict}

    return final_pundit


def _llm_final_read(llm, state: MatchState, usage_acc: dict | None = None,
                    config: dict | None = None) -> JudgeRead:
    from worldcupagents.agents.judge.pundit import reports_block, stage_line
    from worldcupagents.agents.scenario.pundits import _provisional_digest

    fx = state["fixture"]
    home, away = state["home_profile"], state["away_profile"]
    scenario_history = (state.get("scenario_debate_state") or {}).get("history", "")
    pc = state.get("past_context") or ""
    lessons = f"\nLESSONS & HISTORY FROM MEMORY:\n{pc}\n" if pc else ""
    market = ""
    mr = (state.get("matchup_context") or {}).get("market")
    if mr:
        from worldcupagents.dataflows.market import market_digest
        market = f"\nLIVE MARKET (the benchmark prior):\n{market_digest(mr)}\n"
    cal = state.get("calibration_note") or ""
    calibration = (f"\nCALIBRATION FEEDBACK (our own resolved track record — correct for it):\n{cal}\n"
                   if cal else "")
    stage_label, stage_rule = stage_line(config or {}, fx)
    prompt = f"""You are the FINAL pundit — the last word on {home.team} (home) vs {away.team} (away).

Fixture: {stage_label}. {stage_rule}

The judge's PROVISIONAL verdict (post advocate-debate):
{_provisional_digest(state)}
{reports_block(state)}{market}{calibration}{lessons}
Three scenario pundits then stress-tested that verdict:
{scenario_history or '(no scenario debate available)'}

Issue the FINAL verdict. ADJUST the provisional probabilities ONLY where the scenario
debate surfaced concrete, evidence-backed risk — otherwise hold them. State explicitly in
your rationale what (if anything) you moved and which pundit's point justified it.
Return calibrated probabilities for HOME_WIN / DRAW / AWAY_WIN that sum to 1, a likely
scoreline, your confidence, the decisive factors, and the external x-factors."""
    chain = llm.with_structured_output(JudgeRead, include_raw=True)
    result = chain.invoke(prompt)
    raw = result.get("raw") if isinstance(result, dict) else None
    if usage_acc is not None and raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta:
            usage_acc["input"] += meta.get("input_tokens", 0)
            usage_acc["output"] += meta.get("output_tokens", 0)
    parsed = result.get("parsed") if isinstance(result, dict) else result
    return parsed
