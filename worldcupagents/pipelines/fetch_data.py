"""fetch-data (DATA_PLAN M1.1) — populate the SQLite match store.

Two ingesters into the same store:
  * football-data.org (the chosen live source) — finished competition matches.
    Pre-tournament this is ~0 rows (free tier is WC-scoped); it fills as 2026
    matches are played.
  * CSV seed (--from-csv) — historical results to fit strengths on TODAY, so the
    stats tier (M1.2) is demonstrable before the tournament. Reuses the same
    schema; the backtest sample works as a seed.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.football_data_org import FootballDataOrgProvider

logger = logging.getLogger(__name__)


def rows_from_csv(path: str | Path) -> list[dict]:
    out: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append({
                "date": r.get("date") or "",
                "comp": r.get("comp") or "seed",
                "home": r["home"], "away": r["away"],
                "hg": int(r["home_goals"]), "ag": int(r["away_goals"]),
                "xg_home": float(r["xg_home"]) if r.get("xg_home") else None,
                "xg_away": float(r["xg_away"]) if r.get("xg_away") else None,
                "source": r.get("source") or "csv",
            })
    return out


def rows_from_football_data_org(config: dict) -> list[dict]:
    try:
        provider = FootballDataOrgProvider.from_config(config)
        return provider.get_competition_matches()
    except Exception as e:  # noqa: BLE001 — missing token / network must not crash
        logger.warning("fetch-data: football-data.org unavailable (%s)", e)
        return []


def player_rows_from_football_data_org(config: dict) -> list[dict]:
    try:
        return FootballDataOrgProvider.from_config(config).get_scorers()
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch-data: scorers unavailable (%s)", e)
        return []


def player_rows(config: dict) -> tuple[list[dict], str]:
    """Pick the richest available player-stats source: API-Football (passing
    accuracy etc.) when a key is set, else the football-data.org scorers feed."""
    import os
    if os.environ.get("API_FOOTBALL_KEY"):
        try:
            from worldcupagents.dataflows.providers.api_football import ApiFootballProvider
            rows = ApiFootballProvider.from_config(config).get_scorers()
            if rows:
                return rows, "api_football"
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch-data: API-Football unavailable (%s); falling back", e)
    return player_rows_from_football_data_org(config), "football_data_org"


def rows_from_fdcouk(comp: str, seasons: list[str]) -> list[dict]:
    """Multi-season historical results from football-data.co.uk (no key)."""
    from worldcupagents.dataflows.providers.football_data_couk import fetch_season_rows
    out: list[dict] = []
    for s in seasons:
        out.extend(fetch_season_rows(comp, s))
    return out


def rows_from_api_football_national_history(
    config: dict,
    limit: int = 5,
    existing_rows: list[dict] | None = None,
) -> list[dict]:
    """Recent senior national-team results for every WC2026 team.

    API-Football's free tier is daily-request limited, so this is only run when
    explicitly requested from the CLI. It uses the WC2026 source-of-truth team
    list and folds rows into the existing ``matches`` table as comp='INT'.
    """
    from worldcupagents.dataflows.providers.api_football import ApiFootballProvider
    from worldcupagents.dataflows.names import canonical_name, normalize_key
    from worldcupagents.dataflows.world_cup_2026 import WC2026_TEAMS

    try:
        provider = ApiFootballProvider.from_config(config)
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch-data: API-Football national history unavailable (%s)", e)
        return []

    def same(a: str, b: str) -> bool:
        return (
            normalize_key(a) == normalize_key(b)
            or normalize_key(canonical_name(a)) == normalize_key(canonical_name(b))
        )

    existing_rows = existing_rows or []
    filled: set[str] = set()
    for team in WC2026_TEAMS:
        n = sum(
            1 for r in existing_rows
            if str(r.get("source") or "").startswith("api_football:national")
            and (same(r.get("home") or "", team) or same(r.get("away") or "", team))
        )
        if n >= limit:
            filled.add(team)

    rows: list[dict] = []
    seen: set[str] = set()
    for team in WC2026_TEAMS:
        if team in filled:
            continue
        for row in provider.get_recent_national_results(team, limit=limit):
            key = f"{row.get('date') or ''}|{row['home']}|{row['away']}"
            if key in seen:
                continue
            rows.append(row)
            seen.add(key)
    return rows


def fetch_understat_xg(config: dict, season: str | None = None) -> dict:
    """Pull Understat per-team data for the active competition: situation
    breakdowns (set pieces/corners/pens — punditry) into team_situations, and
    per-match xG filled onto EXISTING match rows. Returns counters."""
    from worldcupagents.dataflows.providers.understat import UnderstatProvider

    comp = config.get("fd_competition")
    season = season or config.get("season") or "2025-26"
    store = MatchStore.from_config(config)
    prov = UnderstatProvider.from_config(config)
    teams_updated = xg_updated = xi_updated = players_updated = 0
    try:
        from worldcupagents.seasons import season_range
        lo, hi = season_range(season)
        rows = [r for r in store.all_matches()
                if r.get("comp") == comp and r.get("date") and lo <= r["date"] <= hi]
        teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
        for team in teams:
            sit = prov.situations(team, season)
            xi = prov.probable_xi(team, season)
            if sit:
                store.upsert_situations(comp, season, team, sit[0], sit[1],
                                        xi=xi[0] if xi else None)
                teams_updated += 1
                if xi:
                    xi_updated += 1
            for m in prov.match_xg_rows(team, season):
                if store.update_xg(m["date"], m["home"], m["away"], m["xg_home"], m["xg_away"]):
                    xg_updated += 1
            # Per-player season metrics (shots, key passes, xG/xA, xGBuildup) —
            # same cached getTeamData call, fills the player-granularity gap.
            players_updated += store.upsert_players(prov.player_rows(team, season, comp))
    finally:
        store.close()
    return {"teams": teams_updated, "xg_rows": xg_updated, "xis": xi_updated,
            "players": players_updated, "season": season, "source": "understat"}


def fetch_data(
    config: dict | None = None,
    csv_path: str | Path | None = None,
    seasons: list[str] | None = None,
    national_history: bool = False,
    national_limit: int = 5,
) -> dict:
    config = dict(config or DEFAULT_CONFIG)
    store = MatchStore.from_config(config)
    players_added = 0
    players_source = None
    try:
        if national_history:
            rows, source = rows_from_api_football_national_history(
                config,
                limit=national_limit,
                existing_rows=store.all_matches(),
            ), "api_football_national"
        elif csv_path:
            rows, source = rows_from_csv(csv_path), "csv"
        elif seasons:
            rows, source = rows_from_fdcouk(config.get("fd_competition"), seasons), "fdcouk"
        else:
            rows, source = rows_from_football_data_org(config), "football_data_org"
            prows, players_source = player_rows(config)
            players_added = store.upsert_players(prows)
        added = store.upsert(rows)
        total = store.count()
    finally:
        store.close()
    return {"added": added, "total": total, "players": players_added,
            "players_source": players_source, "source": source}
