# CLAUDE.md — WorldCupAgents

Guidance for Claude Code working in this repo. Keep this file short, current, and
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

**Legitimacy layer (roadmap Phase A, plans/immutable-churning-bunny):** the LLM
debate is no longer unmeasured. `evaluate -L PL -p <provider> --last N` runs the
REAL predict graph over recent store matches (results known) and scores
baseline vs raw-judge vs blend vs market Brier; every read appends to
`data/eval_log.jsonl` (`pipelines/evaluate.py`), and `evaluate --fit-weight`
grid-searches `judge_weight` over all logged reads with zero LLM spend (don't
trust the fit below ~30 reads). `resolve --sync` auto-resolves every pending
prediction whose score already sits in the match store (also runs automatically
at the end of `fetch-data`), so the learning loop compounds hands-free. The
explorer gained a **📐 Calibration tab**: rolling Brier + hit-rate of actual
shipped predictions, a reliability table, and the eval-log summary. Rule that
goes with it: nothing graduates (fitted judge_weight, `use_stats_lambda`, new
layers) without beating the incumbent in this harness.

First eval run (2026-06-12, openai, 10 PL final-day matches): judge raw 0.616 /
blend 0.633 / flat baseline 0.661 Brier — the LLM added signal, but the round
was an upset-fest (even the de-vigged market scored 0.740 on it), so n=10
decides nothing. Consequences shipped: `use_stats_lambda` now defaults **True**
(the rank-Elo anchor has no home advantage — a WC neutral-venue design — which
flattened league baselines; fitted strengths were already LOOCV-validated at
0.579 vs 0.654 on 1,520 PL matches). Eval records mark `llm=False` when the
provider actually failed (placeholder-read detection via zero output tokens);
scoring dedupes reruns per (date, fixture, provider); repeat `evaluate` runs
skip already-evaluated fixtures so a bigger `--last` only spends on new ones.
Keep judge_weight at 0.6 until ≥30 clean reads.

**Live market as a judge feature (2026-06):** with `ODDS_API_KEY` set, the judge
and Final Pundit are shown the **de-vigged bookmaker consensus** (The Odds API,
averaged across books — `providers/odds_api.py`) + a **Polymarket crowd** win
prob (`providers/polymarket.py`), with the instruction to argue where its read
should differ. Assembled in `dataflows/market.py` (`market_read`/`market_digest`/
`divergence_note`); surfaced in `predict` (📈 Market panel with in-line/fading
note), `dossier`, and a standalone `odds HOME AWAY` command. NOT mechanically
blended into the ensemble — it's a reasoning input, so "argue where it's wrong"
stays real and there's no double-count. Flag `enable_market_context` (default on)
is forced **off** by the eval harness so the LLM-lift test stays an honest
*independent*-skill measure (the market would make it circular). Singapore
Pools/Kalshi deliberately not used (single-book/thin-coverage/grey-ToS); The
Odds API consensus is strictly more signal. `--no-market` on `predict`/`dossier`
suppresses it (for hypothetical/unscheduled matchups — which return no odds
anyway — or in-play games). Style prose is added via `qual-data --url <article>
--team X` (Total Football Analysis + BBC scrapable; Coaches' Voice 403; The
Athletic via `--note` paste) or the explorer Manual tab → qualitative warehouse
→ Tactical Analyst. Pass `--team` so the note links and surfaces.

**Alternative outcome / upset watch (2026-06):** every `MatchVerdict` now carries
an `AlternativeOutcome` (`ensemble/alternative.py::build_alternative`) — the
second-most-likely outcome off the SAME Poisson grid, with scoreline, probability,
gap behind the call, a `live` flag (≥25%), and data-backed `swing_factors`
(`upset_factors` reads set pieces, tempo/discipline frailty, form, knockout
variance). Always shown in predict output, the dossier, and the markdown export as
"⚠️ Upset watch" — so a favourite call is never the whole story. The scenario
Upside pundit's prompt now references the live alternative explicitly. Deterministic
and anchored; LLM narration optional. Honest counter to "favourites always win."

**refresh perf (2026-06):** `refresh` was re-ingesting the full ~49k-match
international-results dataset every run (the multi-minute cost). That history is
static, so it's now **opt-in** (`refresh --internationals`); the default matchday
`refresh` (WC results + auto-resolve + 5k-run sim + explorer) is ~14s.

