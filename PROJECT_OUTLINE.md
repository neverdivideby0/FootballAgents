# WorldCupAgents — Project Outline (PRD)

> A multi-agent LLM system that predicts FIFA World Cup 2026 matches through
> structured debate between team advocates and a grounded pundit-judge —
> inspired by the architecture of [TradingAgents](../TradingAgents).

- **Owner:** Bryan
- **Status:** 🟡 Draft v0.1 — design locked, not yet built
- **Last updated:** 2026-05-31
- **Inspiration:** TradingAgents (LangGraph multi-agent trading framework)

---

## 1. Vision & one-liner

**"A trading desk, but for football matches."** Each participating nation gets an
agent that knows its footballing history, squad, tactics, and form. For a given
fixture, the two team agents **debate** — each advocating for its own side while
being forced to name its own weaknesses — and a **judge agent** synthesizes the
debate into a grounded verdict (win / draw / loss + scoreline + calibrated
probabilities). After the real match is played, the system **grades its own
prediction** and updates each team's knowledge base. It gets smarter over the
tournament.

---

## 2. Goals & non-goals

### Goals (v1)
- **G1 — End-to-end single match.** Given `Team A vs Team B` on a date, produce a
  reasoned verdict with probabilities, a scoreline, and a written debate transcript.
- **G2 — Accuracy-first, but readable.** Track calibrated accuracy (Brier score,
  log-loss, calibration curve) *and* produce pundit-quality prose.
- **G3 — Self-correcting memory.** After each result, grade the prediction, write a
  reflection, and refresh the team dossiers — the TradingAgents reflection loop.
- **G4 — Pluggable data layer.** Adding a new data source = drop in a module +
  register it. No core changes. (Explicit requirement.)
- **G5 — Bias-resistant debate.** Each team agent advocates *and* self-critiques;
  the judge weighs both.

