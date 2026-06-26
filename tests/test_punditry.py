"""Structured punditry extraction + recall (hermetic: injected fetch/LLM, no network)."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from worldcupagents.agents.schemas import PunditryDigest, TeamPunditryRead
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.commentary.guardian import GuardianCommentaryProvider
from worldcupagents.pipelines.punditry import analyze_punditry
from worldcupagents.recall import past_context_for, punditry_brief


def _cfg(tmp_path, use_llm=False) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["use_llm"] = use_llm
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["data_dir"] = str(tmp_path / "data")
    return cfg


_ARTICLES = [
    {"title": "England 2-1 Spain: report", "url": "https://x/report", "body": "England pressed high."},
    {"title": "How England won: tactical", "url": "https://x/tactics", "body": "A back three did it."},
]


def _fetch_two(home, away, date):
    return list(_ARTICLES)


# ── pipeline ────────────────────────────────────────────────────────────────

def test_offline_placeholder_digest(tmp_path):
    out = analyze_punditry("England", "Spain", "2026-06-20", _cfg(tmp_path), fetch_articles=_fetch_two)
    assert out.n_articles == 2
    assert out.model is None and out.cost is None        # offline → no spend
    # placeholder digest persisted; raw articles snapshotted to data/punditry/.
    assert out.json_path.exists()
    raw = tmp_path / "data" / "punditry" / "England_vs_Spain_2026-06-20"
    assert (raw / "00.json").exists() and (raw / "01.json").exists()


class _FakeStructured:
    def invoke(self, prompt):
        raw = SimpleNamespace(usage_metadata={"input_tokens": 800, "output_tokens": 120})
        parsed = PunditryDigest(
            match_id="x", home="?", away="?",
            home_read=TeamPunditryRead(team="?", tactical_shape=["4-3-3 high press"],
                                       standout_players=["Bellingham ran midfield"]),
            away_read=TeamPunditryRead(team="?", fatigue_injuries=["legs gone after 70"]),
            sources=[],
        )
        return {"raw": raw, "parsed": parsed, "parsing_error": None}


class _FakeLLM:
    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured()


def test_injected_llm_extracts_structured_digest(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    cfg["llm_provider"], cfg["quick_think_llm"] = "openai", "gpt-5-nano"
    out = analyze_punditry("England", "Spain", "2026-06-20", cfg,
                           llm=_FakeLLM(), fetch_articles=_fetch_two)
    d = out.digest
    # Authoritative identity/provenance set by us, not the model.
    assert d.match_id == "England_vs_Spain_2026-06-20"
    assert d.home_read.team == "England" and d.away_read.team == "Spain"
    assert d.home_read.tactical_shape == ["4-3-3 high press"]
    assert d.sources == ["https://x/report", "https://x/tactics"]
    assert out.usage["input"] > 0 and out.cost is not None


def test_existing_digest_not_clobbered(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    cfg["llm_provider"], cfg["quick_think_llm"] = "openai", "gpt-5-nano"
    analyze_punditry("England", "Spain", "2026-06-20", cfg, llm=_FakeLLM(), fetch_articles=_fetch_two)
    # A second offline run must NOT overwrite the populated digest.
    again = analyze_punditry("England", "Spain", "2026-06-20", _cfg(tmp_path), fetch_articles=_fetch_two)
    assert again.digest.home_read.tactical_shape == ["4-3-3 high press"]


# ── recall → debate ─────────────────────────────────────────────────────────

def test_punditry_brief_and_past_context(tmp_path):
    cfg = _cfg(tmp_path, use_llm=True)
    cfg["llm_provider"], cfg["quick_think_llm"] = "openai", "gpt-5-nano"
    analyze_punditry("England", "Spain", "2026-06-20", cfg, llm=_FakeLLM(), fetch_articles=_fetch_two)

    brief = punditry_brief("England", "France", cfg)
    assert "PUNDITRY SIGNALS" in brief and "Bellingham" in brief
    # Flows into the shared past_context the debate reads.
    assert "PUNDITRY SIGNALS" in past_context_for("England", "France", cfg)


def test_punditry_brief_empty_without_history(tmp_path):
    assert punditry_brief("Brazil", "Argentina", _cfg(tmp_path)) == ""


# ── multi-article fetch + name-variant matching ─────────────────────────────

def _provider_with_response(payload: dict) -> GuardianCommentaryProvider:
    p = GuardianCommentaryProvider(api_key="test", cache_dir="/tmp/guardian-test")
    p.http = SimpleNamespace(get_json=lambda url, ttl=None: payload)
    return p


def test_fetch_articles_returns_both_team_articles_not_liveblog():
    payload = {"response": {"results": [
        {"webTitle": "England 2-1 Spain: minute by minute", "type": "liveblog",
         "webUrl": "u/live", "fields": {"bodyText": "23 min ..."}},
        {"webTitle": "England beat Spain: match report", "type": "article",
         "webUrl": "u/report", "fields": {"bodyText": "England were excellent."}},
        {"webTitle": "Spain vs England tactical analysis", "type": "article",
         "webUrl": "u/tactics", "fields": {"bodyText": "The back three ..."}},
        {"webTitle": "Spain crowd trouble unrelated", "type": "article",
         "webUrl": "u/other", "fields": {"bodyText": "Off-pitch only."}},
    ]}}
    arts = _provider_with_response(payload).fetch_articles("England", "Spain", "2026-06-20")
    urls = [a["url"] for a in arts]
    assert urls == ["u/report", "u/tactics"]   # liveblog skipped, unrelated dropped


def test_fetch_articles_handles_name_variants():
    payload = {"response": {"results": [
        {"webTitle": "United States hold South Korea to a draw", "type": "article",
         "webUrl": "u/usa-kor", "fields": {"bodyText": "A tense night."}},
    ]}}
    arts = _provider_with_response(payload).fetch_articles("USA", "Korea Republic", "2026-06-20")
    assert [a["url"] for a in arts] == ["u/usa-kor"]


def test_fetch_match_uses_name_variants_for_liveblog():
    payload = {"response": {"results": [
        {"webTitle": "Uruguay 2-2 Cape Verde: World Cup 2026 – as it happened",
         "type": "liveblog", "webUrl": "u/uru-cpv",
         "blocks": {"body": [{"bodyTextSummary": "1 min: Uruguay kick off."}]}},
    ]}}
    feed = _provider_with_response(payload).fetch_match("Uruguay", "Cape Verde Islands", "2026-06-21")
    assert feed.sources == ["u/uru-cpv"]
    assert feed.lines == ["1 min: Uruguay kick off."]


def test_fetch_match_refuses_unrelated_liveblog():
    payload = {"response": {"results": [
        {"webTitle": "Belgium 0-0 Iran: World Cup 2026 – as it happened",
         "type": "liveblog", "webUrl": "u/bel-iran",
         "blocks": {"body": [{"bodyTextSummary": "1 min: Belgium kick off."}]}},
    ]}}
    feed = _provider_with_response(payload).fetch_match("Uruguay", "Cape Verde Islands", "2026-06-21")
    assert feed.sources == []
    assert feed.lines == []


def test_search_url_uses_a_date_window_not_a_single_day():
    # Liveblogs/reports are stamped night-of or the next morning, and store dates drift,
    # so the Guardian search must BRACKET the fixture date, not pin a single day.
    from worldcupagents.dataflows.commentary.guardian import _date_window
    assert _date_window("2026-06-25") == ("2026-06-23", "2026-06-28")  # [-2, +3]
    assert _date_window(None) is None and _date_window("not-a-date") is None
    url = _provider_with_response({"response": {"results": []}})._search_url("Japan", "Sweden", "2026-06-25")
    assert "from-date=2026-06-23" in url and "to-date=2026-06-28" in url
