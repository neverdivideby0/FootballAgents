"""Guardian Open Platform commentary provider (COMMENTARY_PLAN.md step A).

Sanctioned API (free key at theguardian.com/open-platform) — NOT page scraping,
per the project's no-scrape hard rule. Searches the Football section for the
match's minute-by-minute liveblog, then flattens its update blocks into
chronological prose lines for the chunker.

Reuses HTTPCache (rate-limit + disk cache) and names.canonical_name (article↔
fixture matching). Degrades gracefully: a missing key raises at construction so
the registry falls back to the placeholder; any fetch/parse error returns an
empty feed rather than crashing the pipeline. Typed events are NOT sourced here
(they come from the stats provider) — events is always empty from Guardian.
"""

from __future__ import annotations

import html
import logging
import os
import re
from urllib.parse import quote_plus

from worldcupagents.agents.schemas import MatchEvent
from worldcupagents.dataflows.commentary.base import RawMatchFeed
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.dataflows.names import canonical_name, normalize_key, surface_forms

logger = logging.getLogger(__name__)

BASE = "https://content.guardianapis.com"
_TTL = 86_400  # 24h; post-game commentary is immutable
_TAG_RE = re.compile(r"<[^>]+>")

# A block body that begins with a minute marker, optionally prefixed "ET" (extra time):
#   "23 min: …", "45+2 min: …", "ET 29 min: …"
_BODY_MIN = re.compile(r"^\s*(ET\b\s*)?(\d{1,3})(?:\s*\+\s*(\d{1,2}))?\s*min\b", re.IGNORECASE)
# Minute inside a goal title's parens: "(Messi 23 pen)", "(Di Maria 36)", "(Mbappe 118 pen)".
_PAREN_MIN = re.compile(r"\([^()]*?\b(\d{1,3})(?:\s*\+\s*(\d{1,2}))?\s*(?:pen|og|o\.g\.)?\s*\)", re.IGNORECASE)
_TITLE_GOAL = re.compile(r"\bGOAL\b", re.IGNORECASE)
_TITLE_HALFTIME = re.compile(r"^\s*HALF[\s-]?TIME\b", re.IGNORECASE)
_TITLE_EXTRA = re.compile(r"^\s*EXTRA", re.IGNORECASE)
_TITLE_END = re.compile(r"\b(FULL[\s-]?TIME|EXTRA[\s-]?TIME|win[s]?\s+the|WORLD CUP)\b", re.IGNORECASE)


