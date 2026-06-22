"""CLI — predict a fixture, choose your LLM provider, or inspect the data layer.

    worldcupagents predict "Spain" "Brazil" --stage group --provider deepseek
    worldcupagents predict "France" "USA" --stage QF --venue "Mexico City" --llm
    worldcupagents predict -i                  # fully guided arrow-key flow
    worldcupagents check --team "South Korea"
    worldcupagents eliminate Germany Belgium   # mark teams eliminated
    worldcupagents eliminate --list            # show eliminated teams
    worldcupagents eliminate --reset           # clear all

After every prediction you will be asked: "Export to .txt? [y/N]"
If you press Y the file is saved to exports/ with a unique filename.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.world_cup_2026 import (
    TEAMS_BY_CONF,
    WC2026_TEAMS,
    WC2026_VENUES,
    add_eliminated,
    load_eliminated,
    remove_eliminated,
    reset_eliminated,
)
from worldcupagents.graph.predict import Predictor
from worldcupagents.llm_clients.model_catalog import (
    API_KEY_ENV,
    PRICING_AS_OF,
    PROVIDERS,
    cost_label,
    default_models,
    model_choices,
)

app = typer.Typer(
    add_completion=False,
    help="FootballAgents — predict football matches (World Cup + big-5 leagues) via agent debate.",
)
console = Console()


def _refit_judge_weight_and_report(config: dict | None = None) -> None:
    """Recompute the recency-weighted, shrunk-to-prior blend weight from the eval
    log and print one line. Best-effort — never breaks the matchday loop."""
    try:
        from worldcupagents.calibration import refit_judge_weight
        info = refit_judge_weight(config or DEFAULT_CONFIG)
        if info is None:
            return  # no usable eval reads yet — stays at the 0.6 prior
        console.print(
            f"[green]✓ judge_weight[/green] {info['weight']} "
            f"[dim](shrunk from fit {info['w_fit']} toward 0.6 prior, n={info['n']})[/dim]")
    except Exception as e:  # noqa: BLE001 — weight refit must not break the loop
        console.print(f"[dim]judge_weight refit skipped ({e})[/dim]")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    """FootballAgents — predict football matches via an agent debate.

    Run with no command (`footballagents`) for a guided, arrow-key menu.
    """
    if ctx.invoked_subcommand is not None:
        return  # a real command was given — run it normally
    # No command: show the guided launcher on a TTY, else fall back to --help.
    if sys.stdin.isatty():
        _launch_menu()
    else:
        typer.echo(ctx.get_help())


@app.command()
def predict(
    home: str = typer.Argument(None, help="Home / first team (leave blank with -i to pick from list)"),
    away: str = typer.Argument(None, help="Away / second team (leave blank with -i to pick from list)"),
    date: str = typer.Option(None, "--date", help="Kickoff date YYYY-MM-DD"),
    stage: Stage = typer.Option(None, "--stage",
                                help="group/R32/R16/QF/SF/F (default: auto-detect from the fixture feed)"),
    venue: str = typer.Option(None, "--venue", help="Host city — leave blank with -i to pick from list"),
    provider: str = typer.Option(
        None, "--provider", "-p",
        help="LLM provider: anthropic/openai/google/deepseek (implies --llm)",
    ),
    deep_model: str = typer.Option(None, "--deep-model", help="Override the judge/reasoning model"),
    quick_model: str = typer.Option(None, "--quick-model", help="Override the advocate model"),
    llm: bool = typer.Option(None, "--llm/--no-llm", help="Force LLM agents on/off"),
    rounds: int = typer.Option(None, "--rounds", help="Debate rounds (default 2)"),
    depth: str = typer.Option(
        None, "--depth", help="Research depth preset: shallow / medium / deep "
        "(maps debate+scenario rounds, TA-style; individual flags still override)",
    ),
    scenario: bool = typer.Option(
        None, "--scenario/--no-scenario",
        help="Run the scenario (risk) debate + Final Pundit after the judge",
    ),
    scenario_rounds: int = typer.Option(None, "--scenario-rounds", help="Scenario debate rounds (default 1)"),
    market: bool = typer.Option(
        None, "--market/--no-market",
        help="Show the live bookmaker consensus to the judge. --no-market for hypothetical "
             "matchups or in-play games (it's auto-skipped offline and when no fixture odds exist).",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i",
        help="Fully guided arrow-key flow: pick teams, venue, provider, models, and depth",
    ),
    league: str = typer.Option(
        None, "--league", "-L",
        help="Competition: WC2026 (default) / PL / PD / SA / BL1 / FL1. See `leagues`.",
    ),
    season: str = typer.Option(
        None, "--season",
        help="Club-league season, e.g. 2025-26 (current) or 2023-24 (historical: that "
             "season's squad via Wikipedia, form/records/strengths cut off at season end)",
    ),
):
    """Run the prediction pipeline for one fixture and print the verdict.

    After the verdict is shown you will be prompted: Export to .txt? [y/N]
    If you choose Y the report is saved to the exports/ folder automatically.
    """
    # ── Guided flow: league → season → teams → venue (runs before LLM config) ──
    if interactive and sys.stdin.isatty():
        from worldcupagents.leagues.registry import get_league
        if not league:
            league = _pick_league() or league
        try:
            lg_obj = get_league(league)
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            raise typer.Exit(1)
        if season is None and lg_obj.kind == "league":
            season = _pick_season(lg_obj)
        sel = _guided_setup(lg_obj, home, away, venue)
        if sel:
            home  = home  or sel["home"]
            away  = away  or sel["away"]
            venue = venue or sel["venue"]

    # ── Validate teams were provided one way or another ────────────────────
    if not home or not away:
        console.print("[red]✗ Home and away teams are required.[/red] "
                      "Pass them as arguments or use [bold]-i[/bold] for the interactive picker.")
        raise typer.Exit(1)

    cfg = _build_config(provider, deep_model, quick_model, llm, rounds, interactive)

    # Research depth: -i prompts for it; preset applies first, explicit flags override.
    if interactive and depth is None and sys.stdin.isatty():
        depth = _pick_depth()
    if depth:
        from worldcupagents.config import apply_research_depth
        try:
            apply_research_depth(cfg, depth)
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            raise typer.Exit(1)
        if rounds is not None:                  # explicit flag beats the preset
            cfg["max_debate_rounds"] = rounds
    if scenario is not None:
        cfg["enable_scenario_debate"] = scenario
    if scenario_rounds is not None:
        cfg["max_scenario_rounds"] = scenario_rounds
    if market is not None:
        cfg["enable_market_context"] = market

    if season:
        from worldcupagents.seasons import normalize_season
        try:
            cfg["season"] = normalize_season(season)
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            raise typer.Exit(1)

    lg = _resolve_league(cfg, league)
    resolved_stage, stage_src = _resolve_fixture_stage(stage, home, away, date, cfg, lg, interactive)
    fx = Fixture(home=home, away=away, stage=resolved_stage, venue=venue, kickoff=_parse_date(date))

    mode = (
        f"LLM: {cfg['llm_provider']} (deep={cfg['deep_think_llm']}, quick={cfg['quick_think_llm']})"
        if cfg["use_llm"] else "baseline-only (no LLM)"
    )
    scenario_note = f"   scenario debate: {'on' if cfg.get('enable_scenario_debate') else 'off'}"
    season_note = ""
    if cfg.get("season") and lg.kind == "league":
        historic = " (historical)" if cfg["season"] != lg.season else ""
        season_note = f"   season: {cfg['season']}{historic}"
    stage_src_note = f" [dim](from {stage_src})[/dim]" if stage_src in ("feed", "default (group)") else ""
    stage_label = ("league match" if cfg.get("league_kind") == "league" and not fx.knockout
                   else f"stage={fx.stage.value}{stage_src_note}")
    venue_label = fx.venue or ("home/away" if cfg.get("league_kind") == "league" else "TBD")
    console.print(f"[dim]competition: {lg.name}{season_note}{scenario_note}[/dim]")
    console.print(Panel.fit(
        f"[bold]{fx.home}[/bold]  vs  [bold]{fx.away}[/bold]\n"
        f"{stage_label}   venue={venue_label}\n[dim]{mode}[/dim]",
        title="Fixture",
    ))

    predictor = Predictor(cfg)
    if sys.stdout.isatty():
        from worldcupagents.tui import run_predict_live
        final, v = run_predict_live(predictor, fx, console)
    else:
        final, v = predictor.predict(fx)   # CI/scripts: plain run, output below

    _print_squads(final)            # the rich inputs, visibly
    _print_analyst_reports(final)

    console.print(Panel(
        (final["debate_state"]["history"].strip() or "(no debate)"),
        title="Advocate debate", border_style="cyan",
    ))

    scenario_hist = (final.get("scenario_debate_state") or {}).get("history", "").strip()
    if scenario_hist:
        console.print(Panel(scenario_hist, title="Scenario (risk) debate", border_style="magenta"))

    t = Table(title="Verdict", show_header=False)
    suffix = f"  (via {v.decided_by.value})" if fx.knockout else ""
    t.add_row("Outcome", f"[bold]{v.outcome.value}[/bold]{suffix}")
    score_extra = (f"   [dim](expected goals {v.exp_goals_home:.1f}–{v.exp_goals_away:.1f})[/dim]"
                   if v.exp_goals_home is not None else "")
    t.add_row("Scoreline", f"{v.scoreline}{score_extra}")
    t.add_row("Probabilities", f"H {v.p_home:.0%}  /  D {v.p_draw:.0%}  /  A {v.p_away:.0%}")
    prov = final.get("provisional_verdict")
    if prov is not None and prov != v:
        t.add_row(
            "↳ provisional",
            f"[dim]judge had H {prov.p_home:.0%} / D {prov.p_draw:.0%} / A {prov.p_away:.0%} "
            f"({prov.outcome.value} {prov.scoreline}) — adjusted by the Final Pundit "
            f"after the scenario debate[/dim]",
        )
    if v.breakdown:
        b = v.breakdown
        t.add_row(
            "↳ how",
            f"[dim]judge read {b.judge_home:.0%}/{b.judge_draw:.0%}/{b.judge_away:.0%}  "
            f"⊕ baseline {b.base_home:.0%}/{b.base_draw:.0%}/{b.base_away:.0%}  "
            f"(judge weight {b.judge_weight:.0%}) → blended above[/dim]",
        )
    t.add_row("Confidence", v.confidence)
    t.add_row("Key factors", "; ".join(v.key_factors) or "—")
    t.add_row("X-factors", "; ".join(v.x_factors) or "—")
    t.add_row("Rationale", v.rationale)

    # Token usage + cost (only meaningful when use_llm is True and tokens were captured)
    usage = predictor.last_usage
    if usage["input"] or usage["output"]:
        cost_str = (
            f"  ≈ [bold]${predictor.last_cost:.4f}[/bold]" if predictor.last_cost is not None else ""
        )
        t.add_row(
            "Token usage",
            f"[dim]{usage['input']:,} in / {usage['output']:,} out[/dim]{cost_str}",
        )

    console.print(t)

    # The honest counterweight: the upset watch (always shown — favourites lose).
    alt = v.alternative
    if alt is not None:
        factors = []
        try:
            from worldcupagents.ensemble.alternative import upset_factors
            factors = upset_factors(cfg, fx, final.get("home_profile"), final.get("away_profile"), alt)
        except Exception:  # noqa: BLE001
            pass
        alt.swing_factors = factors  # persist so the markdown export carries them
        flag = "⚠️  Upset watch" if alt.live else "Long-shot alternative"
        body = [f"[bold]{alt.outcome.value}  {alt.scoreline}[/bold]   "
                f"[bold]{alt.probability:.0%}[/bold]  [dim](call is {alt.gap:.0%} ahead)[/dim]",
                alt.narrative]
        body += [f"  • {f}" for f in factors]
        console.print(Panel("\n".join(body), title=flag,
                            border_style="yellow" if alt.live else "dim"))

    # Model vs market — how the call differs from the sharpest prior.
    mr = (final.get("matchup_context") or {}).get("market")
    if mr:
        from worldcupagents.dataflows.market import divergence_note, market_digest
        console.print(Panel(f"{market_digest(mr)}\n[bold]{divergence_note(v, mr)}[/bold]",
                            title="📈 Market", border_style="cyan"))

    # ── Post-output export prompt ───────────────────────────────────────────
    if sys.stdin.isatty():
        if typer.confirm("\nExport this report?", default=False):
            import questionary
            fmt = questionary.select(
                "Format",
                choices=[
                    questionary.Choice("Markdown — sectioned complete report (recommended)", value="md"),
                    questionary.Choice("Plain text", value="txt"),
                    questionary.Choice("Both", value="both"),
                ],
                default="md",
            ).ask() or "md"
            if fmt in ("md", "both"):
                from worldcupagents.pipelines.report_export import export_markdown_report
                md_path = export_markdown_report(fx, v, final, predictor, cfg)
                console.print(f"[green]✓ Saved[/green] {md_path}")
            if fmt in ("txt", "both"):
                out_path = _auto_export_path(fx, cfg)
                _export_txt(out_path, fx, v, final, predictor, cfg)
                console.print(f"[green]✓ Saved[/green] {out_path}")


@app.command()
def check(team: str = typer.Option(None, "--team", help="Resolve a team against the active vendor")):
    """Show the active data vendor, token status, and (optionally) resolve a team."""
    from worldcupagents.dataflows import fifa_rankings
    from worldcupagents.dataflows.interface import available_providers, get_provider

    has_token = bool(os.environ.get("FOOTBALL_DATA_ORG_TOKEN"))
    provider = get_provider(DEFAULT_CONFIG, "squads")

    t = Table(title="WorldCupAgents — data layer", show_header=False)
    t.add_row("football-data.org token", "✅ detected" if has_token else "❌ not set (using placeholder)")
    t.add_row("Active 'squads' provider", provider.name)
    t.add_row("Registered providers", ", ".join(available_providers()))
    t.add_row("Rankings snapshot", fifa_rankings.RANKING_AS_OF)
    console.print(t)

    if team:
        p = provider.get_team_profile(team)
        rt = Table(title=f"Resolved: {team!r}", show_header=False)
        rt.add_row("Name", p.team)
        rt.add_row("FIFA rank", str(p.fifa_rank))
        rt.add_row("Squad size", str(len(p.squad)))
        rt.add_row("Recent results", str(len(p.form)))
        rt.add_row("Style", p.style or "—")
        rt.add_row("Sources", ", ".join(p.sources))
        console.print(rt)


@app.command()
def sources(
    probe: bool = typer.Option(False, "--probe/--no-probe",
                               help="Live-ping each source (off by default — key-presence + store freshness only)"),
):
    """Supervise every data source: is the key set, is it reachable (--probe), and how
    fresh/covered is its data in the store. Deterministic, no LLM."""
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    from worldcupagents.pipelines.data_explorer import _sources_with_checks

    srcs = _sources_with_checks(probe=probe)
    icon = {"ok": "🟢", "error": "🔴", "skipped": "⚪", "unprobed": "⚪"}
    t = Table(title=f"Data sources{' (live probe)' if probe else ''}")
    t.add_column("Source"); t.add_column("Key"); t.add_column("Status"); t.add_column("Supplies", max_width=58)
    for s in srcs:
        keyed = "✅" if s["configured"] else "—"
        chk = s.get("check") or {}
        status = f"{icon.get(chk.get('status'), '⚪')} {chk.get('detail', '')}".strip()
        if chk.get("ms"):
            status += f" ({chk['ms']}ms)"
        t.add_row(s["name"], keyed, status, s["provides"])
    console.print(t)

    healthy = sum(1 for s in srcs if (s.get("check") or {}).get("status") == "ok")
    missing = sum(1 for s in srcs if not s["configured"])
    console.print(f"[dim]{len(srcs)} sources · {missing} missing a key"
                  + (f" · {healthy} reachable" if probe else " · run --probe for live reachability") + "[/dim]")

    # Store coverage — what data actually landed, by source, with freshness.
    if db_path(DEFAULT_CONFIG).exists():
        store = MatchStore.from_config(DEFAULT_CONFIG)
        try:
            cov, wh = store.source_coverage(), store.warehouse_counts()
        finally:
            store.close()
        if cov:
            ct = Table(title="Match store — coverage by source")
            ct.add_column("source"); ct.add_column("rows", justify="right"); ct.add_column("newest")
            for r in cov:
                ct.add_row(r["source"], f"{r['rows']:,}", r["latest"] or "—")
            console.print(ct)
        live_wh = {k: v for k, v in (wh or {}).items() if v}
        if live_wh:
            console.print("[dim]warehouse: " + ", ".join(f"{k}={v:,}" for k, v in live_wh.items()) + "[/dim]")
    else:
        console.print("[dim]No match store yet — run `fetch-data` to populate coverage.[/dim]")


@app.command()
def players(
    team: str = typer.Argument(..., help="Team to show per-player metrics for"),
    league: str = typer.Option(None, "--league", "-L", help="Competition (default WC2026). See `leagues`."),
):
    """Show a team's leading players (goals/assists) from the stats store.

    Seed first, e.g. `fetch-data -L PL`, then `players "Manchester City FC" -L PL`.
    """
    from worldcupagents.recall import top_players

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    _resolve_league(cfg, league)
    ps = top_players(team, cfg, n=10)
    if not ps:
        console.print(f"[yellow]No player metrics for {team}.[/yellow] "
                      f"Run [bold]fetch-data -L {cfg.get('league')}[/bold] first.")
        raise typer.Exit(1)
    has_rich = any(p.pass_accuracy is not None or p.rating is not None for p in ps)
    t = Table(title=f"Top players — {team}")
    t.add_column("Player"); t.add_column("G", justify="right"); t.add_column("A", justify="right")
    t.add_column("Pens", justify="right"); t.add_column("Apps", justify="right")
    if has_rich:
        t.add_column("Pass%", justify="right"); t.add_column("Rating", justify="right")
    for p in ps:
        row = [p.player, str(p.goals), str(p.assists), str(p.penalties), str(p.matches)]
        if has_rich:
            row += [f"{p.pass_accuracy:.0f}" if p.pass_accuracy is not None else "—",
                    f"{p.rating:.2f}" if p.rating is not None else "—"]
        t.add_row(*row)
    console.print(t)


@app.command()
def explore(
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page in your browser"),
):
    """Generate the Data Explorer — one HTML page showing every data source,
    the match/player store, memory artifacts, and (the point) the DATA GAPS
    panel suggesting which source to add next."""
    from worldcupagents.pipelines.data_explorer import export_data_explorer

    path = export_data_explorer(DEFAULT_CONFIG)
    console.print(f"[green]✓ Data explorer written to[/green] {path}")
    if open_browser:
        import webbrowser
        webbrowser.open(path.as_uri())


@app.command()
def leagues():
    """List the competitions you can predict (pass one with --league/-L)."""
    from worldcupagents.leagues.registry import DEFAULT_LEAGUE_KEY, list_leagues

    t = Table(title="Competitions")
    t.add_column("Key"); t.add_column("Name"); t.add_column("Kind"); t.add_column("FD code")
    for lg in list_leagues():
        key = f"{lg.key}  [dim](default)[/dim]" if lg.key == DEFAULT_LEAGUE_KEY else lg.key
        t.add_row(key, lg.name, lg.kind, lg.fd_competition)
    console.print(t)
    console.print("[dim]e.g.  predict \"Arsenal FC\" \"Liverpool FC\" -L PL -p openai[/dim]")


@app.command(name="resolve-name")
def resolve_name_cmd(
    name: str = typer.Argument(..., help="Team/club/country spelling to resolve"),
    kind: str = typer.Option(None, "--kind", help="Optional entity kind: national|club|regional|unknown"),
    source: str = typer.Option(None, "--source", help="Optional source id, e.g. international_results"),
):
    """Resolve a team/club/country spelling to the internal entity registry."""
    from worldcupagents.dataflows.entities import resolution_to_json, resolve_team, seed_identity_registry

    seed_identity_registry(DEFAULT_CONFIG)
    res = resolve_team(name, kind=kind, source_id=source, config=DEFAULT_CONFIG, record_unresolved=True, context="cli")
    console.print_json(resolution_to_json(res))


@app.command(name="fetch-data")
def fetch_data_cmd(
    from_csv: str = typer.Option(None, "--from-csv", help="Seed from a results CSV (home,away,home_goals,away_goals[,date,comp,xg_home,xg_away])"),
    seasons: str = typer.Option(None, "--seasons", help="Comma list of football-data.co.uk season codes for deep history, e.g. 2223,2324,2425"),
    national_history: bool = typer.Option(False, "--national-history", help="Fetch recent senior national-team results for all WC2026 teams via API-Football"),
    national_limit: int = typer.Option(5, "--national-limit", min=1, max=30, help="Recent matches per WC2026 team for --national-history"),
    xg: bool = typer.Option(False, "--xg", help="Pull Understat per-team data: situation breakdowns (set pieces/corners/pens) + per-match xG onto stored rows"),
    season: str = typer.Option(None, "--season", help="Season for --xg (default: the league's current season)"),
    league: str = typer.Option(None, "--league", "-L", help="Competition to fetch (default WC2026). See `leagues`."),
    competition: str = typer.Option(None, "--competition", "-c", help="Raw football-data.org code override: PL/PD/SA/BL1/FL1/WC"),
):
    """Populate the SQLite match store (data/football.db) that the stats tier fits on.

    Sources: current season from football-data.org (`fetch-data -L PL`); deep
    multi-season history from football-data.co.uk (`fetch-data -L PL --seasons
    2223,2324,2425`); recent WC2026 national-team form from API-Football
    (`fetch-data --national-history --national-limit 5`); or a CSV via --from-csv.
    """
    from worldcupagents.pipelines.fetch_data import fetch_data

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    _resolve_league(cfg, league)
    if competition:                       # raw code wins over the league mapping
        cfg["fd_competition"] = competition

    if xg:
        from worldcupagents.pipelines.fetch_data import fetch_understat_xg
        res = fetch_understat_xg(cfg, season=season)
        console.print(
            f"[green]✓ understat[/green] season={res['season']}  "
            f"teams with situation data={res['teams']}  xG filled on {res['xg_rows']} match rows  "
            f"likely XIs={res.get('xis', 0)}  player metric rows={res.get('players', 0)}"
        )
        return

    season_list = [s.strip() for s in seasons.split(",") if s.strip()] if seasons else None
    res = fetch_data(
        cfg,
        csv_path=from_csv,
        seasons=season_list,
        national_history=national_history,
        national_limit=national_limit,
    )
    players_note = f"  players={res['players']}" if res.get("players") else ""
    console.print(
        f"[green]✓ fetch-data[/green] source={res['source']}  "
        f"added/updated={res['added']}  total in store={res['total']}{players_note}"
    )
    if res["total"] == 0:
        console.print("[yellow]Store is empty.[/yellow] Pre-tournament, football-data.org has no "
                      "finished WC matches yet — seed with --from-csv to fit strengths now.")

    # Auto-close the learning loop: any pending prediction whose result just
    # landed in the store gets resolved (no LLM reflection here; use
    # `resolve --sync --provider X` for that).
    try:
        from worldcupagents.graph.reflection import sync_pending
        synced = sync_pending(DEFAULT_CONFIG)
        if synced:
            console.print(f"[green]✓ auto-resolved {len(synced)} pending prediction(s)[/green] "
                          f"[dim](see `resolve --sync` for details / LLM reflections)[/dim]")
    except Exception as e:  # noqa: BLE001 — learning loop must not break a fetch
        console.print(f"[dim]auto-resolve skipped ({e})[/dim]")


@app.command(name="hoard-data")
def hoard_data_cmd(
    source: str = typer.Option("international-results", "--source",
                               help="Source to hoard: international-results|wikipedia-player-totals|statsbomb|openfootball|football-data-couk|all"),
    refresh: bool = typer.Option(False, "--refresh/--no-refresh", help="Re-download raw source files even if this snapshot exists"),
    populate_summary: bool = typer.Option(True, "--populate-summary/--no-populate-summary",
                                          help="Also feed existing matches/player_stats summary tables"),
    limit_source: int = typer.Option(None, "--limit-source", help="Debug/test limit for rows read from each source file"),
):
    """Hoard public football data into raw snapshots + warehouse tables.

    Current public sources: martj42/international_results, Wikipedia player
    career totals, and StatsBomb Open Data World Cup events.
    """
    from worldcupagents.pipelines.hoard_data import hoard_data

    source_key = source.replace("_", "-").lower()
    if source_key == "international-results":
        source_key = "international_results"
    elif source_key == "wikipedia-player-totals":
        source_key = "wikipedia_player_totals"
    elif source_key in ("statsbomb", "statsbomb-open-data"):
        source_key = "statsbomb_open_data"
    res = hoard_data(
        DEFAULT_CONFIG,
        source=source_key,
        refresh=refresh,
        populate_summary=populate_summary,
        limit_source=limit_source,
    )
    console.print(
        f"[green]✓ hoard-data[/green] source={res.source} snapshot={res.snapshot} raw={res.raw_dir}"
    )
    for k, v in sorted(res.counts.items()):
        console.print(f"  {k}: {v}")


@app.command(name="qual-data")
def qual_data_cmd(
    url: str = typer.Option(None, "--url", help="Public article URL to ingest"),
    feed_url: str = typer.Option(None, "--feed-url", help="Public RSS/Atom feed URL to ingest"),
    delete_document: str = typer.Option(None, "--delete-document", help="Delete one qualitative document by document_id"),
    note: str = typer.Option(None, "--note", help="Manual analysis text to ingest"),
    note_file: str = typer.Option(None, "--note-file", help="Path to a text/markdown file with manual analysis"),
    title: str = typer.Option(None, "--title", help="Title for --note/--note-file or override for --url"),
    author: str = typer.Option("user", "--author", help="Author/source label for manual analysis"),
    limit: int = typer.Option(10, "--limit", min=1, max=100, help="Max feed items to ingest for --feed-url"),
    include: list[str] = typer.Option([], "--include", help="Only ingest feed items containing this text; repeatable"),
    team: list[str] = typer.Option([], "--team", help="Team mentioned in the public article; repeatable"),
    home: str = typer.Option(None, "--home", help="Home / first team for Guardian commentary"),
    away: str = typer.Option(None, "--away", help="Away / second team for Guardian commentary"),
    date: str = typer.Option(None, "--date", help="Match/article date YYYY-MM-DD when known"),
    refresh: bool = typer.Option(False, "--refresh/--no-refresh", help="Replace existing segments for the same document"),
):
    """Ingest qualitative football text into the warehouse.

    Use --url for one public article, --feed-url for RSS/Atom feeds, or
    --home/--away for Guardian commentary.
    """
    from worldcupagents.pipelines.qualitative_data import (
        delete_qual_document,
        ingest_guardian_match,
        ingest_manual_note,
        ingest_public_article,
        ingest_rss_feed,
    )

    try:
        if delete_document:
            deleted = delete_qual_document(delete_document, config=DEFAULT_CONFIG)
            console.print(f"[green]✓ deleted[/green] document={deleted.document_id}")
            for k, v in sorted(deleted.counts.items()):
                console.print(f"  {k}: {v}")
            return
        if note or note_file:
            text = note or Path(note_file).read_text(encoding="utf-8")
            res = ingest_manual_note(
                text,
                config=DEFAULT_CONFIG,
                teams=list(team),
                title=title,
                date=date,
                author=author,
                refresh=refresh,
            )
        elif feed_url:
            res = ingest_rss_feed(
                feed_url,
                config=DEFAULT_CONFIG,
                teams=list(team),
                limit=limit,
                include_terms=list(include),
                refresh=refresh,
            )
        elif url:
            res = ingest_public_article(url, config=DEFAULT_CONFIG, teams=list(team), title=title, refresh=refresh)
        else:
            if not home or not away:
                console.print("[red]✗ Provide --url, --feed-url, --note/--note-file, --delete-document, or both --home and --away.[/red]")
                raise typer.Exit(1)
            res = ingest_guardian_match(home, away, date=date, config=DEFAULT_CONFIG, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ qual-data failed:[/red] {exc}")
        raise typer.Exit(1)

    console.print(
        f"[green]✓ qual-data[/green] source={res.source_id} document={res.document_id}"
    )
    console.print(f"[dim]raw: {res.raw_path}[/dim]")
    for k, v in sorted(res.counts.items()):
        console.print(f"  {k}: {v}")


@app.command()
def backtest(
    fixtures: str = typer.Argument(None, help="CSV of historical results (home,away,home_goals,away_goals). Omit for the bundled sample."),
    from_store: bool = typer.Option(False, "--from-store", help="Backtest on the SQLite match store instead of a CSV"),
    league: str = typer.Option(None, "--league", "-L", help="Filter the store to a league (default WC2026). See `leagues`."),
    competition: str = typer.Option(None, "--competition", "-c", help="Raw fd code override for the store filter, e.g. PL"),
):
    """Calibration yardstick: Brier score + hit-rate of the rank-Elo Poisson baseline
    vs naive references on historical results. Re-run after the stats tier to prove the gain.

    Use --from-store -L PL to backtest a real league season (where the fitted-strength
    model finally has the match volume to beat the references).
    """
    from worldcupagents.pipelines.backtest import backtest as run

    comp = competition
    if from_store and comp is None and league:
        from worldcupagents.leagues.registry import get_league
        try:
            comp = get_league(league).fd_competition
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]"); raise typer.Exit(1)
    res = run(fixtures, config=DEFAULT_CONFIG, from_store=from_store, comp=comp)
    if res.n_matches == 0:
        console.print("[yellow]No matches to backtest.[/yellow] Seed the store first, e.g. `fetch-data -c PL`.")
        raise typer.Exit(1)
    label = f"{comp or 'store'} " if from_store else ""
    t = Table(title=f"Backtest — {label}{res.n_matches} matches (lower Brier = better)")
    t.add_column("Model"); t.add_column("Mean Brier", justify="right")
    t.add_column("Hit-rate", justify="right"); t.add_column("n", justify="right")
    # Sort by Brier ascending so the best model is on top.
    for s in sorted(res.scores.values(), key=lambda s: s.mean_brier):
        n = str(s.n) if s.n != res.n_matches else "—"
        t.add_row(s.name, f"{s.mean_brier:.3f}", f"{s.hit_rate:.0%}", n)
    console.print(t)
    console.print("[dim]Caveat: pre-2026 backtest leaks training data & uses present-day ranks — "
                  "read the relative gaps, not absolute values.[/dim]")


@app.command()
def evaluate(
    provider: str = typer.Option(None, "--provider", "-p",
                                 help="LLM provider to evaluate (spends tokens!). Omit with --fit-weight to reuse the log."),
    league: str = typer.Option(None, "--league", "-L", help="Competition to sample matches from, e.g. PL"),
    season: str = typer.Option(None, "--season", help="Season window, e.g. 2025-26"),
    last: int = typer.Option(10, "--last", "-n", help="How many recent matches to evaluate (newest first)"),
    depth: str = typer.Option("shallow", "--depth", help="Research depth preset (shallow keeps cost sane)"),
    fit_weight: bool = typer.Option(False, "--fit-weight",
                                    help="Only grid-search judge_weight over ALL logged reads (no LLM spend)"),
):
    """Does the LLM debate earn its cost? Runs the REAL predict graph over recent
    store matches with known results and scores baseline vs raw-judge vs blend
    (vs market where odds exist). Reads accumulate in data/eval_log.jsonl.

    \b
      worldcupagents evaluate -L PL --season 2025-26 --last 10 -p deepseek
      worldcupagents evaluate --fit-weight      # re-fit judge_weight, zero spend
    """
    from worldcupagents.pipelines.evaluate import (
        fit_judge_weight, load_eval_log, pick_rows, run_eval, score_records)

    cfg = copy.deepcopy(DEFAULT_CONFIG)

    if not fit_weight:
        if not provider:
            console.print("[red]✗ --provider is required to run an eval (it spends tokens), "
                          "or use --fit-weight to reuse logged reads.[/red]")
            raise typer.Exit(1)
        cfg["use_llm"] = True
        cfg["llm_provider"] = provider
        from worldcupagents.llm_clients.model_catalog import default_models
        cfg["deep_think_llm"], cfg["quick_think_llm"] = default_models(provider)
        from worldcupagents.config import apply_research_depth
        apply_research_depth(cfg, depth)
        if season:
            from worldcupagents.seasons import normalize_season
            cfg["season"] = normalize_season(season)
        _resolve_league(cfg, league)

        from worldcupagents.pipelines.evaluate import evaluated_keys
        done = evaluated_keys(cfg, provider)
        rows = pick_rows(cfg, last_n=last, exclude=done)
        if done:
            console.print(f"[dim]{len(done)} fixture(s) already evaluated with {provider} — "
                          f"skipped (reads accumulate in the log).[/dim]")
        if not rows:
            console.print("[yellow]No store matches to evaluate.[/yellow] Run fetch-data first.")
            raise typer.Exit(1)
        console.print(f"[dim]Evaluating {len(rows)} matches "
                      f"({rows[0]['date']} → {rows[-1]['date']}) with {provider} at depth={depth}. "
                      f"Results may predate the model's training cutoff — newest matches are cleanest.[/dim]")

        def progress(i, n, rec):
            console.print(f"  [{i}/{n}] {rec['home']} {rec['hg']}–{rec['ag']} {rec['away']}  "
                          f"blend=({rec['blend'][0]:.2f}/{rec['blend'][1]:.2f}/{rec['blend'][2]:.2f})")

        records = run_eval(cfg, rows, on_progress=progress)
        if not records:
            console.print("[red]✗ No evaluations completed.[/red]")
            raise typer.Exit(1)
    else:
        records = load_eval_log(cfg)
        if not records:
            console.print("[yellow]Eval log is empty — run `evaluate -p <provider>` first.[/yellow]")
            raise typer.Exit(1)

    # Score everything logged so far (this run + previous sessions).
    all_records = load_eval_log(cfg)
    scores = score_records(all_records)
    t = Table(title=f"LLM-lift evaluation — {len(all_records)} logged match(es), lower Brier = better")
    t.add_column("Model"); t.add_column("Mean Brier", justify="right")
    t.add_column("Hit-rate", justify="right"); t.add_column("n", justify="right")
    for s in sorted(scores.values(), key=lambda s: s.mean_brier):
        t.add_row(s.name, f"{s.mean_brier:.3f}", f"{s.hit_rate:.0%}", str(s.n))
    console.print(t)

    best_w, curve = fit_judge_weight(all_records)
    if curve:
        configured = float(DEFAULT_CONFIG.get("ensemble_judge_weight", 0.6))
        at_best = min(curve, key=lambda x: x[1])[1]
        at_cfg = min(curve, key=lambda x: abs(x[0] - configured))[1]
        from worldcupagents.pipelines.evaluate import dedupe_records
        n_reads = sum(1 for r in dedupe_records(all_records)
                      if r.get("llm") and r.get("judge") and r.get("base"))
        console.print(f"\n[bold]judge_weight fit[/bold] over {n_reads} reads: "
                      f"best w={best_w:.2f} (Brier {at_best:.3f}) vs configured {configured:.2f} (Brier {at_cfg:.3f})")
        spark = "  ".join(f"{w:.1f}:{b:.3f}" for w, b in curve if round(w * 10) % 2 == 0)
        console.print(f"[dim]curve  {spark}[/dim]")
        if len(all_records) < 30:
            console.print("[dim]Small sample — collect ≥30 reads before trusting the fitted weight.[/dim]")


@app.command(name="guardian-guide")
def guardian_guide_cmd(
    limit: int = typer.Option(None, "--limit", help="Only the first N teams (quick test)"),
):
    """Ingest the Guardian WC2026 player guide: a prose bio + position/club/caps/age
    for ~1,250 players, and a tactical brief for all 48 nations. Player bios →
    per-player notes; team briefs → the qualitative warehouse. Both flow into the
    dossier and the debate.
    """
    from worldcupagents.pipelines.guardian_guide import ingest_guardian_player_guide
    with console.status("Fetching the Guardian player guide…"):
        res = ingest_guardian_player_guide(DEFAULT_CONFIG, limit=limit)
    if not res.players and not res.teams:
        console.print("[yellow]Nothing ingested — the Guardian feed may have moved.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]✓ Guardian guide[/green] {res.teams} team briefs, "
                  f"{res.players} player profiles, {res.coaches} coach names"
                  f"{f', {res.errors} errors' if res.errors else ''}. "
                  f"They now appear in the dossier + debate.")


@app.command(name="bbc-guide")
def bbc_guide_cmd(
    no_full: bool = typer.Option(False, "--no-full", help="Inline summaries only; skip the 48 full-profile fetches"),
    limit: int = typer.Option(None, "--limit", help="Only the first N teams (quick test)"),
):
    """Ingest the BBC Sport WC2026 team guide: a ranking/appearances/best-performance
    summary for all 48 nations, plus each team's FULL TEAM PROFILE article. Both go
    to the qualitative warehouse (team-linked → tactical analyst + dossier).
    """
    from worldcupagents.pipelines.bbc_guide import ingest_bbc_team_guide
    with console.status("Fetching the BBC team guide" + ("" if no_full else " + full profiles") + "…"):
        res = ingest_bbc_team_guide(DEFAULT_CONFIG, full_profiles=not no_full, limit=limit)
    if not res.teams:
        console.print("[yellow]Nothing ingested — the BBC guide may have moved.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]✓ BBC guide[/green] {res.teams} team summaries, "
                  f"{res.full_profiles} full profiles{f', {res.errors} errors' if res.errors else ''}.")


@app.command(name="guardian-experts")
def guardian_experts_cmd(
    limit: int = typer.Option(None, "--limit", help="Only the first N teams (quick test)"),
):
    """Ingest the Guardian 'Experts' Network' WC2026 team guides for all 48 nations:
    long-form previews (The plan / The coach / Star player / Unsung hero / One to
    watch) by local experts. Prose → the qualitative warehouse (team-linked →
    tactical analyst + dossier); the coach section → a structured coach note (→
    dossier + debate). Re-run as the Guardian publishes any still-pending nations.
    """
    from worldcupagents.pipelines.guardian_experts import ingest_guardian_experts
    with console.status("Fetching the Guardian Experts' Network guides (all 48)…"):
        res = ingest_guardian_experts(DEFAULT_CONFIG, limit=limit)
    if not res.teams:
        console.print("[yellow]Nothing ingested — the Guardian index may have moved.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]✓ Guardian Experts' Network[/green] {res.teams} team guides, "
                  f"{res.coaches} coach profiles{f', {res.errors} errors' if res.errors else ''}. "
                  f"They now appear in the dossier + debate.")


@app.command(name="note-player")
def note_player(
    player: str = typer.Argument(..., help="Player name (as it appears in the squad)"),
    team: str = typer.Option(..., "--team", "-t", help="The player's team"),
    note: str = typer.Option(None, "--note", help="Your scouting/style note (free text)"),
    note_file: str = typer.Option(None, "--note-file", help="Read the note from a file instead"),
    delete: bool = typer.Option(False, "--delete", help="Delete this player's note"),
):
    """Attach a qualitative scouting/style note to a specific player. It surfaces
    next to that player in the Player Analyst and the dossier (squad members only).

    \b
      footballagents note-player "Bukayo Saka" -t "Arsenal FC" --note "Inverted right winger; cuts onto his left, drifts into the half-space, Arsenal's main creator from open play."
      footballagents note-player "Bukayo Saka" -t "Arsenal FC" --delete
    """
    from worldcupagents.dataflows.match_store import MatchStore
    store = MatchStore.from_config(DEFAULT_CONFIG)
    try:
        if delete:
            ok = store.delete_player_note(team, player)
            console.print(f"[green]✓ deleted[/green] note for {player} ({team})" if ok
                          else f"[yellow]No note found for {player} ({team}).[/yellow]")
            return
        text = note
        if note_file:
            text = Path(note_file).read_text(encoding="utf-8").strip()
        if not text:
            console.print("[red]✗ Provide --note \"...\" or --note-file PATH (or --delete).[/red]")
            raise typer.Exit(1)
        store.upsert_player_note(team, player, text)
        console.print(f"[green]✓ saved[/green] scouting note for [bold]{player}[/bold] ({team}). "
                      f"It will appear in the player analyst + dossier.")
    finally:
        store.close()


@app.command()
def odds(
    home: str = typer.Argument(..., help="Home / first team"),
    away: str = typer.Argument(..., help="Away / second team"),
    league: str = typer.Option(None, "--league", "-L", help="Competition (default WC2026). See `leagues`."),
):
    """Live market read for a fixture: de-vigged bookmaker consensus (The Odds API)
    + Polymarket crowd. Needs ODDS_API_KEY in .env for the bookmaker line.

    \b
      footballagents odds "Arsenal FC" "Liverpool FC" -L PL
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    _resolve_league(cfg, league)
    from worldcupagents.dataflows.market import market_digest, market_read
    with console.status(f"Fetching market for {home} vs {away}…"):
        mr = market_read(cfg, home, away)
    if not mr:
        import os
        hint = ("" if os.environ.get("ODDS_API_KEY")
                else " [dim](set ODDS_API_KEY in .env for the bookmaker consensus — free at the-odds-api.com)[/dim]")
        console.print(f"[yellow]No market found for {home} vs {away}.[/yellow]{hint}")
        raise typer.Exit(1)
    console.print(Panel(market_digest(mr), title="📈 Market", border_style="cyan"))


