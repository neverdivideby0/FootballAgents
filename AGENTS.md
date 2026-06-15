# AGENTS.md — WorldCupAgents

Guidance for Codex working in this repo. Keep this file short, current, and
honest. If something here is wrong, fix it in the same PR.

## What this project is

A multi-agent LLM system that predicts **FIFA World Cup 2026** matches. Two team
"advocate" agents debate a fixture — each arguing for its side while naming its own
weaknesses — and a judge agent issues a grounded verdict (win/draw/loss +
scoreline + calibrated probabilities). After the real result, the system grades
its own prediction and updates each team's knowledge base.

Architecturally it is a deliberate adaptation of **TradingAgents** (see
`../TradingAgents`). Full design lives in [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md) —
**read it before making structural changes.**

Mental model: *a trading desk, but for football.* Tickers → fixtures, bull/bear →
team advocates, research manager → match pundit/judge, decision-vs-realized-return
reflection → prediction-vs-actual-result reflection.

## Status

🟡 v1 target = a working **single-match predictor**. Not building the full bracket
yet. **Done: M0** (pipeline + CLI + tests), **M1** (football-data.org live data),
**M2** (LLM advocates + structured judge, ensembled with a Poisson goals baseline),
**M3** (post-match Brier scoring + per-team lessons via `resolve`), plus the
commentary→tactical pipeline (`analyze-match`), the `predictive_brief` memory
bridge (tactical history flows into the debate), and the Senior-Scout report
(`scout-report`). See COMMENTARY_PLAN.md and DATA_PLAN.md.

Also shipped: the stats tier (DATA_PLAN Phase 1) — SQLite match store
(`fetch-data`), Dixon–Coles strength→λ (gated by `use_stats_lambda`), profile
enrichment (form/xG into briefs & scout), a Brier `backtest` harness, and the
Critic Loop (`critic`, quant-vs-qual cross-examination).

**Full TradingAgents topology** (see plans/immutable-churning-bunny review): the
predict graph is now `Scouts → Analyst reports (form/tactical/player digests,
free) → Home⇄Away advocate debate → Judge (provisional verdict) → Upside⇄Downside
⇄Neutral scenario pundits → Final Pundit (final verdict)`. Both new layers are
flag-gated (`enable_analyst_reports` on, `enable_scenario_debate` on;
`--depth shallow|medium|deep` presets map rounds TA-style). Every verdict —
provisional and final — goes through `ensemble/verdict.py::assemble_verdict`, so
probabilities stay blended with the Poisson baseline (never raw LLM numbers).
Predictions run under a **live TUI** (`tui.py`, progress/messages/report/stats)
on a TTY via `Predictor.predict_stream`. The memory loop is **closed**: `resolve
--provider X` writes an LLM reflection into `prediction_log.md`, and
`recall.prediction_lessons` (n_same=5, n_cross=3, TA's get_past_context port)
injects resolved lessons back into the Judge & Final Pundit prompts.

CLI commands: `predict` (with `--depth`, `--scenario/--no-scenario`,
`--scenario-rounds`), `analyze-match` (Guardian commentary → 5-phase tactical
report in memory/matches/), `scout-report` (stats + tactical memory → report),
`critic` (quant vs qual cross-examination), `resolve` (Brier + optional LLM
reflection), `backtest` (calibration yardstick), `fetch-data` (populate the
SQLite match store), `players`, `leagues`, `check`, `eliminate`. Exports:
sectioned markdown (`pipelines/report_export.py`) or txt. All LLM steps are
offline-by-default; add `--provider`/`--llm` to spend.

To run with real LLMs: pick a provider at the CLI (`--provider anthropic|openai|
google|deepseek`) with the matching key in `.env` (DeepSeek routes through the
OpenAI-compatible client). Or use `-i`/`--interactive` for an **arrow-key picker**
(via `questionary`) that selects provider + deep/quick models — see `_guided_select`
in `cli.py`. Model lists/defaults live in `llm_clients/model_catalog.py`. The picker
is guarded by `sys.stdin.isatty()` so non-interactive runs never hang. Without a key
it degrades to a baseline-only verdict (no crash).

**Probable line-ups:** FotMob/WhoScored are bot-blocked, so `fetch-data --xg`
derives a **most-used XI by minutes** from Understat's `playersData` (no extra
requests — same `getTeamData` call as xG/situations). Stored on `team_situations.xi`,
surfaced on `TeamProfile.probable_xi`, in the predict Squads panel + Form Analyst
report (sourced), and counted in the explorer ("XI teams" column). It's a
data-driven probable lineup, **not** a confirmed teamsheet — confirmed XIs +
injuries still want API-Football (Phase 2). Position grouping mirrors Understat's
own classification (it tags deep mids like Rice/Zubimendi as "D").

