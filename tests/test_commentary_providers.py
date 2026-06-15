"""Milestone 2 tests — commentary providers (hermetic: mocked HTTP, no key)."""

from __future__ import annotations

import pytest

from worldcupagents.agents.schemas import (
    PHASE_CRUNCH,
    PHASE_FIRST_HALF,
    PHASE_HALF_TIME,
)
from worldcupagents.dataflows.commentary.base import RawMatchFeed
from worldcupagents.dataflows.commentary.chunker import chunk_commentary
from worldcupagents.dataflows.commentary.guardian import GuardianCommentaryProvider, _strip_html
from worldcupagents.dataflows.commentary.placeholder import PlaceholderCommentaryProvider
from worldcupagents.dataflows.commentary.registry import (
    clear_commentary_cache,
    get_commentary_provider,
)


# ── placeholder (offline) ────────────────────────────────────────────────────

def test_placeholder_returns_usable_feed():
    feed = PlaceholderCommentaryProvider().fetch_match("Argentina", "France", "2022-12-18")
    assert isinstance(feed, RawMatchFeed)
    assert feed.home == "Argentina" and feed.away == "France"
    assert len(feed.lines) > 10
    assert feed.events and feed.events[0].type == "goal"
    assert feed.sources == ["placeholder:bundled-sample"]


def test_placeholder_feed_chunks_into_all_five_phases():
    feed = PlaceholderCommentaryProvider().fetch_match("Argentina", "France")
    chunks = chunk_commentary(feed.lines, feed.events)
    # Every phase should have at least one entry or event (sample is designed for this).
    assert all(c.entries or c.events for c in chunks), {
        c.phase: (len(c.entries), len(c.events)) for c in chunks
    }


# ── Guardian (mocked HTTP) ───────────────────────────────────────────────────

class FakeHTTP:
    def __init__(self, mapping: dict):
        self.mapping = mapping
        self.calls: list[str] = []

    def get_json(self, url, headers=None, ttl=None):
        self.calls.append(url)
        for frag, data in self.mapping.items():
            if frag in url:
                return data
        raise KeyError(url)


_SEARCH = {
    "response": {
        "status": "ok",
        "results": [
            # decoy first — different match
            {
                "webTitle": "Brazil v Croatia: World Cup 2022 quarter-final – live",
                "webUrl": "https://g/braocro",
                "blocks": {"body": [{"firstPublishedDate": "2022-12-09T15:00:00Z",
                                     "bodyHtml": "<p>1 min: under way</p>"}]},
            },
            # the real match — newest-first block order (as Guardian serves liveblogs)
            {
                "webTitle": "Argentina v France: World Cup 2022 final – live",
                "webUrl": "https://g/argfra",
                "blocks": {"body": [
                    {"firstPublishedDate": "2022-12-18T15:36:00Z",
                     "bodyHtml": "<p>36 min: <strong>GOAL!</strong> Di Maria scores</p>"},
                    {"firstPublishedDate": "2022-12-18T15:23:00Z",
                     "bodyHtml": "<p>23 min: GOAL! Messi pen</p>"},
                    {"firstPublishedDate": "2022-12-18T15:01:00Z",
                     "bodyHtml": "<p>1 min: Under way</p>"},
                ]},
            },
        ],
    }
}


def _guardian() -> GuardianCommentaryProvider:
    return GuardianCommentaryProvider(api_key="x", http=FakeHTTP({"search": _SEARCH}))


def test_guardian_picks_matching_article_and_orders_chronologically():
    feed = _guardian().fetch_match("Argentina", "France", "2022-12-18")
    assert feed.sources == ["https://g/argfra"]            # matched both teams, not the decoy
    assert feed.lines == [                                  # reversed to oldest-first
        "1 min: Under way",
        "23 min: GOAL! Messi pen",
        "36 min: GOAL! Di Maria scores",
    ]
    assert feed.events == []                                # Guardian supplies no typed events


def test_guardian_strips_html():
    assert _strip_html("<p>23 min: <strong>GOAL!</strong> Messi</p>") == "23 min: GOAL! Messi"


