"""football-data.org (v4) provider — free-tier live data for TeamProfile.

Supplies real squads and recent results. FIFA rank comes from the curated
``fifa_rankings`` table (the feed has none). Needs a free token in
FOOTBALL_DATA_ORG_TOKEN. Falls back to a minimal profile (never crashes a
prediction) when a team can't be resolved in the competition feed.

Free-tier notes: ~10 req/min (handled by HTTPCache), limited competitions.
``competition`` defaults to "WC" (FIFA World Cup); override via WCA_FD_COMPETITION.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

from worldcupagents.agents.schemas import MatchResult, Player, TeamProfile
from worldcupagents.dataflows import fifa_rankings
from worldcupagents.dataflows.http_cache import HTTPCache
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

BASE = "https://api.football-data.org/v4"
_SQUAD_TTL = 86_400   # 24h
_MATCH_TTL = 21_600   # 6h


class FootballDataOrgProvider:
    name = "football_data_org"

    def __init__(self, token: str, competition: str = "WC",
                 cache_dir: str = ".cache/football_data_org", http=None):
        if not token:
            raise ValueError("football-data.org token required (set FOOTBALL_DATA_ORG_TOKEN)")
        self.token = token
        self.competition = competition
        self.http = http or HTTPCache(cache_dir)
        self._team_index: dict | None = None

    @classmethod
    def from_config(cls, config: dict) -> "FootballDataOrgProvider":
        token = os.environ.get("FOOTBALL_DATA_ORG_TOKEN", "")
        cache_dir = f"{config.get('cache_dir', '.cache')}/football_data_org"
        return cls(token, competition=config.get("fd_competition", "WC"), cache_dir=cache_dir)

    # --- internals ---

    @property
    def _headers(self) -> dict:
        return {"X-Auth-Token": self.token}

    def _teams(self) -> dict:
        """name-key -> team dict for the competition (fetched once, cached)."""
        if self._team_index is None:
            data = self.http.get_json(
                f"{BASE}/competitions/{self.competition}/teams", self._headers, ttl=_SQUAD_TTL
            )
            self._team_index = {normalize_key(t["name"]): t for t in data.get("teams", [])}
        return self._team_index

    def _resolve(self, team: str) -> dict | None:
        idx = self._teams()
        for key in (normalize_key(canonical_name(team)), normalize_key(team)):
            if key in idx:
                return idx[key]
        # last resort: substring match (handles "United States" vs "USA" feeds)
        probe = normalize_key(canonical_name(team))
        for key, t in idx.items():
            if probe and (probe in key or key in probe):
                return t
        return None

    def _minimal(self, team: str, reason: str) -> TeamProfile:
        """Degrade to a placeholder-grade profile without crashing the prediction."""
        return TeamProfile(
            team=canonical_name(team),
            fifa_rank=fifa_rankings.get_rank(team),
            style=f"(football-data.org: {reason})",
            sources=[f"football_data_org:{self.competition}:{reason}"],
            last_updated=datetime.now(timezone.utc),
        )

    # --- public contract ---

    def get_team_profile(self, team: str) -> TeamProfile:
        try:
            t = self._resolve(team)
            if t is None:
                logger.warning("football-data.org: %r not in '%s' feed; minimal profile", team, self.competition)
                return self._minimal(team, "not_found")

            # The competition teams list (already fetched + cached for name
            # resolution) carries each team's FULL squad — and unlike the
            # standalone /teams/{id} endpoint, it is never per-team restricted
            # on the free tier (e.g. Wolves' detail endpoint 403s while the
            # PL feed serves their 30-man squad fine). Zero extra API calls.
            squad = [
                Player(name=p.get("name", "?"), position=p.get("position"), status="fit")
                for p in t.get("squad", [])
            ]
            coach = (t.get("coach") or {}).get("name")

            if not squad or not coach:
                # Best-effort enrichment only — a 403 here must not cost us the profile.
                try:
                    detail = self.http.get_json(f"{BASE}/teams/{t['id']}", self._headers, ttl=_SQUAD_TTL)
                    if not squad:
                        squad = [
                            Player(name=p.get("name", "?"), position=p.get("position"), status="fit")
                            for p in detail.get("squad", [])
                        ]
                    coach = coach or (detail.get("coach") or {}).get("name")
                except Exception as e:  # noqa: BLE001 — restricted team detail (free-tier quirk)
                    logger.info("football-data.org team detail unavailable for %r (%s); "
                                "using competition-feed data", team, e)

            return TeamProfile(
                team=t.get("name", canonical_name(team)),
                fifa_rank=fifa_rankings.get_rank(team),
                squad=squad,
                coach=coach or None,
                style=f"coach: {coach}" if coach else "",
                form=self.get_recent_results(team, 5),
                tournament_pedigree=(t.get("area") or {}).get("name", ""),
                sources=[f"football_data_org:{self.competition}/teams#{t['id']}"],
                last_updated=datetime.now(timezone.utc),
            )
        except Exception as e:  # noqa: BLE001 — bad token / network / rate limit shouldn't crash predict
            logger.warning("football-data.org error for %r (%s); minimal profile", team, e)
            return self._minimal(team, "error")

    def get_recent_results(self, team: str, n: int = 5) -> list[MatchResult]:
        try:
            t = self._resolve(team)
            if t is None:
                return []
            data = self.http.get_json(
                f"{BASE}/teams/{t['id']}/matches?status=FINISHED&limit={n}", self._headers, ttl=_MATCH_TTL
            )
        except httpx.HTTPStatusError as e:
            # Per-team endpoints are restricted for some clubs on the free tier
            # (expected); form still comes from the local match store.
            logger.info("football-data.org results restricted for %r (%s); using match store", team, e)
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("football-data.org results error for %r (%s)", team, e)
            return []
        out: list[MatchResult] = []
        team_key = normalize_key(t["name"])
        for m in data.get("matches", []):
            home, away = m.get("homeTeam", {}), m.get("awayTeam", {})
            score = (m.get("score", {}) or {}).get("fullTime", {}) or {}
            is_home = normalize_key(home.get("name", "")) == team_key
            gf = score.get("home") if is_home else score.get("away")
            ga = score.get("away") if is_home else score.get("home")
            if gf is None or ga is None:
                continue
            out.append(MatchResult(
                opponent=(away if is_home else home).get("name", "?"),
                goals_for=gf, goals_against=ga,
                date=(m.get("utcDate") or "")[:10] or None,
            ))
        return out

    def get_head_to_head(self, home: str, away: str) -> list[MatchResult]:
        away_key = normalize_key(canonical_name(away))
        return [r for r in self.get_recent_results(home, 50) if normalize_key(r.opponent) == away_key]

    def get_scorers(self, limit: int = 50) -> list[dict]:
        """Top scorers (per-player goals/assists/penalties/appearances) as player-stat
        rows. Empty on error. Free tier returns the competition's leading scorers."""
        try:
            data = self.http.get_json(
                f"{BASE}/competitions/{self.competition}/scorers?limit={limit}",
                self._headers, ttl=_MATCH_TTL,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("football-data.org scorers error (%s)", e)
            return []
        rows: list[dict] = []
        for s in data.get("scorers", []):
            p, t = s.get("player") or {}, s.get("team") or {}
            rows.append({
                "comp": self.competition,
                "player": p.get("name", "?"),
                "team": t.get("name", "?"),
                "goals": s.get("goals") or 0,
                "assists": s.get("assists") or 0,
                "penalties": s.get("penalties") or 0,
                "matches": s.get("playedMatches") or 0,
                "source": f"football_data_org:{self.competition}/scorers",
            })
        return rows

    def get_competition_matches(self) -> list[dict]:
        """Finished matches in the competition, as match-store rows. Empty on error
        or pre-tournament (free tier returns 0 finished WC matches before kickoff)."""
        try:
            data = self.http.get_json(
                f"{BASE}/competitions/{self.competition}/matches?status=FINISHED",
                self._headers, ttl=_MATCH_TTL,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("football-data.org competition matches error (%s)", e)
            return []
        rows: list[dict] = []
        for m in data.get("matches", []):
            score = (m.get("score", {}) or {}).get("fullTime", {}) or {}
            hg, ag = score.get("home"), score.get("away")
            if hg is None or ag is None:
                continue
            rows.append({
                "date": (m.get("utcDate") or "")[:10] or None,
                "comp": self.competition,
                "home": (m.get("homeTeam") or {}).get("name", "?"),
                "away": (m.get("awayTeam") or {}).get("name", "?"),
                "hg": hg, "ag": ag, "xg_home": None, "xg_away": None,
                "source": f"football_data_org:{self.competition}",
            })
        return rows
