"""Guardian Experts' Network ingester + the coach layer (hermetic — injected fetch).

Covers: section splitting, warehouse + coach-note ingestion, the coach_brief/digest
merge (name from the data vendor, prose from the guide), and the coach line flowing
into the form report (→ debate + judge).
"""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Player, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.coach import coach_brief, coach_digest
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines.guardian_experts import (
    _team_from_title, fetch_index_urls, ingest_guardian_experts, split_sections,
    title_and_body)


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


_BODY = (
    "This article is part of the Guardian's 2026 World Cup Experts' Network, a "
    "cooperation between media organisations. theguardian.com is running previews. "
    "The plan England cruised through qualifying with eight wins from eight. "
    "The coach Thomas Tuchel is one of the best managers in the world, a Champions "
    "League winner with Chelsea who is pragmatic under pressure. "
    "Star player Jude Bellingham drives the team forward. "
    "Unsung hero Declan Rice does the dirty work in midfield. "
    "One to watch Cole Palmer can change a game off the bench."
)
_ENGLAND_URL = "https://www.theguardian.com/football/2026/jun/11/england-world-cup-2026-team-guide"
# Article HTML: <title> headline + the body as <h2> labels and <p> paragraphs.
_ARTICLE_HTML = (
    '<html><head><meta property="og:title" content="England World Cup 2026 team '
    'guide | World Cup 2026 | The Guardian"></head><body><article>'
    "<p>This article is part of the Guardian's 2026 World Cup Experts' Network.</p>"
    "<h2>The plan</h2><p>England cruised through qualifying with eight wins from eight.</p>"
    "<h2>The coach</h2><p>Thomas Tuchel is one of the best managers in the world, a "
    "Champions League winner with Chelsea who is pragmatic under pressure.</p>"
    "<h2>Star player</h2><p>Jude Bellingham drives the team forward.</p>"
    "<h2>Unsung hero</h2><p>Declan Rice does the dirty work in midfield.</p>"
    "<h2>One to watch</h2><p>Cole Palmer can change a game off the bench.</p>"
    "</article></body></html>"
)
_INDEX_HTML = (
    '<html><body><a href="/football/2026/jun/11/england-world-cup-2026-team-guide">England</a>'
    '</body></html>'
)


def _fake_fetch(url: str) -> str:
    if "guardian-experts-network" in url:          # the series index page
        return _INDEX_HTML if "page=" not in url else "<html></html>"  # page 2 = empty → stop
    return _ARTICLE_HTML                            # any article page


def test_team_from_title():
    assert _team_from_title("England World Cup 2026 team guide") == "England"
    assert _team_from_title("USA World Cup 2026 team guide") == "United States"
    # tolerates the trailing site furniture in a real <title>
    assert _team_from_title("Spain World Cup 2026 team guide | The Guardian") == "Spain"


def test_fetch_index_urls_dedupes_and_stops():
    urls = fetch_index_urls(fetch_text=_fake_fetch)
    assert urls == [_ENGLAND_URL]


def test_title_and_body_from_article_html():
    title, body = title_and_body(_ARTICLE_HTML)
    assert _team_from_title(title) == "England"
    assert "The coach Thomas Tuchel" in body and "Star player Jude Bellingham" in body


def test_split_sections_drops_boilerplate_and_labels():
    secs = split_sections(_BODY)
    headers = [s["header"] for s in secs]
    assert headers == ["The plan", "The coach", "Star player", "Unsung hero", "One to watch"]
    # boilerplate before "The plan" is gone
    assert not any("part of the Guardian" in s["text"] for s in secs)
    coach = next(s for s in secs if s["header"] == "The coach")
    assert "Thomas Tuchel" in coach["text"] and "Star player" not in coach["text"]


def test_ingest_populates_warehouse_and_coach_note(tmp_path):
    cfg = _cfg(tmp_path)
    res = ingest_guardian_experts(cfg, fetch_text=_fake_fetch)
    assert res.teams == 1 and res.coaches == 1 and res.errors == 0

    store = MatchStore.from_config(cfg)
    try:
        row = store.team_coach("England")
        docs = store.conn.execute(
            "SELECT title FROM wh_qual_documents WHERE title LIKE 'Guardian Experts%'").fetchall()
    finally:
        store.close()
    assert row and "Thomas Tuchel" in row["note"] and "Champions League" in row["note"]
    assert row["source"] == "The Guardian Experts' Network"
    assert docs, "article should land in the qualitative warehouse"


def test_coach_brief_merges_vendor_name_and_guide_note(tmp_path):
    cfg = _cfg(tmp_path)
    ingest_guardian_experts(cfg, fetch_text=_fake_fetch)
    # The data vendor supplies the NAME via the profile; the guide supplies the prose.
    profile = TeamProfile(team="England", coach="Thomas Tuchel",
                          squad=[Player(name="Jude Bellingham")])
    brief = coach_brief(cfg, "England", profile)
    assert brief and brief["name"] == "Thomas Tuchel"
    digest = coach_digest(brief)
    assert digest.startswith("Thomas Tuchel — ") and "pragmatic" in digest


def test_coach_brief_none_when_unknown(tmp_path):
    cfg = _cfg(tmp_path)
    MatchStore.from_config(cfg).close()  # empty store
    assert coach_brief(cfg, "Nowhere", None) is None
    assert coach_digest(None) == ""


def test_coach_line_in_form_report(tmp_path):
    from worldcupagents.agents.analyst.reports import _coach_line
    cfg = _cfg(tmp_path)
    ingest_guardian_experts(cfg, fetch_text=_fake_fetch)
    profile = TeamProfile(team="England", coach="Thomas Tuchel")
    line = _coach_line(cfg, profile)
    assert "Coach — England:" in line and "Thomas Tuchel" in line
    assert "Guardian Experts' Network" in line  # provenance cited
