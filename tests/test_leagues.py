"""M-ML1 tests — League registry + apply_league (hermetic)."""

from __future__ import annotations

import copy

import pytest

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.leagues.registry import (
    DEFAULT_LEAGUE_KEY,
    apply_league,
    get_league,
    list_leagues,
)


def test_default_is_world_cup_tournament():
    assert DEFAULT_LEAGUE_KEY == "WC2026"
    wc = get_league(None)                       # None -> default
    assert wc.key == "WC2026" and wc.kind == "tournament"
    assert wc.has_knockouts and wc.neutral_venue and wc.fd_competition == "WC"


def test_registry_has_big_five_plus_wc():
    keys = {lg.key for lg in list_leagues()}
    assert keys == {"WC2026", "PL", "PD", "SA", "BL1", "FL1"}


def test_get_league_by_key_and_by_fd_code_case_insensitive():
    assert get_league("PL").name.startswith("Premier League")
    assert get_league("pl") is get_league("PL")          # case-insensitive
    assert get_league("WC").key == "WC2026"              # raw fd code resolves too


def test_leagues_are_not_knockout_and_not_neutral():
    pl = get_league("PL")
    assert pl.kind == "league" and not pl.has_knockouts and not pl.neutral_venue


def test_unknown_league_raises():
    with pytest.raises(ValueError):
        get_league("La Ligue Imaginaire")


def test_apply_world_cup_leaves_competition_unchanged():
    # Regression guard: applying the default WC league must NOT change the
    # competition the rest of the system already uses.
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    before = cfg["fd_competition"]
    apply_league(cfg, get_league("WC2026"))
    assert cfg["fd_competition"] == before == "WC"
    assert cfg["league"] == "WC2026"
    assert cfg["has_knockouts"] is True and cfg["neutral_venue"] is True


def test_apply_premier_league_sets_competition_and_flags():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    apply_league(cfg, get_league("PL"))
    assert cfg["fd_competition"] == "PL"
    assert cfg["league"] == "PL"
    assert cfg["has_knockouts"] is False and cfg["neutral_venue"] is False
