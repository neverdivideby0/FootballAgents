"""BBC Sport WC2026 team-guide ingester (hermetic — injected fetch_text)."""

from __future__ import annotations

import copy

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.pipelines.bbc_guide import ingest_bbc_team_guide, parse_team_sections


def _cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["memory_dir"] = str(tmp_path / "memory")
    cfg["prediction_log_path"] = str(tmp_path / "memory" / "prediction_log.md")
    cfg["data_vendors"] = {c: "placeholder" for c in cfg["data_vendors"]}
    return cfg


_HTML = """
<h2>Accessibility links</h2>
<h3>Brazil</h3>
<p>World Ranking: 5 World Cup Appearances: 22</p>
<p>Five-time winners chasing a sixth.</p>
<a href="https://www.bbc.co.uk/sport/football/articles/brazil123">FULL TEAM PROFILE</a>
<h3>Curacao (debut)</h3>
<p>World Ranking: 82. Smallest nation ever to qualify.</p>
<a href="https://www.bbc.co.uk/sport/football/articles/cur456"><span>FULL TEAM PROFILE</span></a>
<h3>England</h3>
<p>World Ranking: 4. Perennial contenders.</p>
"""


def test_parse_sections_extracts_team_summary_and_link():
    secs = {s["team"]: s for s in parse_team_sections(_HTML)}
    assert "Brazil" in secs and "England" in secs
    assert secs["Brazil"]["full_url"].endswith("brazil123")
    assert "FULL TEAM PROFILE" not in secs["Brazil"]["summary"]
    assert "Five-time winners" in secs["Brazil"]["summary"]
    # "(debut)" stripped from the team name.
    assert any(t == "Curacao" or t.startswith("Cura") for t in secs)
    assert secs["England"]["full_url"] is None        # no link → None, no crash


def test_ingest_summaries_into_warehouse(tmp_path):
    cfg = _cfg(tmp_path)
    # full_profiles off via fetch_text injection (no network for the link fetches)
    res = ingest_bbc_team_guide(cfg, fetch_text=lambda url: _HTML, full_profiles=True)
    assert res.teams == 3 and res.full_profiles == 0   # injected mode skips link fetches
    from worldcupagents.recall import qualitative_brief
    brief = qualitative_brief("Brazil", "Brazil", cfg)
    assert "Five-time winners" in brief


def test_ingest_graceful_on_fetch_failure(tmp_path):
    def boom(url):
        raise RuntimeError("down")
    res = ingest_bbc_team_guide(_cfg(tmp_path), fetch_text=boom)
    assert res.teams == 0
