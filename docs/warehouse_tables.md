# Warehouse table registry

Every table declared in `worldcupagents/dataflows/match_store.py`, its purpose, and
whether it's **consumed** at predict time, pure ingest **infra**, or **orphaned**
(written but never read into the debate).

- Source of truth for the *list* is the `CREATE TABLE` statements in `match_store.py`;
  the pre-commit doc check warns if a table is missing a row here.
- Refresh a row with the **`data-auditor`** subagent ("use data-auditor on `wh_goals`").
- Status legend: **consumed** (reaches predict/debate) · **infra** (entity resolution /
  provenance / ingest plumbing) · **orphaned** (write-only, no read path).

| Table | Purpose | Status | Last audited |
|---|---|---|---|
| `matches` | Core results + odds + per-match stats; the prediction baseline, form, dossier | consumed | — |
| `player_stats` | Per-player club metrics (goals/xG/key passes…) → player analyst, dossier | consumed | — |
| `team_situations` | Set-piece breakdown + most-used XI (Understat) → form analyst, weaknesses | consumed | — |
| `player_notes` | Manual / Guardian per-player notes → player analyst, dossier | consumed | — |
| `team_coach` | Coach name + prose → form analyst, dossier | consumed | — |
| `injuries` | Player availability (manual + punditry-harvested) → status overlay, probable XI filter, form report | consumed | 2026-06-23 |
| `wh_sources` | Warehouse provenance: registered ingest sources | infra | — |
| `wh_source_files` | Raw snapshot file ledger per source | infra | — |
| `wh_ingestion_runs` | Ingest run history (status/counts/timing) | infra | — |
| `wh_teams` | Canonical team entities | infra | — |
| `wh_team_aliases` | Team name → entity resolution | infra | — |
| `wh_unresolved_names` | Names that didn't resolve (resolution backlog) | infra | — |
| `wh_competitions` | Competition metadata | infra | — |
| `wh_matches` | International results history → form/H2H for tournament fixtures | consumed | — |
| `wh_match_sources` | Provenance link: match → source | infra | — |
| `wh_goals` | Minute-level goal events (scorer, minute) | **orphaned** | 2026-06-22 |
| `wh_lineups` | Historical XIs (StatsBomb) | **orphaned** | 2026-06-22 |
| `wh_events` | StatsBomb pass/carry/shot event aggregates | **orphaned** | 2026-06-22 |
| `wh_team_match_stats` | Per-team granular match stats | **orphaned** | 2026-06-22 |
| `wh_player_match_stats` | Per-player WC aggregates (passes/progressive/xG) → player analyst | consumed | — |
| `wh_players` | Canonical player entities | infra | — |
| `wh_player_aliases` | Player name → entity resolution | infra | — |
| `wh_player_career_totals` | Caps/goals → player analyst, dossier | consumed | — |
| `wh_qual_documents` | Qualitative warehouse: ingested articles → `qualitative_brief` | consumed | — |
| `wh_qual_segments` | Article text chunks → `qualitative_brief` | consumed | — |
| `wh_qual_claims` | Claim tags on segments | consumed | — |
| `wh_qual_links` | Segment/document ↔ team links → `qualitative_brief` | consumed | — |
