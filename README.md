# FootballAgents ⚽🤖

Predict football matches with a **team-advocate debate** and a grounded **judge**.
Two AI advocates argue each side (and name their own weaknesses), a judge weighs the
debate, and the verdict's probabilities are anchored to a statistical goals model —
not a raw "the LLM said 70%". Works for the **FIFA World Cup 2026** and the **big-5
European leagues** (Premier League, La Liga, Serie A, Bundesliga, Ligue 1).

Inspired by [TradingAgents](../TradingAgents).

---

## 1. Install

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
```

It runs **offline with no API keys** (a statistical baseline). Add keys to unlock the
LLM debate and live data — see step 4.

Run the command as `footballagents` (or the older alias `worldcupagents`):

```bash
uv run footballagents --help
```

---

## 2. The easy way — just run it

```bash
uv run footballagents
```

With no command, it opens a **guided menu** — arrow-key your way to predict, dossier,
odds, watch, refresh, resolve, credit, or the data explorer. Each choice asks only for
what it needs (and the LLM steps let you pick the provider + model, e.g. `gpt-5.4-mini`).

Or jump straight to a prediction:

```bash
uv run footballagents predict -i
```

Arrow keys walk you through everything, then it can save the result to a text file:

```
1. Competition   →  World Cup / Premier League / La Liga / Serie A / Bundesliga / Ligue 1
2. Teams         →  pick home & away (from that competition)
3. Venue         →  tournaments only (leagues are home/away automatically)
4. LLM provider + models   (or skip for the offline baseline)
   → the debate runs →
5. Export to .txt?  [y/N]   →  saved to exports/
```

That's the whole app in one command. Everything below is the same thing with flags.

---

## 3. Common commands

```bash
# list the competitions you can predict
uv run footballagents predict --help
uv run footballagents leagues

# a World Cup match (default competition)
uv run footballagents predict "Spain" "Brazil" --stage group

# a Premier League match  (-L PL)
uv run footballagents predict "Arsenal FC" "Liverpool FC" -L PL

# with a real LLM debate (pick a provider you have a key for)
uv run footballagents predict "Arsenal FC" "Liverpool FC" -L PL --provider openai
```

Other tools (all default to offline; add `--provider` to use an LLM):

| Command | What it does |
|---|---|
| `predict` | Debate + verdict for one fixture (with an upset watch + live market) |
| `dossier` | **The pre-match brief** — line-up, squad player stats, recent games *with stats*, style of play, weaknesses, scouting notes, market. No LLM. |
| `odds` | Live de-vigged bookmaker consensus + Polymarket crowd for a fixture |
| `simulate-tournament` | 10k Monte-Carlo runs of WC2026 → each team's advancement odds |
| `evaluate` | Does the LLM debate beat the baseline/market? (Brier scoreboard) |
| `credit` | Which signals actually helped? Simple with-vs-without Brier per signal (punditry, market, …) |
| `refresh` | One matchday command (~15s): pull results, auto-resolve, re-simulate, rebuild the explorer |
| `watch` | Matchday autopilot: poll for finished matches → distil punditry + tactics → auto-resolve. `--interval N` to loop |
| `analyze-match` | Turn a match's text commentary into a 5-phase tactical report |
| `scout-report` | A team's stats + tactical history → a scouting report |
| `players` | A team's leading players (goals/assists/xG) from the stats store |
| `explore` | **Data Explorer** — one HTML page: guide, every data source, the store tables, calibration, tournament sim, a "data gaps" panel |
| `critic` | Cross-examine a team's numbers against the commentary |
| `resolve` | Score a played prediction (Brier); `--sync` auto-resolves from the store |
| `fetch-data` | Download results + odds + per-match stats + (`--xg`) Understat metrics |
| `hoard-data` | Snapshot public datasets (international results, StatsBomb, Wikipedia totals) |
| `guardian-guide` / `bbc-guide` / `guardian-experts` | Ingest the Guardian player guide / BBC team guide / Guardian Experts' Network previews (player bios, team profiles, **coach style & pedigree**) |
| `qual-data` / `note-player` | Add your own article/team/player style notes |
| `backtest` | Measure prediction accuracy vs naive + market baselines |
| `leagues` / `check` / `resolve-name` / `eliminate` | List competitions / data-source status / name resolution / mark teams out |

---

## 4. Optional: API keys (in `.env`)

```bash
cp .env.example .env
```

| Key | Unlocks | Free? |
|---|---|---|
| `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`) | the LLM debate (`--provider openai` etc.) | paid, ~$0.02/match |
| `FOOTBALL_DATA_ORG_TOKEN` | live squads + current-season results | free tier |
| `ODDS_API_KEY` | live de-vigged bookmaker odds shown to the judge (`odds`, `dossier`, `predict`) | free tier (~500/mo) |
| `API_FOOTBALL_KEY` | recent national-team form (free tier capped to 2022–24 seasons) | free tier |
| `GUARDIAN_API_KEY` | match commentary for `analyze-match` | free tier |

No keys → it still runs, using the statistical baseline and bundled sample data.
A missing key never crashes anything — it just falls back.

For World Cup teams, seed recent senior-national form into the existing
`matches` table with:

```bash
uv run footballagents fetch-data --national-history --national-limit 5
```

For the broader public-data warehouse, snapshot and normalize the CC0 men's
international-results dataset:

```bash
uv run footballagents hoard-data --source international-results --populate-summary
```

---

## 5. Better league predictions (more data = sharper)

The stats model gets stronger with more match history. Seed several seasons (free,
no key) and predictions improve:

```bash
# pull seasons of results + odds + per-match stats (shots, SoT, corners, fouls, cards)
uv run footballagents fetch-data -L PL --seasons 2122,2223,2324,2425,2526