@app.command()
def dossier(
    home: str = typer.Argument(..., help="Home / first team"),
    away: str = typer.Argument(..., help="Away / second team"),
    league: str = typer.Option(None, "--league", "-L", help="Competition (default WC2026). See `leagues`."),
    season: str = typer.Option(None, "--season", help="Club-league season, e.g. 2025-26"),
    market: bool = typer.Option(True, "--market/--no-market",
                                help="Show the live bookmaker consensus. --no-market for hypothetical or in-play games."),
):
    """Pre-match dossier: line-ups, squad-scoped player stats, recent scores+stats
    (last 5 years), style of play (attack/defense forte, set pieces, tempo &
    discipline), and learnings from past predictions — the exact data the debate
    will see. No LLM, no tokens.

    \b
      footballagents dossier "Arsenal FC" "Liverpool FC" -L PL --season 2025-26
      footballagents dossier "Argentina" "France"
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["enable_market_context"] = market
    if season:
        from worldcupagents.seasons import normalize_season
        try:
            cfg["season"] = normalize_season(season)
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]"); raise typer.Exit(1)
    _resolve_league(cfg, league)
    from worldcupagents.pipelines.prematch import build_dossier
    with console.status(f"Assembling dossier: {home} vs {away}…"):
        doss = build_dossier(home, away, cfg)
    _print_dossier(doss)


def _print_dossier(doss: dict) -> None:
    from rich.columns import Columns

    def team_panel(b: dict) -> Panel:
        L = []
        rank = f"  [dim]FIFA #{b['fifa_rank']}[/dim]" if b.get("fifa_rank") else ""
        L.append(f"[bold]{b['team']}[/bold]{rank}")
        if b.get("formation"):
            L.append(f"Formation: {b['formation']}")
        fo = b.get("forte")
        if fo:
            L.append(f"[cyan]Forte:[/cyan] {fo['label']}  [dim](att {fo['attack']}, "
                     f"def {fo['defense']} → solidity {fo['solidity']})[/dim]")
        if b.get("xg_for") is not None:
            L.append(f"xG for/against: {b['xg_for']} / {b['xg_against']}")
        tp = b.get("tempo")
        if tp:
            L.append(f"[cyan]Tempo/discipline[/cyan] [dim](last {tp['n']})[/dim]: "
                     f"{tp['shots']} shots ({tp['sot']} on target), {tp['corners']} corners, "
                     f"{tp['fouls']} fouls, {tp['yellow']}🟨 {tp['red']}🟥 per game")
        sp = b.get("set_pieces")
        if sp:
            from worldcupagents.dataflows.providers.understat import situations_digest
            try:
                L.append(f"[cyan]Set pieces:[/cyan] {situations_digest(sp['data'], b['team'])[:200]}")
            except Exception:  # noqa: BLE001
                pass
        if b.get("probable_xi"):
            L.append(f"[cyan]Likely XI[/cyan] [dim](most-used)[/dim]: " + ", ".join(b["probable_xi"][:11]))
        sf = b.get("style_fingerprint")
        if sf:
            bits = []
            if sf.get("possession_share"):
                bits.append(f"possession {sf['possession_share']:.0%}")
            if sf.get("directness"):
                bits.append(f"directness {sf['directness']:.2f}")
            if sf.get("top_pass_pairs"):
                bits.append("key combos: " + ", ".join(sf["top_pass_pairs"][:2]))
            if sf.get("build_up_zones"):
                bits.append("builds via " + ", ".join(sf["build_up_zones"][:2]))
            if bits:
                L.append(f"[cyan]Playing style:[/cyan] " + "; ".join(bits))
        if b.get("style_note"):
            L.append(f"[dim]Style: {b['style_note'][:240]}[/dim]")
        if b.get("qual_notes"):
            L.append("[magenta]Style notes:[/magenta] [dim]" + b["qual_notes"][:300] + "[/dim]")
        if b.get("weaknesses"):
            L.append("[red]Weaknesses:[/red]")
            L += [f"  [red]✗[/red] {w}" for w in b["weaknesses"]]
        if b.get("player_notes"):
            L.append("[magenta]Scouting notes:[/magenta]")
            L += [f"  • [bold]{n['player']}[/bold]: {n['note']}"
                  f" [dim]({n.get('source') or 'manual'})[/dim]" for n in b["player_notes"]]
        return Panel("\n".join(L), border_style="blue")

    console.print(Columns([team_panel(doss["home"]), team_panel(doss["away"])], equal=True, expand=True))

    # Squad-scoped player stats, side by side
    for side, label in (("home", doss["home"]["team"]), ("away", doss["away"]["team"])):
        ps = doss[side]["players"]
        if not ps:
            continue
        has_club = any(p.get("club") for p in ps)   # national fallback shows club form
        title = f"{label} — key players ({'club form, squad' if has_club else 'squad only'})"
        t = Table(title=title, show_header=True, header_style="bold")
        base = ["Player"] + (["Club"] if has_club else [])
        cols = base + ["G", "A", "Sh", "xG", "xA", "npxG/build", "KeyP", "Min"]
        for c in cols:
            t.add_column(c, justify="left" if c in ("Player", "Club") else "right")
        for p in ps:
            row = [p["player"]] + ([p.get("club") or "—"] if has_club else [])
            row += [str(p.get("goals") or 0), str(p.get("assists") or 0),
                    str(p.get("shots") or "—"),
                    f"{p['xg']:.1f}" if p.get("xg") is not None else "—",
                    f"{p['xa']:.1f}" if p.get("xa") is not None else "—",
                    f"{p['xg_buildup']:.1f}" if p.get("xg_buildup") is not None else "—",
                    str(p.get("key_passes") or "—"), str(p.get("minutes") or "—")]
            t.add_row(*row)
        console.print(t)

    # Recent matches with per-match stats (the "past games + stats" view)
    for side, label in (("home", doss["home"]["team"]), ("away", doss["away"]["team"])):
        recent = doss[side].get("recent") or []
        if not recent:
            continue
        has_stats = any(m.get("shots") is not None for m in recent)
        t = Table(title=f"{label} — recent matches", show_header=True, header_style="bold")
        cols = ["Date", "", "Opponent", "Score"] + (
            ["Shots (OT)", "Corners", "Fouls", "Cards", "xG", "xGA"] if has_stats else ["xG", "xGA"])
        for c in cols:
            t.add_column(c, justify="left" if c in ("Date", "Opponent", "") else "right")
        for m in recent:
            row = [m["date"], m["venue"], m["opponent"], f"{m['result']} {m['gf']}-{m['ga']}"]
            if has_stats:
                cards = f"{int(m['yellow'] or 0)}🟨" + (f" {int(m['red'])}🟥" if m.get("red") else "")
                row += [f"{m['shots'] or '—'} ({m['sot'] or '—'})", str(m.get("corners") or "—"),
                        str(m.get("fouls") or "—"), cards,
                        f"{m['xg']:.1f}" if m.get("xg") is not None else "—",
                        f"{m['xga']:.1f}" if m.get("xga") is not None else "—"]
            else:
                row += [f"{m['xg']:.1f}" if m.get("xg") is not None else "—",
                        f"{m['xga']:.1f}" if m.get("xga") is not None else "—"]
            t.add_row(*row)
        console.print(t)

    # National-team career caps/goals (when no club player stats apply)
    for side, label in (("home", doss["home"]["team"]), ("away", doss["away"]["team"])):
        career = doss[side].get("career") or []
        if not career or doss[side]["players"]:
            continue
        line = "; ".join(f"{c['player']} {c['caps']} caps/{c['goals'] or 0} gls" for c in career)
        console.print(f"[bold]{label}[/bold] career leaders: {line}  [dim][source: Wikipedia][/dim]")

    # Recent form within the window
    for side in ("home", "away"):
        b = doss[side]
        if not b["form"]:
            continue
        line = "; ".join(
            f"{'W' if f['gf']>f['ga'] else 'L' if f['gf']<f['ga'] else 'D'} {f['gf']}-{f['ga']} v {f['opponent']}"
            + (f" ({f['date']})" if f["date"] else "")
            for f in b["form"])
        console.print(f"[bold]{b['team']}[/bold] recent (≤5y): {line}")

    mr = doss.get("market")
    if mr:
        from worldcupagents.dataflows.market import market_digest
        console.print(Panel(market_digest(mr), title="📈 Market", border_style="cyan"))

    v = doss.get("verdict")
    if v is not None:
        console.print(f"\n[bold]Model call (no LLM):[/bold] {v.outcome.value} {v.scoreline}  "
                      f"[dim](H {v.p_home:.0%} / D {v.p_draw:.0%} / A {v.p_away:.0%})[/dim]")
        if mr:
            from worldcupagents.dataflows.market import divergence_note
            note = divergence_note(v, mr)
            if note:
                console.print(f"[cyan]{note}[/cyan]")
        a = v.alternative
        if a is not None:
            flag = "⚠️  Upset watch" if a.live else "Long-shot alternative"
            lines = [f"[bold]{a.outcome.value} {a.scoreline}[/bold] at [bold]{a.probability:.0%}[/bold] "
                     f"[dim](call {a.gap:.0%} ahead)[/dim]"]
            lines += [f"  • {f}" for f in a.swing_factors]
            console.print(Panel("\n".join(lines), title=flag,
                                border_style="yellow" if a.live else "dim"))

    if doss.get("records"):
        console.print(f"\n[bold]Head-to-head / records:[/bold] {doss['records']}")
    if doss.get("learnings"):
        console.print(Panel(doss["learnings"], title="Learnings from past predictions", border_style="yellow"))
    if doss.get("notes"):
        console.print(Panel(doss["notes"][:1500], title="Manual / qualitative notes", border_style="magenta"))
    console.print(f"[dim]Recency cutoff: matches on/after {doss['since']}. "
                  f"No LLM was called — this is the raw debate input.[/dim]")


@app.command()
def refresh(
    leagues: str = typer.Option("WC2026", "--leagues", "-L",
                                help="Comma list of competitions to refresh, e.g. WC2026,PL"),
    sim: bool = typer.Option(True, "--sim/--no-sim", help="Re-run the WC2026 tournament simulation"),
    runs: int = typer.Option(5_000, "--runs", help="Simulation runs for --sim"),
    internationals: bool = typer.Option(
        False, "--internationals",
        help="Also re-ingest the full international-results history (~49k matches; slow, rarely changes)",
    ),
):
    """One command after every matchday: pull the newest results (which also
    auto-resolves your pending predictions), re-simulate the tournament, and
    regenerate the data explorer — typically ~15s.

    International history (~49k matches) is static context that rarely changes, so
    it is NOT re-ingested by default; pass --internationals (or run
    `hoard-data --source international-results`) occasionally to update it.

    \b
      footballagents refresh                 # fast: WC results + sim + explorer
      footballagents refresh -L WC2026,PL    # also refresh the PL store
      footballagents refresh --internationals  # also re-pull all intl history (slow)
    """
    from worldcupagents.leagues.registry import apply_league, get_league
    from worldcupagents.pipelines.fetch_data import fetch_data

    for lg_key in [s.strip() for s in leagues.split(",") if s.strip()]:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        try:
            apply_league(cfg, get_league(lg_key))
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            continue
        res = fetch_data(cfg)
        console.print(f"[green]✓ {lg_key}[/green] results updated "
                      f"(+{res['added']} rows, {res['total']} total)")

    if internationals:
        try:
            from worldcupagents.pipelines.hoard_data import hoard_international_results
            hres = hoard_international_results(copy.deepcopy(DEFAULT_CONFIG), refresh=True)
            console.print(f"[green]✓ internationals[/green] warehouse refreshed "
                          f"({hres.counts.get('wh_matches', 0)} matches)")
        except Exception as e:  # noqa: BLE001 — best-effort; results above already landed
            console.print(f"[yellow]⚠ internationals refresh skipped ({e})[/yellow]")

    # Auto-resolve any pending predictions whose results just arrived.
    from worldcupagents.graph.reflection import sync_pending
    synced = sync_pending(DEFAULT_CONFIG)
    if synced:
        console.print(f"[green]✓ auto-resolved {len(synced)} pending prediction(s)[/green]")

    # Re-fit the blend weight from the eval log (recency-weighted, shrunk to prior).
    _refit_judge_weight_and_report()

    if sim:
        from worldcupagents.pipelines.simulate import export_simulation, simulate_tournament
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["fd_competition"] = "WC"
        with console.status(f"Simulating {runs:,} tournaments…"):
            sres = simulate_tournament(cfg, n=runs)
        if sres.n:
            export_simulation(sres, cfg)
            top3 = sorted(sres.teams, key=lambda t: sres.teams[t]["champion"], reverse=True)[:3]
            console.print("[green]✓ simulation[/green] title odds: " + ", ".join(
                f"{t} {sres.share(t, 'champion'):.1%}" for t in top3))

    from worldcupagents.pipelines.data_explorer import export_data_explorer
    path = export_data_explorer(copy.deepcopy(DEFAULT_CONFIG))
    console.print(f"[green]✓ explorer[/green] {path}")


def _watch_tick(cfg: dict, leagues: str, reflect_llm) -> None:
    """One idempotent matchday tick: pull results, distil punditry + tactics for any
    newly-FINISHED match not yet processed, then auto-resolve + re-fit the weight."""
    from pathlib import Path

    from worldcupagents.dataflows.match_store import MatchStore
    from worldcupagents.graph.reflection import sync_pending
    from worldcupagents.leagues.registry import apply_league, get_league
    from worldcupagents.pipelines.analyze_match import analyze_match
    from worldcupagents.pipelines.fetch_data import fetch_data
    from worldcupagents.pipelines.punditry import analyze_punditry

    # 1. Pull the newest results into the store (per league).
    for lg_key in [s.strip() for s in leagues.split(",") if s.strip()]:
        lcfg = copy.deepcopy(cfg)
        try:
            apply_league(lcfg, get_league(lg_key))
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            continue
        res = fetch_data(lcfg)
        console.print(f"[green]✓ {lg_key}[/green] results updated "
                      f"(+{res['added']} rows, {res['total']} total)")

    # 2. Finished WC matches whose punditry digest doesn't exist yet = "just finished".
    comp = cfg.get("fd_competition")
    store = MatchStore.from_config(cfg)
    try:
        finished = [m for m in store.all_matches()
                    if (comp is None or m.get("comp") == comp) and m.get("date")]
    finally:
        store.close()
    pdir = Path(cfg.get("memory_dir", "memory")) / "punditry"
    from worldcupagents.agents.analyst.tactical import make_match_id
    todo = [m for m in finished
            if not (pdir / f"{make_match_id(m['home'], m['away'], m['date'])}.json").exists()]

    if todo:
        console.print(f"[cyan]▸ {len(todo)} newly-finished match(es) to analyse[/cyan]")
    for m in todo:
        h, a, d = m["home"], m["away"], m["date"]
        try:
            po = analyze_punditry(h, a, d, cfg)
            analyze_match(h, a, d, cfg)  # liveblog tactical report (existing pipeline)
            tag = f"{po.n_articles} article(s)" if po.n_articles else "no punditry found"
            console.print(f"  [green]✓[/green] {h} vs {a} ({d}) — {tag}")
        except Exception as e:  # noqa: BLE001 — one bad fixture must not sink the tick
            console.print(f"  [yellow]⚠ {h} vs {a} ({d}) skipped ({e})[/yellow]")

    # 3. Close the learning loop: resolve any newly-decided predictions, re-fit weight.
    synced = sync_pending(cfg, reflect_llm=reflect_llm)
    if synced:
        console.print(f"[green]✓ auto-resolved {len(synced)} pending prediction(s)[/green]")
    _refit_judge_weight_and_report(cfg)


@app.command()
def watch(
    interval: int = typer.Option(
        0, "--interval", help="Minutes between polls; 0 = a single tick (cron/launchd-friendly)"),
    leagues: str = typer.Option("WC2026", "--leagues", "-L", help="Competitions to poll for results"),
    provider: str = typer.Option(None, "--provider", "-p", help="LLM provider for punditry extraction (implies --llm)"),
    model: str = typer.Option(None, "--model", help="Override the analyst model"),
    llm: bool = typer.Option(None, "--llm/--no-llm", help="Run the real LLM analyst (default: offline placeholder)"),
):
    """Matchday autopilot: poll football-data, and for every newly-FINISHED match
    distil its punditry into structured signals (+ the liveblog tactical report),
    then auto-resolve predictions and re-fit the blend weight.

    \b
      footballagents watch                       # one idempotent tick (run from cron)
      footballagents watch --interval 30         # in-process loop, poll every 30 min
      footballagents watch --provider openai     # real LLM punditry extraction

    The tick is idempotent: a match is processed once (keyed on its punditry digest
    file), so re-running — or polling on a timer — never repeats work.
    """
    import time

    from worldcupagents.llm_clients.factory import create_llm

    cfg = _build_config(provider, None, model, llm, None)
    _resolve_league(cfg, leagues.split(",")[0].strip())
    reflect_llm = None
    if cfg.get("use_llm"):
        try:
            reflect_llm = create_llm(cfg["llm_provider"], cfg["quick_think_llm"])
        except Exception as e:  # noqa: BLE001 — reflection is best-effort
            console.print(f"[yellow]⚠ reflection LLM unavailable ({e}); resolving without one.[/yellow]")
    mode = (f"LLM analyst: {cfg['llm_provider']} ({cfg['quick_think_llm']})"
            if cfg["use_llm"] else "offline placeholder (add --provider/--llm for real extraction)")
    console.print(f"[dim]{mode}[/dim]")

    if interval <= 0:
        _watch_tick(cfg, leagues, reflect_llm)
        return
    console.print(f"[bold]watching[/bold] — polling every {interval} min (Ctrl-C to stop)")
    try:
        while True:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            console.print(f"\n[dim]── tick {stamp} ──[/dim]")
            _watch_tick(cfg, leagues, reflect_llm)
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        console.print("\n[dim]watch stopped.[/dim]")


@app.command()
def credit():
    """Which signals actually helped? A simple with-vs-without Brier scoreboard.

    Each shipped prediction records the extra signals it used (punditry, market,
    tactical history, lessons, qualitative notes, the calibration note). Once they
    resolve, this compares the average Brier of predictions that carried each signal
    against those that didn't. It's an association, not proof — read it as a scoreboard
    that sharpens as the tournament fills in.
    """
    from worldcupagents.credit import credit_report
    console.print(credit_report(DEFAULT_CONFIG))


@app.command(name="simulate-tournament")
def simulate_tournament_cmd(
    n: int = typer.Option(10_000, "-n", "--runs", help="Monte-Carlo tournament runs"),
    seed: int = typer.Option(1, "--seed", help="RNG seed (same seed = same odds)"),
    top: int = typer.Option(20, "--top", help="Show the top N teams by title odds"),
):
    """Simulate WC2026 from the current state: real fixtures, played results
    locked in, every unplayed match sampled from the Poisson engine (no LLM
    cost). Prints per-team advancement odds and exports wc2026_sim.json.
    """
    from worldcupagents.pipelines.simulate import export_simulation, simulate_tournament

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["fd_competition"] = "WC"
    with console.status(f"Simulating {n:,} tournaments…"):
        res = simulate_tournament(cfg, n=n, seed=seed)
    if not res.n or not res.teams:
        console.print("[yellow]No WC fixtures available[/yellow] — set FOOTBALL_DATA_ORG_TOKEN "
                      "(the fixtures feed drives the simulation).")
        raise typer.Exit(1)

    group_of = {t: g for g, ts in res.groups.items() for t in ts}
    t = Table(title=f"WC2026 — {res.n:,} simulated tournaments "
                    f"({res.played} results locked, {res.remaining} group games simulated)")
    for col, justify in [("Team", "left"), ("Grp", "left"), ("Win grp", "right"),
                         ("R32", "right"), ("R16", "right"), ("QF", "right"),
                         ("SF", "right"), ("Final", "right"), ("🏆", "right")]:
        t.add_column(col, justify=justify)
    ranked = sorted(res.teams, key=lambda x: res.teams[x]["champion"], reverse=True)
    for team in ranked[:top]:
        t.add_row(team, group_of.get(team, "?"),
                  f"{res.share(team, 'group_win'):.0%}", f"{res.share(team, 'r32'):.0%}",
                  f"{res.share(team, 'r16'):.0%}", f"{res.share(team, 'qf'):.0%}",
                  f"{res.share(team, 'sf'):.0%}", f"{res.share(team, 'final'):.0%}",
                  f"{res.share(team, 'champion'):.1%}")
    console.print(t)
    console.print(f"[dim]Bracket: {res.bracket_source}. λ: fitted strengths where the store "
                  f"knows both teams, else rank-Elo. Re-run after each matchday — locked results "
                  f"sharpen everything.[/dim]")
    path = export_simulation(res, cfg)
    console.print(f"[green]✓ exported[/green] {path}")


@app.command(name="analyze-match")
def analyze_match_cmd(
    home: str = typer.Argument(..., help="Home / first team"),
    away: str = typer.Argument(..., help="Away / second team"),
    date: str = typer.Option(None, "--date", help="Match date YYYY-MM-DD (helps Guardian find the liveblog)"),
    provider: str = typer.Option(
        None, "--provider", "-p", help="LLM provider for tactical analysis (implies --llm)"
    ),
    model: str = typer.Option(None, "--model", help="Override the analyst model"),
    llm: bool = typer.Option(None, "--llm/--no-llm", help="Run the real LLM analyst (default: offline placeholder)"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing populated report"),
    league: str = typer.Option(None, "--league", "-L", help="Competition (default WC2026). See `leagues`."),
):
    """Harvest post-game commentary and build a 5-phase tactical report.

    Offline by default (no spend). Add --provider/--llm to run the real analyst,
    e.g. `analyze-match Argentina France --date 2022-12-18 --provider openai`.
    Existing LLM-populated reports are protected and won't be overwritten unless
    you pass --force.
    """
    from worldcupagents.pipelines.analyze_match import analyze_match

    # Reuse the predict config builder: resolves provider/models/use_llm + arrow-key picker.
    cfg = _build_config(provider, None, model, llm, None)
    _resolve_league(cfg, league)
    mode = (
        f"LLM analyst: {cfg['llm_provider']} ({cfg['quick_think_llm']})"
        if cfg["use_llm"] else "offline placeholder (no LLM) — add --provider/--llm for real analysis"
    )
    console.print(Panel.fit(
        f"[bold]{home}[/bold]  vs  [bold]{away}[/bold]\n"
        f"date={date or 'unspecified'}\n[dim]{mode}[/dim]",
        title="Analyze match",
    ))

    outcome = analyze_match(home, away, date, cfg, force=force)
    rep = outcome.report

    src = rep.sources[0] if rep.sources else "—"
    console.print(f"[dim]source: {src}[/dim]\n")

    for p in rep.phases:
        console.print(f"[bold cyan]{p.phase}[/bold cyan]")
        console.print(f"  {p.summary or '(no summary)'}")
        if p.formations_blocks:
            console.print(f"  [dim]formations/blocks:[/dim] {'; '.join(p.formations_blocks)}")
        if p.adjustments:
            console.print(f"  [dim]adjustments:[/dim] {'; '.join(p.adjustments)}")
        if p.key_matchups:
            console.print(f"  [dim]key matchups:[/dim] {'; '.join(p.key_matchups)}")
        console.print()

    if outcome.model:
        u = outcome.usage
        cost = f"  ≈ ${outcome.cost:.4f}" if outcome.cost is not None else ""
        console.print(f"[dim]tokens: {u['input']:,} in / {u['output']:,} out{cost}  ({outcome.model})[/dim]")
    if outcome.json_path:
        if not outcome.model and not outcome.usage.get("input"):
            console.print(f"[dim]↩ Loaded existing report (use --force to re-analyse)[/dim]")
        else:
            console.print(f"[green]✓ Saved[/green] {outcome.json_path}  and  {outcome.md_path}")


@app.command()
def critic(
    team: str = typer.Argument(..., help="Team to run the critic loop on"),
    provider: str = typer.Option(None, "--provider", "-p", help="LLM provider (implies --llm)"),
    model: str = typer.Option(None, "--model", help="Override the critic model"),
    llm: bool = typer.Option(None, "--llm/--no-llm", help="Run the real LLM critic (default: offline placeholder)"),
    league: str = typer.Option(None, "--league", "-L", help="Competition (default WC2026). See `leagues`."),
):
    """Critic Loop: cross-examine a team's quantitative metrics (xG/form/goals)
    against the qualitative tactical commentary to surface deep context.

    Offline by default; add --provider/--llm for the synthesised cross-examination.
    """
    from worldcupagents.pipelines.critic import run_critic

    cfg = _build_config(provider, model, None, llm, None)
    _resolve_league(cfg, league)
    mode = f"LLM critic: {cfg['llm_provider']} ({cfg['deep_think_llm']})" if cfg["use_llm"] else "offline placeholder"
    console.print(Panel.fit(f"[bold]{team}[/bold]\n[dim]{mode}[/dim]", title="Critic loop"))

    out = run_critic(team, cfg)
    r = out.report

    console.print(f"[bold]Summary:[/bold] {r.summary or '—'}\n")
    if r.findings:
        console.print("[bold cyan]Findings (metric ← evidence → insight)[/bold cyan]")
        for f in r.findings:
            console.print(f"  • [bold]{f.metric}[/bold] ← {f.commentary}")
            console.print(f"    → {f.insight}")
        console.print()
    if r.tensions:
        console.print("[bold yellow]Tensions[/bold yellow]")
        for t in r.tensions:
            console.print(f"  • {t}")
        console.print()
    if out.model:
        cost = f"  ≈ ${out.cost:.4f}" if out.cost is not None else ""
        console.print(f"[dim]tokens: {out.usage['input']:,} in / {out.usage['output']:,} out{cost}[/dim]")
    if out.json_path:
        console.print(f"[green]✓ Saved[/green] {out.json_path}  and  {out.md_path}")


@app.command(name="scout-report")
def scout_report_cmd(
    team: str = typer.Argument(..., help="Team to scout"),
    provider: str = typer.Option(None, "--provider", "-p", help="LLM provider (implies --llm)"),
    model: str = typer.Option(None, "--model", help="Override the scout model"),
    llm: bool = typer.Option(None, "--llm/--no-llm", help="Run the real LLM scout (default: offline placeholder)"),
    league: str = typer.Option(None, "--league", "-L", help="Competition (default WC2026). See `leagues`."),
):
    """Blend a team's stats with tactical memory into a Contextual Performance Report.

    Offline by default; add --provider/--llm for a synthesised report, e.g.
    `scout-report Argentina --provider openai`.
    """
    from worldcupagents.pipelines.scout_report import generate_scout_report

    cfg = _build_config(provider, model, None, llm, None)  # deep model = scout model
    _resolve_league(cfg, league)
    mode = f"LLM scout: {cfg['llm_provider']} ({cfg['deep_think_llm']})" if cfg["use_llm"] else "offline placeholder"
    console.print(Panel.fit(f"[bold]{team}[/bold]\n[dim]{mode}[/dim]", title="Scout report"))

    out = generate_scout_report(team, cfg)
    r = out.report

    t = Table(title=f"Scouting Report — {r.team}", show_header=False)
    t.add_row("Summary", r.summary or "—")
    t.add_row("Strengths", "; ".join(r.strengths) or "—")
    t.add_row("Weaknesses", "; ".join(r.weaknesses) or "—")
    t.add_row("Tactical tendencies", "; ".join(r.tactical_tendencies) or "—")
    t.add_row("Key players", "; ".join(r.key_players) or "—")
    if out.model:
        cost = f"  ≈ ${out.cost:.4f}" if out.cost is not None else ""
        t.add_row("Token usage", f"[dim]{out.usage['input']:,} in / {out.usage['output']:,} out{cost}[/dim]")
    console.print(t)
    if out.json_path:
        console.print(f"[green]✓ Saved[/green] {out.json_path}  and  {out.md_path}")


@app.command()
def resolve(
    home: str = typer.Argument(None, help="Home / first team (must match the logged prediction)"),
    away: str = typer.Argument(None, help="Away / second team"),
    score: str = typer.Option(None, "--score", help="Final score 'H-A', e.g. 2-1 (derives the outcome)"),
    outcome: str = typer.Option(None, "--outcome", help="Or give the outcome directly: HOME_WIN/DRAW/AWAY_WIN"),
    sync: bool = typer.Option(False, "--sync",
                              help="Auto-resolve ALL pending predictions whose results are already in the match store"),
    provider: str = typer.Option(
        None, "--provider", "-p",
        help="LLM provider for a written reflection (TA Reflector style; lessons feed future predictions)",
    ),
):
    """Score a played match against its logged prediction (Brier) and mark it resolved.

    With --provider, an LLM writes a 2-4 sentence reflection that future
    predictions for these teams read back (the learning loop).
    With --sync, every pending prediction whose final score already sits in the
    match store is resolved automatically (run fetch-data first).

    \b
      worldcupagents resolve "Argentina" "Brazil" --score 2-1
      worldcupagents resolve "Argentina" "Brazil" --score 2-1 --provider openai
      worldcupagents resolve --sync
      worldcupagents resolve --sync --provider openai
    """
    from worldcupagents.agents.schemas import Outcome
    from worldcupagents.graph.reflection import outcome_from_score, quality_label, resolve_prediction

    reflect_llm = None
    if provider:
        from worldcupagents.llm_clients.factory import create_llm
        from worldcupagents.llm_clients.model_catalog import default_models
        try:
            _, quick = default_models(provider)
            reflect_llm = create_llm(provider, quick)
        except Exception as e:  # noqa: BLE001 — reflection is best-effort
            console.print(f"[yellow]⚠ reflection LLM unavailable ({e}); resolving without one.[/yellow]")

    if sync:
        from worldcupagents.graph.reflection import sync_pending
        results = sync_pending(DEFAULT_CONFIG, reflect_llm=reflect_llm)
        if not results:
            console.print("[yellow]Nothing to sync — no pending prediction has a stored result yet.[/yellow]")
            raise typer.Exit(0)
        t = Table(title=f"Auto-resolved {len(results)} prediction(s)")
        t.add_column("Fixture"); t.add_column("Result"); t.add_column("Predicted")
        t.add_column("Brier", justify="right"); t.add_column("Quality")
        for r in results:
            t.add_row(f"{r['home']} vs {r['away']} ({r['match_date']})", r["actual"],
                      r["predicted"] or "?", f"{r['brier']:.3f}", quality_label(r["brier"]))
        console.print(t)
        _refit_judge_weight_and_report()
        return

    if not home or not away:
        console.print("[red]✗ Provide HOME and AWAY (or use --sync to auto-resolve from the store).[/red]")
        raise typer.Exit(1)

    actual_scoreline = None
    if score:
        try:
            h, a = (int(x) for x in score.lower().replace("–", "-").split("-"))
        except ValueError:
            console.print(f"[red]✗ Couldn't parse score {score!r}; expected 'H-A' like 2-1.[/red]")
            raise typer.Exit(1)
        actual = outcome_from_score(h, a)
        actual_scoreline = f"{h}-{a}"
    elif outcome:
        try:
            actual = Outcome(outcome.upper())
        except ValueError:
            console.print(f"[red]✗ Invalid outcome {outcome!r}; use HOME_WIN/DRAW/AWAY_WIN.[/red]")
            raise typer.Exit(1)
    else:
        console.print("[red]✗ Provide --score (e.g. 2-1) or --outcome (HOME_WIN/DRAW/AWAY_WIN).[/red]")
        raise typer.Exit(1)

    res = resolve_prediction(home, away, actual, DEFAULT_CONFIG, actual_scoreline,
                             reflect_llm=reflect_llm)
    if not res["found"]:
        console.print(f"[yellow]No pending prediction found for {home} vs {away}.[/yellow] "
                      "Run `predict` first, or check the team names match the log.")
        raise typer.Exit(1)

    b = res["brier"]
    t = Table(title=f"Resolved: {home} vs {away}", show_header=False)
    t.add_row("Actual", f"{res['actual']}" + (f"  ({actual_scoreline})" if actual_scoreline else ""))
    t.add_row("Predicted", res["predicted"] or "—")
    t.add_row("Brier score", f"[bold]{b:.3f}[/bold]  ({quality_label(b)})  [dim]lower is better; 0.667 = coin-flip[/dim]")
    if res.get("reflection"):
        t.add_row("Reflection", res["reflection"])
    console.print(t)
    console.print("[green]✓ Log updated and per-team lessons appended to memory/teams/.[/green]")


@app.command()
def eliminate(
    teams: list[str] = typer.Argument(None, help="Team names to mark as eliminated"),
    undo: bool = typer.Option(False, "--undo", help="Remove listed teams from the eliminated set"),
    list_: bool = typer.Option(False, "--list", "-l", help="Show currently eliminated teams"),
    reset: bool = typer.Option(False, "--reset", help="Clear all eliminated teams"),
):
    """Mark teams as eliminated (crossed out in the -i team picker).

    \b
    Examples:
      worldcupagents eliminate Germany Belgium
      worldcupagents eliminate --list
      worldcupagents eliminate --undo Germany
      worldcupagents eliminate --reset
    """
    if reset:
        reset_eliminated(DEFAULT_CONFIG)
        console.print("[green]✓ All eliminations cleared.[/green]")
        return

    if list_ or (not teams and not undo and not reset):
        current = load_eliminated(DEFAULT_CONFIG)
        if current:
            console.print("[bold]Eliminated teams:[/bold]")
            for t in sorted(current):
                console.print(f"  [red]✗[/red] {t}")
        else:
            console.print("[dim]No teams eliminated yet.[/dim]")
        return

    if not teams:
        console.print("[yellow]No teams specified.[/yellow]")
        return

    if undo:
        updated = remove_eliminated(list(teams), DEFAULT_CONFIG)
        for t in teams:
            console.print(f"[green]✓[/green] Removed [bold]{t}[/bold] from eliminated list")
    else:
        # Validate team names
        unknown = [t for t in teams if t not in WC2026_TEAMS]
        if unknown:
            console.print(f"[yellow]⚠ Unknown team(s): {', '.join(unknown)}[/yellow]")
            console.print(f"  Known teams: {', '.join(sorted(WC2026_TEAMS))}")
        valid = [t for t in teams if t in WC2026_TEAMS]
        if not valid:
            return
        updated = add_eliminated(valid, DEFAULT_CONFIG)
        for t in valid:
            console.print(f"[red]✗[/red] Marked [bold]{t}[/bold] as eliminated")

    console.print(f"[dim]Total eliminated: {len(updated)}[/dim]")


def _resolve_league(cfg: dict, league_opt: str | None):
    """Resolve a league (flag > config default) and fold it into cfg. Exits with a
    friendly message on an unknown key."""
    from worldcupagents.leagues.registry import apply_league, get_league
    try:
        lg = get_league(league_opt or cfg.get("league"))
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)
    apply_league(cfg, lg)
    return lg


def _build_config(provider, deep_model, quick_model, llm, rounds, interactive=False) -> dict:
    """Assemble a run config from CLI flags, with an arrow-key picker when needed."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    provider_chosen = provider is not None
    env_provider = os.environ.get("WCA_LLM_PROVIDER")

    # Decide whether to use the LLM at all (-i implies on).
    if interactive:
        use_llm = True
    elif llm is None:
        use_llm = bool(provider_chosen or cfg.get("use_llm"))
    else:
        use_llm = llm

    # Show the guided picker when interactive, or when LLM is on but no provider was set.
    needs_pick = use_llm and not provider_chosen and (interactive or not env_provider)
    if needs_pick and sys.stdin.isatty():
        sel = _guided_select()
        if sel:
            provider, provider_chosen = sel["provider"], True
            deep_model = deep_model or sel["deep"]
            quick_model = quick_model or sel["quick"]

    if provider:
        cfg["llm_provider"] = provider
    cfg["use_llm"] = use_llm

    # Models: explicit override > provider-default catalog (when provider chosen) > config default.
    d_default, q_default = default_models(cfg["llm_provider"])
    cfg["deep_think_llm"] = deep_model or (d_default if provider_chosen else cfg["deep_think_llm"])
    cfg["quick_think_llm"] = quick_model or (q_default if provider_chosen else cfg["quick_think_llm"])

    if rounds is not None:
        cfg["max_debate_rounds"] = rounds

    if use_llm:
        key_env = API_KEY_ENV.get(cfg["llm_provider"], "")
        if key_env and not os.environ.get(key_env):
            console.print(f"[yellow]⚠ {key_env} not set — will fall back to a baseline-only verdict.[/yellow]")

    return cfg