**Scoreline = mean, not mode, for blowouts (2026-06):** two xG concepts —
`matches.xg_home/away` is REAL observed xG (Understat/StatsBomb, fetched, not
computed); the predicted **expected goals (λ)** come from rank-Elo → convex
supremacy → λ (`baseline.expected_goals`). The displayed scoreline was the grid
MODE, which undersells blowouts (Germany λ=4.6 showed 4-0 since P(4)≈P(5)). Fix:
`_scoreline` uses **rounded expected goals** for clearly lopsided wins
(`max(λ)≥2.5 and |Δλ|≥1.5`) → Germany–Curaçao now 5-0; normal games keep the mode
(Spain–Croatia 2-1). `MatchVerdict.exp_goals_home/away` carry λ, shown in the
predict verdict + report ("expected goals 4.6–0.3"). Probabilities are unchanged
(still the full grid) — only the illustrative scoreline.

**Match focus / battlegrounds (2026-06):** `ensemble/focus.py::match_focus` derives
*where the game is won* from the dossier data — a player to watch per side (top
xG+xA contributor; club form for nationals), the decisive battleground (attack vs
defence forte clash, set-piece edge), and the stylistic clash (possession vs
directness/pace). It's injected into the judge prompt as "MATCH FOCUS" with an
instruction to name the decisive area in `key_factors` and the player most likely
to decide it in `x_factors`; advocates are told to anchor on a specific area, not
"better overall". In `assemble_verdict` the focus tops up the LLM read's factors,
and IS the key/x factors on the no-LLM baseline. Deterministic + sourced.

**BBC team guide (2026-06):** `footballagents bbc-guide` ingests the BBC Sport
WC2026 team guide (a Shorthand story — content inline in the HTML, not a JSON
feed). Per team: an inline summary (world ranking / appearances / best
performance / a line) → team note, AND the "FULL TEAM PROFILE" link followed
through the existing public-article scraper (`ingest_public_article`) for the
rich prose — both team-linked → tactical analyst + dossier. `--no-full` skips
the 48 article fetches. `pipelines/bbc_guide.py` (injectable `fetch_text`).
Complements the Guardian guide (BBC = tournament framing/history; Guardian =
strengths/weaknesses/coach + per-player bios).

