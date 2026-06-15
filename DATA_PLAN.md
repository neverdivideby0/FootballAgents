# Data Pipeline Plan — better scorelines & calibrated verdicts

Status: **Phase 0 + Phase 1 shipped** (incl. the Critic Loop). Phases 2–3 designed.

## Why this exists

Scorelines were stuck at 2-1/1-1/1-2 because the judge used a hardcoded lookup
table and the only real input to the model was FIFA rank. Phase 0 fixed the
*mechanism*; Phases 1–3 feed the *model* real data, in priority order:
**stats → news/lineups → commentary.**

## The spine: one Poisson goals model (shipped in Phase 0)

```
strength → expected_goals(λ_home, λ_away) → score_grid → { P(W/D/L), scoreline }
```

Everything downstream plugs into `expected_goals()` in
`worldcupagents/ensemble/baseline.py`. Each phase below improves the inputs to
that one function (and enriches the LLM briefs); the grid, the judge, and the
CLI never change. This is the key design property — **add data, not plumbing.**

---

## Phase 1 — Stats (the biggest accuracy lever)

Goal: replace rank-derived λ with **attack/defense strengths fitted from real
match results + xG**, and replace the FIFA-rank Elo prior with a proper rating.

### Sources (all free / ToS-safe — no paywalled scraping per CLAUDE.md hard rules)

| Source | Gives us | Access |
|---|---|---|
| **football-data.co.uk** | Historical results + closing odds (CSV) | Free download, no key |
| **ClubElo** (clubelo.com) | Daily Elo ratings | Free JSON API |
| **FBref / Understat** via `soccerdata` pkg | Team & player xG, shots | Free, attribution + rate-limit |
| football-data.org (already wired) | WC results once tournament starts | Free key (have it) |

### Collection
- New providers under `dataflows/providers/`:
  `football_data_couk.py`, `club_elo.py`, `fbref_stats.py`.
- Register them in `dataflows/interface.py` under the existing `results` and
  `stats_xg` categories (the slots already exist; they currently fall back to
  placeholder).
- Reuse `http_cache.py` for rate-limit + disk cache. Add a `--refresh-data`
  flag / `worldcupagents fetch-data` command to pull/refresh on demand.

### Storage — local SQLite (`data/football.db`)
Derived, queryable data — distinct from the human-readable markdown memory.
```sql
matches(date, comp, home, away, hg, ag, xg_home, xg_away, source)   -- append-only
ratings(team, date, elo, att_strength, def_strength, source)         -- recomputed
```
- SQLite chosen over flat files: thousands of rows, needs filtering/aggregation,
  single file, no server, git-ignored.
- `data/` added to `.gitignore`; a small `fetch-data` run rebuilds it.