def _pick_league() -> str | None:
    """Arrow-key competition picker (step 1 of the guided flow)."""
    import questionary
    from worldcupagents.leagues.registry import list_leagues

    choices = [
        questionary.Choice(f"{lg.name}  [dim]({lg.kind})[/dim]".replace("[dim]", "").replace("[/dim]", ""),
                           value=lg.key)
        for lg in list_leagues()
    ]
    return questionary.select("Competition  (↑/↓ then Enter)", choices=choices).ask()


def _teams_for_league(league, config: dict | None = None) -> dict[str, list[str]]:
    """Team list for the picker. WC = the 48 grouped by confederation; a club
    league = its teams pulled from the match store (run `fetch-data -L <key>` first)."""
    if league.key == "WC2026":
        return TEAMS_BY_CONF
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    cfg = {
        "data_dir": (config or DEFAULT_CONFIG).get("data_dir", "data"),
        "fd_competition": league.fd_competition,
    }
    if not db_path(cfg).exists():
        return {league.name: []}
    store = MatchStore.from_config(cfg)
    try:
        rows = [r for r in store.all_matches() if r.get("comp") == league.fd_competition]
    finally:
        store.close()
    return {league.name: sorted({r["home"] for r in rows} | {r["away"] for r in rows})}


def _guided_setup(league, home: str | None, away: str | None, venue: str | None) -> dict | None:
    """Arrow-key selection of teams (and venue for tournaments) within a league."""
    groups = _teams_for_league(league)
    if not any(groups.values()) and league.kind == "league" \
            and os.environ.get("FOOTBALL_DATA_ORG_TOKEN") and sys.stdin.isatty():
        # Don't dead-end the guided flow — offer to pull the season right here.
        if typer.confirm(f"No local data for {league.name}. Fetch it from football-data.org now?",
                         default=True):
            from worldcupagents.leagues.registry import apply_league
            from worldcupagents.pipelines.fetch_data import fetch_data
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            apply_league(cfg, league)
            res = fetch_data(cfg)
            console.print(f"[green]✓ fetched[/green] {res['added']} matches "
                          f"(+{res.get('players') or 0} player stats)")
            groups = _teams_for_league(league)
    if not any(groups.values()):
        console.print(f"[yellow]No teams known for {league.name}.[/yellow] "
                      f"Run [bold]fetch-data -L {league.key}[/bold] first, or pass teams as arguments.")
        return None
    eliminated = load_eliminated(DEFAULT_CONFIG) if league.key == "WC2026" else set()

    if not home:
        home = _pick_team("Home team  (↑/↓ then Enter)", groups, eliminated, exclude=None)
        if home is None:
            return None
    if not away:
        away = _pick_team("Away team", groups, eliminated, exclude=home)
        if away is None:
            return None
    if not venue and league.kind == "tournament":   # leagues: home/away ground is implicit
        venue = _pick_venue()
        if venue is None:
            return None

    return {"home": home, "away": away, "venue": venue}


