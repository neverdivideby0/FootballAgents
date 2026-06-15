# Multi-League Plan — one engine, many competitions

Status: **designed, not built.** Companion to DATA_PLAN.md / COMMENTARY_PLAN.md.
Validated by the EPL spike (stats-λ Brier 0.618 on a real PL season; a live
Arsenal–Liverpool debate ran with ~4 lines of league config and no engine
changes).

## Goal (what the user asked for)

One product that, for **any year's tournament or league**, lets you:
**pick league/tournament → pick teams → pick LLM models → run a critical agent
debate → save it as a .txt** — clear, guided, one command. Plus an
all-encompassing match database, and **World Cup 2026 must keep working exactly
as it does today.**

## Doable? Yes — most of it already exists

| Requirement | Status today |
|---|---|
| Pick provider + models (arrow keys) | ✅ `_guided_select` |
| Pick teams (arrow keys, grouped) | ✅ `_pick_team` / `_guided_fixture` |
| Critical debate (self-critique + judge + critic) | ✅ advocates/judge/critic |
| Save as .txt | ✅ post-verdict Y/N export |
| All-encompassing DB | ✅ one SQLite store, `comp`-tagged (just needs per-league filtering) |
| Pick the league/tournament | ⬜ **new** — the `League` layer below |
| Don't break WC 2026 | ✅ WC becomes the *default* league pack; behaviour unchanged |

So the build is a thin abstraction + a league picker, not a rewrite.

## The League abstraction (the only real new concept)

```python
@dataclass(frozen=True)
class League:
    key: str            # "WC2026" | "PL" | "PD" | "SA" | "BL1" | "FL1"
    name: str           # "FIFA World Cup 2026", "Premier League 2025-26"
    kind: str           # "tournament" | "league"
    season: str         # "2026" | "2025-26"
    fd_competition: str # football-data.org code (WC/PL/PD/SA/BL1/FL1)
    has_knockouts: bool # WC True; leagues False (draws valid, no penalty fold)
    neutral_venue: bool # WC True; leagues False (home advantage from data)
```

- `worldcupagents/leagues/registry.py` — `get_league(key)`, `list_leagues()`.
- Packs: `leagues/world_cup_2026.py` (wraps the existing teams/venues/ranks),
  `leagues/premier_league.py`, `la_liga.py`, … (each ~30 lines).
- Team list per league: tournament packs ship a curated list (WC's 48); league
  packs read the 20 clubs from the store/standings for that competition.
- `apply_league(config, league)` sets `fd_competition`, the memory dir, stage
  rules, and `use_stats_lambda` (on for leagues with match data) — one function,
  so every command becomes league-aware by passing `--league`.

## All-encompassing database + isolation

- **One** SQLite match store (`data/football.db`), already `comp`-tagged → the
  all-encompassing DB. Fix the spike's only blemish: `load_strength_model` and
  `backtest` filter by `comp` so leagues don't cross-contaminate strengths.
- **Per-league memory**: selecting a league points `memory_dir` at
  `memory/<league.key>/` so tactical reports, scouting, critic, team lessons and
  the prediction log are isolated per competition (WC ≠ PL). Minimal change —
  the paths already derive from `config["memory_dir"]`.

## The guided UX (`predict -i`, league-aware)

```
1. League/tournament   ← NEW first step (registry list, grouped tournament/league)
2. Teams               ← existing picker, now sourced from the chosen league
3. Venue               ← tournaments only (leagues skip; home/away implicit)
4. Provider + models   ← existing arrow-key picker
   → critical debate runs (advocates self-critique → judge → ensemble verdict)
5. Save to .txt?  [y/N] ← existing export
```

Non-`-i` usage stays flag-driven: `predict "Arsenal FC" "Liverpool FC" -L PL -p openai`.

## Backward compatibility (WC 2026 stays intact)

- Default league = **WC2026**. `predict "Spain" "Brazil"` and every current
  command/flag behave exactly as today.
- The WC-coupled files (`world_cup_2026.py`, `fifa_rankings.py`, knockout/
  penalty/venue logic in `pundit.py`) move *behind* the abstraction as the WC
  pack — same code, same outputs. All 112 existing tests must stay green.

## File-by-file migration

- New: `leagues/` package (registry + packs), `apply_league()`.
- Touch (league-gate, not rewrite): `config.py` (default league, `--league`),
  `cli.py` (league picker + `-L` on each command), `pundit.py` (knockout/venue
  gated by league flags), `strength.py` + `backtest.py` (`comp` filter),
  `scouts/dossier.py`/pipelines (memory dir from league).
- Rename project → **FootballAgents** (WC + leagues are instances of one engine).

## Milestones

1. **M-ML1 — League core (no behaviour change).** `League` + registry + WC pack;
   `apply_league`; default WC. All commands accept `-L` (defaults WC). 112 tests
   stay green; add league-registry tests.
2. **M-ML2 — Big 5 packs + isolation.** PL/PD/SA/BL1/FL1 packs; comp-filtered
   strengths & backtest; per-league `memory/<key>/`. `fetch-data -L`, `backtest -L`.
3. **M-ML3 — Guided league-first UX.** League picker as step 1 of `-i`;
   league-sourced team picker; venue only for tournaments; txt export.
4. **M-ML4 — Polish.** `leagues` command, README/CLAUDE refresh, rename.

## Testing

- Hermetic: league registry lookups; `apply_league` sets the right config;
  comp-filtered strength fit; per-league memory isolation (two leagues, separate
  dirs); WC pack reproduces today's verdicts (regression guard).

## Out of scope (later)

- Per-player metrics (FBref/Understat) for the player-level Critic Loop.
- Cross-competition (Champions League mixing clubs from many leagues).
- Live in-season scheduling/automation.
