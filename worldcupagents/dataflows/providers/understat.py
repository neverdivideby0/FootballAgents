"""Understat provider — shot-situation punditry + per-match xG (scrape-based).

Allowed under the relaxed scraping rule (public pages, polite rate-limit via
HTTPCache, provenance attached). Endpoint verified live: the team page's JS does
``GET understat.com/getTeamData/{Team}/{YYYY}`` returning JSON:

  * statistics.situation — shots/goals/xG for OpenPlay, FromCorner, SetPiece,
    DirectFreekick, Penalty (for AND against)  → the punditry signal
    ("Arsenal score from set pieces") stored per team-season.
  * dates — per-match xG/xGA for the whole season → fills the match store's
    empty xg_home/xg_away columns (the explorer's #1 gap).

Covers the big-5 leagues. Season form: start year ("2025-26" -> "2025").
"""

from __future__ import annotations

import logging

from worldcupagents.dataflows.club_aliases import canon_club, understat_name
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.seasons import normalize_season

logger = logging.getLogger(__name__)

BASE = "https://understat.com"
_TTL = 21_600  # 6h — in-season numbers move
_UA = {"User-Agent": "FootballAgents/0.2 (personal research tool)",
       "X-Requested-With": "XMLHttpRequest"}

# Friendly labels for the punditry digest, in display order.
_SITU_LABELS = [
    ("FromCorner", "corners"),
    ("SetPiece", "set pieces"),
    ("DirectFreekick", "direct FKs"),
    ("Penalty", "penalties"),
    ("OpenPlay", "open play"),
]


class UnderstatProvider:
    name = "understat"

    def __init__(self, cache_dir: str = ".cache/understat", http=None):
        self.http = http or HTTPCache(cache_dir, min_interval=1.5)  # polite, not API-slow

    @classmethod
    def from_config(cls, config: dict) -> "UnderstatProvider":
        return cls(cache_dir=f"{config.get('cache_dir', '.cache')}/understat")

    def get_team_data(self, team: str, season: str) -> dict | None:
        """Raw {dates, statistics, players} for a canonical club name, or None."""
        u_name = understat_name(team).replace(" ", "_")
        year = normalize_season(season)[:4]
        url = f"{BASE}/getTeamData/{u_name}/{year}"
        try:
            data = self.http.get_json(url, headers=_UA, ttl=_TTL)
        except Exception as e:  # noqa: BLE001 — scraping must never crash a fetch
            logger.warning("understat: getTeamData failed for %s %s (%s)", team, season, e)
            return None
        return data if isinstance(data, dict) and "statistics" in data else None

    def situations(self, team: str, season: str) -> tuple[dict, str] | None:
        """(situation breakdown, source URL) or None."""
        data = self.get_team_data(team, season)
        if not data:
            return None
        sit = (data.get("statistics") or {}).get("situation") or {}
        if not sit:
            return None
        url = f"{BASE}/team/{understat_name(team).replace(' ', '_')}/{normalize_season(season)[:4]}"
        return sit, url

    def probable_xi(self, team: str, season: str) -> tuple[list[dict], str] | None:
        """Most-used XI by minutes (a data-driven probable lineup), with source URL."""
        data = self.get_team_data(team, season)
        if not data or not data.get("players"):
            return None
        xi = parse_xi(data["players"])
        if not xi:
            return None
        url = f"{BASE}/team/{understat_name(team).replace(' ', '_')}/{normalize_season(season)[:4]}"
        return xi, url

    def player_rows(self, team: str, season: str, comp: str) -> list[dict]:
        """Per-player season metrics → match-store player_stats rows. The granular
        layer FBref/FotMob won't serve (both bot-blocked): shots, key passes,
        xG/xA, and xGBuildup (involvement in possession chains minus shots/assists
        — a possession-circuit signal). Already part of the cached getTeamData call."""
        data = self.get_team_data(team, season)
        if not data or not data.get("players"):
            return []
        url = f"{BASE}/team/{understat_name(team).replace(' ', '_')}/{normalize_season(season)[:4]}"

        def num(v, cast=int):
            try:
                return cast(float(v))
            except (TypeError, ValueError):
                return None

        rows = []
        for p in data["players"]:
            if not p.get("player_name"):
                continue
            rows.append({
                "comp": comp,
                "player": p["player_name"],
                "team": team,
                "goals": num(p.get("goals")),
                "assists": num(p.get("assists")),
                "penalties": (num(p.get("goals")) or 0) - (num(p.get("npg")) or 0),
                "matches": num(p.get("games")),
                "key_passes": num(p.get("key_passes")),
                "minutes": num(p.get("time")),
                "shots": num(p.get("shots")),
                "xg": num(p.get("xG"), float),
                "xa": num(p.get("xA"), float),
                "xg_buildup": num(p.get("xGBuildup"), float),
                "source": url,
            })
        return rows

    def match_xg_rows(self, team: str, season: str) -> list[dict]:
        """Per-match xG rows: {date, home, away, xg_home, xg_away} with CANONICAL
        team names, ready to update the match store."""
        data = self.get_team_data(team, season)
        if not data:
            return []
        out = []
        for m in data.get("dates") or []:
            xg = m.get("xG") or {}
            if not m.get("datetime") or xg.get("h") in (None, "") or xg.get("a") in (None, ""):
                continue  # unplayed fixture
            out.append({
                "date": m["datetime"][:10],
                "home": canon_club((m.get("h") or {}).get("title", "")),
                "away": canon_club((m.get("a") or {}).get("title", "")),
                "xg_home": round(float(xg["h"]), 2),
                "xg_away": round(float(xg["a"]), 2),
            })
        return out


