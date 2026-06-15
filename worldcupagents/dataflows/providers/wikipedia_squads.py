"""Historical season squads from Wikipedia season pages (WS-C).

Free tiers don't serve past-season squads (football-data.org = current only;
API-Football free = 2021–23). Wikipedia's per-season club pages do — e.g.
"2024–25 Arsenal F.C. season" has a 'First-team squad' wikitable — and they're
CC-licensed and accessed via the SANCTIONED MediaWiki API (no page scraping,
consistent with the project's ToS rule). Every squad carries its page URL as
provenance.

Parser shape (verified against live pages): squad section → first wikitable →
position-group header rows ('Goalkeepers', 'Defenders', …) then player rows
whose first wikilink is the player.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlencode

from worldcupagents.agents.schemas import Player
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.seasons import season_dash

logger = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
_TTL = 30 * 86_400  # historical pages are stable

# club name (our canonical, from football-data.org) -> Wikipedia article base name.
_WIKI_NAMES = {
    "AFC Bournemouth": "AFC Bournemouth",
    "Arsenal FC": "Arsenal F.C.",
    "Aston Villa FC": "Aston Villa F.C.",
    "Brentford FC": "Brentford F.C.",
    "Brighton & Hove Albion FC": "Brighton & Hove Albion F.C.",
    "Burnley FC": "Burnley F.C.",
    "Chelsea FC": "Chelsea F.C.",
    "Crystal Palace FC": "Crystal Palace F.C.",
    "Everton FC": "Everton F.C.",
    "Fulham FC": "Fulham F.C.",
    "Leeds United FC": "Leeds United F.C.",
    "Liverpool FC": "Liverpool F.C.",
    "Manchester City FC": "Manchester City F.C.",
    "Manchester United FC": "Manchester United F.C.",
    "Newcastle United FC": "Newcastle United F.C.",
    "Nottingham Forest FC": "Nottingham Forest F.C.",
    "Sunderland AFC": "Sunderland A.F.C.",
    "Tottenham Hotspur FC": "Tottenham Hotspur F.C.",
    "West Ham United FC": "West Ham United F.C.",
    "Wolverhampton Wanderers FC": "Wolverhampton Wanderers F.C.",
}

_GROUP_POS = {
    "goalkeeper": "Goalkeeper",
    "defender": "Defender", "defence": "Defender",
    "midfielder": "Midfielder", "midfield": "Midfielder",
    "forward": "Forward", "attacker": "Forward", "striker": "Forward",
}

_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")


class WikipediaSquadsProvider:
    name = "wikipedia_squads"

    def __init__(self, cache_dir: str = ".cache/wikipedia", http=None):
        self.http = http or HTTPCache(cache_dir, min_interval=1.0)  # be polite, not free-tier slow

    @classmethod
    def from_config(cls, config: dict) -> "WikipediaSquadsProvider":
        return cls(cache_dir=f"{config.get('cache_dir', '.cache')}/wikipedia")

    def get_season_squad(self, team: str, season: str) -> tuple[list[Player], str | None]:
        """(players, page_url) for the team's season page; ([], None) on failure."""
        for title in self._title_candidates(team, season):
            wikitext = self._fetch_wikitext(title)
            if wikitext is None:
                continue
            players = parse_squad_wikitext(wikitext)
            if players:
                url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                return players, url
            logger.info("wikipedia: %r parsed but no squad table found", title)
        logger.warning("wikipedia: no season squad found for %s %s", team, season)
        return [], None

    # ── internals ────────────────────────────────────────────────────────────

    def _title_candidates(self, team: str, season: str) -> list[str]:
        dash = season_dash(season)
        names = []
        if team in _WIKI_NAMES:
            names.append(_WIKI_NAMES[team])
        if team.endswith(" FC"):
            names.append(team[:-3].strip() + " F.C.")
        names.append(team)
        seen: set[str] = set()
        out = []
        for n in names:
            t = f"{dash} {n} season"
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _fetch_wikitext(self, title: str) -> str | None:
        params = urlencode({
            "action": "parse", "page": title, "prop": "wikitext",
            "format": "json", "formatversion": "2", "redirects": "1",
        })
        try:
            data = self.http.get_json(
                f"{API}?{params}",
                headers={"User-Agent": "FootballAgents/0.2 (local research tool)"},
                ttl=_TTL,
            )
        except Exception as e:  # noqa: BLE001 — network must not crash predict
            logger.warning("wikipedia fetch failed for %r (%s)", title, e)
            return None
        if "error" in data:  # e.g. missingtitle -> try the next candidate
            return None
        return (data.get("parse") or {}).get("wikitext")


def parse_squad_wikitext(wikitext: str) -> list[Player]:
    """Extract (name, position) from the season page's squad wikitable."""
    sect = _squad_section(wikitext)
    if sect is None:
        return []
    table = _first_table(sect)
    if table is None:
        return []

    players: list[Player] = []
    position: str | None = None
    for row in table.split("|-"):
        header = re.search(r"!\s*colspan[^|]*\|\s*([A-Za-z ]+)", row)
        if header:
            word = header.group(1).strip().lower().rstrip("s")  # Goalkeepers -> goalkeeper
            pos = _GROUP_POS.get(word)
            if pos is None and players:
                break  # e.g. an 'Out on loan' block after the squad — stop here
            position = pos or position
            continue
        if row.lstrip().startswith("!"):
            continue  # column-header row
        m = _LINK_RE.search(row)
        if not m:
            continue
        name = (m.group(2) or m.group(1)).strip()
        if name and not name.lower().startswith(("file:", "image:")):
            players.append(Player(name=name, position=position, status="fit"))
    return players


def _squad_section(wikitext: str) -> str | None:
    """Text from a 'squad' heading to the next same-or-higher heading."""
    for m in re.finditer(r"(={2,4})\s*([^=\n]*[Ss]quad[^=\n]*)\s*\1", wikitext):
        title = m.group(2).lower()
        if "squad" in title and "statistics" not in title and "changes" not in title:
            start = m.end()
            nxt = re.search(rf"\n={{2,{len(m.group(1))}}}[^=]", wikitext[start:])
            return wikitext[start:start + nxt.start()] if nxt else wikitext[start:]
    return None


def _first_table(section: str) -> str | None:
    i = section.find("{|")
    if i < 0:
        return None
    j = section.find("\n|}", i)
    return section[i:j] if j > 0 else section[i:]
