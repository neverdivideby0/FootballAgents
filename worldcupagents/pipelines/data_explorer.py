"""Data explorer (WS-B) — one self-contained HTML page showing EVERYTHING the
model can see: API connections with live health checks, the full match/player/
situations store tables, memory artifacts, and a DATA GAPS panel.

``footballagents explore`` writes data_explorer.html (repo root) and opens it.
No server, no build step: embedded JSON + vanilla JS filtering + tab navigation.
"""

from __future__ import annotations

import html as html_mod
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore, db_path


# ── inventory ────────────────────────────────────────────────────────────────

def build_inventory(config: dict | None = None) -> dict:
    config = dict(config or DEFAULT_CONFIG)
    store_data = _store(config)
    memory_data = _memory(config)
    sources = _sources_with_checks()
    rankings = _rankings()
    inv = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sources": sources,
        "store": store_data,
        "memory": memory_data,
        "rankings": rankings,
        "calibration": _calibration(config),
        "wc_sim": _wc_sim(config),
        "gaps": [],
    }
    inv["gaps"] = _gaps(inv)
    return inv


def _sources_with_checks(probe: bool = True) -> list[dict]:
    """Source definitions + (optionally) live health probe results (short timeout,
    never crashes). ``probe=False`` skips all network calls — key-presence only —
    so the ``sources`` CLI command can run fast and offline."""
    import time

    def key(name: str) -> bool:
        return bool(os.environ.get(name))

    sources = [
        {
            "name": "football-data.org",
            "kind": "API (live fixtures/squads)",
            "configured": key("FOOTBALL_DATA_ORG_TOKEN"),
            "env_key": "FOOTBALL_DATA_ORG_TOKEN",
            "provides": "current-season squads, results, fixtures, top scorers (free tier; some per-team endpoints restricted)",
            "probe_url": "https://api.football-data.org/v4/competitions",
            "probe_headers": {"X-Auth-Token": os.environ.get("FOOTBALL_DATA_ORG_TOKEN", "")},
            "probe_key": "competitions",
        },
        {
            "name": "football-data.co.uk",
            "kind": "CSV download (no key)",
            "configured": True,
            "env_key": None,
            "provides": "multi-season results + closing odds (B365/Avg/PS) + per-match stats "
                        "(shots, shots on target, fouls, corners, yellow/red cards) — no scraping",
            "probe_url": "https://www.football-data.co.uk/englandm.php",
            "probe_headers": {},
            "probe_key": None,  # HTML page, just check 200
        },
        {
            "name": "Guardian Open Platform",
            "kind": "API (commentary)",
            "configured": key("GUARDIAN_API_KEY"),
            "env_key": "GUARDIAN_API_KEY",
            "provides": "minute-by-minute match commentary → tactical reports and qualitative warehouse segments",
            "probe_url": f"https://content.guardianapis.com/search?q=football&api-key={os.environ.get('GUARDIAN_API_KEY','test')}",
            "probe_headers": {},
            "probe_key": "response",
        },
        {
            "name": "Public articles",
            "kind": "public web pages (user-supplied URLs)",
            "configured": True,
            "env_key": None,
            "provides": "public football analysis/articles → raw snapshots, segments, claim tags, team links via qual-data --url",
            "probe_url": None,
            "probe_headers": {},
            "probe_key": None,
        },
        {
            "name": "API-Football",
            "kind": "API (player detail)",
            "configured": key("API_FOOTBALL_KEY"),
            "env_key": "API_FOOTBALL_KEY",
            "provides": "scorers + national-team results. ⚠ Free tier is season-capped to 2022–2024 "
                        "(probed 2026-06-12): NO current-season line-ups/injuries/per-fixture player "
                        "stats without a paid tier",
            "probe_url": "https://v3.football.api-sports.io/status",
            "probe_headers": {"x-apisports-key": os.environ.get("API_FOOTBALL_KEY", "")},
            "probe_key": "response",
        },
        {
            "name": "Understat",
            "kind": "scrape (xG + situations + XI + player metrics)",
            "configured": True,
            "env_key": None,
            "provides": "per-match xG, shot-situation breakdowns, most-used XI by minutes, and per-player "
                        "season metrics (shots, key passes, xG/xA, xGBuildup) — all from one cached call",
            "probe_url": "https://understat.com/league/EPL",
            "probe_headers": {"User-Agent": "FootballAgents/0.2 (personal research tool)"},
            "probe_key": None,
        },
        {
            "name": "FBref",
            "kind": "scrape — BLOCKED",
            "configured": False,
            "env_key": None,
            "provides": "would give pass accuracy + progressive carries/passes, but serves a Cloudflare "
                        "JS challenge to all non-browser clients (probed 2026-06-12) — not bypassed per "
                        "house rule (no defeating technical access controls)",
            "probe_url": None,
            "probe_headers": {},
            "probe_key": None,
        },
        {
            "name": "Wikipedia (MediaWiki API)",
            "kind": "API (historical squads)",
            "configured": True,
            "env_key": None,
            "provides": "per-season squad lists plus player career caps/goals via hoard-data --source wikipedia-player-totals",
            "probe_url": "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=Arsenal+FC+season&format=json",
            "probe_headers": {"User-Agent": "FootballAgents/0.2"},
            "probe_key": "query",
        },
        {
            "name": "StatsBomb Open Data",
            "kind": "public JSON event data",
            "configured": True,
            "env_key": None,
            "provides": "past World Cup matches, lineups, shot events, and team situation summaries via hoard-data --source statsbomb",
            "probe_url": "https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json",
            "probe_headers": {"User-Agent": "FootballAgents/0.2"},
            "probe_key": None,
        },
        {
            "name": "Curated FIFA rankings",
            "kind": "static table (bundled)",
            "configured": True,
            "env_key": None,
            "provides": "strength prior for all 48 WC2026 qualifiers + extended club rankings",
            "probe_url": None,
            "probe_headers": {},
            "probe_key": None,
        },
        {
            "name": "LLM providers",
            "kind": "API (reasoning)",
            "configured": any(key(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY")),
            "env_key": "ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY / DEEPSEEK_API_KEY",
            "provides": "debate/judge/scenario agents (offline baseline without — picks best available key automatically)",
            "probe_url": None,
            "probe_headers": {},
            "probe_key": None,
        },
        {
            "name": "The Odds API",
            "kind": "API (live market odds)",
            "configured": key("ODDS_API_KEY"),
            "env_key": "ODDS_API_KEY",
            "provides": "live de-vigged bookmaker consensus shown to the judge ('argue where the market is "
                        "wrong'); also `odds`/dossier/predict. Free ~500 req/mo — set ODDS_API_KEY",
            "probe_url": f"https://api.the-odds-api.com/v4/sports?apiKey={os.environ.get('ODDS_API_KEY','')}" if key("ODDS_API_KEY") else None,
            "probe_headers": {},
            "probe_key": None,
        },
        {
            "name": "Polymarket",
            "kind": "API (prediction market, no key)",
            "configured": True,
            "env_key": None,
            "provides": "real-money crowd win-probability for marquee fixtures (secondary market signal "
                        "alongside The Odds API) — coverage is event-driven, most club games absent",
            "probe_url": "https://gamma-api.polymarket.com/markets?limit=1",
            "probe_headers": {"User-Agent": "FootballAgents/0.2"},
            "probe_key": None,
        },
    ]

    # Run probes (short timeout, never blocks the build if they fail)
    for s in sources:
        url = s.pop("probe_url", None)
        headers = s.pop("probe_headers", {})
        probe_key = s.pop("probe_key", None)
        if not probe:
            s["check"] = {"status": "unprobed", "ms": None,
                          "detail": "key set (not probed)" if s["configured"] else "no key"}
            continue
        if not url or not s["configured"]:
            s["check"] = {"status": "skipped", "ms": None, "detail": "not configured" if not s["configured"] else "no probe URL"}
            continue
        try:
            import httpx
            t0 = time.perf_counter()
            r = httpx.get(url, headers=headers, timeout=6, follow_redirects=True)
            ms = round((time.perf_counter() - t0) * 1000)
            if r.status_code == 200:
                detail = f"200 OK"
                if probe_key:
                    try:
                        body = r.json()
                        if probe_key in body:
                            val = body[probe_key]
                            if isinstance(val, list):
                                detail = f"200 OK · {len(val)} item(s)"
                            elif isinstance(val, dict):
                                detail = f"200 OK · keys: {', '.join(list(val.keys())[:6])}"
                    except Exception:
                        pass
                s["check"] = {"status": "ok", "ms": ms, "detail": detail}
            else:
                s["check"] = {"status": "error", "ms": ms, "detail": f"HTTP {r.status_code}"}
        except Exception as exc:  # noqa: BLE001
            s["check"] = {"status": "error", "ms": None, "detail": str(exc)[:80]}

    return sources


def _store(config: dict) -> dict:
    out: dict = {
        "db": str(db_path(config)),
        "exists": db_path(config).exists(),
        "competitions": [], "players": [], "matches": [],
        "player_rows": [], "situation_rows": [],
        "wc_coverage": [],
        "warehouse_counts": {}, "raw_snapshots": [], "ingestion_runs": [],
        "entity_resolution": {}, "unresolved_names": [],
        "qualitative": {}, "qual_documents": [],
    }
    if not out["exists"]:
        return out
    store = MatchStore.from_config(config)
    try:
        rows = store.all_matches()
        players = store.players()
        player_rows_full = store.all_player_stats()
        situation_rows = store.all_situations()
        coverage = store.situation_coverage()
        warehouse_counts = store.warehouse_counts()
        raw_snapshots = store.raw_snapshots()
        ingestion_runs = store.latest_ingestion_runs()
        entity_resolution = store.entity_resolution_summary()
        unresolved_names = store.unresolved_names()
        qualitative = store.qualitative_summary()
        qual_documents = store.latest_qual_documents()
        player_notes = store.all_player_notes()
    finally:
        store.close()

    by_comp: dict[str, list[dict]] = {}
    for r in rows:
        by_comp.setdefault(r.get("comp") or "?", []).append(r)
    for comp, cr in sorted(by_comp.items()):
        teams = {x["home"] for x in cr} | {x["away"] for x in cr}
        dates = sorted(x["date"] for x in cr if x.get("date"))
        out["competitions"].append({
            "comp": comp, "matches": len(cr), "teams": len(teams),
            "from": dates[0] if dates else "—", "to": dates[-1] if dates else "—",
            "xg_rows": sum(1 for x in cr if x.get("xg_home") is not None),
            "odds_rows": sum(1 for x in cr if x.get("odds_h") is not None),
            "xi_teams": coverage.get(comp, {}).get("xis", 0),
            "sources": sorted({x.get("source") or "?" for x in cr})[:4],
        })

    pby: dict[str, list[dict]] = {}
    for p in players:
        pby.setdefault(p.get("comp") or "?", []).append(p)
    for comp, ps in sorted(pby.items()):
        out["players"].append({
            "comp": comp, "players": len(ps),
            "with_pass_accuracy": sum(1 for p in ps if p.get("pass_accuracy") is not None),
            "with_rating": sum(1 for p in ps if p.get("rating") is not None),
            "with_xg": sum(1 for p in ps if p.get("xg") is not None),
            "with_key_passes": sum(1 for p in ps if p.get("key_passes") is not None),
        })

    out["matches"] = rows
    out["wc_coverage"] = _wc_team_coverage(rows)
    out["player_rows"] = player_rows_full
    out["warehouse_counts"] = warehouse_counts
    out["raw_snapshots"] = raw_snapshots
    out["ingestion_runs"] = ingestion_runs
    out["entity_resolution"] = entity_resolution
    out["unresolved_names"] = unresolved_names
    out["qualitative"] = qualitative
    out["qual_documents"] = qual_documents
    # Flatten situation rows for the table (xi as formatted string)
    for sr in situation_rows:
        xi = sr.get("xi") or []
        sr["xi_summary"] = "; ".join(
            f"{p['pos']} {p['name']} ({p['minutes']}min)" for p in xi[:4]
        ) + ("…" if len(xi) > 4 else "")
        sit = sr.get("situations") or {}
        sr["situations_summary"] = "; ".join(
            f"{k}: {v.get('shots', '?')}sh/{v.get('goals', '?')}g"
            + (f"/xG {float(v.get('xG', 0)):.2f}" if v.get("xG") is not None else "")
            for k, v in list(sit.items())[:6] if isinstance(v, dict)
        )
    out["situation_rows"] = situation_rows
    out["player_notes"] = player_notes
    return out


def _wc_team_coverage(rows: list[dict], target: int = 5) -> list[dict]:
    from worldcupagents.dataflows.entities import resolve_team
    from worldcupagents.dataflows.names import normalize_key
    from worldcupagents.dataflows.world_cup_2026 import WC2026_TEAMS

    team_ids = {
        team: resolve_team(team, kind="national").team_id or normalize_key(team)
        for team in WC2026_TEAMS
    }
    names = {r.get("home") or "" for r in rows} | {r.get("away") or "" for r in rows}
    name_ids = {
        name: resolve_team(name, kind="national").team_id or normalize_key(name)
        for name in names
    }

    out: list[dict] = []
    for team in WC2026_TEAMS:
        tid = team_ids[team]
        team_rows = [
            r for r in rows
            if name_ids.get(r.get("home") or "") == tid or name_ids.get(r.get("away") or "") == tid
        ]
        dates = sorted(r.get("date") for r in team_rows if r.get("date"))
        sources = sorted({r.get("source") or "?" for r in team_rows})[:3]
        out.append({
            "team": team,
            "matches": len(team_rows),
            "target": target,
            "missing": max(target - len(team_rows), 0),
            "latest": dates[-1] if dates else "—",
            "sources": sources,
        })
    return out


def _memory(config: dict) -> dict:
    mem = Path(config.get("memory_dir", "memory"))
    out: dict = {"dir": str(mem), "tactical": [], "scouting": [], "critic": [],
                 "log": {"pending": 0, "resolved": 0, "avg_brier": None, "with_reflection": 0}}

    for f in sorted((mem / "matches").glob("*.json")) if (mem / "matches").exists() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            populated = sum(1 for p in d.get("phases", [])
                            if p.get("formations_blocks") or p.get("adjustments") or p.get("key_matchups"))
            out["tactical"].append({"match": d.get("match_id", f.stem), "date": d.get("date"),
                                    "phases_with_content": populated,
                                    "source": next((s for s in d.get("sources", []) if s.startswith("http")),
                                                   (d.get("sources") or ["?"])[0])})
        except Exception:  # noqa: BLE001
            continue

    for kind in ("scouting", "critic"):
        d = mem / kind
        if d.exists():
            out[kind] = sorted(f.stem for f in d.glob("*.json"))

    log = Path(config.get("prediction_log_path", str(mem / "prediction_log.md")))
    if log.exists():
        text = log.read_text(encoding="utf-8")
        out["log"]["pending"] = text.count("| pending]")
        out["log"]["resolved"] = text.count("| resolved:")
        out["log"]["with_reflection"] = text.count("REFLECTION:")
        import re
        briers = [float(m) for m in re.findall(r"Brier=([\d.]+)\]", text)]
        if briers:
            out["log"]["avg_brier"] = round(sum(briers) / len(briers), 3)
    return out


def _calibration(config: dict) -> dict:
    """The honest scoreboard: every RESOLVED prediction parsed from the log, with
    rolling Brier, hit-rate, and reliability bins (predicted % vs realized %).
    This scores the system's actual shipped predictions — not a backtest.

    Parsing + reliability bins live in ``worldcupagents.calibration`` (shared with
    the Judge's calibration feedback)."""
    from worldcupagents.calibration import reliability_bins, resolved_predictions

    out: dict = {"resolved": [], "mean_brier": None, "hit_rate": None,
                 "n_with_eval_log": 0, "bins": []}
    out["resolved"] = resolved_predictions(config)

    rs = out["resolved"]
    if rs:
        out["mean_brier"] = round(sum(r["brier"] for r in rs) / len(rs), 3)
        out["hit_rate"] = round(sum(1 for r in rs if r["predicted"] == r["actual"]) / len(rs), 2)
        out["bins"] = reliability_bins(rs)

    # LLM-lift eval log (pipelines/evaluate.py), summarized if present.
    try:
        from worldcupagents.pipelines.evaluate import load_eval_log, score_records
        records = load_eval_log(config)
        out["n_with_eval_log"] = len(records)
        if records:
            out["eval_scores"] = [
                {"model": s.name, "brier": round(s.mean_brier, 3),
                 "hit_rate": round(s.hit_rate, 2), "n": s.n}
                for s in sorted(score_records(records).values(), key=lambda s: s.mean_brier)
            ]
    except Exception:  # noqa: BLE001 — eval summary is best-effort
        pass
    return out


def _wc_sim(config: dict) -> dict | None:
    """Latest tournament-simulation export (simulate-tournament), if any."""
    p = Path(config.get("exports_dir", "exports")) / "wc2026_sim.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _rankings() -> dict:
    from worldcupagents.dataflows import fifa_rankings
    return {"as_of": fifa_rankings.RANKING_AS_OF,
            "count": len(fifa_rankings._RANK_BY_KEY)}  # noqa: SLF001 — read-only inventory


def _gaps(inv: dict) -> list[dict]:
    gaps: list[dict] = []
    comps = inv["store"]["competitions"]
    total_matches = sum(c["matches"] for c in comps)
    total_xg = sum(c["xg_rows"] for c in comps)

    if total_matches and total_xg == 0:
        gaps.append({"gap": "No xG in any stored match — the strength model fits on goals only",
                     "fix": "Understat / FBref shot data (free) → fills matches.xg_home/xg_away"})
    players = inv["store"]["players"]
    if players and all(p.get("with_xg", 0) == 0 and p["with_pass_accuracy"] == 0 for p in players):
        gaps.append({"gap": "Player stats are goals/assists only (no shots/key passes/xG)",
                     "fix": "Run fetch-data --xg -L <league> (Understat per-player metrics, free)"})
    if players and all(p["with_pass_accuracy"] == 0 for p in players):
        gaps.append({"gap": "No passing accuracy / progressive carries for the current season "
                            "(FBref Cloudflare-blocked; API-Football free tier capped to 2022–2024)",
                     "fix": "Paid API-Football tier is the only honest route — or live without it"})
    total_odds = sum(c.get("odds_rows", 0) for c in comps)
    if total_matches and total_odds == 0:
        gaps.append({"gap": "No bookmaker odds stored — backtests lack the market baseline",
                     "fix": "Re-run fetch-data -L PL --seasons … (B365 odds captured from CSVs)"})
    if len(inv["memory"]["tactical"]) < 5:
        gaps.append({"gap": f"Only {len(inv['memory']['tactical'])} match(es) in tactical memory",
                     "fix": "Run `analyze-match` over recent fixtures (Guardian key configured) to deepen the debate"})
    wc_cov = inv["store"].get("wc_coverage") or []
    if wc_cov:
        missing_teams = [r["team"] for r in wc_cov if r["missing"] > 0]
        if missing_teams:
            gaps.append({"gap": f"WC2026 recent-form coverage incomplete: {len(missing_teams)}/48 teams below 5 stored matches",
                         "fix": "Run `fetch-data --national-history --national-limit 5` (API-Football; ~2 requests/team on first run)"})
    if inv["memory"]["log"]["resolved"] == 0:
        gaps.append({"gap": "No resolved predictions — the learning loop has no lessons to feed back",
                     "fix": "`resolve --sync` auto-resolves pending predictions once results land via fetch-data"})
    if not (inv.get("calibration") or {}).get("n_with_eval_log"):
        gaps.append({"gap": "LLM debate layer is unmeasured — unknown whether it improves on the pure baseline",
                     "fix": "`evaluate -L PL -p <provider> --last 10` scores blend vs baseline vs market (Calibration tab)"})
    total_xi = sum(c.get("xi_teams", 0) for c in comps)
    if total_xi == 0:
        gaps.append({"gap": "No probable line-ups in the debate",
                     "fix": "Run fetch-data --xg (Understat most-used XI by minutes) — provider built"})
    else:
        gaps.append({"gap": f"Probable XIs are most-used-by-minutes ({total_xi} teams), not confirmed teamsheets/injuries",
                     "fix": "API-Football line-ups + injuries endpoint (same free key) — DATA_PLAN Phase 2"})
    seasons_covered = {c["from"][:4] for c in comps if c["from"] != "—"}
    if len(seasons_covered) <= 1:
        gaps.append({"gap": "Single-season history only — H2H records and strength fits are shallow",
                     "fix": "fetch-data -L PL --seasons 2122,2223,2324,2425 (football-data.co.uk, free)"})

    # Flag any source with a failed health check
    for s in inv["sources"]:
        if s["configured"] and s.get("check", {}).get("status") == "error":
            gaps.append({"gap": f"{s['name']} health check failed: {s['check']['detail']}",
                         "fix": f"Verify the key in .env ({s['env_key'] or 'no key needed'}) and re-run `footballagents explore`"})
    return gaps


# ── rendering ────────────────────────────────────────────────────────────────

# The landing-page user guide: how the whole application works, what the
# statistics mean, and FAQs. Pure static HTML (no template braces).
_GUIDE_HTML = """
<h2>What is FootballAgents?</h2>
<p>A football match predictor built like a <b>panel of arguing experts</b> rather than a single
model. Several AI agents — each seeing the same rich, sourced data — research a fixture, debate
it from opposing sides, stress-test the conclusion, and only then issue a verdict. Every
probability is <b>anchored to a statistical model</b>, every claim must cite its source, and
every prediction is <b>graded against the real result</b> so the system learns. This page is the
window into everything it knows.</p>

<h2>How one prediction works, step by step</h2>
<table>
<tr><th style="width:30px">1</th><th style="text-align:left">Scouts gather the dossier</th></tr>
<tr><td></td><td>Squads, form, rankings, probable XI, xG, set-piece profiles, style fingerprints,
international history, career totals, your saved notes — all pulled from the local database
(see the <i>matches / player_stats / team_situations</i> tabs). Nothing is fetched mid-debate;
the data is gathered first, with provenance.</td></tr>
<tr><th>2</th><th style="text-align:left">Three analysts write reports (free, deterministic)</th></tr>
<tr><td></td><td><b>Form analyst</b>: dated results, head-to-head, expected goals, set-piece punditry, likely XI.
<b>Tactical analyst</b>: lessons from previously analysed matches. <b>Player analyst</b>: leading
contributors with their metrics (xG, xA, key passes). Each line carries a [source: …] tag.</td></tr>
<tr><th>3</th><th style="text-align:left">Two advocates debate</th></tr>
<tr><td></td><td>A Home advocate and an Away advocate argue their side over multiple rounds —
and each is REQUIRED to name its own team's weaknesses. Uncited claims are treated as
hallucinations.</td></tr>
<tr><th>4</th><th style="text-align:left">The Judge reads everything → provisional verdict</th></tr>
<tr><td></td><td>The judge weighs the debate and reports and produces a probability read:
p(home win), p(draw), p(away win) plus a rationale.</td></tr>
<tr><th>4½</th><th style="text-align:left">The market check (when a key is set)</th></tr>
<tr><td></td><td>If <code>ODDS_API_KEY</code> is configured, the judge is also shown the <b>live
de-vigged bookmaker consensus</b> (averaged across many books) plus the Polymarket crowd, with
an instruction to argue explicitly where its read should differ. The market is the sharpest
available prior; the verdict reports whether the model is <i>in line with</i> or <i>fading</i> it.
(The eval harness hides the market so the "does the debate beat the market" test stays honest.)</td></tr>
<tr><th>5</th><th style="text-align:left">The anchor: blending with the Poisson baseline</th></tr>
<tr><td></td><td>The judge's read is NOT trusted on its own. It is blended with a purely
statistical baseline (a Poisson goals model fitted on real results):<br>
<code>final_prob = judge_weight × judge_read + (1 − judge_weight) × baseline</code><br>
with judge_weight = 0.6 today. This stops the language model from inventing overconfident
numbers — the verdict can never drift far from what the data supports.</td></tr>
<tr><th>6</th><th style="text-align:left">Scenario pundits stress-test it</th></tr>
<tr><td></td><td>An Upside pundit (argues the upset/variance case), a Downside pundit (argues the
favourite/class case) and a Neutral arbiter challenge the provisional verdict.</td></tr>
<tr><th>7</th><th style="text-align:left">The Final Pundit issues the verdict</th></tr>
<tr><td></td><td>It may adjust the probabilities ONLY where the scenario debate surfaced concrete
evidence — and the result is re-anchored to the same baseline. You see the full breakdown:
judge read vs baseline vs blend.</td></tr>
<tr><th>7½</th><th style="text-align:left">Upset watch — the honest counterweight</th></tr>
<tr><td></td><td>Every verdict also carries the <b>second-most-likely outcome</b> off the same
Poisson grid: its scoreline, its probability, how far behind the call it sits, and — from the
data — <b>how the upset happens</b> (the underdog's set-piece threat, the favourite's defensive
frailty, recent form, knockout-shootout variance). Favourites lose ~1 game in 3; you are never
shown "favourite wins" without its live alternative.</td></tr>
<tr><th>8</th><th style="text-align:left">The prediction is logged as pending</th></tr>
<tr><td></td><td>Fixture, probabilities and rationale go into an append-only log
(memory/prediction_log.md).</td></tr>
<tr><th>9</th><th style="text-align:left">After the real match: automatic grading + learning</th></tr>
<tr><td></td><td>When the result lands in the database, the prediction is auto-resolved: it gets a
Brier score, an optional AI-written reflection, and a lesson appended to each team's dossier —
which future predictions for those teams read back. That is the learning loop, and the
<i>Calibration</i> tab is its scoreboard.</td></tr>
</table>

<h2>The daily workflow (3 commands)</h2>
<table>
<tr><th>Command</th><th>What it does</th></tr>
<tr><td><code>footballagents refresh</code></td><td>After each matchday (~15s): pulls the newest results
(auto-resolving your pending predictions), re-runs the tournament simulation, regenerates this page.
(<code>--internationals</code> also re-pulls the static intl history — slow, occasional.)</td></tr>
<tr><td><code>footballagents predict "Argentina" "France" -i</code></td><td>Run the full agent
debate on a fixture (interactive picker with -i; add a provider key to enable the LLM agents,
or run free for the baseline verdict).</td></tr>
<tr><td><code>footballagents dossier "Argentina" "France"</code></td><td>The pre-match brief — the exact
data the agents see (line-up, player stats, recent games with stats, style, weaknesses, market). No LLM.</td></tr>
<tr><td><code>footballagents explore</code></td><td>Rebuild and open this page.</td></tr>
</table>
<p class="dim"><b>Add knowledge</b> (all flow into the dossier + debate): <code>guardian-guide</code>
&amp; <code>bbc-guide</code> (WC2026 player bios + team profiles) · <code>qual-data --url "&lt;article&gt;"
--team X</code> (any tactics article) · <code>note-player "Name" -t "Team" --note "…"</code> (or the
Player Notes tab).</p>
<p class="dim"><b>Occasional data</b>: <code>fetch-data -L PL --xg</code> (club xG/XI/player metrics) ·
<code>hoard-data --source statsbomb / wikipedia-player-totals / international-results</code>
(deep history) · <code>simulate-tournament</code> · <code>evaluate -p openai</code> (see below) ·
<code>odds "A" "B"</code> (live market) · <code>backtest --from-store -L PL</code>.</p>

<h2>The numbers, explained in plain language</h2>

<h3>Brier score — "how wrong were the probabilities?" (lower = better)</h3>
<p>For each match the system states three probabilities (home/draw/away). After the result, the
Brier score measures the squared gap between what it said and what happened:</p>
<p><code>Brier = (p_home − home?)² + (p_draw − draw?)² + (p_away − away?)²</code>
&nbsp;where each "?" is 1 if that outcome happened, else 0.</p>
<table>
<tr><th>Example</th><th>Prediction</th><th>Result</th><th>Brier</th></tr>
<tr><td>Confident &amp; right</td><td>80 / 15 / 5</td><td>home win</td><td>(0.8−1)² + 0.15² + 0.05² = <b>0.065</b> ✅</td></tr>
<tr><td>Hedged</td><td>40 / 30 / 30</td><td>home win</td><td><b>0.54</b></td></tr>
<tr><td>Pure guess (⅓ each)</td><td>33 / 33 / 33</td><td>any</td><td><b>0.667</b> ← the coin-flip line</td></tr>
<tr><td>Confident &amp; wrong</td><td>80 / 15 / 5</td><td>away win</td><td><b>1.57</b> ❌</td></tr>
</table>
<p>So: <b>below 0.667 beats guessing</b>; elite football models live around 0.55–0.60. Brier
rewards honesty — being 60% sure and right often beats being 95% sure and sometimes wrong.</p>

<h3>Hit-rate — "how often was the most likely outcome the actual one?"</h3>
<p>The blunt companion metric. A model can have a decent hit-rate with terrible probabilities, or
vice versa — read them together, and trust Brier more.</p>

<h3>De-vigged market odds — the benchmark to respect</h3>
<p>Bookmaker odds contain a built-in profit margin (the "vig"): the implied probabilities of
1/odds sum to ~105%. De-vigging strips that margin — divide each implied probability by the
total — leaving the market's true opinion. This is the hardest opponent on the scoreboard:
the market aggregates the world's sharpest models plus insider information. Matching it is
excellent; beating it durably is near-impossible from public data.</p>

<h3>The LLM-lift table — does the agent debate actually help?</h3>
<p>This is the table on the <i>Calibration</i> tab, and it answers the most important honest
question: <b>is the expensive multi-agent debate adding real signal, or just nice prose?</b>
Each row is the SAME matches scored by a different layer of the system:</p>
<table>
<tr><th>Row</th><th>What it is</th><th>How it relates to the debate</th></tr>
<tr><td><b>baseline(no LLM)</b></td><td>The pure Poisson statistical model.</td>
<td>What you'd get with the agents switched off. The debate must beat this to justify existing.</td></tr>
<tr><td><b>llm-judge(raw)</b></td><td>The judge's unblended probability read.</td>
<td>The distilled output OF the debate — the judge only sees what scouts, analysts and advocates
produced. If this row is good, the debate is producing genuine insight.</td></tr>
<tr><td><b>llm-blend(final)</b></td><td>The shipped verdict (judge × 0.6 + baseline × 0.4).</td>
<td>What the product actually outputs. Sits between the two rows above by construction.</td></tr>
<tr><td><b>market(de-vigged odds)</b></td><td>The bookmakers, on the same matches.</td>
<td>The external yardstick.</td></tr>
</table>
<p>Reading the current sample (10 matches): the judge (0.616) beat the baseline (0.661) — the
debate added signal — and even beat the market (0.749) on that round, but that round was a
final-day upset-fest where every confident model suffered. <b>n=10 proves nothing yet</b>; the
table firms up as more evaluations accumulate (≥30 needed before re-tuning judge_weight).</p>

<h3>Reliability table — "when it says 70%, does it happen 70% of the time?"</h3>
<p>All forecasts are grouped into probability bins; for each bin we compare the average forecast
with how often the outcome actually occurred. Perfectly calibrated = the two bars match. This
catches systematic over- or under-confidence that a single Brier number can hide.</p>

<h2>FAQ</h2>
<table>
<tr><th>Why don't the verdict probabilities match what the AI wrote in its rationale?</th></tr>
<tr><td>Because of the anchor (step 5): the shipped numbers are a blend of the AI's read and the
statistical baseline. The breakdown panel of every verdict shows both ingredients.</td></tr>
<tr><th>Where does every number come from?</th></tr>
<tr><td>Every analyst line carries a [source: …] tag (URL or dataset id), raw downloads are kept
as snapshots under data/raw/, and this page's tables show the underlying rows. If a claim has no
source tag, the agents are instructed to treat it as opinion.</td></tr>
<tr><th>How does it learn over time?</th></tr>
<tr><td>Two loops. Vertical: every resolved prediction writes a Brier score + reflection that
future predictions for the same teams read back. Horizontal: every data refresh adds new matches,
metrics and articles to the corpus. Run <code>refresh</code> after matchdays and both loops turn
automatically.</td></tr>
<tr><th>Why is "n" different per row in the eval table?</th></tr>
<tr><td>Honest counting: the market row only scores matches that have stored odds; LLM rows only
count runs where the model actually responded (failures are recorded as non-LLM); reruns of the
same fixture are de-duplicated.</td></tr>
<tr><th>Why a "seeded approximation" in the tournament sim?</th></tr>
<tr><td>Until the group stage ends, FIFA's bracket slots for third-placed teams are literally
undetermined. The sim uses a clearly-labelled seeded bracket until the official LAST_32 pairings
appear in the fixtures feed, then switches to them automatically.</td></tr>
<tr><th>Why are some sources missing (FotMob, FBref, current-season injuries)?</th></tr>
<tr><td>FotMob/SofaScore/FBref block non-browser clients with technical countermeasures, and this
project's rule is to never bypass those. API-Football's free tier is capped to 2022–2024 seasons.
The Data Sources tab records every such verdict with the probe date.</td></tr>
<tr><th>Can the backtest numbers be trusted?</th></tr>
<tr><td>Partially — older matches may appear in the LLM's training data ("leakage"), so absolute
scores flatter the models. Read the RELATIVE gaps between rows, and weight live, post-cutoff
predictions (the Calibration tab) far more than historical backtests.</td></tr>
<tr><th>Is this betting advice?</th></tr>
<tr><td>No. It is a personal research and punditry tool; there is deliberately no betting or
real-money integration.</td></tr>
</table>
"""


def render_html(inv: dict) -> str:
    e = html_mod.escape

    # JSON payloads for client-side tables. Cap by complete rows, never by
    # slicing serialized JSON text; a partial JSON literal breaks all tab JS.
    # Keep the NEWEST rows when capping — recent matches are the relevant ones
    # (capping the head silently dropped every PL/WC row behind 1870s friendlies).
    matches_payload = sorted(inv["store"]["matches"],
                             key=lambda r: r.get("date") or "")[-20_000:]
    # Same idea for players: metric-rich club/career rows first, then INT
    # scorers by goals — so the cap drops only low-signal historical rows.
    players_payload = sorted(inv["store"]["player_rows"],
                             key=lambda r: (r.get("comp") == "INT",
                                            -(r.get("goals") or 0)))[:10_000]
    matches_json = json.dumps(matches_payload)
    players_json = json.dumps(players_payload)
    matches_total = len(inv["store"]["matches"])
    players_total = len(inv["store"]["player_rows"])
    situations_json = json.dumps([
        {k: v for k, v in r.items() if k not in ("situations", "xi")}
        for r in inv["store"]["situation_rows"]
    ])
    player_notes_json = json.dumps(inv["store"].get("player_notes", []))

    # ── section renderers ─────────────────────────────────────────────────────

    def gap_rows():
        return "\n".join(
            f"<tr><td>⚠️ {e(g['gap'])}</td><td>→ {e(g['fix'])}</td></tr>"
            for g in inv["gaps"])

    def src_rows():
        out = []
        for s in inv["sources"]:
            badge = "✅" if s["configured"] else "❌"
            chk = s.get("check", {})
            status = chk.get("status", "skipped")
            if status == "ok":
                chk_badge = f'<span class="chk ok">✓ {e(chk["detail"])} ({chk["ms"]}ms)</span>'
            elif status == "error":
                chk_badge = f'<span class="chk err">✗ {e(chk["detail"])}</span>'
            else:
                chk_badge = f'<span class="chk skip">{e(chk.get("detail","—"))}</span>'
            env_hint = f'<br><code class="dim">{e(s["env_key"])}</code>' if s["env_key"] else ""
            out.append(
                f"<tr>"
                f"<td>{badge}</td>"
                f"<td><b>{e(s['name'])}</b>{env_hint}</td>"
                f"<td>{e(s['kind'])}</td>"
                f"<td>{e(s['provides'])}</td>"
                f"<td>{chk_badge}</td>"
                f"</tr>"
            )
        return "\n".join(out)

    def comp_rows():
        return "\n".join(
            f"<tr><td><b>{e(c['comp'])}</b></td><td>{c['matches']}</td><td>{c['teams']}</td>"
            f"<td>{e(c['from'])} → {e(c['to'])}</td>"
            f"<td>{c['xg_rows']}</td><td>{c.get('odds_rows', 0)}</td>"
            f"<td>{c.get('xi_teams', 0)}</td>"
            f"<td>{e(', '.join(c['sources']))}</td></tr>"
            for c in inv["store"]["competitions"])

    def player_summary_rows():
        return "\n".join(
            f"<tr><td><b>{e(p['comp'])}</b></td><td>{p['players']}</td>"
            f"<td>{p.get('with_xg', 0)}</td><td>{p.get('with_key_passes', 0)}</td>"
            f"<td>{p['with_pass_accuracy']}</td><td>{p['with_rating']}</td></tr>"
            for p in inv["store"]["players"])

    def warehouse_rows():
        counts = inv["store"].get("warehouse_counts") or {}
        return "\n".join(
            f"<tr><td><b>{e(k)}</b></td><td>{v}</td></tr>"
            for k, v in sorted(counts.items()) if v
        )

    def raw_snapshot_rows():
        return "\n".join(
            f"<tr><td><b>{e(r['source_id'])}</b></td><td>{e(r['snapshot'])}</td>"
            f"<td>{r['files']}</td><td>{r.get('bytes') or 0}</td>"
            f"<td>{e(r.get('last_fetched') or '—')}</td></tr>"
            for r in inv["store"].get("raw_snapshots", []))

    def ingestion_rows():
        out = []
        for r in inv["store"].get("ingestion_runs", []):
            counts = r.get("counts_json") or "{}"
            try:
                counts = ", ".join(f"{k}={v}" for k, v in json.loads(counts).items())
            except Exception:
                pass
            out.append(
                f"<tr><td><b>{e(r['source_id'])}</b></td><td>{e(r['snapshot'])}</td>"
                f"<td>{e(r.get('status') or '')}</td><td>{e(r.get('finished_at') or '—')}</td>"
                f"<td class=dim>{e(str(counts))}</td></tr>"
            )
        return "\n".join(out)

    def entity_source_rows():
        summary = inv["store"].get("entity_resolution") or {}
        return "\n".join(
            f"<tr><td><b>{e(r['source_id'])}</b></td><td>{r['aliases']}</td></tr>"
            for r in summary.get("aliases_by_source", []))

    def unresolved_rows():
        return "\n".join(
            f"<tr><td><b>{e(r['raw_name'])}</b></td><td>{e(r.get('kind') or '')}</td>"
            f"<td>{e(r.get('source_id') or '')}</td><td>{e(r.get('reason') or '')}</td>"
            f"<td>{r.get('count') or 0}</td></tr>"
            for r in inv["store"].get("unresolved_names", []))

    def qual_source_rows():
        summary = inv["store"].get("qualitative") or {}
        return "\n".join(
            f"<tr><td><b>{e(r['source_id'])}</b></td><td>{r['documents']}</td>"
            f"<td>{r.get('text_chars') or 0}</td></tr>"
            for r in summary.get("by_source", []))

    def qual_claim_rows():
        summary = inv["store"].get("qualitative") or {}
        return "\n".join(
            f"<tr><td><b>{e(r['claim_type'] or 'unknown')}</b></td><td>{r['claims']}</td></tr>"
            for r in summary.get("claim_types", []))

    def qual_document_rows():
        out = []
        for r in inv["store"].get("qual_documents", []):
            url = r.get("url") or ""
            link = f'<a href="{e(url)}" target="_blank">{e(url[:72])}…</a>' if url.startswith("http") else e(url or "—")
            delete_cmd = f"uv run footballagents qual-data --delete-document {r['document_id']}"
            out.append(
                f"<tr><td><b>{e(r.get('title') or r['document_id'])}</b></td>"
                f"<td>{e(r.get('source_id') or '')}</td><td>{e(r.get('source_type') or '')}</td>"
                f"<td>{e(r.get('published_at') or '—')}</td><td>{r.get('text_chars') or 0}</td>"
                f"<td>{link}</td><td><code class=dim>{e(delete_cmd)}</code></td></tr>"
            )
        return "\n".join(out)

    def wc_coverage_rows():
        return "\n".join(
            f"<tr><td><b>{e(r['team'])}</b></td><td>{r['matches']}/{r['target']}</td>"
            f"<td>{r['missing']}</td><td>{e(r['latest'])}</td>"
            f"<td>{e(', '.join(r['sources']))}</td></tr>"
            for r in inv["store"].get("wc_coverage", []))

    def tactical_rows():
        out = []
        for t in inv["memory"]["tactical"]:
            src = t["source"]
            link = f'<a href="{e(src)}" target="_blank">{e(src[:60])}…</a>' if src.startswith("http") else e(src)
            out.append(f"<tr><td>{e(t['match'])}</td><td>{e(t['date'] or '—')}</td>"
                       f"<td>{t['phases_with_content']}/5</td><td>{link}</td></tr>")
        return "\n".join(out)

    log = inv["memory"]["log"]
    calib = inv.get("calibration") or {"resolved": [], "mean_brier": None,
                                       "hit_rate": None, "n_with_eval_log": 0, "bins": []}

    def _pct_bar(v: float | None) -> str:
        if v is None:
            return ""
        w = max(1, round(v * 100))
        return (f'<div style="background:#e8eef8;border-radius:3px;width:110px">'
                f'<div style="background:#0066cc;height:9px;border-radius:3px;width:{w}%"></div></div>')

    def reliability_rows():
        out = []
        for b in calib["bins"]:
            if not b["n"]:
                continue
            out.append(
                f"<tr><td>{e(b['range'])}</td><td>{b['n']}</td>"
                f"<td>{b['forecast']:.0%}</td><td>{b['realized']:.0%}</td>"
                f"<td>{_pct_bar(b['forecast'])}{_pct_bar(b['realized'])}</td></tr>")
        return "\n".join(out)

    def resolved_rows():
        out = []
        for r in sorted(calib["resolved"], key=lambda x: x["date"], reverse=True)[:100]:
            ok = "✓" if r["predicted"] == r["actual"] else "✗"
            out.append(
                f"<tr><td>{e(r['date'])}</td><td><b>{e(r['fixture'])}</b></td>"
                f"<td>{e(r['predicted'])}</td><td>{ok} {e(r['actual'])} {e(r['score'])}</td>"
                f"<td class=dim>{r['p'][0]:.2f}/{r['p'][1]:.2f}/{r['p'][2]:.2f}</td>"
                f"<td>{r['brier']:.3f}</td></tr>")
        return "\n".join(out)

    sim = inv.get("wc_sim")

    def wc_sim_subtitle():
        if not sim:
            return ""
        return (f'<span class="dim">({sim["n"]:,} runs · {sim["played"]} results locked · '
                f'{e(sim["bracket_source"])})</span>')

    def wc_sim_rows():
        if not sim:
            return ""
        ranked = sorted(sim["teams"].items(), key=lambda kv: kv[1].get("champion", 0), reverse=True)
        return "\n".join(
            f"<tr><td><b>{e(team)}</b></td>"
            f"<td>{c.get('group_win', 0):.0%}</td><td>{c.get('r32', 0):.0%}</td>"
            f"<td>{c.get('r16', 0):.0%}</td><td>{c.get('qf', 0):.0%}</td>"
            f"<td>{c.get('sf', 0):.0%}</td><td>{c.get('final', 0):.0%}</td>"
            f"<td><b>{c.get('champion', 0):.1%}</b></td></tr>"
            for team, c in ranked[:24])

    def eval_scores_table():
        rows = calib.get("eval_scores") or []
        if not rows:
            return ('<p class="dim">No LLM-lift eval yet — run '
                    '<code>footballagents evaluate -L PL -p &lt;provider&gt;</code> to measure '
                    'whether the agent debate beats the pure baseline.</p>')
        body = "\n".join(
            f"<tr><td><b>{e(s['model'])}</b></td><td>{s['brier']:.3f}</td>"
            f"<td>{s['hit_rate']:.0%}</td><td>{s['n']}</td></tr>" for s in rows)
        return ("<h2>LLM-lift evaluation <span class='dim'>(data/eval_log.jsonl — lower Brier wins · "
                "row meanings + formulas explained on the <a href='#' onclick=\"showTab('guide');return false\">📖 Guide</a> tab)</span></h2>"
                "<table><tr><th>Model</th><th>Mean Brier</th><th>Hit-rate</th><th>n</th></tr>"
                f"{body}</table>")

    # ── HTML ──────────────────────────────────────────────────────────────────
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>FootballAgents — Data Explorer</title>
<style>
 *{{box-sizing:border-box}}
 body{{font:13px/1.5 -apple-system,system-ui,sans-serif;margin:0;background:#f4f5f7}}
 .page{{max-width:1260px;margin:0 auto;padding:20px}}
 h1{{font-size:20px;margin:0 0 4px}} .gen{{color:#888;font-size:11px;margin-bottom:16px}}
 h2{{font-size:15px;margin:22px 0 8px;padding-bottom:4px;border-bottom:1px solid #ddd}}
 table{{border-collapse:collapse;width:100%;margin:6px 0;background:#fff;border-radius:4px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
 td,th{{border:1px solid #e8e8e8;padding:5px 8px;text-align:left;vertical-align:top;font-size:12px}}
 th{{background:#f0f0f0;font-weight:600}}
 tr:hover td{{background:#fafafa}}
 .pill{{display:inline-block;background:#e8eef8;border-radius:10px;padding:1px 9px;margin:2px;font-size:11px}}
 input.flt{{padding:6px 10px;width:300px;margin:4px 0;border:1px solid #ccc;border-radius:4px;font-size:12px}}
 .gaps td{{background:#fffbe6}} .gaps tr:hover td{{background:#fff8d6}}
 .dim{{color:#888}} code.dim{{font-size:10px;color:#aaa}}
 .chk{{font-size:11px;padding:2px 7px;border-radius:10px;white-space:nowrap}}
 .chk.ok{{background:#d4edda;color:#155724}} .chk.err{{background:#f8d7da;color:#721c24}} .chk.skip{{background:#e2e3e5;color:#636464}}
 /* tabs */
 .tabs{{display:flex;gap:4px;margin-bottom:0;border-bottom:2px solid #0066cc;flex-wrap:wrap}}
 .tab{{padding:7px 14px;cursor:pointer;background:#e8eef8;border-radius:4px 4px 0 0;font-size:12px;font-weight:600;color:#555;user-select:none;transition:background .15s}}
 .tab.active{{background:#0066cc;color:#fff}}
 .tab-content{{display:none;background:#fff;border:1px solid #ddd;border-top:none;padding:16px;border-radius:0 4px 4px 4px;box-shadow:0 2px 6px rgba(0,0,0,.06)}}
 .tab-content.active{{display:block}}
 /* section cards */
 .card{{background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
 /* row count badge */
 .cnt{{font-size:10px;color:#888;margin-left:6px}}
 /* sortable headers */
 th.sortable{{cursor:pointer;user-select:none}} th.sortable:hover{{background:#e4e8f0}}
 h3{{font-size:13px;margin:16px 0 4px}}
 select.flt{{margin-left:6px}}
</style></head><body>
<div class="page">
<h1>⚽ FootballAgents — Data Explorer</h1>
<p class="gen">Generated {e(inv['generated'])} · everything the model can currently see · re-run <code>footballagents explore</code> to refresh</p>

<div class="tabs">
  <div class="tab active" onclick="showTab('guide')">📖 Guide</div>
  <div class="tab" onclick="showTab('gaps')">🚨 Data Gaps</div>
  <div class="tab" onclick="showTab('sources')">🔌 Data Sources</div>
  <div class="tab" onclick="showTab('matches')">📋 matches table</div>
  <div class="tab" onclick="showTab('players')">👟 player_stats table</div>
  <div class="tab" onclick="showTab('situations')">🧩 team_situations table</div>
  <div class="tab" onclick="showTab('qualitative')">🗣️ Qualitative</div>
  <div class="tab" onclick="showTab('manual')">✍️ Manual Analysis</div>
  <div class="tab" onclick="showTab('playernotes')">🧑 Player Notes</div>
  <div class="tab" onclick="showTab('memory')">🧠 Memory</div>
  <div class="tab" onclick="showTab('calibration')">📐 Calibration</div>
  <div class="tab" onclick="showTab('wcsim')">🏆 WC2026 sim</div>
  <div class="tab" onclick="showTab('store')">🗄️ Store summary</div>
</div>

<!-- GUIDE (landing page) -->
<div id="tab-guide" class="tab-content active">
{_GUIDE_HTML}
</div>

<!-- GAPS -->
<div id="tab-gaps" class="tab-content">
  <h2>Data gaps — where to improve next</h2>
  <table class="gaps"><tr><th>Gap</th><th>Suggested source / action</th></tr>
  {gap_rows() or '<tr><td colspan=2 class=dim>No gaps detected 🎉</td></tr>'}
  </table>
</div>

<!-- SOURCES -->
<div id="tab-sources" class="tab-content">
  <h2>Data sources &amp; API health checks <span class="dim">(probed at build time)</span></h2>
  <table>
    <tr><th></th><th>Source</th><th>Kind</th><th>Provides</th><th>Health check</th></tr>
    {src_rows()}
  </table>
  <p class="dim" style="margin-top:8px;font-size:11px">
    ✅ = key configured (or no key needed) · ❌ = key missing · health checks run at <code>footballagents explore</code> time with a 6 s timeout.
    To re-run, execute <code>footballagents explore</code> again.
  </p>
</div>

<!-- MATCHES TABLE -->
<div id="tab-matches" class="tab-content">
  <h2>matches table <span class="cnt" id="m-cnt"></span></h2>
  <p class="dim" id="m-note" style="font-size:11px;margin:0 0 6px"></p>
  <p class="dim" style="font-size:11px;margin:0 0 6px">
    Default view hides <code>INT</code> matches before 1988 to reduce historical noise.
  </p>
  <input class="flt" id="mq" placeholder="filter by team / date…" oninput="fltM()">
  <select id="mcomp" class="flt" style="width:auto" onchange="fltM()"><option value="">all comps</option></select>
  <select id="mseason" class="flt" style="width:auto" onchange="fltM()"><option value="">all seasons</option></select>
  <select id="msrc" class="flt" style="width:auto" onchange="fltM()"><option value="">all sources</option></select>
  <label class="dim" style="font-size:11px;margin-left:8px">
    <input type="checkbox" id="mall" onchange="fltM()"> include pre-1988 INT
  </label>
  <span class="dim" style="font-size:11px;margin-left:8px">click a column header to sort</span>
  <table id="mt">
    <tr><th>Date</th><th>Comp</th><th>Home</th><th>Score</th><th>Away</th>
        <th>xG</th><th>Odds H/D/A</th><th>Source</th></tr>
  </table>
</div>

<!-- PLAYER STATS TABLE -->
<div id="tab-players" class="tab-content">
 <h2>player_stats table <span class="cnt" id="p-cnt"></span></h2>
  <p class="dim" id="p-note" style="font-size:11px;margin:0 0 6px"></p>
  <p class="dim" style="font-size:11px;margin:0 0 6px">
    Source caveat: INT rows from <code>martj42/international_results:goalscorers.csv</code>
    are event aggregates from that goalscorer file, not verified career totals.
    Their Matches value is scoring matches found in that source, not caps/appearances.
  </p>
  <input class="flt" id="pq" placeholder="filter by player / team…" oninput="fltP()">
  <select id="pcomp" class="flt" style="width:auto" onchange="fltP()"><option value="">all comps</option></select>
  <select id="psrc" class="flt" style="width:auto" onchange="fltP()"><option value="">all sources</option></select>
  <label class="dim" style="font-size:11px;margin-left:8px">
    min goals <input id="pmin" type="number" min="0" value="10" style="width:58px;padding:4px" oninput="fltP()">
  </label>
  <label class="dim" style="font-size:11px;margin-left:8px">
    <input type="checkbox" id="pall" onchange="fltP()"> show all low-signal rows
  </label>
  <span class="dim" style="font-size:11px;margin-left:8px">click a column header to sort</span>
  <table id="pt">
    <tr><th>Comp</th><th>Player</th><th>Team</th><th>G</th><th>A</th><th>Pen</th>
        <th>Matches / coverage</th><th>Min</th><th>Pass%</th><th>Key passes</th><th>Rating</th><th>Source</th></tr>
  </table>
</div>

<!-- TEAM SITUATIONS TABLE -->
<div id="tab-situations" class="tab-content">
  <h2>team_situations table <span class="cnt" id="s-cnt"></span></h2>
  <p class="dim" style="font-size:11px;margin:0 0 6px">
    StatsBomb WC rows are team attacking shot situations only: shots/goals/xG by play pattern.
    They are not full team strengths and do not include opponent conceded patterns in this summary.
  </p>
  <input class="flt" id="sq" placeholder="filter by team…" oninput="fltS()">
  <select id="scomp" class="flt" style="width:auto" onchange="fltS()"><option value="">all comps</option></select>
  <select id="sseason" class="flt" style="width:auto" onchange="fltS()"><option value="">all seasons</option></select>
  <span class="dim" style="font-size:11px;margin-left:8px">click a column header to sort</span>
  <table id="st">
    <tr><th>Comp</th><th>Season</th><th>Team</th><th>Shot situations for</th><th>Likely XI (top 4)</th><th>Source</th></tr>
  </table>
</div>

<!-- QUALITATIVE -->
<div id="tab-qualitative" class="tab-content">
  <h2>Qualitative warehouse</h2>
  <div class="card">
    <span class="pill">documents: {inv['store'].get('qualitative', {}).get('documents', 0)}</span>
    <span class="pill">segments: {inv['store'].get('qualitative', {}).get('segments', 0)}</span>
    <span class="pill">claims: {inv['store'].get('qualitative', {}).get('claims', 0)}</span>
    <span class="pill">entity links: {inv['store'].get('qualitative', {}).get('links', 0)}</span>
  </div>
  <h2>Sources</h2>
  <table>
    <tr><th>Source</th><th>Documents</th><th>Text chars</th></tr>
    {qual_source_rows() or '<tr><td colspan=3 class=dim>(none — run qual-data)</td></tr>'}
  </table>
  <h2>Claim tags</h2>
  <table>
    <tr><th>Claim type</th><th>Claims</th></tr>
    {qual_claim_rows() or '<tr><td colspan=2 class=dim>(none)</td></tr>'}
  </table>
  <h2>Recent qualitative documents</h2>
  <p class="dim" style="font-size:11px;margin:0 0 6px">
    Delete a document with <code>uv run footballagents qual-data --delete-document DOCUMENT_ID</code>.
  </p>
  <table>
    <tr><th>Title</th><th>Source</th><th>Type</th><th>Published/date</th><th>Chars</th><th>URL</th><th>Delete</th></tr>
    {qual_document_rows() or '<tr><td colspan=7 class=dim>(none)</td></tr>'}
  </table>
</div>

<!-- MANUAL ANALYSIS -->
<div id="tab-manual" class="tab-content">
  <h2>Manual analysis note</h2>
  <p class="dim" style="font-size:11px;margin:0 0 8px">
    This static page cannot write directly to SQLite. Write your note here, download it,
    then run the generated command to ingest it into the qualitative warehouse.
  </p>
  <div class="card">
    <label>Team / country<br><input class="flt" id="man-team" placeholder="Argentina" oninput="buildManualCmd()"></label>
    <label style="margin-left:8px">Date<br><input class="flt" id="man-date" placeholder="2026-06-12" oninput="buildManualCmd()"></label>
    <label style="margin-left:8px">Title<br><input class="flt" id="man-title" placeholder="Argentina defensive transition note" oninput="buildManualCmd()"></label>
    <br>
    <textarea id="man-text" oninput="buildManualCmd()" placeholder="Free-form analysis: tactical shape, strengths, weaknesses, pundit notes you have rights to use, your own scouting observations…" style="width:100%;min-height:170px;margin-top:10px;padding:8px;border:1px solid #ccc;border-radius:4px;font:12px/1.5 ui-monospace,Menlo,monospace"></textarea>
    <p>
      <button type="button" onclick="downloadManualNote()">Download note file</button>
      <button type="button" onclick="copyManualCmd()">Copy command</button>
    </p>
    <pre id="man-cmd" style="white-space:pre-wrap;background:#f6f8fa;border:1px solid #e2e2e2;border-radius:4px;padding:8px"></pre>
    <p class="dim" style="font-size:11px">
      After running the command, refresh this explorer with <code>uv run footballagents explore --no-open</code>.
    </p>
  </div>
</div>

<!-- PLAYER NOTES -->
<div id="tab-playernotes" class="tab-content">
  <h2>Player scouting / style notes <span class="dim">(your qualitative layer)</span></h2>
  <p class="dim" style="font-size:11px;margin:0 0 8px">
    Type a style note per player — exactly the prose data can't capture (role, movement, tendencies,
    a line from The Athletic). This static page can't write the database, so it builds the command
    to run; the note then surfaces next to that player in the Player Analyst and the dossier.
  </p>
  <div class="card">
    <label>Team<br><input class="flt" id="pn-team" placeholder="Arsenal FC" oninput="buildNoteCmd()"></label>
    <label style="margin-left:8px">Player<br><input class="flt" id="pn-player" placeholder="Bukayo Saka" oninput="buildNoteCmd()"></label>
    <br>
    <textarea id="pn-note" oninput="buildNoteCmd()" placeholder="Inverted right winger; cuts onto his left, drifts into the half-space; Arsenal's main open-play creator. Quiet away to deep blocks." style="width:100%;min-height:120px;margin-top:10px;padding:8px;border:1px solid #ccc;border-radius:4px;font:12px/1.5 ui-monospace,Menlo,monospace"></textarea>
    <p><button type="button" onclick="copyNoteCmd()">Copy command</button></p>
    <pre id="pn-cmd" style="white-space:pre-wrap;background:#f6f8fa;border:1px solid #e2e2e2;border-radius:4px;padding:8px"></pre>
    <p class="dim" style="font-size:11px">Run it, then refresh with <code>footballagents explore --no-open</code>. Delete with <code>note-player "Name" -t "Team" --delete</code>.</p>
  </div>
  <h2>Existing player notes <span class="cnt" id="pn-cnt"></span></h2>
  <input class="flt" id="pnq" placeholder="filter by player / team…" oninput="fltPN()">
  <table id="pnt"><tr><th>Team</th><th>Player</th><th>Note</th><th>Updated</th></tr></table>
</div>

<!-- MEMORY -->
<div id="tab-memory" class="tab-content">
  <h2>Memory</h2>
  <div class="card">
    <span class="pill">tactical reports: {len(inv['memory']['tactical'])}</span>
    <span class="pill">scouting: {len(inv['memory']['scouting'])}</span>
    <span class="pill">critic: {len(inv['memory']['critic'])}</span>
    <span class="pill">predictions: {log['pending']} pending / {log['resolved']} resolved</span>
    <span class="pill">avg Brier: {log['avg_brier'] if log['avg_brier'] is not None else '—'}</span>
    <span class="pill">reflections: {log['with_reflection']}</span>
  </div>
  <h2>Tactical reports</h2>
  <table>
    <tr><th>Match</th><th>Date</th><th>Phases with content</th><th>Source</th></tr>
    {tactical_rows() or '<tr><td colspan=4 class=dim>(none — run analyze-match)</td></tr>'}
  </table>
</div>

<!-- CALIBRATION -->
<div id="tab-calibration" class="tab-content">
  <h2>Calibration — the honest scoreboard <span class="dim">(actual shipped predictions, not a backtest)</span></h2>
  <div class="card">
    <span class="pill">resolved predictions: {len(calib['resolved'])}</span>
    <span class="pill">mean Brier: {calib['mean_brier'] if calib['mean_brier'] is not None else '—'} <span class="dim">(coin-flip = 0.667)</span></span>
    <span class="pill">hit-rate: {f"{calib['hit_rate']:.0%}" if calib['hit_rate'] is not None else '—'}</span>
    <span class="pill">eval-log reads: {calib['n_with_eval_log']}</span>
  </div>
  {eval_scores_table()}
  <h2>Reliability <span class="dim">(forecast probability vs how often it actually happened — perfect calibration: bars match)</span></h2>
  <table>
    <tr><th>Forecast bin</th><th>n forecasts</th><th>Avg forecast</th><th>Realized</th><th></th></tr>
    {reliability_rows() or '<tr><td colspan=5 class=dim>(no resolved predictions yet — predict, then `resolve --sync` after results land)</td></tr>'}
  </table>
  <h2>Resolved predictions</h2>
  <table>
    <tr><th>Date</th><th>Fixture</th><th>Predicted</th><th>Actual</th><th>p(H/D/A)</th><th>Brier</th></tr>
    {resolved_rows() or '<tr><td colspan=6 class=dim>(none yet)</td></tr>'}
  </table>
</div>

<!-- WC2026 SIMULATION -->
<div id="tab-wcsim" class="tab-content">
  <h2>WC2026 tournament simulation {wc_sim_subtitle()}</h2>
  <table>
    <tr><th>Team</th><th>Win group</th><th>R32</th><th>R16</th><th>QF</th><th>SF</th><th>Final</th><th>Champion</th></tr>
    {wc_sim_rows() or '<tr><td colspan=8 class=dim>(no simulation yet — run `footballagents simulate-tournament`)</td></tr>'}
  </table>
</div>

<!-- STORE SUMMARY -->
<div id="tab-store" class="tab-content">
  <h2>Match store summary <span class="dim">({e(inv['store']['db'])})</span></h2>
  <table>
    <tr><th>Comp</th><th>Matches</th><th>Teams</th><th>Date range</th>
        <th>xG rows</th><th>Odds rows</th><th>XI teams</th><th>Sources</th></tr>
    {comp_rows() or '<tr><td colspan=8 class=dim>(empty — run fetch-data)</td></tr>'}
  </table>
  <h2>Player stats summary</h2>
  <table>
    <tr><th>Comp</th><th>Players</th><th>with xG/xA</th><th>with key passes</th><th>with pass-accuracy</th><th>with rating</th></tr>
    {player_summary_rows() or '<tr><td colspan=6 class=dim>(none)</td></tr>'}
  </table>
  <h2>WC2026 recent-result coverage</h2>
  <table>
    <tr><th>Team</th><th>Stored matches</th><th>Missing to 5</th><th>Latest</th><th>Sources</th></tr>
    {wc_coverage_rows() or '<tr><td colspan=5 class=dim>(none)</td></tr>'}
  </table>
  <h2>Warehouse counts</h2>
  <table>
    <tr><th>Table</th><th>Rows</th></tr>
    {warehouse_rows() or '<tr><td colspan=2 class=dim>(none — run hoard-data)</td></tr>'}
  </table>
  <h2>Raw snapshots</h2>
  <table>
    <tr><th>Source</th><th>Snapshot</th><th>Files</th><th>Bytes</th><th>Fetched</th></tr>
    {raw_snapshot_rows() or '<tr><td colspan=5 class=dim>(none)</td></tr>'}
  </table>
  <h2>Ingestion runs</h2>
  <table>
    <tr><th>Source</th><th>Snapshot</th><th>Status</th><th>Finished</th><th>Counts</th></tr>
    {ingestion_rows() or '<tr><td colspan=5 class=dim>(none)</td></tr>'}
  </table>
  <h2>Entity Resolution</h2>
  <div class="card">
    <span class="pill">canonical teams: {inv['store'].get('entity_resolution', {}).get('teams', 0)}</span>
    <span class="pill">aliases: {inv['store'].get('entity_resolution', {}).get('aliases', 0)}</span>
    <span class="pill">unresolved: {inv['store'].get('entity_resolution', {}).get('unresolved', 0)}</span>
    <span class="pill">ambiguous aliases: {inv['store'].get('entity_resolution', {}).get('ambiguous_aliases', 0)}</span>
  </div>
  <table>
    <tr><th>Alias source</th><th>Aliases</th></tr>
    {entity_source_rows() or '<tr><td colspan=2 class=dim>(none)</td></tr>'}
  </table>
  <h2>Unresolved / ambiguous names</h2>
  <table>
    <tr><th>Name</th><th>Kind</th><th>Source</th><th>Reason</th><th>Count</th></tr>
    {unresolved_rows() or '<tr><td colspan=5 class=dim>(none)</td></tr>'}
  </table>
  <h2>Rankings</h2>
  <p>{inv['rankings']['count']} teams · {e(inv['rankings']['as_of'])}</p>
</div>

</div><!-- /page -->
<script>
// ── data ──────────────────────────────────────────────────────────────────
const MATCHES = {matches_json};
const PLAYERS = {players_json};
const SITU    = {situations_json};
const MATCHES_TOTAL = {matches_total};
const PLAYERS_TOTAL = {players_total};

// ── tabs ──────────────────────────────────────────────────────────────────
const TABS = ['guide','gaps','sources','matches','players','situations','qualitative','manual','playernotes','memory','calibration','wcsim','store'];
const PLAYER_NOTES = {player_notes_json};
function showTab(id) {{
  TABS.forEach(t => {{
    document.getElementById('tab-'+t).classList.toggle('active', t===id);
    document.querySelectorAll('.tab').forEach((el,i) => el.classList.toggle('active', TABS[i]===id));
  }});
  if (id==='matches' && !document.getElementById('mt').dataset.init) {{ fltM(); document.getElementById('mt').dataset.init=1; }}
  if (id==='players' && !document.getElementById('pt').dataset.init) {{ fltP(); document.getElementById('pt').dataset.init=1; }}
  if (id==='situations' && !document.getElementById('st').dataset.init) {{ fltS(); document.getElementById('st').dataset.init=1; }}
}}

// ── sorting (shared by the three data tables) ─────────────────────────────
const SORT = {{ m: {{k:'date', d:-1}}, p: {{k:'goals', d:-1}}, s: {{k:'team', d:1}} }};
function sortBy(t, k) {{
  const s = SORT[t];
  if (s.k === k) s.d = -s.d; else {{ s.k = k; s.d = 1; }}
  ({{m: fltM, p: fltP, s: fltS}})[t]();
}}
function cmpRows(t) {{
  const s = SORT[t];
  return (a, b) => {{
    let va = a[s.k], vb = b[s.k];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;            // empties sink regardless of direction
    if (vb == null) return -1;
    if (typeof va === 'string' || typeof vb === 'string') {{
      va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
    }}
    return va < vb ? -s.d : va > vb ? s.d : 0;
  }};
}}
function head(t, cols) {{
  const s = SORT[t];
  return '<tr>' + cols.map(([k, label]) =>
    k ? `<th class="sortable" onclick="sortBy('${{t}}','${{k}}')">${{label}}${{s.k===k ? (s.d>0?' ▲':' ▼') : ''}}</th>`
      : `<th>${{label}}</th>`).join('') + '</tr>';
}}
function seasonOf(r) {{
  const d = r.date || '';
  if (!d) return '';
  const y = Number(d.slice(0,4)), mo = Number(d.slice(5,7));
  if ((r.comp||'') === 'INT' || (r.comp||'') === 'WC') return String(y);
  return mo >= 7 ? y + '-' + String((y+1) % 100).padStart(2,'0')
                 : (y-1) + '-' + String(y % 100).padStart(2,'0');
}}
function fillSelect(id, values) {{
  const sel = document.getElementById(id);
  const keep = sel.value;
  [...new Set(values.filter(Boolean))].sort().forEach(v => {{
    const o = document.createElement('option'); o.value = v; o.textContent = v; sel.appendChild(o);
  }});
  sel.value = keep;
}}

// ── matches table ─────────────────────────────────────────────────────────
function rowM(r) {{
  const xg = r.xg_home!=null ? r.xg_home+'–'+r.xg_away : '';
  const odds = r.odds_h ? r.odds_h.toFixed(2)+' / '+r.odds_d.toFixed(2)+' / '+r.odds_a.toFixed(2) : '';
  return `<tr><td>${{r.date||'—'}}</td><td>${{r.comp||''}}</td><td>${{esc(r.home)}}</td>`+
    `<td><b>${{r.hg}}–${{r.ag}}</b></td><td>${{esc(r.away)}}</td>`+
    `<td class=dim>${{xg}}</td><td class=dim>${{odds}}</td><td class=dim>${{r.source||''}}</td></tr>`;
}}
const M_COLS = [['date','Date'],['comp','Comp'],['home','Home'],['hg','Score'],['away','Away'],
                ['xg_home','xG'],['odds_h','Odds H/D/A'],['source','Source']];
function fltM() {{
  const q=document.getElementById('mq').value.toLowerCase();
  const comp=document.getElementById('mcomp').value;
  const season=document.getElementById('mseason').value;
  const src=document.getElementById('msrc').value;
  const includeOld = document.getElementById('mall').checked;
  const rows = MATCHES.filter(r => {{
    const oldInt = (r.comp||'') === 'INT' && (r.date||'') < '1988-01-01';
    if (!includeOld && oldInt) return false;
    if (comp && (r.comp||'') !== comp) return false;
    if (season && seasonOf(r) !== season) return false;
    if (src && (r.source||'') !== src) return false;
    return q ? JSON.stringify(r).toLowerCase().includes(q) : true;
  }}).sort(cmpRows('m'));
  const show = rows.slice(0,1000);
  document.getElementById('m-cnt').textContent = rows.length+' rows'+(rows.length>1000?' (showing 1000)':'');
  document.getElementById('m-note').textContent = MATCHES.length < MATCHES_TOTAL ? `Loaded ${{MATCHES.length}} of ${{MATCHES_TOTAL}} rows into this browser table; use SQLite for the complete table.` : '';
  document.getElementById('mt').innerHTML = head('m', M_COLS) + show.map(rowM).join('');
}}

// ── player stats table ────────────────────────────────────────────────────
function rowP(r) {{
  const pa = r.pass_accuracy!=null ? r.pass_accuracy.toFixed(1)+'%' : '—';
  const rt = r.rating!=null ? r.rating.toFixed(1) : '—';
  const source = r.source||'';
  const matchLabel = source.includes('international_results:goalscorers.csv')
    ? (r.matches||'—') + ' scoring'
    : source.includes('wikipedia_player_totals')
    ? (r.matches||'—') + ' caps'
    : (r.matches||'—');
  return `<tr><td>${{r.comp||''}}</td><td><b>${{esc(r.player)}}</b></td><td>${{esc(r.team||'')}}</td>`+
    `<td>${{r.goals||0}}</td><td>${{r.assists||0}}</td><td>${{r.penalties||0}}</td>`+
    `<td>${{matchLabel}}</td><td>${{r.minutes||'—'}}</td>`+
    `<td>${{pa}}</td><td>${{r.key_passes||'—'}}</td><td>${{rt}}</td>`+
    `<td class=dim>${{source}}</td></tr>`;
}}
const P_COLS = [['comp','Comp'],['player','Player'],['team','Team'],['goals','G'],['assists','A'],
                ['penalties','Pen'],['matches','Matches / coverage'],['minutes','Min'],
                ['pass_accuracy','Pass%'],['key_passes','Key passes'],['rating','Rating'],['source','Source']];
function fltP() {{
  const q=document.getElementById('pq').value;
  const comp=document.getElementById('pcomp').value;
  const src=document.getElementById('psrc').value;
  const minGoals = Number(document.getElementById('pmin').value || 0);
  const showAll = document.getElementById('pall').checked;
  const rows = PLAYERS.filter(r => {{
    if (comp && (r.comp||'') !== comp) return false;
    if (src && (r.source||'') !== src) return false;
    const text = [r.player, r.team, r.comp, r.source].join(' ');
    if (q && !smartMatch(text, q)) return false;
    if (!q && !showAll && (Number(r.goals||0) < minGoals) && r.comp !== 'INT_CAREER') return false;
    return true;
  }}).sort(cmpRows('p'));
  const show = rows.slice(0,500);
  document.getElementById('p-cnt').textContent = rows.length+' rows'+(rows.length>500?' (showing 500)':'');
  document.getElementById('p-note').textContent = PLAYERS.length < PLAYERS_TOTAL ? `Loaded ${{PLAYERS.length}} of ${{PLAYERS_TOTAL}} rows into this browser table; use SQLite for the complete table.` : '';
  document.getElementById('pt').innerHTML = head('p', P_COLS) + show.map(rowP).join('');
}}

// ── team situations table ─────────────────────────────────────────────────
function rowS(r) {{
  const src = r.source||'';
  const srcEl = src.startsWith('http') ? `<a href="${{esc(src)}}" target="_blank">${{esc(src.slice(0,40))}}…</a>` : esc(src);
  return `<tr><td>${{r.comp||''}}</td><td>${{r.season||''}}</td><td><b>${{esc(r.team||'')}}</b></td>`+
    `<td class=dim style="font-size:11px">${{esc(r.situations_summary||'—')}}</td>`+
    `<td class=dim style="font-size:11px">${{esc(r.xi_summary||'—')}}</td>`+
    `<td class=dim>${{srcEl}}</td></tr>`;
}}
const S_COLS = [['comp','Comp'],['season','Season'],['team','Team'],
                [null,'Shot situations for'],[null,'Likely XI (top 4)'],['source','Source']];
function fltS() {{
  const q=document.getElementById('sq').value.toLowerCase();
  const comp=document.getElementById('scomp').value;
  const season=document.getElementById('sseason').value;
  const rows = SITU.filter(r => {{
    if (comp && (r.comp||'') !== comp) return false;
    if (season && (r.season||'') !== season) return false;
    return q ? JSON.stringify(r).toLowerCase().includes(q) : true;
  }}).sort(cmpRows('s'));
  document.getElementById('s-cnt').textContent = rows.length+' rows';
  document.getElementById('st').innerHTML = head('s', S_COLS) + rows.map(rowS).join('');
}}

// ── utils ─────────────────────────────────────────────────────────────────
function esc(s){{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
function norm(s){{
  return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,'');
}}
function isSubsequence(q, text){{
  let i=0;
  for (const ch of text) if (ch===q[i]) i++;
  return i===q.length;
}}
function smartMatch(text, query){{
  const t=norm(text), q=norm(query);
  if (!q) return true;
  return t.includes(q) || (q.length >= 5 && isSubsequence(q, t));
}}
function shellQuote(s){{
  return "'" + String(s||'').replace(/'/g, "'\\''") + "'";
}}
function manualFilename(){{
  const team = norm(document.getElementById('man-team').value || 'manual');
  const date = (document.getElementById('man-date').value || 'undated').replace(/[^0-9-]/g,'');
  return `manual-analysis-${{team||'team'}}-${{date||'date'}}.md`;
}}
function buildManualCmd(){{
  const team = document.getElementById('man-team').value.trim();
  const date = document.getElementById('man-date').value.trim();
  const title = document.getElementById('man-title').value.trim();
  const file = manualFilename();
  let cmd = `uv run footballagents qual-data --note-file ${{shellQuote(file)}}`;
  if (team) cmd += ` --team ${{shellQuote(team)}}`;
  if (date) cmd += ` --date ${{shellQuote(date)}}`;
  if (title) cmd += ` --title ${{shellQuote(title)}}`;
  document.getElementById('man-cmd').textContent = cmd;
  return cmd;
}}
function downloadManualNote(){{
  const text = document.getElementById('man-text').value || '';
  const blob = new Blob([text], {{type:'text/markdown;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = manualFilename();
  a.click();
  URL.revokeObjectURL(a.href);
  buildManualCmd();
}}
async function copyManualCmd(){{
  const cmd = buildManualCmd();
  if (navigator.clipboard) await navigator.clipboard.writeText(cmd);
}}

// ── per-player notes: build the note-player command + list existing ───────
function buildNoteCmd(){{
  const team = document.getElementById('pn-team').value.trim();
  const player = document.getElementById('pn-player').value.trim();
  const note = document.getElementById('pn-note').value.trim();
  let cmd = 'uv run footballagents note-player';
  cmd += ' ' + shellQuote(player || 'Player Name');
  cmd += ' -t ' + shellQuote(team || 'Team');
  cmd += ' --note ' + shellQuote(note || 'your style note');
  document.getElementById('pn-cmd').textContent = cmd;
  return cmd;
}}
async function copyNoteCmd(){{
  const cmd = buildNoteCmd();
  if (navigator.clipboard) await navigator.clipboard.writeText(cmd);
}}
function rowPN(n){{
  return `<tr><td>${{esc(n.team)}}</td><td><b>${{esc(n.player)}}</b></td>`+
    `<td>${{esc(n.note)}}</td><td class=dim>${{esc(n.updated_at||'')}}</td></tr>`;
}}
function fltPN(){{
  const q=(document.getElementById('pnq').value||'').toLowerCase();
  const rows = q ? PLAYER_NOTES.filter(n=>JSON.stringify(n).toLowerCase().includes(q)) : PLAYER_NOTES;
  document.getElementById('pn-cnt').textContent = rows.length+' notes';
  document.getElementById('pnt').innerHTML =
    '<tr><th>Team</th><th>Player</th><th>Note</th><th>Updated</th></tr>'+rows.map(rowPN).join('');
}}

// ── static tables: click any header to sort its rows ──────────────────────
function makeStaticSortable() {{
  document.querySelectorAll('table').forEach(tbl => {{
    if (['mt','pt','st'].includes(tbl.id)) return;       // data tables sort via state
    const ths = tbl.querySelectorAll('tr:first-child th');
    if (ths.length < 2) return;
    ths.forEach((th, idx) => {{
      th.classList.add('sortable');
      th.addEventListener('click', () => {{
        const rows = [...tbl.querySelectorAll('tr')].slice(1);
        const dir = th.dataset.dir === 'asc' ? -1 : 1;
        ths.forEach(h => delete h.dataset.dir);
        th.dataset.dir = dir === 1 ? 'asc' : 'desc';
        rows.sort((a, b) => {{
          const ta = (a.children[idx]?.textContent || '').trim();
          const tb = (b.children[idx]?.textContent || '').trim();
          const na = parseFloat(ta.replace(/[%,]/g,'')), nb = parseFloat(tb.replace(/[%,]/g,''));
          if (!isNaN(na) && !isNaN(nb)) return (na - nb) * dir;
          return ta.localeCompare(tb) * dir;
        }});
        rows.forEach(r => tbl.appendChild(r));
      }});
    }});
  }});
}}

// init: populate filter dropdowns, render the data tables, arm static sorting
fillSelect('mcomp', MATCHES.map(r => r.comp));
fillSelect('mseason', MATCHES.map(seasonOf));
fillSelect('msrc', MATCHES.map(r => r.source));
fillSelect('pcomp', PLAYERS.map(r => r.comp));
fillSelect('psrc', PLAYERS.map(r => r.source));
fillSelect('scomp', SITU.map(r => r.comp));
fillSelect('sseason', SITU.map(r => r.season));
fltM();
document.getElementById('mt').dataset.init=1;
makeStaticSortable();
buildManualCmd();
buildNoteCmd();
fltPN();
</script>
</body></html>"""


def export_data_explorer(config: dict | None = None) -> Path:
    config = dict(config or DEFAULT_CONFIG)
    inv = build_inventory(config)
    # Write to the repo root (not exports/) so the explorer sits at the
    # top level: <repo>/data_explorer.html. Anchored to the package location
    # so it lands correctly regardless of the working directory.
    out_dir = Path(__file__).resolve().parents[2]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "data_explorer.html"
    path.write_text(render_html(inv), encoding="utf-8")
    return path