def _role(pos: str | None) -> str:
    pos = (pos or "").upper()
    if "GK" in pos or pos.startswith("G"):
        return "GK"
    return {"D": "DEF", "M": "MID", "F": "FWD"}.get(pos.strip()[:1], "?")


def parse_xi(players: list[dict], n: int = 11) -> list[dict]:
    """Most-used XI from Understat playersData: the top GK + top 10 outfielders by
    minutes. A data-driven probable lineup (NOT a leaked teamsheet — labelled as such)."""
    def mins(p: dict) -> int:
        try:
            return int(p.get("time", 0) or 0)
        except (TypeError, ValueError):
            return 0

    gks = sorted((p for p in players if _role(p.get("position")) == "GK"), key=mins, reverse=True)
    outfield = sorted((p for p in players if _role(p.get("position")) != "GK"), key=mins, reverse=True)
    chosen = gks[:1] + outfield[: n - 1]
    return [{
        "name": p.get("player_name", "?"),
        "pos": _role(p.get("position")),
        "minutes": mins(p),
        "goals": int(p.get("goals", 0) or 0),
        "assists": int(p.get("assists", 0) or 0),
    } for p in chosen]


def xi_digest(xi: list[dict]) -> str:
    """'GK Raya; DEF Saliba, Gabriel…' grouped by role."""
    groups: dict[str, list[str]] = {}
    for p in xi:
        groups.setdefault(p["pos"], []).append(p["name"])
    order = ["GK", "DEF", "MID", "FWD", "?"]
    return "; ".join(f"{r} {', '.join(groups[r])}" for r in order if groups.get(r))


def situations_digest(sit: dict, team: str) -> str:
    """One punditry line: how the team scores and concedes, by situation."""
    parts, against = [], []
    for key, label in _SITU_LABELS:
        s = sit.get(key) or {}
        if s.get("goals") is not None:
            parts.append(f"{s['goals']} from {label} (xG {float(s.get('xG', 0)):.1f})")
        a = (s.get("against") or {})
        if a.get("goals"):
            against.append(f"{a['goals']} from {label}")
    line = f"{team} goals: " + ", ".join(parts) if parts else ""
    if against:
        line += f". Conceded: {', '.join(against)}"
    return line
