"""WS-C tests — season utilities, scoping, and the Wikipedia squad parser (hermetic)."""

from __future__ import annotations

import copy

import pytest

from worldcupagents.agents.schemas import TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.enrich import enrich_profile
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.providers.wikipedia_squads import (
    WikipediaSquadsProvider,
    parse_squad_wikitext,
)
from worldcupagents.dataflows.records import h2h_home_record
from worldcupagents.ensemble.strength import load_strength_model
from worldcupagents.leagues.registry import apply_league, get_league
from worldcupagents.seasons import (
    normalize_season,
    season_cutoff,
    season_dash,
    season_range,
    season_to_fdcouk,
)


# ── season utilities ─────────────────────────────────────────────────────────

def test_normalize_accepts_many_spellings():
    assert normalize_season("2025-26") == "2025-26"
    assert normalize_season("2025–26") == "2025-26"   # en-dash
    assert normalize_season("2025/26") == "2025-26"
    assert normalize_season("2526") == "2025-26"      # fdcouk code
    assert normalize_season("2025") == "2025-26"


def test_normalize_rejects_nonsense():
    with pytest.raises(ValueError):
        normalize_season("2025-27")                   # non-consecutive
    with pytest.raises(ValueError):
        normalize_season("banana")


def test_range_cutoff_and_codes():
    assert season_range("2025-26") == ("2025-07-01", "2026-06-30")
    assert season_cutoff("2023-24") == "2024-06-30"
    assert season_to_fdcouk("2025-26") == "2526"
    assert season_dash("2025-26") == "2025–26"


# ── season scoping of the data layer ─────────────────────────────────────────

def _seed(tmp_path):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert([
        {"date": "2023-10-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC",
         "hg": 3, "ag": 0, "xg_home": None, "xg_away": None, "source": "fdcouk:PL:2324"},
        {"date": "2025-09-01", "comp": "PL", "home": "Chelsea FC", "away": "Leeds United FC",
         "hg": 0, "ag": 2, "xg_home": None, "xg_away": None, "source": "fdcouk:PL:2526"},
    ])
    store.close()


def test_form_is_scoped_to_selected_season(tmp_path):
    _seed(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL", "season": "2023-24"}
    p = enrich_profile(TeamProfile(team="Chelsea FC"), cfg)
    assert len(p.form) == 1 and p.form[0].date == "2023-10-01"   # only the 23-24 match


def test_season_overrides_api_prefilled_form(tmp_path):
    """The live API pre-fills 'recent' form relative to NOW — a season view must
    replace it with season-scoped results (or clear it), never leak the future."""
    from worldcupagents.agents.schemas import MatchResult
    _seed(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL", "season": "2023-24"}
    api_form = [MatchResult(opponent="Future FC", goals_for=1, goals_against=0, date="2026-04-07")]
    p = enrich_profile(TeamProfile(team="Chelsea FC", form=list(api_form)), cfg)
    assert all(r.date <= "2024-06-30" for r in p.form)       # scoped override
    # ...and with NO in-season rows, the leaky API form is cleared, not kept.
    cfg_empty = {**cfg, "season": "2019-20"}
    p2 = enrich_profile(TeamProfile(team="Chelsea FC", form=list(api_form)), cfg_empty)
    assert p2.form == []


def test_records_cut_off_at_season_end_no_future_leakage(tmp_path):
    _seed(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL", "season": "2023-24"}
    w, d, loss, n = h2h_home_record("Chelsea FC", "Leeds United FC", cfg)
    assert (w, n) == (1, 1)            # the 2025 defeat is INVISIBLE from 2023-24
    cfg_now = {**cfg, "season": "2025-26"}
    w2, d2, l2, n2 = h2h_home_record("Chelsea FC", "Leeds United FC", cfg_now)
    assert n2 == 2 and l2 == 1         # current season sees full history


def test_strength_fit_respects_season_cutoff(tmp_path):
    _seed(tmp_path)
    cfg = {"data_dir": str(tmp_path / "data"), "fd_competition": "PL", "season": "2023-24"}
    m = load_strength_model(cfg)
    # Only the 3-0 (2023) match is in: Chelsea attack ratio must be > 1 (scored 3, mean 1.5)
    assert m is not None and m.attack["chelsea fc"] > 1.0


def test_apply_league_defaults_and_preserves_explicit_season():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    apply_league(cfg, get_league("PL"))
    assert cfg["season"] == get_league("PL").season         # defaulted
    cfg2 = copy.deepcopy(DEFAULT_CONFIG)
    cfg2["season"] = "2023-24"
    apply_league(cfg2, get_league("PL"))
    assert cfg2["season"] == "2023-24"                      # user override kept
    cfg3 = copy.deepcopy(DEFAULT_CONFIG)
    apply_league(cfg3, get_league("WC2026"))
    assert cfg3["season"] is None                           # tournaments unscoped


# ── Wikipedia squad parser (canned wikitext, real page structure) ────────────

_WIKITEXT = """
==Players==
===First-team squad===
Notes blah.
{| class="wikitable sortable"
|-
! No. !! Player !! Nat.
|-
! colspan="11" | Goalkeepers
|-
| 22
| style="text-align:left;" | [[David Raya]]<sup>*</sup>
| {{flagg|ulc|ESP}}
|-
! colspan="11" | Defenders
|-
| 2
| style="text-align:left;" | [[William Saliba]]
| {{flagg|ulc|FRA}}
|-
! colspan="11" | Forwards
|-
| 7
| style="text-align:left;" | [[Bukayo Saka]]
|-
! colspan="11" | Out on loan
|-
| 99
| style="text-align:left;" | [[Some Loanee]]
|}
===Other section===
"""


def test_parse_squad_wikitext_groups_and_stops_at_loans():
    players = parse_squad_wikitext(_WIKITEXT)
    by = {p.name: p.position for p in players}
    assert by == {"David Raya": "Goalkeeper", "William Saliba": "Defender", "Bukayo Saka": "Forward"}
    assert "Some Loanee" not in by                          # loan block excluded


def test_provider_title_fallback_with_mocked_http():
    class FakeHTTP:
        def __init__(self):
            self.calls = []

        def get_json(self, url, headers=None, ttl=None):
            self.calls.append(url)
            if "Arsenal%20F.C." in url or "Arsenal+F.C." in url:
                return {"parse": {"wikitext": _WIKITEXT}}
            return {"error": {"code": "missingtitle"}}      # first candidate misses

    http = FakeHTTP()
    prov = WikipediaSquadsProvider(http=http)
    players, url = prov.get_season_squad("Arsenal FC", "2024-25")
    assert len(players) == 3
    assert url and "2024%E2%80%9325_Arsenal_F.C._season" in url   # en-dash in the page URL
