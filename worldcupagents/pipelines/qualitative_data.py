"""Qualitative football document ingestion.

First wave: Guardian liveblog commentary via the existing sanctioned API, plus
user-supplied public article URLs. We preserve raw files, segment text for later
RAG, and attach lightweight deterministic claim/entity metadata.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.commentary.base import RawMatchFeed
from worldcupagents.dataflows.commentary.guardian import GuardianCommentaryProvider
from worldcupagents.dataflows.entities import normalize_entity_key, resolve_team, seed_identity_registry
from worldcupagents.dataflows.match_store import MatchStore


@dataclass
class QualIngestResult:
    source_id: str
    document_id: str
    raw_path: str
    counts: dict[str, int]


@dataclass
class QualDeleteResult:
    document_id: str
    counts: dict[str, int]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _snapshot_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _doc_id(source_id: str, value: str) -> str:
    return f"{source_id}:{hashlib.sha1(value.encode('utf-8')).hexdigest()}"


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    key = normalize_entity_key(host).replace(" ", "_")
    return f"public_article:{key or 'unknown'}"


def _raw_dir(config: dict, source_id: str, snapshot: str) -> Path:
    safe = source_id.replace(":", "_")
    return Path(config.get("data_dir", "data")) / "raw" / "qualitative" / safe / snapshot


def _write_raw(config: dict, source_id: str, snapshot: str, name: str, text: str) -> Path:
    out = _raw_dir(config, source_id, snapshot)
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text(text, encoding="utf-8")
    return path


class _ArticleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.published_at = ""
        self._tag_stack: list[str] = []
        self._skip = 0
        self._current: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k.lower(): v or "" for k, v in attrs}
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "form"):
            self._skip += 1
        if tag == "meta":
            name = (attrs_d.get("name") or attrs_d.get("property") or "").lower()
            content = attrs_d.get("content") or ""
            if name in ("og:title", "twitter:title") and not self.title:
                self.title = content
            elif name in ("article:published_time", "pubdate", "date") and not self.published_at:
                self.published_at = content
            elif name in ("author", "article:author") and not self.author:
                self.author = content
        self._tag_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "form") and self._skip:
            self._skip -= 1
        if tag in ("p", "h1", "h2", "h3", "li", "blockquote"):
            self._flush()
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        tag = self._tag_stack[-1] if self._tag_stack else ""
        text = " ".join((data or "").split())
        if not text:
            return
        if tag == "title" and not self.title:
            self.title = text
        if tag in ("p", "h1", "h2", "h3", "li", "blockquote"):
            self._current.append(text)

    def _flush(self) -> None:
        text = " ".join(self._current).strip()
        self._current = []
        if len(text) >= 40 and not _looks_like_chrome(text):
            self.blocks.append(html.unescape(text))


def _looks_like_chrome(text: str) -> bool:
    low = text.lower()
    noisy = ("subscribe", "cookie", "privacy policy", "sign in", "share on", "newsletter")
    return len(text) < 80 and any(x in low for x in noisy)


def _parse_article(html_text: str) -> tuple[str, list[str], dict]:
    parser = _ArticleHTMLParser()
    parser.feed(html_text)
    title = " ".join((parser.title or "Public football article").split())
    blocks = _dedupe_blocks(parser.blocks)
    meta = {"author": parser.author, "published_at": parser.published_at}
    return title, blocks, meta


def _dedupe_blocks(blocks: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for block in blocks:
        key = normalize_entity_key(block[:240])
        if key and key not in seen:
            seen.add(key)
            out.append(block)
    return out


_MINUTE_RE = re.compile(r"^\s*((?:ET\s*)?\d{1,3}(?:\+\d{1,2})?|HT|FT)\s*(?:min|:)", re.I)
_CLAIM_RULES = {
    "tactical": ("press", "pressed", "pressing", "shape", "formation", "block", "line", "overload", "transition"),
    "selection": ("lineup", "line-up", "started", "substitute", "bench", "injury", "injured", "suspended"),
    "performance": ("dominant", "struggled", "created", "threat", "chance", "control", "tempo", "poor", "excellent"),
    "set_piece": ("corner", "free-kick", "set piece", "set-piece", "penalty"),
    "context": ("manager", "coach", "qualifier", "world cup", "friendly", "tournament"),
}


def _segment_blocks(blocks: list[str], max_chars: int = 900) -> list[dict]:
    segments: list[dict] = []
    offset = 0
    idx = 0
    for block in blocks:
        parts = _split_block(block, max_chars=max_chars)
        for part in parts:
            text = part.strip()
            if len(text) < 30:
                continue
            idx += 1
            minute = None
            m = _MINUTE_RE.match(text)
            if m:
                minute = m.group(1).upper().replace(" ", "")
            start = offset
            end = offset + len(text)
            segments.append({
                "idx": idx,
                "minute": minute,
                "heading": None,
                "text": text,
                "text_norm": normalize_entity_key(text),
                "char_start": start,
                "char_end": end,
            })
            offset = end + 1
    return segments


def _split_block(block: str, max_chars: int) -> list[str]:
    text = " ".join(block.split())
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    cur = ""
    for sentence in sentences:
        if cur and len(cur) + len(sentence) + 1 > max_chars:
            out.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        out.append(cur)
    return out


def _team_links(document_id: str, segments: list[dict], teams: list[str], source_id: str) -> list[dict]:
    links = []
    resolved = []
    for team in teams:
        res = resolve_team(team, kind="national", source_id=source_id)
        if res.team_id:
            resolved.append((team, res.team_id, res.canonical_name))
    for raw, team_id, canonical in resolved:
        links.append({
            "link_id": f"{document_id}:team:{team_id}",
            "document_id": document_id,
            "segment_id": None,
            "entity_type": "team",
            "entity_id": team_id,
            "entity_name": canonical,
            "source_id": source_id,
            "confidence": 0.95,
        })
        keys = {normalize_entity_key(raw), normalize_entity_key(canonical)}
        for seg in segments:
            if any(k and k in seg["text_norm"] for k in keys):
                links.append({
                    "link_id": f"{seg['segment_id']}:team:{team_id}",
                    "document_id": document_id,
                    "segment_id": seg["segment_id"],
                    "entity_type": "team",
                    "entity_id": team_id,
                    "entity_name": canonical,
                    "source_id": source_id,
                    "confidence": 0.75,
                })
    return links


def _claims(document_id: str, segments: list[dict], source_id: str, teams: list[str]) -> list[dict]:
    team_res = [resolve_team(t, kind="national", source_id=source_id) for t in teams]
    out = []
    for seg in segments:
        text_norm = seg["text_norm"]
        for claim_type, needles in _CLAIM_RULES.items():
            if not any(normalize_entity_key(n) in text_norm for n in needles):
                continue
            team_id = None
            for res in team_res:
                if res.team_id and normalize_entity_key(res.canonical_name) in text_norm:
                    team_id = res.team_id
                    break
            out.append({
                "claim_id": f"{seg['segment_id']}:claim:{claim_type}",
                "segment_id": seg["segment_id"],
                "document_id": document_id,
                "claim_type": claim_type,
                "team_id": team_id,
                "player": None,
                "claim_text": seg["text"],
                "confidence": 0.45,
                "source_id": source_id,
            })
    return out


def ingest_public_article(
    url: str,
    config: dict | None = None,
    teams: list[str] | None = None,
    title: str | None = None,
    refresh: bool = False,
    html_text: str | None = None,
) -> QualIngestResult:
    config = dict(config or DEFAULT_CONFIG)
    teams = teams or []
    snapshot = _snapshot_id()
    source_id = _source_from_url(url)
    fetched_at = _now()

    if html_text is None:
        headers = {"User-Agent": "WorldCupAgents/0.2 qualitative-research"}
        response = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        response.raise_for_status()
        html_text = response.text
    parsed_title, blocks, meta = _parse_article(html_text)
    doc_title = title or parsed_title
    raw_path = _write_raw(config, source_id, snapshot, f"{_sha256_text(url)[:12]}.html", html_text)
    text = "\n\n".join(blocks)
    document_id = _doc_id(source_id, url)
    return _persist_document(
        config=config,
        document_id=document_id,
        source_id=source_id,
        source_type="article",
        title=doc_title,
        url=url,
        published_at=meta.get("published_at") or None,
        fetched_at=fetched_at,
        snapshot=snapshot,
        raw_path=raw_path,
        raw_text=html_text,
        blocks=blocks,
        teams=teams,
        author=meta.get("author") or None,
        license="public web page; store excerpts/metadata only in generated outputs",
        refresh=refresh,
        meta={"text_sha256": _sha256_text(text), "source_kind": "public_article"},
    )


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _tag_name(child.tag) == name:
            return " ".join((child.text or "").split())
    return ""


def _feed_items(feed_xml: str) -> list[dict]:
    root = ET.fromstring(feed_xml)
    items = []
    for node in root.iter():
        tag = _tag_name(node.tag)
        if tag not in ("item", "entry"):
            continue
        title = _child_text(node, "title")
        link = _child_text(node, "link")
        if not link:
            for child in list(node):
                if _tag_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        published = _child_text(node, "pubdate") or _child_text(node, "published") or _child_text(node, "updated")
        summary = _child_text(node, "description") or _child_text(node, "summary")
        if link:
            items.append({"title": title, "link": link, "published": published, "summary": summary})
    return items


def ingest_rss_feed(
    feed_url: str,
    config: dict | None = None,
    teams: list[str] | None = None,
    limit: int = 10,
    include_terms: list[str] | None = None,
    refresh: bool = False,
    feed_xml: str | None = None,
    article_html_by_url: dict[str, str] | None = None,
) -> QualIngestResult:
    """Ingest article links from a public RSS/Atom feed.

    The feed itself is snapshotted, then each article is stored through
    ``ingest_public_article`` so provenance and segmentation stay consistent.
    """
    config = dict(config or DEFAULT_CONFIG)
    teams = teams or []
    snapshot = _snapshot_id()
    source_id = f"public_feed:{normalize_entity_key(urlparse(feed_url).netloc).replace(' ', '_') or 'unknown'}"
    fetched_at = _now()
    if feed_xml is None:
        headers = {"User-Agent": "WorldCupAgents/0.2 qualitative-research"}
        response = httpx.get(feed_url, headers=headers, timeout=20, follow_redirects=True)
        response.raise_for_status()
        feed_xml = response.text
    raw_path = _write_raw(config, source_id, snapshot, f"{_sha256_text(feed_url)[:12]}.xml", feed_xml)
    store = MatchStore.from_config(config)
    try:
        store.upsert_wh_source({
            "source_id": source_id,
            "name": f"Public feed: {urlparse(feed_url).netloc}",
            "homepage": feed_url,
            "license": "public RSS/Atom feed; article pages keep their own source URLs",
            "notes": "Qualitative football feed source. Feed items are ingested as public article documents.",
        })
        store.upsert_wh_source_file({
            "file_id": f"{source_id}:{snapshot}:{raw_path.name}",
            "source_id": source_id,
            "snapshot": snapshot,
            "path": str(raw_path),
            "url": feed_url,
            "sha256": _sha256_text(feed_xml),
            "bytes": raw_path.stat().st_size,
            "fetched_at": fetched_at,
        })
        store.upsert_wh_ingestion_run({
            "run_id": f"{source_id}:{snapshot}:{fetched_at}",
            "source_id": source_id,
            "snapshot": snapshot,
            "started_at": fetched_at,
            "finished_at": _now(),
            "status": "ok",
            "counts_json": {"feed_items": len(_feed_items(feed_xml))},
        })
    finally:
        store.close()

    counts = {"feed_items": 0, "articles_attempted": 0, "articles_ingested": 0, "article_errors": 0}
    last = QualIngestResult(source_id=source_id, document_id=_doc_id(source_id, feed_url), raw_path=str(raw_path), counts=counts)
    include_norm = [normalize_entity_key(t) for t in (include_terms or []) if t]
    matched_items = []
    for item in _feed_items(feed_xml):
        haystack = normalize_entity_key(" ".join(str(item.get(k) or "") for k in ("title", "summary", "link")))
        if include_norm and not any(term in haystack for term in include_norm):
            counts["feed_items_skipped"] = counts.get("feed_items_skipped", 0) + 1
            continue
        matched_items.append(item)
        if len(matched_items) >= max(limit, 0):
            break
    for item in matched_items:
        counts["feed_items"] += 1
        counts["articles_attempted"] += 1
        try:
            html_text = (article_html_by_url or {}).get(item["link"])
            res = ingest_public_article(
                item["link"],
                config=config,
                teams=teams,
                title=item.get("title") or None,
                refresh=refresh,
                html_text=html_text,
            )
            counts["articles_ingested"] += 1
            for k, v in res.counts.items():
                counts[k] = counts.get(k, 0) + int(v or 0)
            last = res
        except Exception:
            counts["article_errors"] += 1
            continue
    last.counts = counts
    return last


def ingest_guardian_match(
    home: str,
    away: str,
    date: str | None = None,
    config: dict | None = None,
    refresh: bool = False,
    feed: RawMatchFeed | None = None,
) -> QualIngestResult:
    config = dict(config or DEFAULT_CONFIG)
    snapshot = _snapshot_id()
    source_id = "guardian"
    fetched_at = _now()
    if feed is None:
        feed = GuardianCommentaryProvider.from_config(config).fetch_match(home, away, date)
    if not feed.lines:
        raise ValueError(f"No Guardian commentary lines found for {home} v {away} ({date or 'no date'})")
    url = feed.sources[0] if feed.sources else f"guardian:{home}:{away}:{date or ''}"
    title = f"{home} v {away} Guardian commentary"
    raw_payload = feed.model_dump_json(indent=2)
    raw_path = _write_raw(config, source_id, snapshot, f"{_sha256_text(url)[:12]}.json", raw_payload)
    return _persist_document(
        config=config,
        document_id=_doc_id(source_id, f"{home}|{away}|{date or ''}|{url}"),
        source_id=source_id,
        source_type="commentary",
        title=title,
        url=url if url.startswith("http") else None,
        published_at=date,
        fetched_at=fetched_at,
        snapshot=snapshot,
        raw_path=raw_path,
        raw_text=raw_payload,
        blocks=feed.lines,
        teams=[home, away],
        author=None,
        license="Guardian Open Platform terms; source URL retained for provenance",
        refresh=refresh,
        meta={"events": [e.model_dump() for e in feed.events], "source_kind": "guardian_liveblog"},
    )


def ingest_manual_note(
    text: str,
    config: dict | None = None,
    teams: list[str] | None = None,
    title: str | None = None,
    date: str | None = None,
    author: str | None = "user",
    refresh: bool = False,
) -> QualIngestResult:
    config = dict(config or DEFAULT_CONFIG)
    teams = teams or []
    snapshot = _snapshot_id()
    source_id = "manual_analysis"
    fetched_at = _now()
    note_title = title or f"Manual analysis {date or fetched_at[:10]}"
    raw_text = text.strip()
    if not raw_text:
        raise ValueError("Manual analysis text is empty")
    document_id = _doc_id(source_id, f"{note_title}|{date or ''}|{author or ''}|{raw_text[:500]}")
    raw_path = _write_raw(config, source_id, snapshot, f"{_sha256_text(document_id)[:12]}.txt", raw_text)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw_text) if b.strip()] or [raw_text]
    return _persist_document(
        config=config,
        document_id=document_id,
        source_id=source_id,
        source_type="manual_note",
        title=note_title,
        url=None,
        published_at=date,
        fetched_at=fetched_at,
        snapshot=snapshot,
        raw_path=raw_path,
        raw_text=raw_text,
        blocks=blocks,
        teams=teams,
        author=author,
        license="user-provided analysis",
        refresh=refresh,
        meta={"source_kind": "manual_analysis", "teams": teams},
    )


def delete_qual_document(document_id: str, config: dict | None = None) -> QualDeleteResult:
    config = dict(config or DEFAULT_CONFIG)
    store = MatchStore.from_config(config)
    counts: dict[str, int] = {}
    try:
        for table in ("wh_qual_links", "wh_qual_claims", "wh_qual_segments"):
            cur = store.conn.execute(f"DELETE FROM {table} WHERE document_id = ?", [document_id])
            counts[table] = cur.rowcount
        cur = store.conn.execute("DELETE FROM wh_qual_documents WHERE document_id = ?", [document_id])
        counts["wh_qual_documents"] = cur.rowcount
        store.conn.commit()
    finally:
        store.close()
    return QualDeleteResult(document_id=document_id, counts=counts)


def _persist_document(
    *,
    config: dict,
    document_id: str,
    source_id: str,
    source_type: str,
    title: str,
    url: str | None,
    published_at: str | None,
    fetched_at: str,
    snapshot: str,
    raw_path: Path,
    raw_text: str,
    blocks: list[str],
    teams: list[str],
    author: str | None,
    license: str,
    refresh: bool,
    meta: dict,
) -> QualIngestResult:
    store = MatchStore.from_config(config)
    try:
        seed_counts = seed_identity_registry(config)
        store.upsert_wh_source({
            "source_id": source_id,
            "name": "Guardian Open Platform" if source_id == "guardian" else source_id,
            "homepage": url,
            "license": license,
            "notes": "Qualitative football commentary/article source.",
        })
        store.upsert_wh_source_file({
            "file_id": f"{source_id}:{snapshot}:{raw_path.name}",
            "source_id": source_id,
            "snapshot": snapshot,
            "path": str(raw_path),
            "url": url,
            "sha256": _sha256_text(raw_text),
            "bytes": raw_path.stat().st_size,
            "fetched_at": fetched_at,
        })
        if refresh:
            store.conn.execute("DELETE FROM wh_qual_links WHERE document_id = ?", [document_id])
            store.conn.execute("DELETE FROM wh_qual_claims WHERE document_id = ?", [document_id])
            store.conn.execute("DELETE FROM wh_qual_segments WHERE document_id = ?", [document_id])
            store.conn.commit()

        segments = _segment_blocks(blocks)
        for seg in segments:
            seg["document_id"] = document_id
            seg["segment_id"] = f"{document_id}:seg:{seg['idx']:04d}"
        claims = _claims(document_id, segments, source_id, teams)
        links = _team_links(document_id, segments, teams, source_id)
        document = {
            "document_id": document_id,
            "source_id": source_id,
            "source_type": source_type,
            "title": title,
            "url": url,
            "published_at": published_at,
            "fetched_at": fetched_at,
            "snapshot": snapshot,
            "raw_path": str(raw_path),
            "sha256": _sha256_text("\n\n".join(blocks)),
            "license": license,
            "author": author,
            "language": "en",
            "text_chars": sum(len(s["text"]) for s in segments),
            "meta_json": json.dumps(meta, sort_keys=True),
        }
        counts = dict(seed_counts)
        counts["documents"] = store.upsert_qual_documents([document])
        counts["segments"] = store.upsert_qual_segments(segments)
        counts["claims"] = store.upsert_qual_claims(claims)
        counts["links"] = store.upsert_qual_links(links)
        store.upsert_wh_ingestion_run({
            "run_id": f"{source_id}:{document_id}:{fetched_at}",
            "source_id": source_id,
            "snapshot": snapshot,
            "started_at": fetched_at,
            "finished_at": _now(),
            "status": "ok",
            "counts_json": counts,
        })
    finally:
        store.close()
    return QualIngestResult(source_id=source_id, document_id=document_id, raw_path=str(raw_path), counts=counts)