def _resolve_fixture_stage(stage, home, away, date, cfg, lg, interactive):
    """Decide a fixture's stage. Precedence: explicit user choice > feed-derived >
    group fallback. Returns (Stage, source-label for display)."""
    if stage is not None:
        return stage, "you"                       # explicit --stage always wins
    if lg.kind != "tournament":
        return Stage.GROUP, "league"              # leagues have no knockout stage
    from worldcupagents.dataflows.fixtures import resolve_stage
    detected, _ = resolve_stage(home, away, date, cfg)
    default = detected or Stage.GROUP
    if interactive and sys.stdin.isatty():
        return _pick_stage(default), "you"        # guided pick, pre-set to the feed value
    return default, ("feed" if detected else "default (group)")


def _pick_stage(default: Stage) -> Stage:
    """Arrow-key stage picker (tournaments), pre-selected to the detected stage."""
    import questionary
    choices = [questionary.Choice(lbl, value=val) for lbl, val in (
        ("Group stage", Stage.GROUP), ("Round of 32", Stage.R32), ("Round of 16", Stage.R16),
        ("Quarter-final", Stage.QF), ("Semi-final", Stage.SF), ("Final / 3rd place", Stage.FINAL))]
    pick = questionary.select("Stage  (↑/↓ then Enter)", choices=choices, default=default).ask()
    return pick or default