**Guardian Experts' Network + coach layer (2026-06):** `footballagents
guardian-experts` ingests the Guardian's long-form per-nation previews (the series
`football/series/world-cup-2026-guardian-experts-network`). The free Guardian API
tier only serves ~18 of these and tier-blocks the item endpoint for the rest, so
we **enumerate the full series off its paginated public index page and read each
public, non-paywalled article page directly** (owner-sanctioned scraping: polite
UA + retries, cache, provenance kept; never a paywall). Each article has a stable
section set (The plan / The coach / Star player / Unsung hero / One to watch);
`title_and_body` flattens the page's `<h2>`+`<p>` into the same shape the API
bodyText had, `split_sections` slices it (drops the standing boilerplate), and the
prose lands in the qualitative warehouse via `ingest_public_article(html_text=…)`
(team-linked → tactical analyst + dossier). Landed **45 of 48 team guides + 45
coach profiles** (3 — Mexico/South Korea/South Africa — have placeholder index
links that 404 because the Guardian hasn't published them yet; re-run to pick them
up). `pipelines/guardian_experts.py` (injectable `fetch_text`). The Guardian
*player* guide additionally backfills the coach **name** for all 48
(`guardian_guide.py` → `upsert_team_coach`), and football-data supplies it live —
so the 3 stragglers still get a coach line.
The **coach is now a first-class signal**: `TeamProfile.coach` (name, from
football-data) + a new `team_coach` store table (prose, from this guide), merged
by `dataflows/coach.py::coach_brief`/`coach_digest`. Surfaced in the dossier
(`- Coach:` line), the **form report** (`reports._coach_line` → propagates to
advocates + judge + scenario via `reports_block`), and called out explicitly in
the judge + advocate prompts (weigh managerial style/pedigree where real).

**Guardian player guide (2026-06):** `footballagents guardian-guide` ingests the
Guardian WC2026 interactive player guide — its data is a public "docsdata" feed
(`interactive.guim.co.uk/docsdata/{sheet}.json`): a Teams sheet (48 nations: bio/
strengths/weaknesses/coach/key player) linking per-team Players sheets (~26 each:
prose bio, position, club, caps, DOB, key-player tag). Landed **48 team briefs +
1,248 player profiles**. Team briefs → qualitative warehouse (team-linked →
tactical analyst); player bios → `player_notes` (→ player analyst + dossier,
squad-scoped); DOBs → a per-team average-age line. `pipelines/guardian_guide.py`
(idempotent; injectable `fetch_json` for tests). Player-note provenance now cites
the real `source` (Guardian vs manual) in the analyst line + dossier.

**Dossier enrichment (2026-06):** the dossier (CLI + report §0) now also shows a
**recent-matches table with per-match stats** (`recent_team_matches`: shots/SoT/
corners/fouls/cards + xG, team perspective — club fixtures; nationals show
score+xG only), a **richer player table** (adds shots + build-up xG), the
**StatsBomb playing-style fingerprint** (possession share, directness, key pass
combos, build-up zones — WC teams) and **team style-notes prose** from the
qualitative warehouse. Honest data gap: **per-match passing accuracy / possession
is NOT in any free source** (fdcouk has shots/SoT/corners/fouls/cards only;
Understat has xG; FBref/Sofascore/WhoScored are blocked; API-Football free is
capped to 2022–2024) — add it as prose via `qual-data`/`note-player`, or a paid
API-Football/Opta tier.

**Dossier in the report + national club stats (2026-06):** the markdown export
opens with **§0 Pre-Match Dossier** (`prematch.dossier_markdown` — per-team
forte/tempo/set-pieces/XI/players/career/form/weaknesses/notes + market + H2H)
so a reader can sanity-check the data and form their own view. National-team
player tables are no longer empty: `recall.squad_club_stats` matches each squad
player BY NAME across the club leagues (PL/PD/SA/BL1/FL1 Understat) and shows
their **club form** (e.g. Austria → Schmid/Werder, Laimer/Bayern; Turkey → Güler/
Real Madrid) — used by both the player analyst and the dossier when comp=WC has
no rows. Requires the club leagues' Understat data (now fetched for all big-5).
`ODDS_API_KEY` is whitespace-stripped (a leading space in .env caused a 401).

**Per-player qualitative notes (2026-06):** `player_notes` table + `note-player`
CLI (`note-player "Saka" -t "Arsenal FC" --note "..."`/`--delete`) + a 🧑 Player
Notes tab in the explorer (per-player form that builds the command, since the
static page can't write SQLite; plus a filterable table of existing notes).
Notes surface squad-scoped next to the player in the Player Analyst
(`reports._player_notes_line`) and the dossier — the qualitative player layer
that data can't capture. `hoard-data` raw fetches now reuse files across
snapshot dirs (`_ensure_raw`) so StatsBomb's 337 MB isn't re-downloaded daily.
Data-utilization note: matches/wh_matches/situations/career-totals/
player-match-stats/shootouts/qual/player-notes are all consumed at predict time;
**under-used**: `wh_lineups` (historical XIs, unused), `wh_goals` minute-level
timing (only aggregated), and cross-competition club stats for national-team
squad players (the biggest untapped join).

**Data-backed weaknesses (2026-06):** `dataflows/weaknesses.py::find_weaknesses`
surfaces concrete soft spots — ONLY when a real threshold trips (no manufactured
flaws): bogey/can't-beat opponent (H2H vs the actual opponent), falls short in
shootouts (`wh_shootouts`, nationals), set-piece vulnerability (Understat
conceded), soft home / poor away record (`venue_record`), goal over-reliance on
one scorer, form slump, indiscipline, leaky defence, blunt finishing. Each is
sourced + recency-bounded. Shown per team in the `dossier` (red ✗ list) and fed
into the Form Analyst (`reports._weakness_line`) so advocates attack real flaws.
New store readers: `venue_record`, `h2h_vs`, `shootout_record`.

**Pre-match dossier (2026-06):** `footballagents dossier HOME AWAY` is the
unified no-LLM lookup — line-up, squad-scoped player stats, recent scores+stats
(≤5 years), style of play, set pieces, and prior-prediction learnings — i.e. the
exact data the debate will see (`pipelines/prematch.py::build_dossier`). New
clean signals, no scraping: football-data.co.uk per-match stat columns
(HS/HST/HF/HC/HY/HR → shots, shots on target, fouls, corners, cards) now land in
the match store (12 new migrated columns) and aggregate to a per-team
**tempo & discipline** profile (`MatchStore.team_stat_profile`, recency-bounded);
**attack-vs-defense forte** is surfaced from the fitted strength model
(`strength.team_forte` — "attack-leaning / defense-leaning / complete", with
solidity = 1/defense). Both are wired into the Form Analyst so the live debate
sees them too. Player stats are now **squad-scoped** (`top_players(..., squad=)`)
— the dossier never references a non-squad player — and bounded to 5 years.
Honest remaining gaps (need manual input or a paid source): exact formation
(field exists, not auto-derived), average age (no clean birthdate feed),
structured manual team/player style profiles (free-text already flows via the
qualitative warehouse + Manual Analysis tab). Sources verdict unchanged:
Sofascore/WhoScored/FotMob/FBref are bot/ToS-blocked — not scraped.

**User-facing layer (2026-06):** `footballagents refresh` is the one-command
matchday loop (newest results → auto-resolve pendings → refresh internationals
→ re-simulate → regenerate explorer). The explorer's landing tab is now a
**📖 Guide**: step-by-step pipeline explanation, plain-language formula
explainers (Brier with worked examples, de-vig, the blend equation, what each
LLM-lift table row means and how it relates to the debate), and FAQs
(`_GUIDE_HTML` in data_explorer.py). All explorer tables are click-to-sort;
the three data tables have Comp/Season/Source dropdown filters. Payload caps
keep the NEWEST rows (capping the head used to hide every PL/WC row behind
1870s internationals). Wikipedia career-totals ingest now sources titles from
**current WC2026 squads** (competition feed, ~1,170 players) before all-time
INT scorers, with a "(footballer)" disambiguation fallback — landed **2,351
career-total rows / 899 players / 727 INT_CAREER summary** (was 104/30).
`hoard-data` raw fetches now reuse cached files across snapshot dirs
(`_newest_cached`) so a daily re-run doesn't re-download (snapshot id is
date-based). Career caps/goals surface in the `dossier` command for
internationals (`prematch._career_totals`).

**Tournament simulator (roadmap D1, 2026-06):** `simulate-tournament` runs
10k Monte-Carlo WC2026 tournaments in seconds (no LLM): real fixtures from the
football-data.org feed, played results locked in as fact, unplayed matches
sampled from the same λ machinery as the predictor (fitted strengths → rank-Elo
fallback), 2026 format generalized (top-2 per group + best thirds fill the
smallest power-of-two bracket). Knockout pairings use a labelled **seeded
approximation** (`_bracket_order` keeps top seeds apart) until the feed fills
the official LAST_32 slots, which are then used automatically. Output: per-team
advancement odds table + `exports/wc2026_sim.json` + a 🏆 tab in the explorer.
Re-run after each matchday. Lives in `pipelines/simulate.py`.

**Granular metrics layer (roadmap Phase B, 2026-06):** the warehouse now feeds
the debate. For tournament fixtures the form analyst carries international form
+ H2H from `wh_matches` and StatsBomb WC shot profiles + **style fingerprints**
(possession share, pass accuracy, directness, favourite pass pairs, build-up
zones — coordinates translated to coaching language via
`dataflows/pitch_zones.py`, never raw X,Y); the player analyst carries career
caps/goals (`wh_player_career_totals`) and per-player WC event aggregates
(`wh_player_match_stats`: passes/completion, progressive passes+carries —
completed-only, FBref convention — final-third entries, xG). StatsBomb ingestion
(`hoard-data --source statsbomb`) parses Pass/Carry/Shot events into aggregates
(147 matches → ~29k player-match stat rows); raw streams stay in `data/raw/`
snapshots only. For clubs, `fetch-data --xg` now also fills per-player season
metrics from the SAME cached Understat call (shots, key passes, xG/xA,
xGBuildup → `player_stats`, new columns migrated). Source verdicts (probed
2026-06-12): **FBref is Cloudflare-blocked** (not bypassed, house rule) and
**API-Football free tier is season-capped to 2022–2024** — both documented in
the explorer; paid API-Football is the only honest route to current-season pass
accuracy/injuries. Player reads dedupe accent-variant names (Gyökeres/Gyokeres)
in `recall.top_players`.

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
  llm_clients/     # multi-provider factory; default Anthropic Claude
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

## Working agreements for Claude

- Before structural work, skim [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md); it's the
  source of truth for design decisions and the roadmap.
- Keep changes scoped to one milestone (M0–M7 in the outline); each should leave a
  runnable demo.
- When you make a notable design decision, add a line to the outline's §13 or a
  short decision note — don't let the docs drift from the code.
