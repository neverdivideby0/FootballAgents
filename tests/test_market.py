"""Live market: Odds API de-vig consensus, Polymarket crowd, assembly, judge
injection, divergence (hermetic — injected fake HTTP, no network/key)."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG


class _FakeHTTP:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, headers=None, ttl=0):
        return self.payload


# ── The Odds API ──────────────────────────────────────────────────────────────

_ODDS_EVENT = [{
    "home_team": "Arsenal", "away_team": "Liverpool",
    "bookmakers": [
        {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Arsenal", "price": 2.00}, {"name": "Liverpool", "price": 4.00},
            {"name": "Draw", "price": 4.00}]}]},
        {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Arsenal", "price": 2.10}, {"name": "Liverpool", "price": 3.80},
            {"name": "Draw", "price": 3.90}]}]},
    ],
}]


def test_odds_api_devigs_and_averages():
    from worldcupagents.dataflows.providers.odds_api import OddsApiProvider
    prov = OddsApiProvider(api_key="x", http=_FakeHTTP(_ODDS_EVENT))
    mr = prov.match_odds("Arsenal FC", "Liverpool FC", "PL")
    assert mr is not None and mr["books"] == 2
    assert abs(mr["p_home"] + mr["p_draw"] + mr["p_away"] - 1.0) < 1e-6
    # Book 1 (2.00/4.00/4.00) de-vigs to 0.50/0.25/0.25; book 2 ~0.475/0.243/0.281.
    assert 0.47 < mr["p_home"] < 0.50 and mr["p_home"] > mr["p_away"]
    assert "the-odds-api" in mr["source"]


def test_odds_api_no_key_returns_none():
    from worldcupagents.dataflows.providers.odds_api import OddsApiProvider
    prov = OddsApiProvider(api_key="", http=_FakeHTTP(_ODDS_EVENT))
    assert prov.match_odds("Arsenal FC", "Liverpool FC", "PL") is None


def test_odds_api_unknown_fixture_returns_none():
    from worldcupagents.dataflows.providers.odds_api import OddsApiProvider
    prov = OddsApiProvider(api_key="x", http=_FakeHTTP(_ODDS_EVENT))
    assert prov.match_odds("Chelsea FC", "Everton FC", "PL") is None


# ── Polymarket ────────────────────────────────────────────────────────────────

def test_polymarket_extracts_home_crowd_price():
    from worldcupagents.dataflows.providers.polymarket import PolymarketProvider
    payload = [{"question": "Arsenal vs. Liverpool", "active": True,
                "outcomes": '["Arsenal", "Liverpool"]', "outcomePrices": '["0.58", "0.42"]'}]
    prov = PolymarketProvider(http=_FakeHTTP(payload))
    mr = prov.match_market("Arsenal FC", "Liverpool FC")
    assert mr is not None and mr["p_home"] == 0.58
    assert "polymarket" in mr["source"]


def test_polymarket_absent_market_returns_none():
    from worldcupagents.dataflows.providers.polymarket import PolymarketProvider
    prov = PolymarketProvider(http=_FakeHTTP([]))
    assert prov.match_market("Arsenal FC", "Liverpool FC") is None


# ── assembly + digest + divergence ──────────────────────────────────────────

def _cfg() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def test_market_read_combines_sources(monkeypatch):
    import worldcupagents.dataflows.providers.odds_api as oa
    import worldcupagents.dataflows.providers.polymarket as pm
    monkeypatch.setattr(oa.OddsApiProvider, "from_config",
                        classmethod(lambda cls, c: oa.OddsApiProvider(api_key="x", http=_FakeHTTP(_ODDS_EVENT))))
    monkeypatch.setattr(pm.PolymarketProvider, "from_config",
                        classmethod(lambda cls, c: pm.PolymarketProvider(http=_FakeHTTP(
                            [{"question": "Arsenal vs. Liverpool",
                              "outcomes": '["Arsenal","Liverpool"]', "outcomePrices": '["0.55","0.45"]'}]))))
    from worldcupagents.dataflows.market import market_digest, market_read
    cfg = _cfg(); cfg["fd_competition"] = "PL"
    mr = market_read(cfg, "Arsenal FC", "Liverpool FC")
    assert "p_home" in mr and mr["crowd_home"] == 0.55
    d = market_digest(mr)
    assert "bookmaker consensus" in d and "Polymarket crowd" in d


def test_market_context_flag_off_returns_none():
    from worldcupagents.dataflows.market import market_read
    cfg = _cfg(); cfg["enable_market_context"] = False
    assert market_read(cfg, "Arsenal FC", "Liverpool FC") is None


def test_divergence_note_flags_fade():
    from worldcupagents.agents.schemas import MatchVerdict, Outcome
    from worldcupagents.dataflows.market import divergence_note
    v = MatchVerdict(outcome=Outcome.AWAY_WIN, p_home=0.30, p_draw=0.25, p_away=0.45, scoreline="1-2")
    mr = {"p_home": 0.55, "p_draw": 0.25, "p_away": 0.20, "books": 5}
    note = divergence_note(v, mr)
    assert "fading" in note and "home" in note            # model 30% vs market 55%
    agree = divergence_note(
        MatchVerdict(outcome=Outcome.HOME_WIN, p_home=0.54, p_draw=0.25, p_away=0.21, scoreline="2-1"),
        {"p_home": 0.55, "p_draw": 0.25, "p_away": 0.20, "books": 5})
    assert "In line with the market" in agree


def test_judge_prompt_includes_market(monkeypatch):
    """When the matchup context carries a market, the judge prompt shows it."""
    from worldcupagents.agents.judge import pundit
    from worldcupagents.agents.schemas import Fixture, Stage, TeamProfile

    captured = {}

    class _LLM:
        def with_structured_output(self, schema, include_raw=False):
            return self
        def invoke(self, prompt):
            captured["prompt"] = prompt
            from worldcupagents.agents.schemas import JudgeRead
            return {"raw": None, "parsed": JudgeRead(p_home=0.5, p_draw=0.3, p_away=0.2,
                    scoreline="1-0", confidence="medium", key_factors=[], x_factors=[], rationale="x")}

    state = {
        "fixture": Fixture(home="Arsenal FC", away="Liverpool FC", stage=Stage.GROUP),
        "home_profile": TeamProfile(team="Arsenal FC"), "away_profile": TeamProfile(team="Liverpool FC"),
        "matchup_context": {"market": {"p_home": 0.58, "p_draw": 0.24, "p_away": 0.18, "books": 7,
                                       "sources": ["the-odds-api.com (de-vigged consensus)"]}},
        "debate_state": {"history": ""},
    }
    pundit._llm_judge_read(_LLM(), state, None, {"use_llm": True})
    assert "LIVE MARKET" in captured["prompt"] and "58%" in captured["prompt"]
    assert "where and why it is wrong" in captured["prompt"]
