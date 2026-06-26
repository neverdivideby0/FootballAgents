"""Deterministic 5-phase match-timeline chunker (COMMENTARY_PLAN.md step B).

Pure functions only — no network, no LLM. Takes raw text-commentary lines plus
typed events and groups them into the five logical phases defined by
``schemas.PHASE_LABELS``.

Minute handling is the tricky part and is unit-tested hard:
  * "63 min", "63'"            → base 63
  * "45+2 min", "90+4'"        → base + added (stoppage time)
  * "HT" / "Half-time"         → the Half-Time Brief phase
  * "FT" / "Full-time"         → folded into Crunch Time
First-half stoppage (45+x) stays in the First-Half Shift; second-half stoppage
(90+x) stays in Crunch Time. Lines with no parseable minute inherit the phase of
the preceding line (default: Initial Setup).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from worldcupagents.agents.schemas import (
    PHASE_ADJUSTMENTS,
    PHASE_CRUNCH,
    PHASE_FIRST_HALF,
    PHASE_HALF_TIME,
    PHASE_INITIAL,
    PHASE_LABELS,
    CommentaryEntry,
    MatchEvent,
    PhaseChunk,
)

# "45+2 mins", "90 + 4'", "63 min", "63'" — minute with optional added time.
# "mins" (plural) is the Guardian's house style for in-match updates, so accept both.
_MINUTE_RE = re.compile(
    r"\b(\d{1,3})\s*(?:\+\s*(\d{1,2}))?\s*(?:mins?\b|’|'|′)",
    re.IGNORECASE,
)
# Break markers. Order matters: check full/half time words explicitly.
_HALF_TIME_RE = re.compile(r"\b(?:HT|half[\s-]?time)\b", re.IGNORECASE)
_FULL_TIME_RE = re.compile(r"\b(?:FT|full[\s-]?time)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MinuteToken:
    """Parsed minute info from a commentary line."""

    kind: str          # "play" | "HT" | "FT"
    base: int = 0      # base minute (0 for breaks)
    added: int = 0     # stoppage-time minutes (0 if none)

    @property
    def sort_key(self) -> float:
        """Orders 45+2 after 45 but before 46; breaks sort by base only."""
        return self.base + self.added / 100.0


def parse_minute(text: str) -> MinuteToken | None:
    """Extract a MinuteToken from a commentary line, or None if absent.

    Half/full-time markers take precedence over a bare number so a line like
    "HT: 1-0" is treated as the break, not minute 1.
    """
    if _HALF_TIME_RE.search(text):
        return MinuteToken(kind="HT")
    if _FULL_TIME_RE.search(text):
        return MinuteToken(kind="FT")
    m = _MINUTE_RE.search(text)
    if not m:
        return None
    base = int(m.group(1))
    added = int(m.group(2)) if m.group(2) else 0
    return MinuteToken(kind="play", base=base, added=added)


def phase_for_token(token: MinuteToken) -> str:
    """Map a MinuteToken to one of the five phase labels."""
    if token.kind == "HT":
        return PHASE_HALF_TIME
    if token.kind == "FT":
        return PHASE_CRUNCH
    # First-half stoppage (45+x) belongs with the first half, not the 2nd-half band.
    if token.base == 45 and token.added > 0:
        return PHASE_FIRST_HALF
    base = token.base
    if base < 15:
        return PHASE_INITIAL
    if base < 45:
        return PHASE_FIRST_HALF
    if base < 75:
        return PHASE_ADJUSTMENTS
    return PHASE_CRUNCH


def phase_for_minute(minute: int) -> str:
    """Phase for a plain integer minute (used for typed events)."""
    return phase_for_token(MinuteToken(kind="play", base=minute))


def chunk_commentary(
    lines: list[str],
    events: list[MatchEvent] | None = None,
) -> list[PhaseChunk]:
    """Group commentary lines + typed events into exactly five PhaseChunks.

    Returns one chunk per label in PHASE_LABELS order (some may be empty).
    Lines are kept in document order within a phase; events are sorted by minute.
    """
    buckets: dict[str, PhaseChunk] = {
        label: PhaseChunk(phase=label) for label in PHASE_LABELS
    }

    current_phase = PHASE_INITIAL
    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        token = parse_minute(text)
        if token is not None:
            current_phase = phase_for_token(token)
            minute = token.base if token.kind == "play" else None
        else:
            minute = None  # inherit current_phase
        buckets[current_phase].entries.append(CommentaryEntry(minute=minute, text=text))

    for ev in sorted(events or [], key=lambda e: e.minute):
        buckets[phase_for_minute(ev.minute)].events.append(ev)

    return [buckets[label] for label in PHASE_LABELS]
