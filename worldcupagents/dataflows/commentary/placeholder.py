"""Offline commentary provider — bundled realistic sample, no network/key.

Used as the graceful fallback when no commentary vendor is configured (or a real
one fails to construct), and as deterministic fixture data for tests/demos. The
sample mirrors Guardian-style minute-by-minute formatting so it exercises the
chunker realistically. Echoes the requested teams; the timeline is illustrative.
"""

from __future__ import annotations

from pathlib import Path

from worldcupagents.agents.schemas import MatchEvent
from worldcupagents.dataflows.commentary.base import RawMatchFeed

_SAMPLE = Path(__file__).parent / "samples" / "sample_match.txt"

# Typed events accompanying the bundled sample (the stats API supplies these in
# production; hand-listed here so the placeholder is self-contained).
_SAMPLE_EVENTS = [
    MatchEvent(minute=23, type="goal", detail="Messi (pen)"),
    MatchEvent(minute=36, type="goal", detail="Di Maria"),
    MatchEvent(minute=41, type="sub", detail="Kolo Muani & Thuram on"),
    MatchEvent(minute=80, type="goal", detail="Mbappe (pen)"),
    MatchEvent(minute=81, type="goal", detail="Mbappe"),
]


class PlaceholderCommentaryProvider:
    name = "placeholder"

    def fetch_match(self, home: str, away: str, date: str | None = None) -> RawMatchFeed:
        lines = [
            ln.strip()
            for ln in _SAMPLE.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        return RawMatchFeed(
            home=home,
            away=away,
            date=date,
            lines=lines,
            events=list(_SAMPLE_EVENTS),
            sources=["placeholder:bundled-sample"],
        )
