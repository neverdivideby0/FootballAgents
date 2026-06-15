"""API-Football (api-sports.io) provider.

Adds passing accuracy, key passes, minutes and rating on top of goals/assists —
the data football-data.org's scorers feed lacks. Free tier (~100 req/day); we use
the one-request-per-league ``/players/topscorers`` endpoint.

Also supplies recent senior national-team fixtures for WC2026 teams via
``/teams`` + a free-tier-friendly ``/fixtures?team=...&from=...&to=...`` date
range. This is intentionally folded into the same ``matches`` table as every
other result source.

Enable by putting a free key in .env as API_FOOTBALL_KEY; fetch-data then prefers
this source automatically. No key → the basic scorers feed is used instead.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from urllib.parse import quote_plus

from worldcupagents.dataflows.club_aliases import canon_club
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

BASE = "https://v3.football.api-sports.io"
# our competition code -> API-Football league id
LEAGUE_ID = {"PL": 39, "PD": 140, "SA": 135, "BL1": 78, "FL1": 61, "WC": 1}
_TTL = 86_400


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class ApiFootballProvider:
    name = "api_football"

    def __init__(self, api_key: str, competition: str, season: str, cache_dir: str = ".cache/api_football", http=None):
        if not api_key:
            raise ValueError("API-Football key required (set API_FOOTBALL_KEY)")
        self.api_key = api_key
        self.competition = competition
        self.season = season
        self.http = http or HTTPCache(cache_dir)

    @property
    def _headers(self) -> dict:
        return {"x-apisports-key": self.api_key}

    @classmethod
    def from_config(cls, config: dict) -> "ApiFootballProvider":
        return cls(
            os.environ.get("API_FOOTBALL_KEY", ""),
            competition=config.get("fd_competition", "PL"),
            season=str(config.get("api_football_season", "2025")),
            cache_dir=f"{config.get('cache_dir', '.cache')}/api_football",
        )

    def get_scorers(self) -> list[dict]:
        league = LEAGUE_ID.get(self.competition)
        if league is None:
            return []
        url = f"{BASE}/players/topscorers?league={league}&season={self.season}"
        try:
            data = self.http.get_json(url, headers=self._headers, ttl=_TTL)
        except Exception as e:  # noqa: BLE001
            logger.warning("API-Football scorers error (%s)", e)
            return []

        rows: list[dict] = []
        for item in data.get("response", []):
            p = item.get("player") or {}
            st = (item.get("statistics") or [{}])[0]
            goals, passes = st.get("goals") or {}, st.get("passes") or {}
            games, pen = st.get("games") or {}, st.get("penalty") or {}
            rows.append({
                "comp": self.competition,
                "player": p.get("name", "?"),
                "team": canon_club((st.get("team") or {}).get("name", "?")),
                "goals": goals.get("total") or 0,
                "assists": goals.get("assists") or 0,
                "penalties": pen.get("scored") or 0,
                "matches": games.get("appearences") or 0,
                "pass_accuracy": _f(passes.get("accuracy")),
                "key_passes": passes.get("key"),
                "minutes": games.get("minutes"),
                "rating": _f(games.get("rating")),
                "source": f"api_football:{self.competition}/topscorers",
            })
        return rows

    # --- senior national team recent results ---

    def _team_search_names(self, team: str) -> list[str]:
        names = [team, canonical_name(team)]
        extras = {
            "bosnia and herzegovina": ["Bosnia-Herzegovina", "Bosnia Herzegovina", "Bosnia & Herzegovina"],
            "united states": ["USA", "United States"],
            "korea republic": ["South Korea", "Korea Republic"],
            "côte d'ivoire": ["Ivory Coast", "Cote d'Ivoire"],
            "cabo verde": ["Cape Verde", "Cabo Verde", "Cape Verde Islands"],
            "dr congo": ["Congo DR", "DR Congo", "Congo"],
            "türkiye": ["Turkey", "Türkiye"],
        }
        names.extend(extras.get(normalize_key(canonical_name(team)), []))
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = normalize_key(name)
            if key and key not in seen:
                out.append(name)
                seen.add(key)
        return out

    def _wc_display_name(self, name: str) -> str:
        """Prefer the WC2026 source-of-truth display name when a fixture team is in it."""
        try:
            from worldcupagents.dataflows.world_cup_2026 import WC2026_TEAMS
            for team in WC2026_TEAMS:
                if normalize_key(name) == normalize_key(team):
                    return team
                if normalize_key(canonical_name(name)) == normalize_key(canonical_name(team)):
                    return team
        except Exception:  # noqa: BLE001
            pass
        return canonical_name(name)

    def resolve_national_team(self, team: str) -> tuple[int, str] | None:
        """Return ``(api_team_id, api_display_name)`` for a senior national team."""
        target_keys = {normalize_key(n) for n in self._team_search_names(team)}
        target_keys.add(normalize_key(canonical_name(team)))

        best: tuple[int, str, int] | None = None
        for search_name in self._team_search_names(team):
            url = f"{BASE}/teams?name={quote_plus(search_name)}"
            try:
                data = self.http.get_json(url, headers=self._headers, ttl=_TTL)
            except Exception as e:  # noqa: BLE001
                logger.warning("API-Football team search error for %r (%s)", team, e)
                continue
            for item in data.get("response", []):
                t = item.get("team") or {}
                team_id = t.get("id")
                api_name = t.get("name") or ""
                if not team_id:
                    continue
                api_keys = {normalize_key(api_name), normalize_key(canonical_name(api_name))}
                score = 0
                if t.get("national") is True:
                    score += 10
                if api_keys & target_keys:
                    score += 5
                country = normalize_key(item.get("country") or t.get("country") or "")
                if country and country in target_keys:
                    score += 2
                if best is None or score > best[2]:
                    best = (int(team_id), api_name or team, score)
            if best and best[2] >= 15:
                return best[0], best[1]
        if best and best[2] >= 10:
            return best[0], best[1]
        logger.warning("API-Football could not resolve national team %r", team)
        return None

    def get_recent_national_results(self, team: str, limit: int = 5) -> list[dict]:
        resolved = self.resolve_national_team(team)
        if not resolved:
            return []
        team_id, api_name = resolved
        to_date = date.today()
        from_date = to_date - timedelta(days=365 * 4)
        # The API-Football free plan rejects the `last` parameter, so request a
        # broad date range and take the most recent finished fixtures locally.
        url = (
            f"{BASE}/fixtures?team={team_id}&season={self.season}"
            f"&from={from_date.isoformat()}&to={to_date.isoformat()}"
        )
        try:
            data = self.http.get_json(url, headers=self._headers, ttl=_TTL)
        except Exception as e:  # noqa: BLE001
            logger.warning("API-Football fixtures error for %r (%s)", team, e)
            return []

        rows: list[dict] = []
        items = sorted(
            data.get("response", []),
            key=lambda item: ((item.get("fixture") or {}).get("date") or ""),
            reverse=True,
        )
        for item in items:
            teams = item.get("teams") or {}
            goals = item.get("goals") or {}
            fixture = item.get("fixture") or {}
            home = (teams.get("home") or {}).get("name")
            away = (teams.get("away") or {}).get("name")
            hg, ag = goals.get("home"), goals.get("away")
            if not home or not away or hg is None or ag is None:
                continue
            match_date = (fixture.get("date") or "")[:10] or None
            comp = ((item.get("league") or {}).get("name") or "INT").strip()
            rows.append({
                "date": match_date,
                "comp": "INT",
                "home": self._wc_display_name(home),
                "away": self._wc_display_name(away),
                "hg": int(hg),
                "ag": int(ag),
                "xg_home": None,
                "xg_away": None,
                "source": f"api_football:national:{api_name}:last{limit}:{comp}",
            })
            if len(rows) >= limit:
                break
        return rows
