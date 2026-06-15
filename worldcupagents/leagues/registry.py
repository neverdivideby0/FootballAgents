"""League registry + ``apply_league`` (MULTILEAGUE_PLAN.md, M-ML1).

A ``League`` carries only the competition-specific differences. ``apply_league``
folds those into a run config so every command becomes league-aware by passing
``--league``. WC2026 is the default and reproduces today's config exactly — so
the World Cup path is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_LEAGUE_KEY = "WC2026"


@dataclass(frozen=True)
class League:
    key: str             # registry id, e.g. "WC2026", "PL"
    name: str            # human label
    kind: str            # "tournament" | "league"
    season: str          # "2026", "2025-26"
    fd_competition: str  # football-data.org code (WC/PL/PD/SA/BL1/FL1)
    has_knockouts: bool  # tournaments: True (draws fold, penalties). leagues: False
    neutral_venue: bool  # WC: True (no home edge). clubs: False


# The Big 5 + the World Cup. football-data.org free tier covers all of these.
_LEAGUES: dict[str, League] = {
    "WC2026": League("WC2026", "FIFA World Cup 2026", "tournament", "2026", "WC", True, True),
    "PL":  League("PL",  "Premier League 2025-26", "league", "2025-26", "PL",  False, False),
    "PD":  League("PD",  "La Liga 2025-26",        "league", "2025-26", "PD",  False, False),
    "SA":  League("SA",  "Serie A 2025-26",        "league", "2025-26", "SA",  False, False),
    "BL1": League("BL1", "Bundesliga 2025-26",     "league", "2025-26", "BL1", False, False),
    "FL1": League("FL1", "Ligue 1 2025-26",        "league", "2025-26", "FL1", False, False),
}


def list_leagues() -> list[League]:
    return list(_LEAGUES.values())


def get_league(key: str | None) -> League:
    """Resolve a league by key (case-insensitive). None/'' -> the default (WC)."""
    if not key:
        return _LEAGUES[DEFAULT_LEAGUE_KEY]
    up = key.strip().upper()
    # accept either the registry key or the raw fd_competition code
    for lg in _LEAGUES.values():
        if lg.key.upper() == up or lg.fd_competition.upper() == up:
            return lg
    raise ValueError(f"Unknown league {key!r}. Known: {', '.join(_LEAGUES)}")


def apply_league(config: dict, league: League) -> dict:
    """Fold league specifics into a run config (mutates and returns it).

    Per-league memory isolation is M-ML2; M-ML1 only sets the competition + flags,
    so applying WC2026 leaves today's config (fd_competition='WC') unchanged.
    """
    config["league"] = league.key
    config["fd_competition"] = league.fd_competition
    config["league_kind"] = league.kind
    config["has_knockouts"] = league.has_knockouts
    config["neutral_venue"] = league.neutral_venue
    # Default the season for club leagues (user --season overrides upstream);
    # tournaments keep season=None (no season scoping). league_current_season
    # lets downstream code detect a HISTORICAL selection (squads come from
    # Wikipedia then, not the live feed).
    config["league_current_season"] = league.season
    if league.kind == "league" and not config.get("season"):
        config["season"] = league.season
    # Per-league memory isolation: only when using the default memory root AND a
    # non-default league. Keeps WC (memory/) and explicit test dirs untouched, so
    # PL/La Liga/etc. each get memory/<key>/ without disturbing anything else.
    if league.key != DEFAULT_LEAGUE_KEY:
        from worldcupagents.config import DEFAULT_CONFIG  # lazy: avoid import cycles
        current = config.get("memory_dir", "memory")
        # "memory" kept for back-compat with configs built before path anchoring.
        if current in ("memory", DEFAULT_CONFIG["memory_dir"]):
            from pathlib import Path
            config["memory_dir"] = str(Path(current) / league.key)
    return config
