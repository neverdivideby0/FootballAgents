"""Data-supervision layer: source health, store coverage, registry completeness."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.pipelines.data_explorer import _sources_with_checks


def test_sources_without_probe_does_no_network():
    srcs = _sources_with_checks(probe=False)
    assert len(srcs) == 13
    names = {s["name"] for s in srcs}
    assert {"football-data.org", "Understat", "The Odds API"} <= names
    # probe=False → every source is 'unprobed', no live call was made.
    assert all((s.get("check") or {}).get("status") == "unprobed" for s in srcs)
    # internal probe keys are stripped from the returned dicts.
    assert all("probe_url" not in s for s in srcs)


def test_source_coverage_rollup(tmp_path):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    store = MatchStore.from_config(cfg)
    store.upsert([
        {"date": "2026-06-20", "comp": "WC", "home": "Brazil", "away": "Mexico",
         "hg": 2, "ag": 0, "source": "football_data_org:WC"},
        {"date": "2026-06-21", "comp": "WC", "home": "Spain", "away": "Croatia",
         "hg": 1, "ag": 1, "source": "football_data_org:WC"},
        {"date": "2025-05-24", "comp": "PL", "home": "Arsenal", "away": "Chelsea",
         "hg": 1, "ag": 0, "source": "fdcouk:PL:2425"},
    ])
    cov = store.source_coverage()
    store.close()
    by_src = {r["source"]: r for r in cov}
    assert by_src["football_data_org:WC"]["rows"] == 2
    assert by_src["football_data_org:WC"]["latest"] == "2026-06-21"   # newest wins
    assert by_src["fdcouk:PL:2425"]["rows"] == 1


def test_registries_have_no_drift():
    """Every CREATE TABLE and every source spec is registered today (guards drift)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import check_docs
    assert check_docs._registry_drift() == []
