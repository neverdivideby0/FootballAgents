"""Market read — combine the bookmaker consensus and the crowd into one view.

The judge is shown this so it can argue WHERE and WHY its read should differ from
the market (the sharpest available prior). It is NOT mechanically blended into the
ensemble — keeping it a reasoning input, not a silent anchor, preserves the
"argue where it's wrong" behaviour and avoids double-counting.

Honesty guard: showing the market to the judge would make an LLM-lift eval
circular, so the evaluation harness fetches the market with ``enable_market_context``
forced off. Live predictions keep it on.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def market_read(config: dict, home: str, away: str, comp: str | None = None) -> dict | None:
    """{p_home, p_draw, p_away, books, sources[], crowd_home?} or None.

    Bookmaker consensus (The Odds API) is primary; Polymarket adds a crowd
    home-win probability when a market exists. Either source absent → that part
    is simply omitted; both absent → None."""
    if not config.get("enable_market_context", True):
        return None
    comp = comp or config.get("fd_competition")
    out: dict = {"sources": []}
    try:
        from worldcupagents.dataflows.providers.odds_api import OddsApiProvider
        book = OddsApiProvider.from_config(config).match_odds(home, away, comp)
        if book:
            out.update({k: book[k] for k in ("p_home", "p_draw", "p_away", "books")})
            out["sources"].append(book["source"])
    except Exception as e:  # noqa: BLE001 — market is optional colour, never fatal
        logger.warning("market_read: odds api failed (%s)", e)
    try:
        from worldcupagents.dataflows.providers.polymarket import PolymarketProvider
        crowd = PolymarketProvider.from_config(config).match_market(home, away)
        if crowd:
            out["crowd_home"] = crowd["p_home"]
            out["sources"].append(crowd["source"])
    except Exception as e:  # noqa: BLE001
        logger.warning("market_read: polymarket failed (%s)", e)
    return out if "p_home" in out or "crowd_home" in out else None


def market_digest(mr: dict) -> str:
    """One sourced line for an LLM prompt / report."""
    parts = []
    if "p_home" in mr:
        parts.append(f"bookmaker consensus H {mr['p_home']:.0%} / D {mr['p_draw']:.0%} / "
                     f"A {mr['p_away']:.0%} (de-vigged across {mr.get('books', '?')} books)")
    if "crowd_home" in mr:
        parts.append(f"Polymarket crowd: home win {mr['crowd_home']:.0%}")
    src = "; ".join(mr.get("sources", []))
    return "Market — " + "; ".join(parts) + (f" [source: {src}]" if src else "")


def divergence_note(verdict, mr: dict) -> str:
    """How the model's call differs from the bookmaker consensus — the heart of
    'argue where it's wrong'. '' when there's no bookmaker line to compare."""
    if "p_home" not in mr:
        return ""
    dh = verdict.p_home - mr["p_home"]
    da = verdict.p_away - mr["p_away"]
    # The bigger divergence on a decisive side is the story.
    if abs(dh) < 0.06 and abs(da) < 0.06:
        return f"In line with the market (consensus H {mr['p_home']:.0%} / A {mr['p_away']:.0%})."
    side, d = ("home", dh) if abs(dh) >= abs(da) else ("away", da)
    lean = "higher on" if d > 0 else "fading"
    team = "the home side" if side == "home" else "the away side"
    return (f"Model is {lean} {team} vs the market "
            f"(we say {getattr(verdict, 'p_' + side):.0%}, market "
            f"{mr['p_' + side]:.0%}, {abs(d):.0%} gap).")