### Known data limitations (free tier)
- football-data.org free tier scopes a national team's `/matches` to the **WC
  competition only**, so **recent form is empty pre-tournament** (no WC games played
  yet) and historical friendlies/qualifiers aren't visible. Form will populate once
  the tournament starts — which M3's learning loop consumes. Squads, coaches, and
  fixtures are available now.

## Architecture (where things live / will live)

> Layout target — update as files land.

```
worldcupagents/
  agents/
    scouts/        # tool-using data gatherers → TeamProfile (form, squad, tactics, news, H2H)
    advocates/     # team_a_advocate.py, team_b_advocate.py (advocate + self-critique)
    judge/         # match_pundit.py → structured MatchVerdict
    schemas.py     # Pydantic: Fixture, TeamProfile, MatchVerdict
  graph/
    setup.py            # LangGraph StateGraph wiring
    conditional_logic.py# debate round caps (count >= 2 * max_rounds)
    state.py            # MatchState TypedDict
    reflection.py       # post-match scoring + lesson writing
  dataflows/
    interface.py   # vendor registry (category → provider)
    providers/     # football_data_org.py, fbref.py, web_search.py, ...
  llm_clients/     # multi-provider factory; default Anthropic Codex
  memory/
    teams/<TEAM>.md     # living dossier per nation
    prediction_log.md   # append-only pending→resolved with Brier scores
  config.py
  cli.py
```

## Key conventions

- **Mirror TradingAgents patterns** unless there's a reason not to: quick-think vs
  deep-think model split, structured output for the judge only (prose everywhere
  else), append-only markdown memory, swappable data vendors.
- **Provenance is mandatory.** Every stat/roster/injury claim must trace to a tool
  result and be recorded in `TeamProfile.sources`. Unsourced claims = opinion.
- **Advocates self-critique.** A team advocate that doesn't name its own team's
  weaknesses is a bug, not a style choice.
- **Stage-aware outcomes.** Group stage allows draws; knockouts force a winner with
  `decided_by ∈ {regulation, extra_time, penalties}`.
- **Markdown memory stays human-readable and Git-diffable.** Don't switch it to a
  binary/DB format without updating the outline.

## Hard rules

- ✅ **Web scraping of public pages is allowed** (owner's decision, 2026-06) for
  this personal research tool. Guardrails that remain: be polite (rate-limit via
  `HTTPCache`, identify a User-Agent, cache aggressively); ❌ never bypass
  paywalls, logins, or technical access controls (e.g. The Athletic content);
  every scraped stat still carries provenance (source + URL) like all other data.
- ❌ **No betting / real-money integration.** This is analysis, not a sportsbook.
- ⚠️ **Distrust clean backtests.** The model already knows past results — past-
  tournament accuracy is contaminated by training-data leakage. Always report
  against baselines (FIFA rank, de-vigged odds, Elo/Poisson) and weight live 2026
  predictions far more than historical ones. See PROJECT_OUTLINE §11/R1.
- ⚠️ Don't commit secrets. Keys go in `.env` (documented in `.env.example`).

## Commands

> Fill in as the project gains code. Intended:

```bash
uv venv --python 3.12 && uv pip install -e '.[dev]'   # setup
uv run worldcupagents predict "Brazil" "Argentina" --stage group
uv run worldcupagents check --team "South Korea"      # show active vendor + resolve a team
uv run pytest -q                                      # tests (hermetic; no token needed)
```

**Live data:** put a free token from football-data.org in `.env` as
`FOOTBALL_DATA_ORG_TOKEN=...`. The vendor auto-switches from `placeholder` to
`football_data_org` when the token is present (override with `WCA_DATA_VENDOR`).
Tests pin the placeholder, so they never touch the network.

## Working agreements for Codex

- Before structural work, skim [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md); it's the
  source of truth for design decisions and the roadmap.
- Keep changes scoped to one milestone (M0–M7 in the outline); each should leave a
  runnable demo.
- When you make a notable design decision, add a line to the outline's §13 or a
  short decision note — don't let the docs drift from the code.