### Non-goals (for now)
- ❌ Full 48-team bracket simulation (that's v2 — the "Portfolio Manager" analog).
- ❌ Live in-match / minute-by-minute prediction.
- ❌ Betting integration or any real-money action.
- ❌ Scraping paywalled content (e.g. The Athletic). We summarize *public*
  reporting only and respect robots.txt / ToS. See §11.

---

## 3. Success metrics

Because we chose **accuracy-first**, "good" is measurable, not vibes.

| Metric | What it measures | Target signal |
|---|---|---|
| **Brier score** (3-class W/D/L) | Probability calibration + correctness | Beat naive baselines (below) |
| **Log-loss** | Penalizes confident wrong calls | Lower than baseline |
| **Outcome accuracy** | % correct W/D/L | Beat "pick FIFA-ranked favorite" |
| **Scoreline exact / ±1 goal** | Sharpness | Track, don't over-optimize |
| **Calibration curve** | Are "70%" calls right ~70% of the time? | Near-diagonal |

**Baselines to beat** (build these first — they're cheap and keep us honest):
1. Always pick the higher FIFA-ranked team.
2. Bookmaker implied probabilities (de-vigged odds) — the hard target.
3. A simple Elo or Poisson/xG model.

> ⚠️ **Backtesting caveat (critical):** the LLM's training data already "knows" the
> result of past tournaments (e.g. the 2022 final). Naive backtests will look
> amazing and mean nothing. See §11/R1 for the mitigation.

---

## 4. System architecture

Mirrors TradingAgents: a **LangGraph `StateGraph`** with a shared state dict
flowing through agent nodes, conditional edges for the debate loop, and a
deferred-reflection memory layer.

```
                          ┌─────────────────────────────────────────┐
   fixture (A vs B,        │              MatchState (shared)         │
   date, stage) ──────────▶│  fixture · home_profile · away_profile  │
                          │  matchup_context · debate_state · verdict│
                          └─────────────────────────────────────────┘
                                          │
        ┌─────────────────────────────────┼─────────────────────────────────┐
        ▼                                 ▼                                   ▼
  SCOUTS (tool-using, per team)     ADVOCATE DEBATE (round-capped)        JUDGE
  ┌──────────────────────┐         ┌───────────────────────────┐    ┌───────────────┐
  │ Form & Results       │         │  Team A Advocate  ⇄        │    │ Match Pundit  │
  │ Squad & Lineup       │ ──────▶ │  Team B Advocate           │──▶ │ structured    │
  │ Tactics & xG         │         │  (advocate + self-critique)│    │ MatchVerdict  │
  │ News & Punditry      │         └───────────────────────────┘    └───────┬───────┘
  │ History & H2H        │                    ▲                              │
  └──────────────────────┘                    │ (optional)                   ▼
                                      ┌────────────────┐              outcome + probs
                                      │ Scenario Agent │              + scoreline
                                      │ (red card,     │
                                      │  pens, weather)│
                                      └────────────────┘
                                                                            │
   AFTER REAL MATCH ◀──────────────────────────────────────────────────────┘
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Reflection loop: fetch actual result → score (Brier) → write reflection   │
   │ → update team dossiers + append to prediction_log → inject into next run  │
   └─────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Direct mapping from TradingAgents

| TradingAgents | WorldCupAgents | Reuse |
|---|---|---|
| `propagate(ticker, date)` | `predict(fixture)` entry point | Pattern |
| Analyst team (market/news/fundamentals/sentiment) | Scouts (form/squad/tactics/news/H2H) | Pattern + tool-loop |
| Bull / Bear researcher | Team A / Team B advocate | Heavy reuse, reframed |
| `should_continue_debate` (count cap) | Same round-cap logic | Direct reuse |
| Research Manager (5-tier rating, structured) | Match Pundit (verdict schema) | Heavy reuse |
| Risk-mgmt 3-way debate | Scenario stress-test (optional v1) | Pattern |
| Portfolio Manager | Bracket simulator (v2) | Pattern |
| Reflection vs realized return/alpha | Reflection vs actual result / Brier | Heavy reuse |
| `trading_memory.md` append-only log | `prediction_log.md` + per-team dossiers | Heavy reuse |
| `data_vendors` config + vendor methods | Football data vendor registry | Heavy reuse |
| Quick-think / deep-think LLM split | Same (cost control) | Direct reuse |
| Multi-provider LLM factory | Same; default Anthropic Claude | Direct reuse |

### 4.2 What we deliberately change
- **Self-critical advocates.** TradingAgents' bull is *always* bullish. Our advocates
  must output **both** a strengths case and an explicit "where my team loses this"
  section. Bias is reduced at the source *and* arbitrated by the judge.
- **Outcome space depends on stage.** Group stage → {Home, Draw, Away}. Knockout →
  forced winner with `decided_by ∈ {regulation, extra_time, penalties}`. The judge
  schema and conditional logic branch on `fixture.stage`.
- **"X-factor" requirement.** The judge must surface external factors not raised in
  debate (travel, altitude/heat — relevant for 2026 US/Mexico venues, refereeing,
  crowd, congestion). This is your explicit ask, encoded in the verdict schema.
- **Ensemble probabilities.** Final win/draw/loss probabilities are a **blend** of the
  judge's qualitative read and a statistical baseline (Elo/Poisson) rather than a raw
  LLM-stated percentage — LLMs calibrate poorly when asked to emit "70%" directly.
  Lives in `ensemble/`; the blend weight is configurable.

---

## 5. Data model (shared state)

Mirrors `AgentState`. Sketch (Pydantic + a LangGraph `TypedDict` state):

```python
class Fixture(BaseModel):
    home: str; away: str
    kickoff: datetime
    stage: Literal["group","R32","R16","QF","SF","F"]
    venue: str | None          # city/stadium → altitude, climate, travel
    group: str | None

class TeamProfile(BaseModel):           # the evolving "dossier"
    team: str
    fifa_rank: int | None
    squad: list[Player]                 # name, club, pos, status (fit/injured/susp)
    probable_xi: list[str]
    formation: str | None               # "4-3-3"
    style: str                          # prose: press, low block, etc.
    form: list[MatchResult]             # recent results
    xg_for: float | None; xg_against: float | None
    tournament_pedigree: str
    last_updated: datetime
    sources: list[str]                  # provenance for every claim

class MatchVerdict(BaseModel):          # the judge's structured output
    outcome: Literal["HOME_WIN","DRAW","AWAY_WIN"]
    decided_by: Literal["regulation","extra_time","penalties"]  # knockout only
    p_home: float; p_draw: float; p_away: float    # must sum ~1 → calibration
    scoreline: str                       # "2-1"
    confidence: Literal["low","medium","high"]
    key_factors: list[str]
    x_factors: list[str]                 # external angles not in debate
    rationale: str
```

`MatchState` (LangGraph state) holds: `fixture`, `home_profile`, `away_profile`,
`matchup_context`, `debate_state {history, home_history, away_history,
current_response, count, verdict}`, `scenario_state`, `verdict`, and
`past_context` (memory injected at run start) — a near-1:1 analog of
`AgentState` + `InvestDebateState`.

---

## 6. Agent roster

### Scouts (tool-using, one pass each, write into the relevant profile)
1. **Form & Results Scout** — recent matches, momentum, goals for/against.
2. **Squad & Lineup Scout** — availability, injuries, suspensions, probable XI.
3. **Tactics & xG Scout** — formation, style, set-pieces, attacking/defensive xG.
4. **News & Punditry Scout** — LLM web search for injuries, morale, manager quotes,
   pundit opinion (summarized, cited; no paywalled scraping).
5. **History & H2H Scout** — tournament pedigree + head-to-head record + venue/stakes.

> For the v1 MVP these can be **collapsed into 2 nodes** (a `TeamDossierBuilder`
> producing `TeamProfile`, and a `MatchupContext` analyst) to get end-to-end fast,
> then split out as quality demands. Start lean.

### Debaters (deep enough model for argument quality)
6. **Team A Advocate** / 7. **Team B Advocate** — hybrid: build the case for their
   side *and* a mandatory "honest weaknesses" section; engage the opponent's last
   argument directly. Round-capped like `should_continue_debate`.

### Optional
8. **Scenario Agent** — stress-tests low-probability swing events.

### Decision
9. **Match Pundit (Judge)** — deep-think LLM, structured `MatchVerdict`, adds x-factors.

### Background
10. **Reflector** — post-match, scores the call and writes the lesson (not in the live graph).

---

## 7. The prediction pipeline (control flow)

1. `predict(fixture)` → load both `TeamProfile`s from memory; inject `past_context`
   (recent reflections for these teams).
2. Scouts run (refresh dossiers with latest data via the vendor layer).
3. `MatchupContext` assembled (venue → heat/altitude/travel, stakes, H2H).
4. Advocate debate runs `max_debate_rounds` (default 2 → 4 turns), alternating,
   each appending to `debate_state.history`.
5. (Optional) Scenario agent injects swing-event analysis.
6. Judge synthesizes → `MatchVerdict` (structured). Conditional on `stage` for the
   outcome space.
7. Persist: full transcript JSON + append `pending` entry to `prediction_log.md`.

## 8. The learning loop (post-match)

Lifted almost verbatim from TradingAgents' `memory.py` + `reflection.py`:
1. On the next run (or a scheduled job), find `pending` predictions whose match has
   been played.
2. Fetch the **actual result** via the results vendor.
3. Compute **Brier score / correctness**; the `Reflector` writes a 2–4 sentence lesson.
4. Update the `pending` log entry → `resolved` with the score + reflection.
5. Append the lesson to each team's dossier; it's injected into future debates.

Two persistence artifacts (both human-readable markdown, like TradingAgents):
- `memory/teams/<TEAM>.md` — the living dossier per nation.
- `memory/prediction_log.md` — append-only, `pending → resolved`, with Brier scores.

---

## 9. Data sources & vendor abstraction

Selected for v1: **free football APIs + LLM web search**, with a **registry so new
sources plug in without core changes** (your requirement).

```
data_vendors:                      # category → default provider
  fixtures:   football_data_org
  results:    football_data_org
  squads:     football_data_org
  lineups:    web_search           # often only available pre-match via news
  stats_xg:   fbref                # scrape-with-care or manual seed initially
  news:       web_search
tool_vendors: {}                   # per-tool override, e.g. stats_xg: statsbomb
```

| Category | v1 (free) | Future (pluggable) |
|---|---|---|
| Fixtures / results / squads | football-data.org, OpenFootball | API-Football (paid) |
| Advanced stats / xG | FBref, manual seed | StatsBomb, Opta (paid) |
| News / punditry / injuries | Claude web search | Licensed feeds |

**Vendor contract:** every category defines an interface (e.g.
`get_results(team, window) -> list[MatchResult]`); each provider implements it; a
registry maps name → implementation. Adding StatsBomb = new module + one registry
line. This is exactly TradingAgents' `VENDOR_METHODS` pattern in
[dataflows/interface.py](../TradingAgents/tradingagents/dataflows/interface.py).

---

## 10. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.13 | Matches TradingAgents; best football-data ecosystem |
| Orchestration | **LangGraph** | Free debate loop, conditional edges, checkpointer, state |
| LLM provider | Multi-provider factory + **CLI `--provider` selection** (anthropic / openai / google / deepseek) | Pick per run, TradingAgents-style; DeepSeek via OpenAI-compatible API |
| Model split | quick-think (scouts) / deep-think (advocates, judge) | Cost control, per TradingAgents |
| Structured output | Pydantic schemas (judge only) | Calibrated probs need a schema; prose elsewhere |
| Env / deps | `uv` | Matches TradingAgents (`uv.lock`) |
| Caching | local cache dir + per-fixture state logs | Cheap reruns, reproducibility |
| Interface | CLI first (Typer/Rich) | Match TradingAgents; web UI later |

> **Alternative considered:** Claude Agent SDK. Viable, but LangGraph gives the
> round-capped debate + conditional edges + checkpoint-resume essentially for free,
> which is the bulk of what we're reusing. Revisit if we drop the graph model.

---

## 11. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| **R1** | **Backtest leakage** — LLM already knows past results, so backtests are fake | Backtest only on a *held-out* past tournament with explicit "reason as of date X" prompting + knowledge-cutoff awareness; weight live 2026 predictions far more; report both. Treat pre-tournament backtest accuracy with heavy skepticism. |
| R2 | Hallucinated stats / fake injuries | Ground every numeric/roster claim in a tool result; require source provenance in `TeamProfile.sources`; judge discounts unsourced claims |
| R3 | Paywalled content (The Athletic etc.) | Never scrape paywalled/ToS-protected sources. Summarize public reporting only; respect robots.txt. Flag for legal review before any new source |
| R4 | Data licensing & rate limits (free tiers) | Cache aggressively; respect rate limits; vendor layer isolates this |
| R5 | Overconfidence / poor calibration | Track calibration curve; consider probability post-hoc calibration; reward Brier, not just accuracy |
| R6 | LLM cost blowup over 104 matches | quick/deep split, caching, cap debate rounds, batch reflections |
| R7 | Home/host bias, "big nation" bias in the model | Self-critique requirement + adversarial debate + baseline comparison surface it |

---

## 12. Roadmap / milestones

- [x] **M0 — Repo & skeleton.** uv project, package layout, `CLAUDE.md`, config,
  LLM factory, vendor registry, `MatchState`, runnable placeholder pipeline + CLI + tests.
- [x] **M1 — Data layer (football-data.org).** Live provider behind the vendor
  registry: real squads + recent results into `TeamProfile`, curated FIFA-rank
  table for the baseline, name normalization, disk cache + rate-limit, token
  auto-detection, graceful fallback, `check` CLI command. *(web-search provider for
  news/punditry deferred to M1.5 / M2.)*
- [x] **M2 — LLM agents on (the MVP).** Real Claude advocates (argue + mandatory
  self-critique) and a structured-output judge whose qualitative read is ensembled
  with the Elo baseline. Flip `use_llm`/`WCA_USE_LLM`; graceful baseline-only
  degradation when no key. Hermetic tests via a fake LLM.
- [ ] **M3 — Memory & reflection.** `prediction_log.md`, per-team dossiers, post-match
  scoring (Brier), reflection injection.
- [ ] **M4 — Baselines & evaluation.** FIFA-rank, odds, Elo/Poisson baselines + a
  scoring harness + calibration report.
- [ ] **M5 — CLI polish.** Interactive fixture selection, live progress (à la TradingAgents CLI).
- [ ] **M6 — Held-out backtest.** Validate on Euro 2024 / WC 2022 with leakage controls.
- [ ] **M7 (v2) — Bracket simulator.** Iterate the match engine across the full draw → champion.

---

## 13. Decisions & open questions

### Resolved (v1)
- ✅ **Scenario/risk-stress agent — DEFERRED out of v1.** Keep the MVP lean; revisit in v2.
- ✅ **Probabilities — ENSEMBLE.** The judge's qualitative read is blended with a
  statistical baseline (Elo/Poisson) rather than asking the LLM to state a raw "70%".
  Better calibration. See §4.2 and `ensemble/`.
- ✅ **Scouts — COLLAPSED to 2 nodes** for v1 (`build_dossiers` + `matchup_context`);
  split into the full 5 as quality demands.
- ✅ **Env tooling — `uv`** managing a 3.11+ venv (matches TradingAgents).
- ✅ **Qualitative corpus — STRUCTURED RAG first.** Guardian commentary and public
  articles are stored as raw snapshots plus warehouse documents/segments/claim tags
  and team links. Defer GraphRAG until cross-document, multi-hop questions justify it.

### Still open
1. **Naming** — `WorldCupAgents`? `PitchAgents`? `GaffrAI`? (using WorldCupAgents for now)
2. **xG source** — FBref scraping is fragile; OK to hand-seed advanced stats for v1?
3. **Debate rounds** default — 2 (4 turns) balances depth vs cost?
4. **Refresh cadence** — re-scout per fixture, or daily team-dossier refresh job?

---

## 14. Best practices (living list — see also `CLAUDE.md`)

- **`CLAUDE.md`** committed at repo root so every Claude Code session has context.
- **Provenance over fluency** — unsourced stats are treated as opinion, not fact.
- **Deterministic where possible** — pin model IDs; log prompts + outputs per run.
- **Cheap, reproducible reruns** — cache all external data by `(vendor, query, date)`.
- **Markdown-as-memory** — keep the dossiers/logs human-readable and Git-diffable.
- **Evaluate honestly** — always report against baselines; never trust a clean backtest.
- **Secrets in `.env`**, never committed; `.env.example` documents required keys.
- **Small PRs per milestone**, each with a runnable demo.
```
