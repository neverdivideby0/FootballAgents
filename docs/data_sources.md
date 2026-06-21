# Data source registry

Every external data source, what it supplies, the key it needs, and whether its output
is **consumed** at predict time. The live list + health is `footballagents sources`;
this is the annotated, audited view.

- Source of truth for the *list* is the source specs in
  `pipelines/data_explorer.py::_sources_with_checks`; the pre-commit doc check warns if a
  spec is missing a row here.
- Refresh a row with the **`source-auditor`** subagent ("use source-auditor on `Understat`").
- Status legend: **consumed** (reaches predict/debate) · **limited** (works but capped) ·
  **blocked** (unavailable by design) · **orphaned** (fetched, never consumed).

| Source | Key | Supplies | Status | Last audited |
|---|---|---|---|---|
| football-data.org | `FOOTBALL_DATA_ORG_TOKEN` | Live squads, results, fixtures, scorers | consumed | — |
| football-data.co.uk | none | Multi-season results + odds + per-match stats | consumed | — |
| Guardian Open Platform | `GUARDIAN_API_KEY` | Match commentary + articles → tactical/punditry + warehouse | consumed | — |
| Public articles | none | User-supplied analysis URLs → qualitative warehouse | consumed | — |
| API-Football | `API_FOOTBALL_KEY` | Scorers + national results; free tier capped to 2022–24 | limited | — |
| Understat | none | xG, set-piece situations, most-used XI, per-player metrics | consumed | — |
| FBref | none | Pass accuracy / progressive actions — Cloudflare-blocked (house rule) | blocked | — |
| Wikipedia (MediaWiki API) | none | Historical squads + player career caps/goals | consumed | — |
| StatsBomb Open Data | none | Past WC matches, lineups, shot events, situations | consumed | — |
| Curated FIFA rankings | none | Strength prior for all 48 WC2026 sides + clubs | consumed | — |
| LLM providers | `*_API_KEY` | Debate / judge / scenario agents (offline baseline without) | consumed | — |
| The Odds API | `ODDS_API_KEY` | De-vigged bookmaker consensus shown to the judge | consumed | — |
| Polymarket | none | Prediction-market crowd win prob (marquee fixtures) | consumed | — |
