"""Offline placeholder provider so the app runs with zero network/keys.

Empty squad/form; FIFA rank from the shared curated table so the ensemble baseline
still has a strength prior. Active by default until a real vendor (M1:
football-data.org) is configured. See PROJECT_OUTLINE §9.
"""

from __future__ import annotations

from datetime import datetime, timezone

from worldcupagents.agents.schemas import MatchResult, TeamProfile
from worldcupagents.dataflows import fifa_rankings
from worldcupagents.dataflows.names import canonical_name


class PlaceholderProvider:
    name = "placeholder"

    def get_team_profile(self, team: str) -> TeamProfile:
        return TeamProfile(
            team=canonical_name(team),
            fifa_rank=fifa_rankings.get_rank(team),
            style="(placeholder — configure a real data vendor)",
            tournament_pedigree="(placeholder)",
            sources=["placeholder"],
            last_updated=datetime.now(timezone.utc),
        )

    def get_recent_results(self, team: str, n: int = 5) -> list[MatchResult]:
        return []

    def get_head_to_head(self, home: str, away: str) -> list[MatchResult]:
        return []
