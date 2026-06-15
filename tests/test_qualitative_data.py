"""Qualitative document ingestion tests."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.commentary.base import RawMatchFeed
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines.qualitative_data import (
    delete_qual_document,
    ingest_guardian_match,
    ingest_manual_note,
    ingest_public_article,
    ingest_rss_feed,
)


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    return cfg


ARTICLE_HTML = """
<html><head>
  <title>United States tactical questions before Mexico</title>
  <meta name="author" content="Example Writer">
  <meta property="article:published_time" content="2026-06-01T12:00:00Z">
</head><body>
  <nav>Subscribe to the newsletter</nav>
  <h1>United States tactical questions before Mexico</h1>
  <p>The United States pressed high in a 4-3-3 shape, but the midfield line left
  space for Mexico to create chances in transition.</p>
  <p>Mexico looked more comfortable after the substitutions and controlled the
  tempo for long spells.</p>
</body></html>
"""


def test_ingest_public_article_segments_claims_and_links(tmp_path):
    cfg = _cfg(tmp_path)
    res = ingest_public_article(
        "https://example.com/football/us-mexico",
        config=cfg,
        teams=["United States", "Mexico"],
        html_text=ARTICLE_HTML,
    )

    assert res.counts["documents"] == 1
    assert res.counts["segments"] >= 2
    assert res.counts["claims"] >= 2
    assert res.counts["links"] >= 2
    assert "qualitative" in res.raw_path

    store = MatchStore.from_config(cfg)
    try:
        summary = store.qualitative_summary()
        docs = store.latest_qual_documents()
        assert summary["documents"] == 1
        assert summary["segments"] >= 2
        assert any(r["claim_type"] == "tactical" for r in summary["claim_types"])
        assert docs[0]["title"] == "United States tactical questions before Mexico"
    finally:
        store.close()


def test_ingest_guardian_feed_without_network(tmp_path):
    cfg = _cfg(tmp_path)
    feed = RawMatchFeed(
        home="Argentina",
        away="France",
        date="2022-12-18",
        lines=[
            "10 min: Argentina press France into a mistake and control the tempo.",
            "36 min: France struggle with Argentina's shape between the lines.",
        ],
        sources=["https://www.theguardian.com/football/live/example"],
    )
    res = ingest_guardian_match("Argentina", "France", date="2022-12-18", config=cfg, feed=feed)

    store = MatchStore.from_config(cfg)
    try:
        summary = store.qualitative_summary()
        assert res.source_id == "guardian"
        assert summary["documents"] == 1
        assert summary["segments"] == 2
        assert summary["links"] >= 2
    finally:
        store.close()


def test_ingest_rss_feed_articles_without_network(tmp_path):
    cfg = _cfg(tmp_path)
    feed = """
    <rss><channel>
      <item>
        <title>Henry analyses France pressing</title>
        <link>https://example.com/france-analysis</link>
        <pubDate>Fri, 12 Jun 2026 10:00:00 GMT</pubDate>
        <description>Thierry Henry on France shape and transition threat.</description>
      </item>
    </channel></rss>
    """
    article = """
    <html><head><title>Henry analyses France pressing</title></head><body>
      <p>Thierry Henry said France's pressing shape created transition chances,
      but the midfield block still left space behind the line.</p>
    </body></html>
    """

    res = ingest_rss_feed(
        "https://example.com/rss.xml",
        config=cfg,
        teams=["France"],
        limit=1,
        feed_xml=feed,
        article_html_by_url={"https://example.com/france-analysis": article},
    )

    store = MatchStore.from_config(cfg)
    try:
        summary = store.qualitative_summary()
        assert res.counts["articles_ingested"] == 1
        assert summary["documents"] == 1
        assert summary["segments"] == 1
        assert any(r["claim_type"] == "tactical" for r in summary["claim_types"])
    finally:
        store.close()


def test_ingest_rss_feed_include_filter(tmp_path):
    cfg = _cfg(tmp_path)
    feed = """
    <rss><channel>
      <item><title>Cricket notebook</title><link>https://example.com/cricket</link></item>
      <item><title>World Cup tactical analysis</title><link>https://example.com/wc</link></item>
    </channel></rss>
    """
    article = """
    <html><head><title>World Cup tactical analysis</title></head><body>
      <p>The team pressed from a compact shape and created a transition chance.</p>
    </body></html>
    """
    res = ingest_rss_feed(
        "https://example.com/rss.xml",
        config=cfg,
        limit=5,
        include_terms=["world cup"],
        feed_xml=feed,
        article_html_by_url={"https://example.com/wc": article},
    )

    assert res.counts["articles_ingested"] == 1
    assert res.counts["feed_items_skipped"] == 1


def test_manual_note_and_delete_document(tmp_path):
    cfg = _cfg(tmp_path)
    res = ingest_manual_note(
        "Argentina press high, but the fullbacks leave transition space behind them.",
        config=cfg,
        teams=["Argentina"],
        title="Argentina manual tactical note",
        date="2026-06-12",
        author="Bryan",
    )

    store = MatchStore.from_config(cfg)
    try:
        summary = store.qualitative_summary()
        assert summary["documents"] == 1
        assert summary["segments"] == 1
        assert summary["links"] >= 1
    finally:
        store.close()

    deleted = delete_qual_document(res.document_id, config=cfg)
    assert deleted.counts["wh_qual_documents"] == 1

    store = MatchStore.from_config(cfg)
    try:
        assert store.qualitative_summary()["documents"] == 0
    finally:
        store.close()
