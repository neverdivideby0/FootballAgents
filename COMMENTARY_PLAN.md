# Commentary → Tactical Insight Pipeline — Plan

Status: **designed, not built.** Companion to `DATA_PLAN.md`. This covers the
first slice only: **A→B→C** (ingest → 5-phase chunk → tactical extraction).
Deliberately **deferred**: Critic Loop (needs player stats — `DATA_PLAN.md`
Phase 1), Senior-Scout report, `predictive_brief`, vector index.

## Context

We want long-term tactical memory of matches to ground pre-game analysis. The
raw material is post-game text commentary, which an LLM turns into structured
tactical insights (formations, adjustments, key matchups) per game phase.

Two realities shape the design:
1. **No live WC data until June 2026.** Build & validate against **WC 2022**
   (depth) now; point the live path at **2026 friendlies** as they happen.
2. **ToS hard rule (`CLAUDE.md`): no scraping paywalled/restricted sites.** So
   **API-first**: qualitative text from the **Guardian Open Platform** (free,
   sanctioned); typed events (goal/card/sub + minute) from the stats API we
   already use (`football-data.org`). No Playwright, no BeautifulSoup.

## Architecture (offline/batch — separate from the live `predict` graph)

```
A. INGEST            B. CHUNK                    C. ANALYZE                STORE
┌──────────────┐    ┌─────────────────┐         ┌────────────────────┐   ┌────────────────────────┐
│ Guardian API │──▶ │ merge text+events│──5──▶  │ per-phase tactical │──▶│ memory/matches/<id>.json│
│ (prose)      │    │ into one timeline│ phases │ LLM extraction     │   │  (+ .md for humans)     │
│ stats API    │──▶ │ split by minute  │        │ → TacticalInsight  │   └────────────────────────┘
│ (typed events)│   └─────────────────┘         └────────────────────┘
└──────────────┘
```

The 5 phases (deterministic minute split):
`0-15 Initial Setup` · `15-45 First-Half Shift` · `Half-Time Brief` ·
`45-75 Tactical Adjustments` · `75-90+ Crunch Time`.

## Components & files (reuse first)

**A. Ingest** — new `dataflows/commentary/` package (commentary doesn't fit the
existing `FootballDataProvider` protocol, so give it its own small protocol):
- `dataflows/commentary/base.py` — `CommentaryProvider` protocol:
  `fetch_match(home, away, date) -> RawMatchFeed`.
- `dataflows/commentary/guardian.py` — Guardian Open Platform client. Search the
  Football section by team names + date for the min-by-min article; pull
  `bodyText`/`body`. **Reuse `HTTPCache`** (`dataflows/http_cache.py`) for
  rate-limit + disk cache. **Reuse `names.py`** (`canonical_name`) for
  article↔fixture matching. Key in `.env` as `GUARDIAN_API_KEY`.
- Typed events: **reuse the existing `football_data_org` provider**; add a
  `get_match_events(match_id) -> list[MatchEvent]` method (goals/cards/subs with
  minute). Graceful-degrade to text-only if unavailable.
- Register `guardian` under a new `"commentary"` category in
  `dataflows/interface.py` + `config.py` `_DATA_CATEGORIES` (mirrors how
  `news`/`stats_xg` slots were added). Falls back to a `placeholder` commentary
  provider (offline, reads a bundled WC22 sample) so tests/offline never break.

**B. Chunk** — `dataflows/commentary/chunker.py`, **pure functions, no LLM**:
- Parse minute markers from Guardian prose (e.g. `"63 min"`, `"HT"`, `"90+2"`).
- Merge prose entries with typed events into one sorted timeline.
- `split_phases(timeline) -> list[PhaseChunk]` by the minute bands above.

**C. Analyze** — new `agents/analyst/tactical.py`, following the **judge
pattern** exactly (`make_*(config, llm, usage_acc)` + `with_structured_output(
Schema, include_raw=True)` + token accumulation + graceful degradation):
- `make_tactical_analyzer(config, llm, usage_acc)` → callable over one
  `PhaseChunk` → `PhaseTacticalInsight`.
