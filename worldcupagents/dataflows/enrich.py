"""Enrich a TeamProfile with form + xG from the match store (DATA_PLAN M1.3).

The data vendor gives squad/rank; the SQLite match store gives results (and xG
where a source provides it). This fills a profile's empty ``form`` and
``xg_for``/``xg_against`` so the advocate/judge briefs and scout reports reason
from real recent results. Graceful: no store / no matches → profile unchanged.
"""

from __future__ import annotations

from worldcupagents.agents.schemas import MatchResult, TeamProfile
from worldcupagents.dataflows.entities import same_team
from worldcupagents.dataflows.match_store import MatchStore, db_path


def _team_matches(team: str, config: dict) -> list[dict]:
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        rows = store.all_matches()
    finally:
        store.close()
    return [r for r in rows if same_team(team, r["home"], config=config) or same_team(team, r["away"], config=config)]


def enrich_profile(profile: TeamProfile, config: dict, n: int = 5) -> TeamProfile:
    rows = _team_matches(profile.team, config)

    # Season scoping: FORM is what the team did within the selected season
    # (string-comparable ISO dates; rows without dates are dropped from a
    # season-scoped view rather than guessed).
    season = config.get("season")
    if season and rows:
        from worldcupagents.seasons import season_range
        lo, hi = season_range(season)
        rows = [r for r in rows if r.get("date") and lo <= r["date"] <= hi]

    if not rows:
        if season:
            profile.form = []  # never show now-relative API form in a season view
        return profile

    rows = sorted(rows, key=lambda r: r.get("date") or "", reverse=True)

    results: list[MatchResult] = []
    xg_for: list[float] = []
    xg_against: list[float] = []
    for r in rows:
        is_home = same_team(profile.team, r["home"], config=config)
        gf, ga = (r["hg"], r["ag"]) if is_home else (r["ag"], r["hg"])
        opp = r["away"] if is_home else r["home"]
        results.append(MatchResult(opponent=opp, goals_for=gf, goals_against=ga,
                                   date=r.get("date") or None, source=r.get("source") or None))
        xf = r.get("xg_home") if is_home else r.get("xg_away")
        xa = r.get("xg_away") if is_home else r.get("xg_home")
        if xf is not None:
            xg_for.append(xf)
        if xa is not None:
            xg_against.append(xa)

    # When a season is selected, the season-scoped store OVERRIDES any form the
    # live API pre-filled — the API's "recent results" are now-relative and
    # would leak future matches into a historical view.
    if season or not profile.form:
        profile.form = results[:n]
    if profile.xg_for is None and xg_for:
        profile.xg_for = round(sum(xg_for) / len(xg_for), 2)
    if profile.xg_against is None and xg_against:
        profile.xg_against = round(sum(xg_against) / len(xg_against), 2)

    # Most-used XI by minutes (Understat, fetched via `fetch-data --xg`) — a
    # data-driven probable lineup. Graceful: absent unless season+comp scoped.
    comp = config.get("fd_competition")
    if season and comp and not profile.probable_xi:
        try:
            store = MatchStore.from_config(config)
            try:
                hit = store.team_xi(comp, season, profile.team)
            finally:
                store.close()
            if hit:
                xi, url = hit
                profile.probable_xi = [p["name"] for p in xi]
                if url not in profile.sources:
                    profile.sources.append(url)
        except Exception:  # noqa: BLE001 — XI hint must not break enrichment
            pass

    src = f"match_store:{len(rows)}"
    if src not in profile.sources:
        profile.sources.append(src)

    # Availability overlay: mark injured/suspended/doubt players and drop the
    # unavailable from the probable XI (no free injury feed → manual + punditry).
    try:
        from worldcupagents.dataflows.injuries import apply_injuries
        apply_injuries(profile, config)
    except Exception:  # noqa: BLE001 — availability overlay must not break enrichment
        pass
    return profile
