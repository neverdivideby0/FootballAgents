"""Commentary provider contract (COMMENTARY_PLAN.md step A).

Commentary doesn't fit the ``FootballDataProvider`` protocol (team profiles /
results), so it gets its own small protocol. A provider turns a match identifier
into a ``RawMatchFeed``: the prose lines (for chunking + tactical analysis) plus
any typed events, with mandatory source provenance.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from worldcupagents.agents.schemas import MatchEvent


class RawMatchFeed(BaseModel):
    """Raw, unchunked output of the ingest step — input to the chunker."""

    home: str
    away: str
    date: Optional[str] = None
    lines: list[str] = Field(default_factory=list)      # prose commentary, chronological
    events: list[MatchEvent] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)    # provenance is mandatory


@runtime_checkable
class CommentaryProvider(Protocol):
    name: str

    def fetch_match(self, home: str, away: str, date: str | None = None) -> RawMatchFeed:
        ...
