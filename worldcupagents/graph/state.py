"""LangGraph shared state — analog of TradingAgents' AgentState / InvestDebateState
/ RiskDebateState."""

from __future__ import annotations

from typing import Any, TypedDict

from worldcupagents.agents.schemas import Fixture, MatchVerdict, TeamProfile


class DebateState(TypedDict, total=False):
    history: str          # full transcript
    home_history: str     # home advocate's turns only
    away_history: str     # away advocate's turns only
    current_response: str # last turn (drives the conditional edge)
    count: int            # turn counter (cap = 2 * max_debate_rounds)


class ScenarioDebateState(TypedDict, total=False):
    """The risk-team debate over the judge's provisional verdict — direct analog
    of TradingAgents' RiskDebateState (Aggressive/Conservative/Neutral)."""

    history: str                    # full transcript
    upside_history: str             # variance/upset pundit's turns
    downside_history: str           # chalk/class pundit's turns
    neutral_history: str            # arbiter pundit's turns
    latest_speaker: str             # "Upside" | "Downside" | "Neutral" (drives rotation)
    current_upside_response: str
    current_downside_response: str
    current_neutral_response: str
    count: int                      # turn counter (cap = 3 * max_scenario_rounds)


class MatchState(TypedDict, total=False):
    fixture: Fixture
    home_profile: TeamProfile
    away_profile: TeamProfile
    matchup_context: dict[str, Any]
    # Analyst report stage (TA: market/news/fundamentals reports)
    form_report: str
    tactical_report: str
    player_report: str
    # Advocate debate (TA: bull/bear)
    debate_state: DebateState
    # Judge's read before the scenario debate (TA: Research Manager's plan)
    provisional_verdict: MatchVerdict
    # Scenario/risk debate (TA: risk team)
    scenario_debate_state: ScenarioDebateState
    # The final, authoritative verdict (judge's when scenario layer is off;
    # Final Pundit's when it is on)
    verdict: MatchVerdict
    past_context: str     # tactical history + lessons injected from memory at run start