def _pick_team(label: str, groups: dict[str, list[str]], eliminated: set[str], exclude: str | None = None) -> str | None:
    """Arrow-key team picker grouped by section. Eliminated teams are disabled."""
    import questionary

    choices: list = []
    for section, teams in groups.items():
        choices.append(questionary.Separator(f"── {section} ──"))
        for team in teams:
            if team == exclude:
                continue
            if team in eliminated:
                choices.append(
                    questionary.Choice(f"✗ {team}  (eliminated)", value=team, disabled="eliminated")
                )
            else:
                choices.append(questionary.Choice(team, value=team))

    pick = questionary.select(label, choices=choices).ask()
    return pick  # None if user pressed Ctrl-C


def _pick_venue() -> str | None:
    """Arrow-key venue picker grouped by country."""
    import questionary

    countries: dict[str, list[str]] = {}
    for city, info in WC2026_VENUES.items():
        countries.setdefault(info["country"], []).append(city)

    choices: list = []
    for country in ("Mexico", "USA", "Canada"):
        choices.append(questionary.Separator(f"── {country} ──"))
        for city in countries.get(country, []):
            info = WC2026_VENUES[city]
            label = city
            detail_parts = [info["stadium"]]
            if info["note"]:
                detail_parts.append(info["note"])
            label = f"{city}  [{' · '.join(detail_parts)}]"
            choices.append(questionary.Choice(label, value=city))

    choices.append(questionary.Separator("──"))
    choices.append(questionary.Choice("TBD  (no venue specified)", value=""))

    pick = questionary.select("Venue  (↑/↓ then Enter)", choices=choices).ask()
    if pick is None:
        return None
    return pick or None  # "" → None (TBD)