# add Understat xG, set-piece profiles, probable XIs and per-player metrics
uv run footballagents fetch-data -L PL --xg --season 2025-26

# the fitted-strength goals model is ON by default (validated in the backtest);
# prove it helps vs naive + market baselines:
uv run footballagents backtest --from-store -L PL
```

With this data the debate also picks up **home & head-to-head records**, **tempo &
discipline** (shots/SoT/corners/fouls/cards per game), **set-piece** profiles, and
each team's **attack-vs-defence forte** — all sourced.

Swap `PL` for `PD` / `SA` / `BL1` / `FL1` for the other big-5 leagues. Fetching all
big-5 also gives **national-team squad players their club form** in WC dossiers.

**Seasons** — predict within any season (`-i` asks, or pass `--season`):

```bash
# historical: that season's squad (from Wikipedia) + all data cut off at season end
uv run footballagents predict "Arsenal FC" "Chelsea FC" -L PL --season 2023-24
```

**Provenance** — analysts cite every result with its date + source tag (and the
Guardian/Wikipedia URLs are clickable in exports), so you can verify the agents
aren't making things up.

---

## 6. The pre-match dossier & adding your own knowledge

`dossier` is the scout's brief — the exact data the agents see, with no LLM:

```bash
uv run footballagents dossier "Argentina" "France"
uv run footballagents dossier "Arsenal FC" "Liverpool FC" -L PL --season 2025-26
```

It shows line-up, squad player stats (club form for national teams), **recent games
with per-match stats** (shots/SoT/corners/fouls/cards/xG), attack-vs-defence forte,
set pieces, playing style, the **head coach's style & pedigree**, **data-backed
weaknesses**, your scouting notes, the live market, and head-to-head. The same
dossier is embedded as §0 of every exported report.

**Free qualitative sources — one command each** (everything flows into the debate):

```bash
uv run footballagents guardian-guide      # Guardian WC2026 guide: ~1,250 player bios + 48 team briefs
uv run footballagents bbc-guide           # BBC WC2026 guide: full team profiles, ranking, pedigree
uv run footballagents guardian-experts    # Guardian Experts' Network: all-48 long-form previews + coach style & pedigree
uv run footballagents qual-data --url "<article>" --team "Arsenal FC"   # any public tactics article
uv run footballagents note-player "Bukayo Saka" -t "Arsenal FC" --note "Inverted winger…"
```

Or use the **Player Notes** and **Manual Analysis** tabs in the data explorer to type
notes and copy the command. ⚠️ Pass `--team` so a note links and surfaces.

> Honest data gap: per-match **passing accuracy / possession** isn't in any free
> source (FBref/Sofascore/WhoScored are bot-blocked; API-Football free is capped to
> 2022–24). Add it as a note, or use a paid API-Football/Opta tier.

---

## 7. How it works (in one picture)

```
teams → Scouts build the dossier (form · stats · set-pieces · XI · style · weaknesses)
      → Analyst reports (form · tactical · players)
      → Home Advocate ⇄ Away Advocate            (each must list its own weaknesses)
      → Judge → provisional verdict              (blended with a Poisson goals model;
                                                  shown the live market to argue against)
      → Upside ⇄ Downside ⇄ Neutral pundits      (risk debate over the provisional call)
      → Final Pundit → FINAL verdict             (re-blended — probabilities stay anchored)
                       + ⚠️ Upset watch          (the live alternative outcome, always shown)
```

- A **live display** shows each agent working (progress, messages, tokens, cost).
- `--depth shallow|medium|deep` trades cost for rigor (shallow skips the risk debate).
- **Anchored, never raw**: every probability blends the LLM read with a Poisson goals
  model; the verdict shows the breakdown and how it differs from the bookmaker market.
- **Honest counterweight**: a favourite call always ships with the most-likely *upset*
  and the data-backed reasons it could happen — you're never just told "favourite wins".
- **The system learns**: `resolve --sync` grades played predictions from the store
  (with an optional LLM reflection); future predictions for those teams read the lessons.
  It also *adapts*: a recency-weighted **calibration note** (e.g. "favourites trending
  over-backed") is fed to the judge, and the blend weight re-fits from the eval log.
- **Punditry is structured, not pasted**: match reports & tactical columns are distilled
  into per-team signals (shape, key-player verdicts, fatigue) before they reach the debate.
- **Calibration**: `evaluate` and the explorer's Calibration tab measure whether the
  debate actually beats the baseline and the market (lower Brier = better).
- **Memory**: analysed matches, scouting, critiques, prediction lessons, and your own
  notes live under `memory/`; the match/warehouse database lives in `data/`.

---

## 8. Tests

```bash
uv run pytest -q        # 370 hermetic tests, no keys/network needed
```

## More

- Roadmap & open items: [TODO.md](TODO.md)
- Design docs: [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md),
  [DATA_PLAN.md](DATA_PLAN.md), [COMMENTARY_PLAN.md](COMMENTARY_PLAN.md),
  [MULTILEAGUE_PLAN.md](MULTILEAGUE_PLAN.md)
- Conventions for contributors: [CLAUDE.md](CLAUDE.md)
