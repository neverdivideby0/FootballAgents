"""Debate round-cap logic — lifted from TradingAgents' should_continue_debate /
should_continue_risk_analysis."""

from __future__ import annotations

from worldcupagents.graph.state import MatchState


class ConditionalLogic:
    def __init__(self, max_debate_rounds: int = 2, max_scenario_rounds: int = 1):
        self.max_debate_rounds = max_debate_rounds
        self.max_scenario_rounds = max_scenario_rounds

    def should_continue_debate(self, state: MatchState) -> str:
        debate = state["debate_state"]
        # 2 turns per round (one each side); stop and hand to the judge at the cap.
        if debate.get("count", 0) >= 2 * self.max_debate_rounds:
            return "Judge"
        if debate.get("current_response", "").startswith("Home"):
            return "Away Advocate"
        return "Home Advocate"

    def should_continue_scenario(self, state: MatchState) -> str:
        """3-way rotation Upside → Downside → Neutral, capped — TA's risk-team logic."""
        sd = state.get("scenario_debate_state") or {}
        if sd.get("count", 0) >= 3 * self.max_scenario_rounds:
            return "Final Pundit"
        speaker = sd.get("latest_speaker", "")
        if speaker.startswith("Upside"):
            return "Downside Pundit"
        if speaker.startswith("Downside"):
            return "Neutral Pundit"
        return "Upside Pundit"