_POSITION_BUCKETS = (
    ("GK", ("goalkeeper", "keeper")),
    ("DEF", ("back", "defence", "defender", "defensive")),
    ("MID", ("midfield",)),
    ("FWD", ("offence", "forward", "winger", "striker", "attack")),
)


def _bucket(position: str | None) -> str:
    pos = (position or "").lower()
    for label, words in _POSITION_BUCKETS:
        if any(w in pos for w in words):
            return label
    return "—"


def _print_squads(final: dict) -> None:
    """Side-by-side squads grouped by position — visible proof of the data feeding
    the debate. (Predicted line-ups need a richer source: free football-data.org
    has squads only; add an API_FOOTBALL_KEY for line-ups/injuries.)"""
    home, away = final.get("home_profile"), final.get("away_profile")
    if home is None or away is None or (not home.squad and not away.squad):
        return

    def grouped(profile) -> str:
        if not profile.squad:
            return "[dim](squad unavailable)[/dim]"
        groups: dict[str, list[str]] = {}
        for p in profile.squad:
            groups.setdefault(_bucket(p.position), []).append(p.name)
        order = [label for label, _ in _POSITION_BUCKETS] + ["—"]
        lines = []
        for label in order:
            if groups.get(label):
                lines.append(f"[bold]{label}[/bold]  " + ", ".join(groups[label]))
        if profile.probable_xi:
            lines.append("[bold cyan]Likely XI[/bold cyan] [dim](most-used by minutes)[/dim]  "
                         + ", ".join(profile.probable_xi))
        coach = profile.style if profile.style.startswith("coach:") else ""
        if coach:
            lines.append(f"[dim]{coach}[/dim]")
        return "\n".join(lines)

    t = Table(title="Squads", show_header=True, header_style="bold")
    t.add_column(f"{home.team}  ({len(home.squad)})", ratio=1)
    t.add_column(f"{away.team}  ({len(away.squad)})", ratio=1)
    t.add_row(grouped(home), grouped(away))
    console.print(t)


