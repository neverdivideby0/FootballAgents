"""Match focus — battlegrounds + player-to-watch into key/x factors (hermetic)."""

from __future__ import annotations

import copy

from worldcupagents.agents.schemas import Fixture, Player, Stage, TeamProfile
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.ensemble.focus import focus_digest, match_focus


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["fd_competition"] = "PL"
    cfg["league_kind"] = "league"
    cfg["season"] = "2025-26"
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


def _seed(cfg):
    store = MatchStore.from_config(cfg)
    # enough PL matches for the strength model to rate both teams (Cannon attack-heavy)
    rows = []
    for i in range(12):
        rows.append({"date": f"2025-09-{i+1:02d}", "comp": "PL", "home": "Cannon FC",
                     "away": "Wall FC", "hg": 4, "ag": 0})
        rows.append({"date": f"2025-10-{i+1:02d}", "comp": "PL", "home": "Wall FC",
                     "away": "Cannon FC", "hg": 0, "ag": 3})
    store.upsert(rows)
    store.upsert_players([
        {"comp": "PL", "player": "Star Winger", "team": "Cannon FC", "goals": 12,
         "assists": 9, "xg": 10.0, "xa": 8.5, "key_passes": 70, "minutes": 2600},
        {"comp": "PL", "player": "Squad Filler", "team": "Cannon FC", "goals": 1, "assists": 0},
    ])
    store.close()


def test_watch_player_and_battleground(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(cfg)
    home = TeamProfile(team="Cannon FC", squad=[Player(name="Star Winger"), Player(name="Squad Filler")])
    away = TeamProfile(team="Wall FC", squad=[Player(name="Nobody")])
    focus = match_focus(cfg, home, away)
    xf = " | ".join(focus["x_factors"])
    assert "Watch Star Winger (Cannon FC)" in xf and "the creator" in xf   # 8.5 xA
    # A clear attack-vs-defence battleground is identified.
    assert any("Cannon FC's attack" in k or "Wall FC" in k for k in focus["key_factors"])
    assert "Battlegrounds:" in focus_digest(focus)


def test_focus_flows_into_baseline_verdict(tmp_path):
    from worldcupagents.ensemble.verdict import assemble_verdict
    cfg = _cfg(tmp_path)
    _seed(cfg)
    home = TeamProfile(team="Cannon FC", fifa_rank=10,
                       squad=[Player(name="Star Winger"), Player(name="Squad Filler")])
    away = TeamProfile(team="Wall FC", fifa_rank=40, squad=[Player(name="Nobody")])
    v = assemble_verdict(cfg, Fixture(home="Cannon FC", away="Wall FC", stage=Stage.GROUP),
                         home, away, None, 0.6)
    allfac = " | ".join(v.key_factors + v.x_factors)
    assert "Star Winger" in allfac                          # watch player in x_factors
    assert any("attack" in k or "balanced" in k for k in v.key_factors)


def test_blowout_scoreline_rounds_up_and_exposes_xg():
    from worldcupagents.agents.schemas import Fixture, Stage, TeamProfile
    from worldcupagents.ensemble.verdict import assemble_verdict
    cfg = {"use_stats_lambda": False}
    # A huge rank gap → high λ favourite. Mode would show 4-0; we want ~5-0.
    fav = TeamProfile(team="Germany", fifa_rank=10)
    dog = TeamProfile(team="Curacao", fifa_rank=82)
    v = assemble_verdict(cfg, Fixture(home="Germany", away="Curacao", stage=Stage.GROUP),
                         fav, dog, None, 0.6)
    assert v.exp_goals_home is not None and v.exp_goals_home >= 4.0   # λ exposed, high
    h, a = (int(x) for x in v.scoreline.split("-"))
    assert h >= 5 and a == 0                                          # rounded up from the mode

    # A close game must NOT be inflated — keep the modal exact score.
    a1, a2 = TeamProfile(team="Spain", fifa_rank=2), TeamProfile(team="Croatia", fifa_rank=10)
    v2 = assemble_verdict(cfg, Fixture(home="Spain", away="Croatia", stage=Stage.GROUP),
                          a1, a2, None, 0.6)
    sh, sa = (int(x) for x in v2.scoreline.split("-"))
    assert sh <= 2 and sa <= 2                                        # modest, not blown up


def test_focus_empty_without_data(tmp_path):
    cfg = _cfg(tmp_path)
    MatchStore.from_config(cfg).close()                     # empty store
    home = TeamProfile(team="A", squad=[])
    away = TeamProfile(team="B", squad=[])
    assert match_focus(cfg, home, away) == {"key_factors": [], "x_factors": []}