- Prompt instructs extraction of: **formations & blocks** (low-block, high
  press…), **tactical adjustments** ("winger shifting inside"), **key player
  matchups** named in the text. Provenance rule applies: reason only from the
  provided commentary; cite minute references.

## Schemas (add to `agents/schemas.py`)

```python
class MatchEvent(BaseModel):      # typed event from stats API
    minute: int; type: Literal["goal","card","sub","var","other"]; detail: str = ""
class CommentaryEntry(BaseModel): # one prose beat
    minute: Optional[int]; text: str
class PhaseChunk(BaseModel):
    phase: str                    # one of the 5 labels
    entries: list[CommentaryEntry]; events: list[MatchEvent]
class PhaseTacticalInsight(BaseModel):
    phase: str
    formations_blocks: list[str]; adjustments: list[str]; key_matchups: list[str]
    summary: str                  # 2-3 sentence phase synopsis
class MatchTacticalReport(BaseModel):
    match_id: str; home: str; away: str; date: Optional[str]
    phases: list[PhaseTacticalInsight]; sources: list[str]
```

## Storage

- `memory/matches/<home>_<away>_<date>.json` — full `MatchTacticalReport`
  (machine-readable, the future retrieval substrate for `predictive_brief`).
- `memory/matches/<...>.md` — human-readable mirror (Git-diffable, per the
  project's markdown-memory ethos). No vector index in this slice.

## Orchestration & CLI

Linear batch pipeline (a plain function — LangGraph not needed for a straight
line): `pipelines/analyze_match.py::analyze_match(home, away, date, config)`
runs A→B→C→store and returns the report. New CLI command in `cli.py`:
```
worldcupagents analyze-match "Argentina" "France" --date 2022-12-18
worldcupagents analyze-match ... --no-llm     # chunk-only dry run
```

## Dependencies

None new for the happy path — Guardian via `httpx` (already present), events via
existing provider. (Playwright/BS4 explicitly avoided.)

## Testing (hermetic)

- Bundle one **WC22 sample commentary** fixture in `tests/data/`.
- `chunker` unit tests: minute parsing (`HT`, `90+2`), correct phase bucketing.
- Tactical agent test with **FakeLLM** returning a `PhaseTacticalInsight` (mirror
  `tests/test_llm.py` `_FakeStructured`); assert routing + token accounting +
  graceful degradation on LLM error.
- Guardian client test with **mocked HTTP**; placeholder provider proves offline.
- End-to-end `analyze_match` on the sample → asserts 5 phases + stored files.

## Risks / open issues

1. **Guardian minute parsing** — min-by-min format varies; parser must be
   defensive (entries with no minute fall into the current phase).
2. **Article↔fixture matching** — disambiguate by date + both team names; log &
   skip on no confident match (never guess).
3. **Coverage** — Guardian covers major matches well; minnow group games may
   lack min-by-min. Degrade to events-only timeline.

## Milestones within this slice

1. ✅ Schemas + chunker (pure, fully tested) — no network, no LLM.
2. ✅ Guardian client + placeholder + registry wiring (mocked-HTTP tests),
   **validated against the real WC22 final liveblog.**
3. ✅ Tactical analyzer agent (FakeLLM tests).
4. ✅ `analyze_match` pipeline + CLI (`analyze-match`), offline by default,
   `--provider/--llm` opt-in; persists to `memory/matches/<id>.{json,md}`.

### Validation notes (real Guardian data, WC22 final)
- Guardian serves BOTH a match report and the min-by-min liveblog for a big game,
  and both name the two teams — so `_pick_article` ranks by *(both teams, is
  liveblog, block count)* to grab the ~150-block MBM, not the 1-block report.
- Block **titles** carry minute + event ("GOAL! … (Messi 23 pen)", "HALF TIME"),
  so we extract **typed goal events for free** — the stats-API events become an
  enrichment, not a hard dependency.
- Extra time appears as "ET N min" and is folded into the 90+ Crunch band.
- Pre-kickoff build-up (no minute) is dropped so it can't pollute phases.
- Result: ~79% of lines explicitly minute-tagged, all 6 goals correctly phased.
