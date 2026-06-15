"""Milestone 1 tests — the deterministic 5-phase chunker (no network, no LLM)."""

from __future__ import annotations

import pytest

from worldcupagents.agents.schemas import (
    PHASE_ADJUSTMENTS,
    PHASE_CRUNCH,
    PHASE_FIRST_HALF,
    PHASE_HALF_TIME,
    PHASE_INITIAL,
    PHASE_LABELS,
    MatchEvent,
)
from worldcupagents.dataflows.commentary.chunker import (
    chunk_commentary,
    parse_minute,
    phase_for_minute,
    phase_for_token,
)


# ── parse_minute ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text, kind, base, added",
    [
        ("63 min: a chance", "play", 63, 0),
        ("63' great save", "play", 63, 0),
        ("45+2 min before the break", "play", 45, 2),
        ("90 + 4' deep into stoppage", "play", 90, 4),
        ("1 min: kick-off", "play", 1, 0),
    ],
)
def test_parse_minute_play(text, kind, base, added):
    tok = parse_minute(text)
    assert tok is not None
    assert (tok.kind, tok.base, tok.added) == (kind, base, added)


def test_parse_minute_breaks():
    assert parse_minute("HT: 1-0 at the interval").kind == "HT"
    assert parse_minute("That's half-time.").kind == "HT"
    assert parse_minute("FT! It finishes 2-1").kind == "FT"
    assert parse_minute("Peep peep — that's full-time").kind == "FT"


def test_parse_minute_break_beats_bare_number():
    # "HT: 1-0" must be the break, not minute 1.
    assert parse_minute("HT: 1-0").kind == "HT"


def test_parse_minute_none():
    assert parse_minute("Both sides line up for the anthems.") is None
    assert parse_minute("A tense affair so far.") is None


def test_minute_token_sort_key_orders_stoppage():
    a = parse_minute("45 min")
    b = parse_minute("45+2 min")
    c = parse_minute("46 min")
    assert a.sort_key < b.sort_key < c.sort_key


# ── phase mapping ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "minute, expected",
    [
        (0, PHASE_INITIAL),
        (14, PHASE_INITIAL),
        (15, PHASE_FIRST_HALF),
        (44, PHASE_FIRST_HALF),
        (45, PHASE_ADJUSTMENTS),   # plain 45 sits in the inclusive 45-75 band
        (60, PHASE_ADJUSTMENTS),
        (74, PHASE_ADJUSTMENTS),
        (75, PHASE_CRUNCH),
        (90, PHASE_CRUNCH),
    ],
)
def test_phase_for_minute(minute, expected):
    assert phase_for_minute(minute) == expected


def test_first_half_stoppage_stays_in_first_half():
    assert phase_for_token(parse_minute("45+2 min")) == PHASE_FIRST_HALF


def test_second_half_stoppage_is_crunch():
    assert phase_for_token(parse_minute("90+4 min")) == PHASE_CRUNCH


def test_break_phases():
    assert phase_for_token(parse_minute("HT")) == PHASE_HALF_TIME
    assert phase_for_token(parse_minute("FT")) == PHASE_CRUNCH


# ── chunk_commentary ─────────────────────────────────────────────────────────

def test_chunk_returns_five_phases_in_order():
    chunks = chunk_commentary([], [])
    assert [c.phase for c in chunks] == PHASE_LABELS
    assert all(c.entries == [] and c.events == [] for c in chunks)


def test_chunk_buckets_lines_by_minute():
    lines = [
        "Both teams in a 4-3-3.",          # no minute -> inherits Initial
        "8 min: early high press.",         # Initial
        "30 min: midfield battle.",         # First-Half Shift
        "HT: still goalless.",              # Half-Time Brief
        "62 min: winger shifts inside.",    # Tactical Adjustments
        "88 min: late siege.",              # Crunch
        "90+3 min: winner!",                # Crunch (stoppage)
    ]
    by_phase = {c.phase: c for c in chunk_commentary(lines, [])}

    initial = [e.text for e in by_phase[PHASE_INITIAL].entries]
    assert "Both teams in a 4-3-3." in initial   # unattributed -> default phase
    assert "8 min: early high press." in initial

    assert len(by_phase[PHASE_FIRST_HALF].entries) == 1
    assert len(by_phase[PHASE_HALF_TIME].entries) == 1
    assert len(by_phase[PHASE_ADJUSTMENTS].entries) == 1
    assert len(by_phase[PHASE_CRUNCH].entries) == 2  # 88' and 90+3'


def test_unattributed_lines_inherit_current_phase():
    lines = [
        "62 min: the press intensifies.",   # -> Adjustments
        "Messi drops deep to receive.",      # no minute -> inherits Adjustments
    ]
    by_phase = {c.phase: c for c in chunk_commentary(lines, [])}
    texts = [e.text for e in by_phase[PHASE_ADJUSTMENTS].entries]
    assert "Messi drops deep to receive." in texts
    # nothing leaked into Initial
    assert by_phase[PHASE_INITIAL].entries == []


def test_events_bucketed_and_sorted():
    events = [
        MatchEvent(minute=90, type="goal", detail="late winner"),
        MatchEvent(minute=10, type="card", detail="early yellow"),
        MatchEvent(minute=23, type="goal", detail="opener"),
    ]
    by_phase = {c.phase: c for c in chunk_commentary([], events)}
    assert [e.minute for e in by_phase[PHASE_INITIAL].entries] == []
    assert by_phase[PHASE_INITIAL].events[0].minute == 10
    assert by_phase[PHASE_FIRST_HALF].events[0].minute == 23
    assert by_phase[PHASE_CRUNCH].events[0].minute == 90


def test_blank_lines_skipped():
    chunks = chunk_commentary(["   ", "", "5 min: under way"], [])
    total = sum(len(c.entries) for c in chunks)
    assert total == 1
