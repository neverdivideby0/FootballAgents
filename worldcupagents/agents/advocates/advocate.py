"""Team advocate node — analog of TradingAgents' bull/bear researchers, but HYBRID:
each advocate argues its case AND must self-critique (name its own team's
weaknesses). The 'Weaknesses' line is required by the prompt to counter bias.

use_llm=False -> deterministic placeholder text (offline). use_llm=True -> a real
LLM argument that reads the squad, matchup context, and the debate so far. LLM
errors degrade visibly (never crash a prediction).
"""

from __future__ import annotations

import logging

from worldcupagents.agents.briefs import profile_brief
from worldcupagents.graph.state import MatchState

logger = logging.getLogger(__name__)


def make_advocate(side: str, config: dict, llm=None, usage_acc: dict | None = None):
    """side: 'home' or 'away'.

    usage_acc: optional mutable dict {"input": int, "output": int} — mutated in-place
    with each LLM call's token counts so the caller can track total spend.
    """
    assert side in ("home", "away")
    label = "Home" if side == "home" else "Away"
    my_key = f"{side}_profile"
    opp_key = "away_profile" if side == "home" else "home_profile"
    use_llm = bool(config.get("use_llm")) and llm is not None

    def advocate(state: MatchState) -> dict:
        debate = dict(state.get("debate_state") or {})
        me, opp = state[my_key], state[opp_key]

        if use_llm:
            try:
                text = _llm_argument(llm, label, me, opp, state, debate, usage_acc)
            except Exception as e:  # noqa: BLE001 — visible degrade, no crash
                logger.warning("Advocate LLM error for %s (%s); placeholder text", me.team, e)
                text = f"[LLM unavailable] {_placeholder_argument(me, opp)}"
        else:
            text = _placeholder_argument(me, opp)

        argument = f"{label} Advocate ({me.team}): {text}"
        side_hist_key = f"{side}_history"
        debate.update({
            "history": (debate.get("history", "") + "\n" + argument).strip(),
            side_hist_key: (debate.get(side_hist_key, "") + "\n" + argument).strip(),
            "current_response": argument,
            "count": debate.get("count", 0) + 1,
        })
        return {"debate_state": debate}

    return advocate


def _placeholder_argument(me, opp) -> str:
    rank = me.fifa_rank if me.fifa_rank is not None else "unranked"
    return (
        f"[placeholder] Case for {me.team} (FIFA #{rank}) — enable use_llm for a real "
        f"argument. Weaknesses: {me.team}'s vulnerabilities vs {opp.team} go here "
        f"(self-critique is mandatory by design)."
    )


def _llm_argument(llm, label, me, opp, state, debate, usage_acc: dict | None = None) -> str:
    ctx = state.get("matchup_context") or {}
    history = debate.get("history", "")
    last = debate.get("current_response", "")
    pc = state.get("past_context") or ""
    tactical = f"\nTACTICAL HISTORY & PAST-PREDICTION LESSONS (from memory — use them to support your case):\n{pc}\n" if pc else ""
    records = ctx.get("records") or ""
    rec_line = f"HOME & HEAD-TO-HEAD RECORD: {records}\n" if records else ""
    from worldcupagents.agents.judge.pundit import reports_block
    reports = reports_block(state)
    prompt = f"""You are the {label} Team Advocate for {me.team}, debating whether {me.team} \
will get the better of {opp.team}.

YOUR TEAM:  {profile_brief(me)}
OPPONENT:   {profile_brief(opp)}
MATCH:      {ctx.get('stage_label') or ctx.get('stage')}, venue={ctx.get('venue_note') or ctx.get('venue') or 'TBD'}, knockout={ctx.get('knockout')}
{rec_line}{reports}{tactical}
Debate so far:
{history or '(you are speaking first)'}

Opponent's last argument:
{last or '(none yet)'}

Write a persuasive, evidence-grounded argument (≤180 words) for why {me.team} has the edge.
- If the opponent has spoken, engage their strongest point directly.
- Anchor your case on a SPECIFIC area of the game where {me.team} wins it — a particular
  player, a flank, the centre-forward, midfield control, set pieces, pace, or experience —
  not just "we're better overall".
- If the reports describe the coaches, use {me.team}'s manager (their tactical plan,
  big-match pedigree, in-game adjustments) as part of the case where it genuinely helps.
- Reason from squad quality, form, and matchup dynamics. Do NOT invent specific stats you were not given.
- CITE your evidence: when you reference a result, stat, or tactical observation, quote it with
  its date and source tag exactly as it appears in the data above (e.g. "2-1 v Chelsea FC
  (2026-05-24) [fdcouk:PL:2425]"). Uncited specifics will be treated as hallucinations.
- You MUST finish with a line beginning "Weaknesses:" honestly naming {me.team}'s own \
vulnerabilities in THIS matchup. This is required to keep the debate unbiased."""
    msg = llm.invoke(prompt)
    _accum(usage_acc, getattr(msg, "usage_metadata", None))
    return msg.content


def _accum(usage_acc: dict | None, meta) -> None:
    """Merge LangChain usage_metadata into the running accumulator (mutates in-place)."""
    if usage_acc is None or not meta:
        return
    usage_acc["input"]  += meta.get("input_tokens",  0)
    usage_acc["output"] += meta.get("output_tokens", 0)