def _print_analyst_reports(final: dict) -> None:
    """The three analyst reports the advocates/judge read — rich data, visibly."""
    sections = [
        ("Form Analyst", final.get("form_report", "")),
        ("Tactical Analyst", final.get("tactical_report", "")),
        ("Player Analyst", final.get("player_report", "")),
    ]
    body = "\n\n".join(f"[bold]{name}[/bold]\n{text}" for name, text in sections if text)
    if body:
        console.print(Panel(body, title="Analyst reports", border_style="blue"))


def _pick_season(league) -> str | None:
    """Arrow-key season picker: the current season plus the last three.

    Historical seasons use that season's squad (Wikipedia) and cut all data off
    at the season's end (no future leakage).
    """
    import questionary
    from worldcupagents.seasons import normalize_season

    current = normalize_season(league.season)
    start = int(current[:4])
    choices = []
    for offset in range(0, 4):
        y = start - offset
        s = f"{y}-{(y + 1) % 100:02d}"
        label = f"{s}  (current)" if offset == 0 else f"{s}  (historical — Wikipedia squad)"
        choices.append(questionary.Choice(label, value=s))
    return questionary.select("Season  (↑/↓ then Enter)", choices=choices, default=current).ask()


def _pick_depth() -> str | None:
    """Arrow-key research-depth picker (TA's shallow/medium/deep step)."""
    import questionary

    pick = questionary.select(
        "Research depth  (↑/↓ then Enter)",
        choices=[
            questionary.Choice("Shallow — 1 debate round, no scenario debate (fastest/cheapest)", value="shallow"),
            questionary.Choice("Medium  — 2 debate rounds + scenario debate (recommended)", value="medium"),
            questionary.Choice("Deep    — 3 debate rounds + 2 scenario rounds + LLM analyst reports", value="deep"),
        ],
        default="medium",
    ).ask()
    return pick


