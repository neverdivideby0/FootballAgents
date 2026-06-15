"""Polymarket provider — real-money crowd probability (secondary signal).

A prediction MARKET, not a bookmaker: prices are the crowd's money-weighted
probability that an outcome happens. Philosophically distinct from de-vigged
bookmaker odds, and a nice cross-check for marquee fixtures (World Cup ties,
finals). Coverage is event-driven — most ordinary club games have NO market, so
this is best-effort and degrades to None quietly.

Public gamma API, no key. Read-only (we never trade — the project has no
real-money integration).
"""

from __future__ import annotations

import json
import logging

from worldcupagents.dataflows.club_aliases import canon_club
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.dataflows.names import normalize_key

logger = logging.getLogger(__name__)

BASE = "https://gamma-api.polymarket.com"
_TTL = 3_600


def _flat(s: str) -> str:
    return normalize_key(s or "").replace(" ", "")


def _bare(name: str) -> str:
    """Bare, space-free key with no FC/AFC suffix — Polymarket titles use
    'Arsenal', not 'Arsenal FC'."""
    k = _flat(canon_club(name or ""))
    for suffix in ("fc", "afc"):
        if k.endswith(suffix) and len(k) > len(suffix):
            k = k[: -len(suffix)]
    return k


class PolymarketProvider:
    name = "polymarket"

    def __init__(self, cache_dir: str = ".cache/polymarket", http=None):
        self.http = http or HTTPCache(cache_dir, min_interval=1.5)

    @classmethod
    def from_config(cls, config: dict) -> "PolymarketProvider":
        return cls(cache_dir=f"{config.get('cache_dir', '.cache')}/polymarket")

    def search_markets(self, query: str) -> list[dict]:
        url = f"{BASE}/markets?closed=false&active=true&limit=40&query={query}"
        try:
            data = self.http.get_json(url, ttl=_TTL)
        except Exception as e:  # noqa: BLE001
            logger.warning("polymarket: search failed for %r (%s)", query, e)
            return []
        return data if isinstance(data, list) else []

    def match_market(self, home: str, away: str) -> dict | None:
        """Crowd P(home win) for a fixture if a matching market exists, else None.
        Polymarket soccer markets are usually binary moneyline per team, so we
        report the crowd's home-win probability + the market title for provenance."""
        hk, ak = _bare(home), _bare(away)
        for q in (canon_club(home), canon_club(away)):
            for m in self.search_markets(q.split()[0] if q else q):
                title = (m.get("question") or m.get("title") or "")
                tk = _flat(title)
                if hk in tk and ak in tk:
                    p = _yes_price(m, hk)
                    if p is not None:
                        return {"p_home": round(p, 3), "title": title.strip(),
                                "source": f"polymarket.com ({title.strip()[:60]})"}
        return None


def _yes_price(market: dict, home_key: str) -> float | None:
    """Extract the crowd probability the home side wins. Markets store outcomes +
    outcomePrices as parallel (sometimes JSON-encoded) lists. ``home_key`` is the
    bare normalized home name (no FC suffix)."""
    try:
        outcomes = market.get("outcomes")
        prices = market.get("outcomePrices")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)
        if not outcomes or not prices or len(outcomes) != len(prices):
            return None
        for name, price in zip(outcomes, prices):
            n = _flat(str(name))
            if n == home_key or home_key in n:
                return float(price)
    except Exception as e:  # noqa: BLE001
        logger.warning("polymarket: price parse failed (%s)", e)
    return None
