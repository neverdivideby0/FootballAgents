"""Data-backed weaknesses — only surfaced when a real threshold trips (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import MatchResult, Player, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.weaknesses import find_weaknesses


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["fd_competition"] = "PL"
    cfg["season"] = "2025-26"
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def _profile(team: str, form=None, squad=None) -> TeamProfile:
    return TeamProfile(team=team, form=form or [], squad=squad or [])


def test_no_weaknesses_without_store(tmp_path):
    assert find_weaknesses(_cfg(tmp_path), _profile("Arsenal FC"), "Chelsea FC") == []


def test_bogey_and_set_piece_and_venue(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    # Soft FC loses most home games and keeps losing to Boss FC.
    rows = []
    for i in range(10):  # 10 home games, 5 losses → 50% home loss rate
        rows.append({"date": f"2025-09-{i+1:02d}", "comp": "PL", "home": "Soft FC",
                     "away": f"Opp{i} FC", "hg": 0, "ag": 1 if i < 5 else 0,
                     "sh_home": 9, "sh_away": 16, "sot_home": 3, "sot_away": 7,
                     "fouls_home": 9, "fouls_away": 10, "corners_home": 4, "corners_away": 8,
                     "yellow_home": 1, "yellow_away": 1, "red_home": 0, "red_away": 0})
    for i in range(5):  # vs Boss FC: 0W-1D-4L
        rows.append({"date": f"2025-10-{i+1:02d}", "comp": "PL", "home": "Soft FC",
                     "away": "Boss FC", "hg": 0, "ag": 0 if i == 0 else 2})
    store.upsert(rows)
    store.upsert_situations("PL", "2025-26", "Soft FC",
                            {"FromCorner": {"goals": 2, "against": {"goals": 9}},
                             "SetPiece": {"goals": 1, "against": {"goals": 4}}}, "understat")
    store.close()

    ws = " | ".join(find_weaknesses(cfg, _profile("Soft FC"), "Boss FC"))
    assert "Boss FC" in ws and ("bogey" in ws or "struggles" in ws)
    assert "set pieces" in ws and "13 goals conceded" in ws    # 9 + 4
    assert "at home" in ws and "leaky" in ws                   # venue + 16 shots against/game


def test_over_reliance_on_one_scorer(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    store.upsert_players([
        {"comp": "PL", "player": "Solo Striker", "team": "OneMan FC", "goals": 20},
        {"comp": "PL", "player": "Bit Part A", "team": "OneMan FC", "goals": 5},
        {"comp": "PL", "player": "Bit Part B", "team": "OneMan FC", "goals": 3},
    ])
    store.close()
    squad = [Player(name=n) for n in ("Solo Striker", "Bit Part A", "Bit Part B")]
    ws = " | ".join(find_weaknesses(cfg, _profile("OneMan FC", squad=squad), "X FC"))
    assert "over-reliant on Solo Striker" in ws and "71%" in ws   # 20 / 28


def test_shootout_weakness_for_nation(tmp_path):
    cfg = _cfg(tmp_path); cfg["fd_competition"] = "WC"; cfg.pop("season", None)
    store = MatchStore.from_config(cfg)
    store.upsert_wh_team("national:spoils", "Spoils", kind="national", source_id="seed")
    from worldcupagents.dataflows.entities import normalize_entity_key
    store.upsert_wh_team_alias("national:spoils", "Spoils", "seed",
                               normalize_entity_key("Spoils"), confidence=1.0, status="active")
    store.upsert_wh_rows("wh_shootouts", [
        {"shootout_id": f"s{i}", "home_team_id": "national:spoils", "away_team_id": "national:other",
         "winner_team_id": "national:other" if i < 3 else "national:spoils"}
        for i in range(4)])   # lost 3, won 1
    store.close()
    ws = " | ".join(find_weaknesses(cfg, _profile("Spoils"), "Other"))
    assert "shootouts" in ws and "lost 3 of 4" in ws


def test_form_slump(tmp_path):
    form = [MatchResult(opponent="A", goals_for=0, goals_against=2, date="2026-05-01"),
            MatchResult(opponent="B", goals_for=1, goals_against=3, date="2026-04-24"),
            MatchResult(opponent="C", goals_for=0, goals_against=1, date="2026-04-17"),
            MatchResult(opponent="D", goals_for=2, goals_against=0, date="2026-04-10"),
            MatchResult(opponent="E", goals_for=1, goals_against=1, date="2026-04-03")]
    cfg = _cfg(tmp_path)
    MatchStore.from_config(cfg).close()    # store exists but empty
    ws = " | ".join(find_weaknesses(cfg, _profile("Slump FC", form=form), "X FC"))
    assert "out of form" in ws and "LLLWD"[:3] in ws


def test_strong_team_has_no_manufactured_weakness(tmp_path):
    cfg = _cfg(tmp_path)
    store = MatchStore.from_config(cfg)
    # All wins, stout defence, clean discipline, beats everyone.
    store.upsert([{"date": f"2025-09-{i+1:02d}", "comp": "PL", "home": "Elite FC",
                   "away": f"Opp{i} FC", "hg": 3, "ag": 0,
                   "sh_home": 18, "sh_away": 6, "sot_home": 8, "sot_away": 2,
                   "fouls_home": 8, "fouls_away": 11, "corners_home": 8, "corners_away": 2,
                   "yellow_home": 1, "yellow_away": 2, "red_home": 0, "red_away": 0}
                  for i in range(12)])
    store.upsert_situations("PL", "2025-26", "Elite FC",
                            {"FromCorner": {"goals": 10, "against": {"goals": 1}}}, "understat")
    store.close()
    form = [MatchResult(opponent=f"O{i}", goals_for=3, goals_against=0, date=f"2026-04-0{i+1}")
            for i in range(5)]
    assert find_weaknesses(cfg, _profile("Elite FC", form=form), "Opp1 FC") == []