def _launch_menu() -> None:
    """The guided home screen: pick an action, gather the few inputs it needs, run it.

    Shown when `footballagents` is run with no command. Each choice dispatches the
    real CLI command (so behaviour is identical to typing it), with the interactive
    flows reusing the same arrow-key pickers as the flags."""
    import questionary

    console.print("[bold]⚽ FootballAgents[/bold] [dim]— what would you like to do?[/dim]")
    action = questionary.select(
        "Choose an action  (↑/↓ then Enter, Esc to quit)",
        choices=[
            questionary.Choice("🔮  Predict a match (guided)", value="predict"),
            questionary.Choice("📋  Pre-match dossier — the data, no LLM", value="dossier"),
            questionary.Choice("📈  Live odds for a fixture", value="odds"),
            questionary.Choice("📡  Watch — matchday autopilot", value="watch"),
            questionary.Choice("🔄  Refresh after a matchday", value="refresh"),
            questionary.Choice("✅  Resolve played predictions", value="resolve"),
            questionary.Choice("🎯  Signal credit — which signals helped", value="credit"),
            questionary.Choice("🧭  Open the data explorer", value="explore"),
            questionary.Separator(),
            questionary.Choice("❔  Full command list", value="help"),
            questionary.Choice("✋  Quit", value="quit"),
        ],
    ).ask()

    if action in (None, "quit"):
        return
    argv = _menu_argv(action)
    if argv is None:
        return  # the user backed out of a sub-prompt
    _run_argv(argv)


def _menu_argv(action: str) -> list[str] | None:
    """Turn a menu choice into the argv for the real command (gathering inputs)."""
    import questionary

    simple = {"predict": ["predict", "-i"], "refresh": ["refresh"],
              "credit": ["credit"], "explore": ["explore"], "help": ["--help"]}
    if action in simple:
        return simple[action]

    if action in ("dossier", "odds"):
        home = questionary.text("Home / first team:").ask()
        away = questionary.text("Away / second team:").ask()
        return [action, home.strip(), away.strip()] if home and away else None

    if action == "resolve":
        argv = ["resolve", "--sync"]
        sel = _menu_pick_llm("Write an AI reflection on each result? (needs a key)")
        if sel:
            argv += ["--provider", sel["provider"]]
        return argv

    if action == "watch":
        argv = ["watch"]
        sel = _menu_pick_llm("Use an LLM to distil punditry/tactics? (needs a key)")
        if sel:
            argv += ["--provider", sel["provider"], "--model", sel["quick"]]
        else:
            argv += ["--no-llm"]
        if questionary.confirm("Keep polling every 30 min? (No = one tick now)", default=False).ask():
            argv += ["--interval", "30"]
        return argv
    return None


def _menu_pick_llm(prompt: str) -> dict | None:
    """Yes → the provider+model picker (returns the _guided_select dict, so the user
    chooses e.g. gpt-5.4-mini over the cheap default); No/Esc → None (offline)."""
    import questionary
    if not questionary.confirm(prompt, default=False).ask():
        return None
    return _guided_select()


def _run_argv(argv: list[str]) -> None:
    """Dispatch the chosen command through the real Typer app (identical to typing it)."""
    from typer.main import get_command
    console.print(f"[dim]→ footballagents {' '.join(argv)}[/dim]\n")
    get_command(app)(args=argv, prog_name="footballagents")


def _guided_select() -> dict | None:
    """Arrow-key selection of provider + deep/quick models (TradingAgents-style)."""
    import questionary

    provider = questionary.select(
        "LLM provider  (↑/↓ then Enter)",
        choices=[questionary.Choice(f"{p}  ({API_KEY_ENV[p]})", value=p) for p in PROVIDERS],
        default=None,
    ).ask()
    if provider is None:  # Ctrl-C / Esc
        return None

    d_default, q_default = default_models(provider)
    deep = _pick_model(provider, "Deep model  (judge / reasoning)", d_default)
    quick = _pick_model(provider, "Quick model  (advocates)", q_default)
    return {"provider": provider, "deep": deep, "quick": quick}


def _pick_model(provider: str, label: str, default: str) -> str:
    import questionary

    custom = "✎ Custom…"
    options = model_choices(provider) or [default]
    choices = [
        questionary.Choice(f"{m}  [{cost_label(m)}]", value=m)
        for m in options
    ] + [questionary.Choice(custom, value=custom)]
    pick = questionary.select(
        f"{label}  [pricing as of {PRICING_AS_OF}]",
        choices=choices,
        default=default if default in options else None,
    ).ask()
    if pick is None:
        return default
    if pick == custom:
        typed = questionary.text(f"{label} — model id:", default=default).ask()
        return typed or default
    return pick


def _auto_export_path(fx, cfg: dict) -> Path:
    """Build a unique export path inside exports_dir (never overwrites a prior run)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    home_slug = fx.home.replace(" ", "_").replace("/", "-")
    away_slug  = fx.away.replace(" ", "_").replace("/", "-")
    filename = f"{home_slug}_vs_{away_slug}_{stamp}.txt"
    exports_dir = Path(cfg.get("exports_dir", "exports"))
    exports_dir.mkdir(parents=True, exist_ok=True)
    return exports_dir / filename


_SEP = "=" * 72


def _export_txt(path: Path, fx, v, final: dict, predictor, cfg: dict) -> None:
    """Write the full prediction report as a clean plain-text file."""
    from worldcupagents.llm_clients.model_catalog import estimate_cost

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    mode = (
        f"{cfg['llm_provider']}  (deep: {cfg['deep_think_llm']} / quick: {cfg['quick_think_llm']})"
        if cfg.get("use_llm") else "baseline-only (no LLM)"
    )
    debate = final["debate_state"]["history"].strip() or "(no debate — LLM disabled)"
    suffix = f"  (via {v.decided_by.value})" if fx.knockout else ""
    probs = f"H {v.p_home:.0%}  /  D {v.p_draw:.0%}  /  A {v.p_away:.0%}"

    usage = predictor.last_usage
    if usage["input"] or usage["output"]:
        cost_str = ""
        if predictor.last_cost is not None:
            cost_str = f"  ≈ ${predictor.last_cost:.4f}"
        token_line = f"  Token usage    : {usage['input']:,} in / {usage['output']:,} out{cost_str}\n"
    else:
        token_line = ""

    lines = [
        _SEP,
        "WORLDCUPAGENTS — FIFA 2026 PREDICTION",
        f"Generated : {now}",
        f"Mode      : {mode}",
        _SEP,
        "",
        "FIXTURE",
        f"  Home  : {fx.home}",
        f"  Away  : {fx.away}",
        f"  Stage : {fx.stage.value}",
        f"  Venue : {fx.venue or 'TBD'}",
        "",
        _SEP,
        "ADVOCATE DEBATE",
        _SEP,
        "",
    ]

    # Debate — indent each speaker turn for readability
    for turn in debate.split("\n"):
        lines.append(f"  {turn}" if turn.strip() else "")

    lines += [
        "",
        _SEP,
        "VERDICT",
        _SEP,
        "",
        f"  Outcome        : {v.outcome.value}{suffix}",
        f"  Scoreline      : {v.scoreline}",
        f"  Probabilities  : {probs}",
        f"  Confidence     : {v.confidence}",
        f"  Key factors    : {'; '.join(v.key_factors) or '—'}",
        f"  X-factors      : {'; '.join(v.x_factors) or '—'}",
        "",
        "  RATIONALE",
    ]

    # Wrap rationale at ~68 chars so it reads naturally
    import textwrap
    for line in textwrap.wrap(v.rationale, width=68):
        lines.append(f"    {line}")

    lines += [""]
    if token_line:
        lines.append(token_line.rstrip())
    lines += ["", _SEP, ""]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_date(s: str | None):
    return datetime.fromisoformat(s) if s else None


if __name__ == "__main__":
    app()
