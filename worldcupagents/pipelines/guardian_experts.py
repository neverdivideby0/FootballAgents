"""Guardian World Cup 2026 'Experts' Network' team-guide ingester — all 48 nations.

A second, richer Guardian source (distinct from the interactive *player* guide in
``guardian_guide.py`` and the BBC team guide in ``bbc_guide.py``): a series of
long-form per-nation previews written by local experts. Each article has a stable
set of sections — The plan / The coach / Star player / Unsung hero / One to watch —
so it carries exactly the qualitative layer the model can't get from stats,
including a proper read on the **head coach's style and pedigree**.

The Guardian free API tier only serves ~18 of these (and blocks the item endpoint
for the rest: "not permitted via your current user tier"), so we enumerate the
full series off its public index page and read each public, non-paywalled article
page directly — allowed under the project's scraping policy (be polite, cache, cite
provenance; never bypass paywalls/logins). Each article's prose lands in the
qualitative warehouse (team-linked → tactical analyst + dossier) via
``ingest_public_article``; the 'The coach' section is also stored as a structured
coach note (→ ``dataflows/coach.py`` → dossier + debate).

Source: theguardian.com/football/series/world-cup-2026-guardian-experts-network
"""

from __future__ import annotations

import html as _html
import logging
import re
import time
from dataclasses import dataclass

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.names import canonical_name

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.theguardian.com/football/series/world-cup-2026-guardian-experts-network"
SOURCE = "The Guardian Experts' Network"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# A team-guide article path, e.g. /football/2026/jun/06/spain-world-cup-2026-team-guide
_GUIDE_PATH = re.compile(r"/football/2026/[a-z]{3}/\d{2}/[a-z0-9-]*world-cup-2026-team-guide")
_TITLE_SPLIT = re.compile(r"\s*World Cup 2026 team guide", re.I)
# Body text: <h2> section labels + <p> paragraphs, in document order.
_BLOCK_RE = re.compile(r"<(h2|p)\b[^>]*>(.*?)</\1>", re.S)
_OG_TITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', re.I)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

# The guides share a stable section structure; the text between two labels is that
# section's body. Labels are matched only at a sentence boundary, introducing a
# capitalised sentence, so they don't fire mid-prose.
_HEADERS = [
    "The plan", "The coach", "Star player", "Unsung hero", "One to watch",
    "Probable lineup", "Probable XI", "Realistic aim", "The lowdown",
    "Qualifying record", "Did you know",
]
_HEADER_RE = re.compile(
    r"(?:(?<=[.!?\"”’)])|(?<=^))\s*(" + "|".join(re.escape(h) for h in _HEADERS) + r")\s+(?=[A-Z“\"])"
)


@dataclass
class ExpertsResult:
    teams: int = 0          # articles ingested into the warehouse
    coaches: int = 0        # 'The coach' sections stored as coach notes
    errors: int = 0


