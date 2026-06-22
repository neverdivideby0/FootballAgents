"""Scout nodes — collapsed to 2 for the v1 MVP (decision in PROJECT_OUTLINE §13).

Split into the full form/squad/tactics/news/H2H scouts as quality demands.
"""

from __future__ import annotations

from worldcupagents.dataflows.enrich import enrich_profile
from worldcupagents.dataflows.interface import get_provider
from worldcupagents.dataflows.records import records_summary
from worldcupagents.graph.state import MatchState


def make_build_dossiers(config: dict):
    """Builds both TeamProfiles from the configured data vendor, enriched with
    form/xG from the match store (M1.3). For a HISTORICAL season selection the
    squad is swapped for that season's squad from Wikipedia (WS-C)."""
    provider = get_provider(config, "squads")

    def build_dossiers(state: MatchState) -> dict:
        fx = state["fixture"]
        home = enrich_profile(provider.get_team_profile(fx.home), config)
        away = enrich_profile(provider.get_team_profile(fx.away), config)
        if _is_historical_season(config):
            home = _historical_squad(home, config)
            away = _historical_squad(away, config)
        return {"home_profile": home, "away_profile": away}

    return build_dossiers


def _is_historical_season(config: dict) -> bool:
    season, current = config.get("season"), config.get("league_current_season")
    return bool(season and current and season != current and config.get("league_kind") == "league")


def _historical_squad(profile, config: dict):
    """Swap in the selected season's squad from Wikipedia (graceful: keep the
    current squad with a note if the page can't be fetched/parsed)."""
    season = config["season"]
    try:
        from worldcupagents.dataflows.providers.wikipedia_squads import WikipediaSquadsProvider
        players, url = WikipediaSquadsProvider.from_config(config).get_season_squad(profile.team, season)
    except Exception as e:  # noqa: BLE001 — history must not break predict
        players, url = [], None
        import logging
        logging.getLogger(__name__).warning("historical squad lookup failed for %s (%s)", profile.team, e)
    if players:
        profile.squad = players
        profile.style = f"{profile.style} [{season} squad]".strip()
        profile.sources.append(url or f"wikipedia:{season}")
    else:
        profile.style = f"{profile.style} [WARNING: {season} squad unavailable; showing current]".strip()
    return profile


def make_matchup_context(config: dict):
    """Assembles venue / stakes / head-to-head context for the debate + judge."""
    provider = get_provider(config, "results")

    def matchup_context(state: MatchState) -> dict:
        fx = state["fixture"]
        # Venue framing: a real venue if given; else a club fixture is a HOME game
        # for the home side (home advantage applies), while a neutral-venue
        # competition (the World Cup) has no home edge.
        if fx.venue:
            venue_note = fx.venue
        elif config.get("neutral_venue", True):
            venue_note = "neutral venue (no home advantage)"
        else:
            venue_note = f"{fx.home}'s home ground (home advantage applies)"

        # Leagues have no group stage — label the fixture honestly for the prompts.
        is_league = config.get("league_kind") == "league"
        ctx = {
            "stage": fx.stage.value,
            "stage_label": "league match" if is_league else fx.stage.value,
            "knockout": fx.knockout,
            "venue": fx.venue,
            "venue_note": venue_note,
            "head_to_head": [r.model_dump() for r in provider.get_head_to_head(fx.home, fx.away)],
            "records": records_summary(fx.home, fx.away, config),  # home + H2H-at-home from store
            "notes": "",
        }
        # Bilateral data-parity: flag a lopsided dossier so the judge/advocates don't
        # mistake thinner coverage for lower quality (the "single-team scout" failure).
        hp, ap = state.get("home_profile"), state.get("away_profile")
        if hp and ap:
            try:
                from worldcupagents.ensemble.parity import parity_note
                note = parity_note(hp, ap, config)
                if note:
                    ctx["parity"] = note
            except Exception:  # noqa: BLE001 — parity is optional
                pass
        # Live market (bookmaker consensus + crowd) — only worth a network call
        # when an LLM will actually reason about it; eval forces the flag off.
        if config.get("use_llm") and config.get("enable_market_context", True):
            try:
                from worldcupagents.dataflows.market import market_read
                mr = market_read(config, fx.home, fx.away)
                if mr:
                    ctx["market"] = mr
            except Exception:  # noqa: BLE001 — market is optional
                pass
        return {"matchup_context": ctx}

    return matchup_context
