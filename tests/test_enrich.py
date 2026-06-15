"""M1.3 tests — profile enrichment from the match store (hermetic)."""

from __future__ import annotations

from worldcupagents.agents.briefs import profile_brief
from worldcupagents.agents.schemas import TeamProfile
from worldcupagents.dataflows.enrich import enrich_profile
from worldcupagents.dataflows.match_store import MatchStore


def _seed(tmp_path, rows):
    store = MatchStore(tmp_path / "data" / "football.db")
    store.upsert(rows)
    store.close()


def test_enrich_fills_form_from_store(tmp_path):
    _seed(tmp_path, [
        {"date": "2022-12-18", "comp": "WC", "home": "Argentina", "away": "France",
         "hg": 3, "ag": 3, "xg_home": 2.4, "xg_away": 1.2, "source": "t"},
        {"date": "2022-12-13", "comp": "WC", "home": "Argentina", "away": "Croatia",
         "hg": 3, "ag": 0, "xg_home": 2.1, "xg_away": 0.5, "source": "t"},
    ])
    cfg = {"data_dir": str(tmp_path / "data")}
    p = enrich_profile(TeamProfile(team="Argentina"), cfg)

    assert len(p.form) == 2
    assert p.form[0].opponent == "France"          # most recent first
    assert p.form[0].goals_for == 3 and p.form[0].goals_against == 3
    assert p.form[1].opponent == "Croatia" and p.form[1].goals_against == 0
    # xG averaged from Argentina's perspective: (2.4 + 2.1)/2 = 2.25 for
    assert p.xg_for == 2.25 and p.xg_against == round((1.2 + 0.5) / 2, 2)
    assert any(s.startswith("match_store:") for s in p.sources)


def test_enrich_handles_away_perspective(tmp_path):
    _seed(tmp_path, [
        {"date": "2022-12-18", "comp": "WC", "home": "Argentina", "away": "France",
         "hg": 3, "ag": 3, "xg_home": 2.4, "xg_away": 1.2, "source": "t"},
    ])
    cfg = {"data_dir": str(tmp_path / "data")}
    p = enrich_profile(TeamProfile(team="France"), cfg)
    assert p.form[0].goals_for == 3 and p.form[0].goals_against == 3  # France scored 3 (away)
    assert p.form[0].opponent == "Argentina"
    assert p.xg_for == 1.2 and p.xg_against == 2.4                    # flipped for away side


def test_enrich_no_store_is_noop(tmp_path):
    p = enrich_profile(TeamProfile(team="Spain"), {"data_dir": str(tmp_path / "nope")})
    assert p.form == [] and p.xg_for is None


def test_enrich_no_xg_leaves_xg_none_but_fills_form(tmp_path):
    _seed(tmp_path, [
        {"date": "2026-06-12", "comp": "WC", "home": "Mexico", "away": "Poland",
         "hg": 2, "ag": 1, "xg_home": None, "xg_away": None, "source": "t"},
    ])
    cfg = {"data_dir": str(tmp_path / "data")}
    p = enrich_profile(TeamProfile(team="Mexico"), cfg)
    assert len(p.form) == 1 and p.xg_for is None


def test_profile_brief_surfaces_xg():
    p = TeamProfile(team="Argentina", fifa_rank=1, xg_for=2.3, xg_against=0.9)
    brief = profile_brief(p)
    assert "xG: 2.3 for / 0.9 against" in brief