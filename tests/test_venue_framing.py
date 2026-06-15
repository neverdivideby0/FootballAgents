"""M-ML4 — venue/home-advantage framing in the matchup context (hermetic)."""

from __future__ import annotations

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.agents.scouts.dossier import make_matchup_context


def _ctx(config, home="Chelsea FC", away="Leeds United FC", venue=None):
    node = make_matchup_context(config)
    out = node({"fixture": Fixture(home=home, away=away, stage=Stage.GROUP, venue=venue)})
    return out["matchup_context"]


def test_club_fixture_frames_home_advantage():
    # neutral_venue False (a league) and no explicit venue -> home game for the home side.
    ctx = _ctx({"neutral_venue": False, "data_vendors": {"results": "placeholder"}})
    assert ctx["venue_note"] == "Chelsea FC's home ground (home advantage applies)"


def test_tournament_frames_neutral_venue():
    # neutral_venue True (the World Cup) -> no home edge.
    ctx = _ctx({"neutral_venue": True, "data_vendors": {"results": "placeholder"}})
    assert ctx["venue_note"] == "neutral venue (no home advantage)"


def test_explicit_venue_wins():
    ctx = _ctx({"neutral_venue": True, "data_vendors": {"results": "placeholder"}}, venue="Wembley")
    assert ctx["venue_note"] == "Wembley"


def test_default_is_neutral_when_flag_absent():
    # No neutral_venue key (e.g. a bare config) -> defaults to neutral (WC-safe).
    ctx = _ctx({"data_vendors": {"results": "placeholder"}})
    assert ctx["venue_note"] == "neutral venue (no home advantage)"