### Model — fit strengths, feed λ
- New `ensemble/strength.py`:
  - Fit a **Dixon–Coles / Poisson regression** on `matches` → per-team
    `att_strength`, `def_strength`, plus a low-score correlation correction
    (Dixon-Coles fixes Poisson's under-prediction of 0-0/1-1).
  - `expected_goals_from_stats(home, away) -> (λ_home, λ_away)`.
- `baseline.expected_goals()` becomes: use fitted strengths when available in
  the DB, else fall back to today's rank-Elo. **Single conditional, one file.**

### Feed the LLM
- Populate `TeamProfile.xg_for`, `xg_against`, `form` from the DB.
- Extend `briefs.profile_brief()` to include "scored/conceded ~X xG per game,
  last N" and recent results — every number tagged with its `source`.
- Optionally pass the judge the top-5 grid cells as a scoreline anchor so it can
  *adjust* (±1 goal with a stated reason) rather than invent.

### Tests
- Strength fit on a fixed CSV fixture (hermetic, committed sample).
- λ monotonicity: stronger attack ⇒ higher λ.
- Provider HTTP mocked; DB built in `tmp_path`.

---

### ⭐ Execution plan — THE NEXT STEP (refined 2026-05, post-Phase-0)

Two refinements since this doc was first written:

1. **Measure first.** We now have a Brier `resolve` loop. Before adding the
   stats tier we build a tiny **calibration harness** so we can *prove* stats
   improve accuracy rather than assume it. It's the yardstick the whole tier is
   justified against, and it reuses `reflection.brier_score`.
2. **International ≠ club data.** football-data.co.uk / ClubElo are *club-league*
   focused — wrong for national teams. For **team strength** use international
   match history (eloratings.net-style international Elo, or international
   results from football-data.org). Reserve **club** sources (FBref/Understat)
   for **player** xG/metrics — which is exactly what the Critic Loop needs.

Milestones (each shippable, tested, offline-safe; degrades to rank-Elo λ):

- ✅ **M1.0 — Calibration harness.** `backtest` command: Brier + hit-rate for
  rank-poisson vs naive references on historical results. Yardstick = **0.379**.
- ✅ **M1.1 — Match store.** SQLite `data/football.db`; football-data.org +
  CSV-seed ingesters; `fetch-data` command. `data/` gitignored.
- ✅ **M1.2 — Strength → λ.** `ensemble/strength.py` (dependency-free Dixon–Coles
  ratio fit); `team_lambdas` = strengths-or-rank-Elo; **LOOCV** in the backtest
  for honest measurement. Live wiring gated behind `use_stats_lambda` (default
  off — no overfit λ ships). *Finding: 10 one-off matches give no out-of-sample
  signal → ties rank-Elo; the bottleneck is data volume, not the model.*
- ✅ **M1.3 — Enrich briefs + scout.** `enrich_profile()` fills `form`/`xg_for`/
  `xg_against` from the store; `profile_brief()` surfaces xG; wired into the
  dossier builder (predict) and the scout pipeline.
- ✅ **M1.4 — Critic Loop (feature D).** `critic` command + agent cross-examines
  quantitative metrics (xG/form/goals) against the qualitative `memory/matches/`
  tactical insights. **Team-level for now** — per-player metrics (passing
  accuracy etc.) await a club-stats source (FBref/Understat), a Phase-2 add-on.

**Shipped. Remaining honest gap: the stats tier needs match *volume* (many games
per team) to beat rank-Elo — seed a larger historical CSV or let the tournament
fill the store, then re-run `backtest` for the verdict.**

**2026-06 update:** `fetch-data --national-history --national-limit 5` now uses
API-Football to seed recent senior national-team results for the WC2026 team list
into the existing `matches` table as `comp='INT'`. This is the low-request first
step toward the "last 30 games" target while staying inside the free tier.

**2026-06 hoard layer:** `hoard-data --source international-results` snapshots
the CC0 martj42 international-results CSVs under `data/raw/`, normalizes men's
international results/goals/shootouts into `wh_*` warehouse tables, and feeds the
existing `matches` + `player_stats` summary tables for prediction compatibility.

**2026-06 identity layer:** `resolve-name` and `dataflows/entities.py` promote
team/club/country naming to a source-aware entity registry. Aliases are stored in
`wh_team_aliases`, ambiguous/unknown names in `wh_unresolved_names`, and summary
lookups now resolve IDs before falling back to text matching.

---

## Phase 2 — News & lineups (adjust λ for who's actually playing)

Goal: a **λ multiplier** for availability — key striker out ⇒ λ down; back line
decimated ⇒ opponent λ up — plus real lineups in the debate.

### Sources
| Source | Gives us | Access |
|---|---|---|
| **API-Football** (api-sports.io) | Predicted lineups, injuries, suspensions | Free tier ~100 req/day |
| **Web search** (planned vendor) | "predicted XI [team] world cup" | Existing plan |
| Reputable RSS (BBC, federations) | Injury/availability news | Public summaries only |

### Collection
- `dataflows/providers/api_football.py` + `web_search.py` under the `news`
  category (slot already exists). Same cache/rate-limit treatment.

### Storage
- Populate `TeamProfile.probable_xi`, `formation`, and a new
  `availability: list[PlayerStatus]` field (add to `schemas.py`).
- Persist per team to `memory/teams/<TEAM>.json`, timestamped (`last_updated`).

### Model
- `ensemble/availability.py`: map missing/returning key players to a λ
  multiplier (weight players by minutes/xG contribution from Phase 1 data, so
  importance is data-driven, not guessed). Clamp to a sane band (e.g. 0.8–1.15).
- Apply in `expected_goals()` after the base λ — still one function.

### Feed the LLM
- Lineups + injuries go into the advocate briefs so the debate argues about the
  actual XI, and into the judge's x-factor reasoning.

### Tests
- Mocked lineup payload → expected multiplier sign/magnitude.
- Star-out reduces that team's λ; verdict shifts in the right direction.

**Effort: ~1–2 days. Payoff: predictions react to team news, not just history.**

---

## Phase 3 — Commentary / tactical previews (qualitative enrichment)

Goal: richer debate and x-factors. **Does not touch λ** — lowest scoreline
impact, highest narrative value.

### Sources
- Web search for tactical previews; public match-preview text only.

### Storage
- A short, cited `context_notes` blob on the **matchup** (not the team), held in
  `MatchState.matchup_context`.

### Feed the LLM
- Injected into advocate + judge prompts only. Provenance rule still applies:
  cited, and the model is told to treat it as opinion, not fact.

### Tests
- Context string flows into prompts (FakeLLM captures prompt); no effect on the
  numeric grid.

**Effort: ~half day. Payoff: more convincing, better-grounded debates.**

---

## Cross-cutting

- **Provenance is mandatory** (existing rule): every stat in a brief carries a
  `source`; the LLM reasons only from provided numbers.
- **Graceful degradation** (existing pattern): any source missing/erroring →
  fall back to the previous tier, never crash. Phase 1 missing ⇒ rank-Elo λ.
- **Calibration (ties into M4):** once Phase 1 lands, score the Poisson model
  with Brier/log-loss against held-out results and against baselines (bookmaker
  de-vigged odds, rank-only) to prove the data actually helps. Distrust clean
  backtests on pre-2026 matches (training-data leakage — see PROJECT_OUTLINE §11).

## Build order recommendation

1. **Phase 1** — most accuracy per unit effort; everything else amplifies it.
2. **Phase 2** — needs Phase 1's player-importance weights to be data-driven.
3. **Phase 3** — independent; do anytime for debate quality.
