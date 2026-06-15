"""The vendor contract. Any data source (free API, paid API, web search) that
implements this Protocol can be registered and swapped in via config — no core
changes. This is the extensibility requirement, modeled on TradingAgents' vendors.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from worldcupagents.agents.schemas import MatchResult, TeamProfile


@runtime_checkable
class FootballDataProvider(Protocol):
    name: str

    def get_team_profile(self, team: str) -> TeamProfile: ...

    def get_recent_results(self, team: str, n: int = 5) -> list[MatchResult]: ...

    def get_head_to_head(self, home: str, away: str) -> list[MatchResult]: ...
