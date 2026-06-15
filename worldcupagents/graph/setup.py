"""Build the LangGraph StateGraph. Topology mirrors TradingAgents' full pipeline
(analyst team → researcher debate → manager → risk debate → portfolio manager):

    START -> Build Dossiers -> Matchup Context
          -> Form Analyst -> Tactical Analyst -> Player Analyst   (enable_analyst_reports)
          -> Home Advocate <-(should_continue_debate)-> Away Advocate
          -> Judge (provisional verdict)
          -> Upside <-> Downside <-> Neutral Pundit               (enable_scenario_debate)
          -> Final Pundit -> END

With both layers disabled this reduces to the original
    ... -> Home Advocate <-> Away Advocate -> Judge -> END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from worldcupagents.agents.advocates.advocate import make_advocate
from worldcupagents.agents.analyst.reports import (
    make_form_analyst,
    make_player_analyst,
    make_tactical_analyst,
)
from worldcupagents.agents.judge.pundit import make_judge
from worldcupagents.agents.scouts.dossier import make_build_dossiers, make_matchup_context
from worldcupagents.graph.conditional_logic import ConditionalLogic
from worldcupagents.graph.state import MatchState


def build_graph(config: dict, deep_llm=None, quick_llm=None, usage_acc: dict | None = None):
    """usage_acc: optional mutable {"input": int, "output": int} for token tracking."""
    logic = ConditionalLogic(
        max_debate_rounds=config.get("max_debate_rounds", 2),
        max_scenario_rounds=config.get("max_scenario_rounds", 1),
    )

    wf = StateGraph(MatchState)
    wf.add_node("Build Dossiers", make_build_dossiers(config))
    wf.add_node("Matchup Context", make_matchup_context(config))
    wf.add_node("Home Advocate", make_advocate("home", config, quick_llm, usage_acc))
    wf.add_node("Away Advocate", make_advocate("away", config, quick_llm, usage_acc))
    wf.add_node("Judge", make_judge(config, deep_llm, usage_acc))

    wf.add_edge(START, "Build Dossiers")
    wf.add_edge("Build Dossiers", "Matchup Context")

    # Analyst report stage (TA's analyst team) — deterministic digests by default.
    if config.get("enable_analyst_reports", True):
        wf.add_node("Form Analyst", make_form_analyst(config, quick_llm, usage_acc))
        wf.add_node("Tactical Analyst", make_tactical_analyst(config, quick_llm, usage_acc))
        wf.add_node("Player Analyst", make_player_analyst(config, quick_llm, usage_acc))
        wf.add_edge("Matchup Context", "Form Analyst")
        wf.add_edge("Form Analyst", "Tactical Analyst")
        wf.add_edge("Tactical Analyst", "Player Analyst")
        wf.add_edge("Player Analyst", "Home Advocate")
    else:
        wf.add_edge("Matchup Context", "Home Advocate")

    wf.add_conditional_edges(
        "Home Advocate", logic.should_continue_debate,
        {"Away Advocate": "Away Advocate", "Judge": "Judge"},
    )
    wf.add_conditional_edges(
        "Away Advocate", logic.should_continue_debate,
        {"Home Advocate": "Home Advocate", "Judge": "Judge"},
    )

    # Scenario (risk) debate stage (TA's risk team + portfolio manager).
    if config.get("enable_scenario_debate", False):
        from worldcupagents.agents.scenario.final_pundit import make_final_pundit
        from worldcupagents.agents.scenario.pundits import make_scenario_pundit

        wf.add_node("Upside Pundit", make_scenario_pundit("upside", config, quick_llm, usage_acc))
        wf.add_node("Downside Pundit", make_scenario_pundit("downside", config, quick_llm, usage_acc))
        wf.add_node("Neutral Pundit", make_scenario_pundit("neutral", config, quick_llm, usage_acc))
        wf.add_node("Final Pundit", make_final_pundit(config, deep_llm, usage_acc))

        wf.add_edge("Judge", "Upside Pundit")
        wf.add_conditional_edges(
            "Upside Pundit", logic.should_continue_scenario,
            {"Downside Pundit": "Downside Pundit", "Final Pundit": "Final Pundit"},
        )
        wf.add_conditional_edges(
            "Downside Pundit", logic.should_continue_scenario,
            {"Neutral Pundit": "Neutral Pundit", "Final Pundit": "Final Pundit"},
        )
        wf.add_conditional_edges(
            "Neutral Pundit", logic.should_continue_scenario,
            {"Upside Pundit": "Upside Pundit", "Final Pundit": "Final Pundit"},
        )
        wf.add_edge("Final Pundit", END)
    else:
        wf.add_edge("Judge", END)

    return wf.compile()