def _strip_html(s: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", s or ""))
    return re.sub(r"\s+", " ", text).strip()  # collapse the gaps left by removed tags


def _normalize_body(body: str) -> str:
    """Leave normal-time bodies alone; rewrite an 'ET N min' prefix into '90+N min'
    so extra-time commentary buckets into Crunch Time, not the first half."""
    m = _BODY_MIN.match(body)
    if not m or not m.group(1):  # no match, or not extra-time
        return body
    base = min(int(m.group(2)), 99)
    return f"90+{base} min{body[m.end():]}"


def _blocks_to_lines_events(blocks: list[dict]) -> tuple[list[str], list[MatchEvent]]:
    """Turn Guardian liveblog blocks into minute-led lines + typed goal events.

    * Goal/half-time/full-time TITLES carry the minute and event type — use them.
    * Untitled blocks lead their body with the minute ('23 min: …') — keep that.
    * Extra-time ('ET N min') is folded into the 90+ band.
    * Pre-kickoff build-up (no minute yet) is dropped so it can't pollute phases.
    """
    lines: list[str] = []
    events: list[MatchEvent] = []
    started = False

    for b in blocks:
        title = (b.get("title") or "").strip()
        body = _strip_html(b.get("bodyTextSummary") or b.get("bodyHtml", ""))
        line: str | None = None
        minute_seen = False

        if title and _TITLE_GOAL.search(title) and _PAREN_MIN.search(title):
            gm = _PAREN_MIN.search(title)
            base = int(gm.group(1))
            added = int(gm.group(2)) if gm.group(2) else 0
            events.append(MatchEvent(minute=base, type="goal", detail=title))
            mark = f"{base}+{added} min" if added else f"{base} min"
            line, minute_seen = f"{mark}: {title}", True
        elif title and _TITLE_HALFTIME.match(title) and not _TITLE_EXTRA.match(title):
            line, minute_seen = f"HT: {title}", True            # the real half-time break
        elif title and _TITLE_END.search(title):
            line, minute_seen = f"90+1 min: {title}", True        # FT / extra-time / winner -> Crunch
        else:
            bm = _BODY_MIN.match(body)
            if bm:
                line, minute_seen = _normalize_body(body), True
            elif title:
                line = f"{title}. {body}".strip(". ") or None
            else:
                line = body or None

        if minute_seen:
            started = True
        if not started or not line:      # drop pre-kickoff build-up
            continue
        lines.append(line)

    return lines, events


class GuardianCommentaryProvider:
    name = "guardian"

    def __init__(self, api_key: str, cache_dir: str = ".cache/guardian", http=None):
        if not api_key:
            raise ValueError("Guardian API key required (set GUARDIAN_API_KEY)")
        self.api_key = api_key
        self.http = http or HTTPCache(cache_dir)

    @classmethod
    def from_config(cls, config: dict) -> "GuardianCommentaryProvider":
        api_key = os.environ.get("GUARDIAN_API_KEY", "")
        cache_dir = f"{config.get('cache_dir', '.cache')}/guardian"
        return cls(api_key, cache_dir=cache_dir)

    # --- public contract ---

    def fetch_match(self, home: str, away: str, date: str | None = None) -> RawMatchFeed:
        empty = RawMatchFeed(home=home, away=away, date=date)
        try:
            data = self.http.get_json(self._search_url(home, away, date), ttl=_TTL)
        except Exception as e:  # noqa: BLE001 — network/key/rate-limit must not crash the pipeline
            logger.warning("guardian: search failed for %s v %s (%s)", home, away, e)
            return empty

        results = ((data or {}).get("response") or {}).get("results") or []
        article = self._pick_article(results, home, away)
        if not article:
            logger.warning("guardian: no matching liveblog for %s v %s", home, away)
            return empty

        blocks = self._chronological((article.get("blocks") or {}).get("body") or [])
        lines, events = _blocks_to_lines_events(blocks)
        return RawMatchFeed(
            home=home, away=away, date=date,
            lines=lines, events=events,
            sources=[article.get("webUrl", "guardian")],
        )

    # --- internals ---

    def _search_url(self, home: str, away: str, date: str | None) -> str:
        q = quote_plus(f"{home} {away}")
        url = (
            f"{BASE}/search?q={q}&section=football&show-blocks=all&show-fields=bodyText"
            f"&page-size=10&order-by=relevance&api-key={self.api_key}"
        )
        if date:
            url += f"&from-date={date}&to-date={date}"
        return url

    def fetch_articles(self, home: str, away: str, date: str | None = None,
                       limit: int = 5) -> list[dict]:
        """The punditry counterpart of fetch_match: from the SAME search, return the
        report + tactical-column ARTICLES (the ones _pick_article discards), each as
        ``{title, url, body}``. Both team names must appear in the title. Body comes
        from the ``bodyText`` field (articles) or flattened blocks (fallback). Empty
        list on no-key/error — the pipeline then degrades to a placeholder digest."""
        try:
            data = self.http.get_json(self._search_url(home, away, date), ttl=_TTL)
        except Exception as e:  # noqa: BLE001 — network/key/rate-limit must not crash
            logger.warning("guardian: article search failed for %s v %s (%s)", home, away, e)
            return []

        results = ((data or {}).get("response") or {}).get("results") or []
        # Alias-aware: a title matches a team if ANY of its spellings appears — so
        # "South Korea" matches even though our canonical is "Korea Republic".
        home_forms, away_forms = surface_forms(home), surface_forms(away)
        out: list[dict] = []
        for r in results:
            if r.get("type") == "liveblog":
                continue  # the liveblog is fetch_match's job (→ analyze-match)
            title = r.get("webTitle", "")
            t = normalize_key(title)
            if not (any(f in t for f in home_forms) and any(f in t for f in away_forms)):
                continue
            body = (r.get("fields") or {}).get("bodyText") or ""
            if not body:  # fallback: flatten any block bodies into prose
                blocks = (r.get("blocks") or {}).get("body") or []
                body = " ".join(_strip_html(b.get("bodyHtml") or b.get("bodyTextSummary") or "")
                                for b in blocks)
            body = body.strip()
            if not body:
                continue
            out.append({"title": title, "url": r.get("webUrl", "guardian"), "body": body})
            if len(out) >= limit:
                break
        return out

    def _pick_article(self, results: list[dict], home: str, away: str) -> dict | None:
        """Pick the minute-by-minute liveblog, not the match report.

        Guardian returns both for a big game and BOTH name the two teams, so a
        naive title match grabs the report (1 block, no minutes). We rank by
        (both teams named, is a liveblog, number of update blocks) — the real
        min-by-min liveblog has ~150 blocks and wins decisively.
        """
        if not results:
            return None

        h = normalize_key(canonical_name(home))
        a = normalize_key(canonical_name(away))

        def names_match(r: dict) -> bool:
            t = normalize_key(r.get("webTitle", ""))
            return bool(h and a and h in t and a in t)

        def block_count(r: dict) -> int:
            return len((r.get("blocks") or {}).get("body") or [])

        def rank(r: dict) -> tuple:
            return (names_match(r), r.get("type") == "liveblog", block_count(r))

        best = max(results, key=rank)
        if names_match(best) or best.get("type") == "liveblog":
            return best
        logger.warning("guardian: no confident liveblog match; using top result")
        return results[0]

    @staticmethod
    def _chronological(blocks: list[dict]) -> list[dict]:
        """Liveblogs are published newest-first; return oldest-first for the timeline."""
        if blocks and all(b.get("firstPublishedDate") for b in blocks):
            return sorted(blocks, key=lambda b: b["firstPublishedDate"])
        return list(reversed(blocks))
