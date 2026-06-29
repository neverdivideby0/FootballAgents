"""Alias-aware team reads — a nation stored under two vendor spellings ('South
Korea' AND 'Korea Republic') must resolve to ONE team: correct perspective (no
self-match, no flipped score), and notes filed under either spelling are found."""

from __future__ import annotations

from worldcupagents.dataflows.match_store import MatchStore


def _store(tmp_path) -> MatchStore:
    return MatchStore(tmp_path / "data" / "football.db")


def _seed(store):
    # The SAME nation appears under both spellings the feeds actually use.
    store.upsert([
        {"date": "2026-06-11", "comp": "WC", "home": "South Korea", "away": "Czechia", "hg": 2, "ag": 1, "source": "t"},
        {"date": "2026-03-20", "comp": "WC", "home": "Japan", "away": "Korea Republic", "hg": 0, "ag": 3, "source": "t"},
        {"date": "2026-02-01", "comp": "WC", "home": "Korea Republic", "away": "Iran", "hg": 1, "ag": 1, "source": "t"},
    ])


def test_recent_matches_resolve_across_spellings(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store)
        rec = store.recent_team_matches("South Korea", comp="WC", limit=10)
        # All three rows belong to this nation (whichever spelling), none vs itself.
        assert len(rec) == 3
        opps = {m["opponent"] for m in rec}
        assert opps == {"Czechia", "Japan", "Iran"}
        assert "Korea Republic" not in opps and "South Korea" not in opps
        # Perspective is correct: the 0-3 win at Japan reads as a WIN for Korea (away).
        japan = next(m for m in rec if m["opponent"] == "Japan")
        assert japan["result"] == "W" and (japan["gf"], japan["ga"]) == (3, 0)
    finally:
        store.close()


def test_venue_record_and_h2h_alias_aware(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store)
        # Querying by EITHER spelling gives the full record (2 home games, 1 away).
        for name in ("South Korea", "Korea Republic"):
            vr = store.venue_record(name, comp="WC")
            assert sum(vr["home"]) == 2 and sum(vr["away"]) == 1, name
        # H2H vs Iran finds the 'Korea Republic 1-1 Iran' game from the 'South Korea' side.
        h = store.h2h_vs("South Korea", "Iran", comp="WC")
        assert h["n"] == 1 and h["wdl"] == [0, 1, 0]   # one draw
    finally:
        store.close()


def test_player_notes_resolve_across_spellings(tmp_path):
    store = _store(tmp_path)
    try:
        store.upsert_player_note("Korea Republic", "Son Heung-min", "Talisman; drifts left.")
        # Looked up by the OTHER spelling — still found (canonical keying).
        got = store.player_notes_for_team("South Korea")
        assert len(got) == 1 and got[0]["player"] == "Son Heung-min"
        # And a note filed under 'South Korea' is visible from 'Korea Republic'.
        store.upsert_player_note("South Korea", "Kim Min-jae", "Aggressive line.")
        assert {n["player"] for n in store.player_notes_for_team("Korea Republic")} == {
            "Son Heung-min", "Kim Min-jae"}
    finally:
        store.close()