def _strip_tags(s: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def _team_from_title(title: str) -> str:
    return canonical_name(_TITLE_SPLIT.split(title or "")[0].strip())


def _strip_boilerplate(body: str) -> str:
    """Drop the standing 'This article is part of the Guardian's … Network …' intro
    that prefixes every piece, so it isn't ingested 48 times."""
    m = re.search(r"\bThe (?:plan|lowdown)\b", body)
    return body[m.start():] if m else body


def split_sections(body: str) -> list[dict]:
    """[{'header', 'text'}] — one per labelled section, in article order."""
    body = _strip_boilerplate(body or "")
    marks = [(m.start(), m.end(), m.group(1)) for m in _HEADER_RE.finditer(body)]
    out: list[dict] = []
    for i, (_, end, header) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        text = body[end:stop].strip()
        if text:
            out.append({"header": header, "text": text})
    return out


def _coach_section(sections: list[dict]) -> str:
    for s in sections:
        if s["header"].lower() == "the coach":
            return s["text"]
    return ""


def _sections_to_html(title: str, sections: list[dict]) -> str:
    parts = [f"<title>{_html.escape(title)}</title>"]
    for s in sections:
        parts.append(f"<h3>{_html.escape(s['header'])}</h3>"
                     f"<p>{_html.escape(s['text'])}</p>")
    return "\n".join(parts)


def title_and_body(article_html: str) -> tuple[str, str]:
    """(headline, flat body text) from a Guardian article page. The body is the
    <h2> labels + <p> paragraphs joined in order — i.e. the same shape the API's
    bodyText had, so ``split_sections`` works on it unchanged."""
    m = _OG_TITLE.search(article_html) or _TITLE_TAG.search(article_html)
    title = _strip_tags(m.group(1)) if m else ""
    blocks = [_strip_tags(b.group(2)) for b in _BLOCK_RE.finditer(article_html)]
    body = " ".join(b for b in blocks if b)
    return title, body


def _fetch(url: str, fetch_text=None, retries: int = 3) -> str:
    if fetch_text is not None:
        return fetch_text(url)
    import httpx
    last = None
    for attempt in range(retries):
        try:
            r = httpx.get(url, headers={"User-Agent": _UA}, timeout=30, follow_redirects=True)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last  # type: ignore[misc]


def fetch_index_urls(fetch_text=None, max_pages: int = 8) -> list[str]:
    """All distinct team-guide article URLs across the paginated series index."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for page in range(1, max_pages + 1):
        url = INDEX_URL if page == 1 else f"{INDEX_URL}?page={page}"
        try:
            html = _fetch(url, fetch_text)
        except Exception as e:  # noqa: BLE001
            logger.warning("guardian experts: index page %d fetch failed (%s)", page, e)
            break
        new = 0
        for path in _GUIDE_PATH.findall(html):
            full = "https://www.theguardian.com" + path
            if full not in seen_set:
                seen_set.add(full)
                seen.append(full)
                new += 1
        if new == 0:  # page repeated (Guardian wraps past the last page) → done
            break
    return seen


def ingest_guardian_experts(config: dict | None = None, fetch_text=None,
                            limit: int | None = None) -> ExpertsResult:
    """Ingest the Experts' Network series (all 48). Each article → qualitative
    warehouse (team-linked) + its coach section → the structured coach note.
    ``fetch_text`` is injectable for tests; ``limit`` caps the number of articles."""
    config = dict(config or DEFAULT_CONFIG)
    from worldcupagents.dataflows.match_store import MatchStore
    from worldcupagents.pipelines.qualitative_data import ingest_public_article

    res = ExpertsResult()
    urls = fetch_index_urls(fetch_text)
    if not urls:
        logger.warning("guardian experts: no article URLs found on the index")
        return res

    store = MatchStore.from_config(config)
    try:
        for url in urls[: limit or len(urls)]:
            try:
                article_html = _fetch(url, fetch_text)
            except Exception as e:  # noqa: BLE001 — one team must not sink the run
                logger.warning("guardian experts: article fetch failed for %s (%s)", url, e)
                res.errors += 1
                continue
            title, body = title_and_body(article_html)
            team = _team_from_title(title)
            sections = split_sections(body)
            if not team or not sections:
                logger.warning("guardian experts: no team/sections parsed for %s", url)
                res.errors += 1
                continue
            doc_title = f"Guardian Experts' Network: {team}"
            try:
                ingest_public_article(url, config=config, teams=[team], title=doc_title,
                                      html_text=_sections_to_html(doc_title, sections))
                res.teams += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("guardian experts: article ingest failed for %s (%s)", team, e)
                res.errors += 1
                continue
            coach_text = _coach_section(sections)
            if coach_text:
                try:
                    store.upsert_team_coach(team, name=None, note=coach_text, source=SOURCE)
                    res.coaches += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("guardian experts: coach note failed for %s (%s)", team, e)
                    res.errors += 1
            if fetch_text is None:
                time.sleep(0.3)  # polite between article fetches
    finally:
        store.close()
    return res