def test_guardian_no_results_returns_empty_feed():
    g = GuardianCommentaryProvider(api_key="x", http=FakeHTTP({"search": {"response": {"results": []}}}))
    feed = g.fetch_match("Narnia", "Atlantis")
    assert feed.lines == [] and feed.sources == []


def test_guardian_http_error_degrades_to_empty_feed():
    class BoomHTTP:
        def get_json(self, url, headers=None, ttl=None):
            raise RuntimeError("403 rate limited")

    feed = GuardianCommentaryProvider(api_key="x", http=BoomHTTP()).fetch_match("A", "B")
    assert feed.lines == [] and feed.events == []


def test_guardian_requires_key():
    with pytest.raises(ValueError):
        GuardianCommentaryProvider(api_key="")


# --- block-aware extraction (mirrors the REAL Guardian liveblog structure) ---

_RICH = {
    "response": {"results": [{
        "webTitle": "Hosts v Visitors: the final – live",
        "webUrl": "https://g/final",
        "type": "liveblog",
        "blocks": {"body": [
            # newest-first as Guardian serves; sorted chronologically by date.
            {"firstPublishedDate": "2022-01-01T17:20:00Z",
             "bodyHtml": "<p>ET 5 min: into extra time now, both sides weary.</p>"},
            {"firstPublishedDate": "2022-01-01T16:30:00Z",
             "title": "HALF TIME: Hosts 1-0 Visitors", "bodyHtml": "<p>A dominant half.</p>"},
            {"firstPublishedDate": "2022-01-01T16:09:00Z",
             "title": "GOAL! Hosts 1-0 Visitors (Striker 23 pen)", "bodyHtml": "<p>Tucked away.</p>"},
            {"firstPublishedDate": "2022-01-01T15:46:00Z",
             "bodyHtml": "<p>1 min: Under way at the Lusail.</p>"},
            {"firstPublishedDate": "2022-01-01T15:00:00Z",
             "title": "Preamble", "bodyHtml": "<p>Welcome to the final. The teams are in.</p>"},
        ]},
    }]}
}


def _rich_feed():
    g = GuardianCommentaryProvider(api_key="x", http=FakeHTTP({"search": _RICH}))
    return g.fetch_match("Hosts", "Visitors", "2022-01-01")


def test_guardian_extracts_goal_event_from_title():
    feed = _rich_feed()
    goals = [e for e in feed.events if e.type == "goal"]
    assert len(goals) == 1 and goals[0].minute == 23


def test_guardian_drops_prematch_buildup():
    feed = _rich_feed()
    assert not any("Welcome to the final" in ln for ln in feed.lines)
    assert feed.lines[0].startswith("1 min")          # match starts at kickoff


def test_guardian_titles_and_extra_time_bucket_correctly():
    feed = _rich_feed()
    phases = {c.phase: c for c in chunk_commentary(feed.lines, feed.events)}
    assert any("GOAL" in e.text for e in phases[PHASE_FIRST_HALF].entries)   # 23' goal -> first half
    assert phases[PHASE_HALF_TIME].entries                                    # HALF TIME title -> HT brief
    assert any("extra time" in e.text.lower() for e in phases[PHASE_CRUNCH].entries)  # ET folded -> Crunch


# ── registry routing + graceful fallback ────────────────────────────────────

def test_registry_routes_to_placeholder():
    clear_commentary_cache()
    cfg = {"data_vendors": {"commentary": "placeholder"}}
    assert get_commentary_provider(cfg).name == "placeholder"


def test_registry_falls_back_when_guardian_has_no_key(monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    clear_commentary_cache()
    cfg = {"data_vendors": {"commentary": "guardian"}, "cache_dir": ".cache"}
    # guardian factory raises (no key) -> registry degrades to placeholder, no crash.
    assert get_commentary_provider(cfg).name == "placeholder"
    clear_commentary_cache()


def test_registry_unknown_vendor_uses_placeholder():
    clear_commentary_cache()
    assert get_commentary_provider({"data_vendors": {"commentary": "nope"}}).name == "placeholder"
