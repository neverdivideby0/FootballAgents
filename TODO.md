# TODO / Roadmap

Living backlog. Done items are kept briefly for context; the live work is **Next** and **Parked**.
Guiding rules: every layer keeps a no-LLM deterministic path; nothing graduates without beating the
incumbent in the `backtest`/`evaluate` harness; no vector stores/microservices until SQLite + markdown
+ one HTML page demonstrably break.

## Next (in priority order)

- [ ] **Leak-free live eval** — run `evaluate` on live WC2026 fixtures (post-training-cutoff) and
      re-fit `judge_weight` on *that*. The current 50-read fit (judge 0.556 < market 0.593 < baseline
      0.603) is PL 2025-26 → likely training-leaked, so don't trust w→1.0 yet. Interim: 0.6 → ~0.7 ok.
- [ ] **`predict-round -L PL`** — predict a whole matchday in one run (baseline for all, LLM depth on
      demand, one report). Weekly-use ergonomics + feeds eval volume automatically.
- [ ] **Daily feed cron** — scheduled `qual-data --feed-url` pull (Guardian/blogs) to grow the
      qualitative corpus hands-free (dedup already exists).
- [ ] **One `data` front door** — `data refresh/status/add` consolidating fetch-data/hoard-data/
      qual-data (today: `refresh` covers the matchday loop).
- [ ] **Proper Dixon–Coles** strength model (time-decay + low-score correction), validated by LOOCV
      before replacing the ratio model.
- [ ] **Ensemble stacking** — fit blend weights across baseline / stats / market / LLM read on
      resolved predictions (only legitimate once the leak-free eval has volume).

## Parked (need a decision or a paid key)

- [ ] **Per-match passing accuracy / possession** — not in any free source (FBref/Sofascore/WhoScored
      bot-blocked; API-Football free capped to 2022–24). Needs a paid API-Football/Opta tier, or add
      as prose via `note-player`/`qual-data`. Provider scaffold exists; ~1 day to wire a paid key.
- [ ] **Formation auto-detect** — field exists, not derived (Understat positions too crude).
      Average age is now filled from the Guardian guide DOBs.
- [ ] **Under-used stored data** — `wh_lineups` (historical XIs, unused) and `wh_goals` minute-level
      timing ("scores late / fast starter", only aggregated today).

## Done (highlights, 2026-06)

- Legitimacy harness: `evaluate` (LLM-lift Brier), `--fit-weight`, `resolve --sync` auto-grading,
  Calibration tab. `use_stats_lambda` now default-on (LOOCV-validated).
- Granular metrics: fdcouk per-match stats (shots/SoT/corners/fouls/cards), StatsBomb event
  aggregates + style fingerprints, `pitch_zones`, Understat per-player metrics for all big-5.
- Pre-match `dossier` (squad-scoped, ≤5y): forte, tempo/discipline, set pieces, likely XI, recent
  games *with stats*, weaknesses, career caps, club form for nationals, embedded as report §0.
- Honest counterweight: ⚠️ Upset Watch (alternative outcome + data-backed swing factors) on every verdict.
- Live market as a judge feature: The Odds API de-vig consensus + Polymarket crowd, `--no-market`
  toggle, model-vs-market divergence. (Singapore Pools/Kalshi rejected — single-book/thin/grey-ToS.)
- Qualitative layer: Guardian guide (~1,250 player bios + 48 team briefs), BBC guide (48 full team
  profiles), per-player `note-player` + explorer Player Notes tab, sourced provenance.
- Data hygiene: cross-snapshot raw-file reuse (`_ensure_raw`) so daily re-runs don't re-download;
  seed rows removed; Wikipedia career totals → 2,351 rows.
- WC2026 Monte-Carlo `simulate-tournament`; explorer overhaul (Guide/Calibration/WC-sim tabs,
  sortable + filterable tables); `refresh` one-command matchday loop.
