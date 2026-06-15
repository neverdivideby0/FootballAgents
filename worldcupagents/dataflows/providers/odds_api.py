"""The Odds API provider — live, de-vigged bookmaker consensus.

The clean, purpose-built market source: one call returns h2h (1X2) odds for every
upcoming fixture in a competition, quoted by many bookmakers. We de-vig EACH
book (strip its overround) then average across books → a sharp consensus
probability the judge can be shown ("market says 58/24/18 — argue where it's
wrong"). Free tier ~500 requests/month, so cache hard.

Key in .env as ODDS_API_KEY (free at the-odds-api.com). No key → degrades to
None, never crashes.
"""

from __future__ import annotations

import logging
import os

from worldcupagents.dataflows.club_aliases import canon_club
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.dataflows.names import normalize_key

logger = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"
_TTL = 3_600  # 1h — live odds move, but the free quota is precious

# our league code -> The Odds API sport key
SPORT_KEY = {
    "WC": "soccer_fifa_world_cup",
    "PL": "soccer_epl",
    "PD": "soccer_spain_la_liga",
    "SA": "soccer_italy_serie_a",
    "BL1": "soccer_germany_bundesliga",
    "FL1": "soccer_france_ligue_one",
}


class OddsApiProvider:
    name = "odds_api"

    def __init__(self, api_key: str | None = None, cache_dir: str = ".cache/odds_api", http=None):
        # Only fall back to the env when no key was passed at all; an explicit
        # "" means "no key" (so tests stay hermetic regardless of the shell env).
        # Strip whitespace — a stray leading space in .env yields a 401.
        self.api_key = (os.environ.get("ODDS_API_KEY", "") if api_key is None else api_key).strip()
        self.http = http or HTTPCache(cache_dir, min_interval=1.0)

    @classmethod
    def from_config(cls, config: dict) -> "OddsApiProvider":
        return cls(cache_dir=f"{config.get('cache_dir', '.cache')}/odds_api")

    def get_events(self, sport_key: str) -> list[dict]:
        """All upcoming events with h2h odds for a competition, or []."""
        if not self.api_key:
            return []
        url = (f"{BASE}/sports/{sport_key}/odds?apiKey={self.api_key}"
               f"&regions=us,uk,eu&markets=h2h&oddsFormat=decimal")
        try:
            data = self.http.get_json(url, ttl=_TTL)
        except Exception as e:  # noqa: BLE001 — quota/network must not crash a predict
            logger.warning("odds_api: events fetch failed for %s (%s)", sport_key, e)
            return []
        return data if isinstance(data, list) else []

    def match_odds(self, home: str, away: str, comp: str) -> dict | None:
        """De-vigged consensus (p_home, p_draw, p_away) across all books for the
        fixture, with book count + source, or None if not found / no key."""
        sport_key = SPORT_KEY.get(comp)
        if not sport_key:
            return None
        want = {normalize_key(canon_club(home)), normalize_key(canon_club(away))}
        for ev in self.get_events(sport_key):
            eh, ea = ev.get("home_team"), ev.get("away_team")
            if {normalize_key(canon_club(eh or "")), normalize_key(canon_club(ea or ""))} != want:
                continue
            return _consensus(ev, eh, ea)
        return None


def _consensus(event: dict, event_home: str, event_away: str) -> dict | None:
    """Average the per-book de-vigged probabilities. Each book's three prices are
    normalized to strip its margin before averaging, so no book's vig leaks in."""
    sums = {"home": 0.0, "draw": 0.0, "away": 0.0}
    n_books = 0
    for book in event.get("bookmakers", []):
        market = next((m for m in book.get("markets", []) if m.get("key") == "h2h"), None)
        if not market:
            continue
        price = {}
        for o in market.get("outcomes", []):
            name, p = o.get("name"), o.get("price")
            if not p or p <= 1:
                continue
            if name == "Draw":
                price["draw"] = p
            elif name == event_home:
                price["home"] = p
            elif name == event_away:
                price["away"] = p
        if len(price) != 3:
            continue
        inv = {k: 1.0 / v for k, v in price.items()}
        total = sum(inv.values())
        for k in sums:
            sums[k] += inv[k] / total
        n_books += 1
    if not n_books:
        return None
    return {
        "p_home": round(sums["home"] / n_books, 3),
        "p_draw": round(sums["draw"] / n_books, 3),
        "p_away": round(sums["away"] / n_books, 3),
        "books": n_books,
        "source": "the-odds-api.com (de-vigged consensus)",
    }
