"""BBC Sport WC2026 team-guide ingester — full team profiles for all 48 nations.

The BBC guide is a Shorthand story: each team is an <h3> heading + an inline
summary (world ranking, appearances, best performance, a sentence) and a
"FULL TEAM PROFILE" link to a longer BBC article. We ingest the inline summary
as a team note AND follow each full-profile link through the existing public-
article scraper, so the rich prose lands in the qualitative warehouse (team-
linked → tactical analyst + dossier).

Source: bbc.com/sport/extra/nsli2gnmtj/fifa-world-cup-team-guide
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.names import canonical_name

logger = logging.getLogger(__name__)

GUIDE_URL = "https://www.bbc.com/sport/extra/nsli2gnmtj/fifa-world-cup-team-guide"
SOURCE = "BBC Sport WC2026 team guide"


@dataclass
class BBCGuideResult:
    teams: int = 0
    full_profiles: int = 0
    errors: int = 0


def _clean(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", s or "").split())


def parse_team_sections(html: str) -> list[dict]:
    """[{team, summary, full_url}] — one per <h3> team heading in the guide."""
    parts = re.split(r"<h3[^>]*>(.*?)</h3>", html, flags=re.S)
    out: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        name = _clean(parts[i])
        body_html = parts[i + 1]
        if not name or name.lower() == "accessibility links":
            continue
        team = canonical_name(re.sub(r"\s*\(debut\)\s*", "", name).strip())
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*FULL TEAM PROFILE', body_html, re.I)
        out.append({"team": team, "summary": _clean(body_html).replace(" FULL TEAM PROFILE", "").strip(),
                    "full_url": m.group(1) if m else None})
    return out


def ingest_bbc_team_guide(config: dict | None = None, fetch_text=None,
                          full_profiles: bool = True, limit: int | None = None) -> BBCGuideResult:
    """Ingest the BBC team guide. ``fetch_text`` injectable for tests;
    ``full_profiles`` follows each FULL TEAM PROFILE link; ``limit`` caps teams."""
    config = dict(config or DEFAULT_CONFIG)
    from worldcupagents.pipelines.qualitative_data import ingest_manual_note, ingest_public_article

    res = BBCGuideResult()
    try:
        if fetch_text is not None:
            html = fetch_text(GUIDE_URL)
        else:
            from worldcupagents.pipelines.hoard_data import _fetch_text
            html = _fetch_text(GUIDE_URL)
    except Exception as e:  # noqa: BLE001
        logger.warning("bbc guide: fetch failed (%s)", e)
        return res

    for sec in parse_team_sections(html)[: limit or None]:
        team, summary, url = sec["team"], sec["summary"], sec["full_url"]
        if not team or not summary:
            continue
        try:
            ingest_manual_note(summary, config=config, teams=[team],
                               title=f"BBC guide: {team}", author="BBC Sport")
            res.teams += 1
        except Exception as e:  # noqa: BLE001 — one team must not sink the run
            logger.warning("bbc guide: summary note failed for %s (%s)", team, e)
            res.errors += 1
        if full_profiles and url and fetch_text is None:
            try:
                ingest_public_article(url, config=config, teams=[team],
                                      title=f"BBC team profile: {team}")
                res.full_profiles += 1
                time.sleep(0.4)  # polite between BBC articles
            except Exception as e:  # noqa: BLE001
                logger.warning("bbc guide: full profile failed for %s (%s)", team, e)
                res.errors += 1
    return res
