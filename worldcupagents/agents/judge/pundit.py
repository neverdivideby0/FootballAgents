"""Match Pundit (judge) — analog of TradingAgents' Research Manager.

Synthesizes the debate into a structured MatchVerdict via the SHARED assembly
pipeline (ensemble/verdict.py): the judge's qualitative read (LLM structured
output, or a softened baseline when use_llm is off) blended with the statistical
Poisson baseline. Never a raw un-anchored LLM percentage.

The judge's verdict is PROVISIONAL when the scenario debate layer is enabled —
the Final Pundit (Portfolio-Manager analog) may adjust it after the risk debate.
It is also written to ``verdict`` so the graph remains correct with the scenario
layer disabled (today's topology).
"""

from __future__ import annotations

import logging

from worldcupagents.agents.briefs import profile_brief
from worldcupagents.agents.schemas import JudgeRead
from worldcupagents.ensemble.verdict import assemble_verdict
from worldcupagents.graph.state import MatchState

logger = logging.getLogger(__name__)


def make_judge(config: dict, llm=None, usage_acc: dict | None = None):
    """usage_acc: optional mutable dict {"input": int, "output": int} for token tracking."""
    from worldcupagents.calibration import effective_judge_weight
    judge_weight = effective_judge_weight(config)
    use_llm = bool(config.get("use_llm")) and llm is not None

    def judge(state: MatchState) -> dict:
        fx = state["fixture"]
        home, away = state["home_profile"], state["away_profile"]

        read: JudgeRead | None = None
        if use_llm:
            try:
                read = _llm_judge_read(llm, state, usage_acc, config)
            except Exception as e:  # noqa: BLE001 — degrade to baseline, visibly
                logger.warning("Judge LLM error (%s); baseline-only verdict", e)

        verdict = assemble_verdict(config, fx, home, away, read, judge_weight)
        # provisional_verdict feeds the scenario debate; verdict keeps the graph
        # correct (and identical to today) when the scenario layer is off.
        return {"verdict": verdict, "provisional_verdict": verdict}

    return judge


def stage_line(config: dict, fx) -> tuple[str, str]:
    """(stage label, draw/knockout rule) phrased per competition kind — leagues
    have no 'group stage'."""
    if fx.knockout:
        return fx.stage.value, ("KNOCKOUT — there must be a winner; set p_draw to 0 "
                                "(extra time / penalties decide).")
    if config.get("league_kind") == "league":
        return "league match", "LEAGUE MATCH — a draw is a valid outcome."
    return fx.stage.value, "GROUP STAGE — a draw is a valid outcome."


def reports_block(state: MatchState) -> str:
    """Analyst report sections shared by judge / advocates / scenario pundits."""
    parts = []
    for label, key in (("FORM REPORT", "form_report"),
                       ("TACTICAL REPORT", "tactical_report"),
                       ("PLAYER REPORT", "player_report")):
        text = state.get(key) or ""
        if text:
            parts.append(f"{label}:\n{text}")
    return ("\n" + "\n\n".join(parts) + "\n") if parts else ""


def _llm_judge_read(llm, state: MatchState, usage_acc: dict | None = None,
                    config: dict | None = None) -> JudgeRead:
    fx = state["fixture"]
    home, away = state["home_profile"], state["away_profile"]
    ctx = state.get("matchup_context") or {}
    history = (state.get("debate_state") or {}).get("history", "")
    pc = state.get("past_context") or ""
    tactical = f"\nTACTICAL HISTORY & PAST-PREDICTION LESSONS (from memory):\n{pc}\n" if pc else ""
    records = ctx.get("records") or ""
    rec_line = f"HOME & HEAD-TO-HEAD RECORD: {records}\n" if records else ""
    market = ""
    if ctx.get("market"):
        from worldcupagents.dataflows.market import market_digest
        market = ("\nLIVE MARKET (the sharpest available prior — treat it as the benchmark; "
                  "if your probabilities differ, say explicitly where and why it is wrong):\n"
                  f"{market_digest(ctx['market'])}\n")
    reports = reports_block(state)
    focus = ""
    try:
        from worldcupagents.ensemble.focus import focus_digest, match_focus
        fd = focus_digest(match_focus(config or {}, home, away))
        if fd:
            focus = ("\nMATCH FOCUS (data-derived — decide which of these actually swings the game):\n"
                     f"{fd}\n")
    except Exception:  # noqa: BLE001
        pass
    cal = state.get("calibration_note") or ""
    calibration = (f"\nCALIBRATION FEEDBACK (our own resolved track record — correct for it):\n{cal}\n"
                   if cal else "")
    stage_label, stage_rule = stage_line(config or {}, fx)
    prompt = f"""You are a seasoned, neutral football pundit giving the final verdict on \
{home.team} (home) vs {away.team} (away).

Fixture: {stage_label}. {stage_rule}
Venue: {ctx.get('venue_note') or ctx.get('venue') or 'TBD'}.

{home.team}: {profile_brief(home)}
{away.team}: {profile_brief(away)}
{rec_line}{market}{focus}{calibration}{reports}{tactical}
The two team advocates debated:
{history or '(no debate available)'}

Synthesize the debate into a grounded verdict. Weigh the strongest points from each side,
discount weak or biased claims, and account for external x-factors the advocates may have
under-weighted (travel, heat/altitude, knockout pressure, squad depth, fatigue).
Weigh the two head COACHES where the reports give them: their style (pragmatic/low-block
vs expansive), in-tournament adjustment, and big-match pedigree can decide a tight game —
name the coaching edge in key_factors when it is real.
Be SPECIFIC about WHERE the game is won: name the decisive battleground (a flank, the
centre-forward, midfield control, set pieces, pace vs experience) in key_factors, and the
single player most likely to decide it in x_factors.
In key_factors and rationale, CITE the evidence you rely on with its date/source tag exactly
as given above (e.g. "(2026-05-24) [fdcouk:PL:2425]"); treat any advocate claim that lacks a
source in the data as unverified.
Return calibrated probabilities for HOME_WIN / DRAW / AWAY_WIN that sum to 1, a likely
scoreline, your confidence, the decisive factors, and the external x-factors."""
    # include_raw=True lets us capture token usage from the underlying AIMessage
    chain = llm.with_structured_output(JudgeRead, include_raw=True)
    result = chain.invoke(prompt)
    raw = result.get("raw") if isinstance(result, dict) else None
    if usage_acc is not None and raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta:
            usage_acc["input"]  += meta.get("input_tokens",  0)
            usage_acc["output"] += meta.get("output_tokens", 0)
    parsed = result.get("parsed") if isinstance(result, dict) else result
    return parsed
