"""Scenario pundits — analog of TradingAgents' Aggressive/Conservative/Neutral
risk debators. They stress-test the judge's PROVISIONAL verdict in rotation:

  * Upside ("variance"):  argues the upset/chaos case — set pieces, momentum,
    knockout variance, rotation/fatigue risk for the favourite, complacency.
  * Downside ("chalk"):   argues the class case — squad depth, pedigree,
    baseline strength; why the favourite holds.
  * Neutral (arbiter):    weighs both, flags where the provisional probabilities
    look over- or under-confident.

State threading is a direct port of TA's risk debators: each turn appends to the
shared history, records itself as latest_speaker (drives the 3-way rotation in
ConditionalLogic.should_continue_scenario), and bumps count.
"""

from __future__ import annotations

import logging

from worldcupagents.graph.state import MatchState

logger = logging.getLogger(__name__)

_ROLES = {
    "upside": {
        "label": "Upside",
        "persona": (
            "You are the UPSIDE (variance) pundit. Argue where the provisional verdict "
            "UNDER-prices chaos: upset potential, set pieces, momentum swings, red-card and "
            "penalty variance, favourite complacency, rotation or fatigue risk. You believe "
            "football is higher-variance than models admit."
        ),
    },
    "downside": {
        "label": "Downside",
        "persona": (
            "You are the DOWNSIDE (chalk) pundit. Argue where the provisional verdict "
            "UNDER-prices class: squad depth, tournament pedigree, baseline strength, "
            "game-management. You believe quality tells over 90 minutes and upsets are rarer "
            "than narratives suggest."
        ),
    },
    "neutral": {
        "label": "Neutral",
        "persona": (
            "You are the NEUTRAL pundit, the arbiter. Weigh the Upside and Downside arguments, "
            "call out which specific claims are evidence-backed vs vibes, and state plainly "
            "whether the provisional probabilities look over- or under-confident, and in which "
            "direction."
        ),
    },
}


def make_scenario_pundit(role: str, config: dict, llm=None, usage_acc: dict | None = None):
    """role: 'upside' | 'downside' | 'neutral'."""
    assert role in _ROLES
    label = _ROLES[role]["label"]
    persona = _ROLES[role]["persona"]
    use_llm = bool(config.get("use_llm")) and llm is not None

    def pundit(state: MatchState) -> dict:
        sd = dict(state.get("scenario_debate_state") or {})

        if use_llm:
            try:
                text = _llm_turn(llm, persona, label, state, sd, usage_acc)
            except Exception as e:  # noqa: BLE001 — visible degrade, never crash
                logger.warning("%s pundit LLM error (%s); placeholder turn", label, e)
                text = f"[LLM unavailable] {_placeholder_turn(label, state)}"
        else:
            text = _placeholder_turn(label, state)

        turn = f"{label} Pundit: {text}"
        hist_key = f"{role}_history"
        sd.update({
            "history": (sd.get("history", "") + "\n" + turn).strip(),
            hist_key: (sd.get(hist_key, "") + "\n" + turn).strip(),
            "latest_speaker": label,
            f"current_{role}_response": turn,
            "count": sd.get("count", 0) + 1,
        })
        return {"scenario_debate_state": sd}

    return pundit


def _provisional_digest(state: MatchState) -> str:
    v = state.get("provisional_verdict")
    if v is None:
        return "(no provisional verdict available)"
    alt = ""
    if getattr(v, "alternative", None) is not None:
        a = v.alternative
        alt = (f" Live alternative: {a.outcome.value} {a.scoreline} at {a.probability:.0%} "
               f"({a.gap:.0%} behind). The model already says the favourite is not a lock — "
               f"argue whether that upset path is under- or over-priced.")
    return (
        f"{v.outcome.value} {v.scoreline} — "
        f"H {v.p_home:.0%} / D {v.p_draw:.0%} / A {v.p_away:.0%} ({v.confidence}). "
        f"Rationale: {v.rationale}{alt}"
    )


def _placeholder_turn(label: str, state: MatchState) -> str:
    v = state.get("provisional_verdict")
    call = v.outcome.value if v else "?"
    angle = {
        "Upside": "the variance case against",
        "Downside": "the class case for",
        "Neutral": "an arbiter's read of",
    }[label]
    return (
        f"[placeholder] {angle} the provisional {call} goes here. "
        "Enable use_llm for a real scenario debate."
    )


def _llm_turn(llm, persona, label, state, sd, usage_acc) -> str:
    from worldcupagents.agents.judge.pundit import reports_block

    fx = state["fixture"]
    home, away = state["home_profile"], state["away_profile"]
    others = "\n".join(
        resp for key, resp in (
            ("current_upside_response", sd.get("current_upside_response", "")),
            ("current_downside_response", sd.get("current_downside_response", "")),
            ("current_neutral_response", sd.get("current_neutral_response", "")),
        ) if resp and not resp.startswith(f"{label} Pundit")
    )
    prompt = f"""{persona}

MATCH: {home.team} (home) vs {away.team} (away), stage={fx.stage.value}, knockout={fx.knockout}.

PROVISIONAL VERDICT (from the judge, after the advocate debate):
{_provisional_digest(state)}
{reports_block(state)}
Scenario debate so far:
{sd.get('history', '') or '(you are speaking first)'}

The other pundits' latest points:
{others or '(none yet)'}

Make your case in ≤160 words. Engage the other pundits' strongest specific points directly —
no generic hedging. Ground every claim in the reports/verdict above; do NOT invent stats, and
CITE evidence with its date/source tag exactly as given (uncited specifics = hallucination).
End with one sentence: how the provisional probabilities should move (or hold), and why."""
    msg = llm.invoke(prompt)
    meta = getattr(msg, "usage_metadata", None)
    if usage_acc is not None and meta:
        usage_acc["input"] += meta.get("input_tokens", 0)
        usage_acc["output"] += meta.get("output_tokens", 0)
    return msg.content
