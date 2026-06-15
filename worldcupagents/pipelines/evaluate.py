"""LLM-lift evaluation (roadmap Phase A) — does the agent debate earn its cost?

The backtest harness scores no-LLM models only, so until now the entire agentic
layer (advocates → judge → scenario pundits → final pundit) was unvalidated.
This module runs the REAL predict graph over recent store matches with known
results and scores, side by side:

  * baseline          — the Poisson anchor alone (what you'd get with no LLM)
  * llm-judge(raw)    — the judge's unblended read (is the LLM itself calibrated?)
  * llm-blend(final)  — the shipped output (blend of the two, judge_weight)
  * market            — de-vigged odds on the same rows, when stored

Every run appends to ``data/eval_log.jsonl`` so reads accumulate across sessions;
``fit_judge_weight`` then grid-searches the blend weight over ALL recorded reads
with zero extra LLM spend — replacing the hand-tuned 0.6 with a fitted value.

Leakage caveat (PROJECT_OUTLINE §11): the LLM may have seen past results in
training. Evaluate on the most recent matches available and read the *relative*
gaps; only post-cutoff fixtures are fully clean.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.schemas import Fixture, Stage
from worldcupagents.ensemble.baseline import blend
from worldcupagents.graph.reflection import brier_score, outcome_from_score
from worldcupagents.pipelines.backtest import ModelScore, devig_odds, _argmax_outcome

logger = logging.getLogger(__name__)

Probs = tuple[float, float, float]


def eval_log_path(config: dict) -> Path:
    return Path(config.get("data_dir", "data")) / "eval_log.jsonl"


def evaluated_keys(config: dict, provider: str) -> set[tuple]:
    """(date, home, away) of fixtures already evaluated with this provider —
    so a bigger --last on a later run only spends on NEW fixtures."""
    return {(r.get("date"), r.get("home"), r.get("away"))
            for r in load_eval_log(config)
            if r.get("llm") and r.get("provider") == provider}


def pick_rows(config: dict, comp: str | None = None, last_n: int = 20,
              exclude: set[tuple] | None = None) -> list[dict]:
    """The most recent ``last_n`` finished store matches for the competition
    (season-windowed when config['season'] is set) — newest minimizes the chance
    the LLM saw the result in training. ``exclude`` drops already-evaluated
    fixtures (keys from ``evaluated_keys``)."""
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        rows = store.all_matches()
    finally:
        store.close()
    comp = comp or config.get("fd_competition")
    rows = [r for r in rows if (comp is None or r.get("comp") == comp) and r.get("date")]
    season = config.get("season")
    if season:
        from worldcupagents.seasons import season_range
        lo, hi = season_range(season)
        rows = [r for r in rows if lo <= r["date"] <= hi]
    if exclude:
        rows = [r for r in rows if (r["date"], r["home"], r["away"]) not in exclude]
    rows.sort(key=lambda r: r["date"])
    return rows[-last_n:]


def run_eval(config: dict, rows: list[dict], on_progress=None) -> list[dict]:
    """Run the real predict graph over ``rows`` (known results), returning one
    record per match and appending each to the eval log as it lands (crash-safe:
    LLM spend is never lost). The predictor does NOT persist pending entries."""
    from worldcupagents.graph.predict import Predictor

    predictor = Predictor(config)
    log = eval_log_path(config)
    log.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    for i, r in enumerate(rows):
        fx = Fixture(home=r["home"], away=r["away"], stage=Stage.GROUP)
        try:
            final, verdict = predictor.predict(fx, persist=False)
        except Exception as e:  # noqa: BLE001 — one bad fixture must not sink the run
            logger.warning("eval: predict failed for %s vs %s (%s)", r["home"], r["away"], e)
            continue
        b = verdict.breakdown
        # Honesty check: use_llm set but zero output tokens means every LLM call
        # failed and the "judge read" is just the shrunk baseline placeholder —
        # record llm=False so it can't pollute the judge statistics.
        llm_ran = bool(config.get("use_llm")) and predictor.last_usage.get("output", 0) > 0
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date": r.get("date"), "comp": r.get("comp"),
            "home": r["home"], "away": r["away"],
            "hg": r["hg"], "ag": r["ag"],
            "blend": [verdict.p_home, verdict.p_draw, verdict.p_away],
            "judge": [b.judge_home, b.judge_draw, b.judge_away] if b else None,
            "base": [b.base_home, b.base_draw, b.base_away] if b else None,
            "judge_weight": b.judge_weight if b else None,
            "llm": llm_ran,
            "provider": config.get("llm_provider") if llm_ran else None,
            "odds": [r["odds_h"], r["odds_d"], r["odds_a"]]
                    if r.get("odds_h") and r.get("odds_d") and r.get("odds_a") else None,
        }
        records.append(rec)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        if on_progress is not None:
            on_progress(i + 1, len(rows), rec)
    return records


def load_eval_log(config: dict) -> list[dict]:
    log = eval_log_path(config)
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def dedupe_records(records: list[dict]) -> list[dict]:
    """Keep only the LATEST record per (date, fixture, provider) — re-running an
    eval over the same matches must not double-count them."""
    latest: dict[tuple, dict] = {}
    for rec in records:  # log is append-only, so later lines win
        latest[(rec.get("date"), rec.get("home"), rec.get("away"), rec.get("provider"))] = rec
    return list(latest.values())


def score_records(records: list[dict]) -> dict[str, ModelScore]:
    """Brier + hit-rate per model over the recorded evals (deduped). LLM models are
    scored only on records that actually ran with an LLM; market only where odds
    exist — so n varies per model (shown in the table)."""
    records = dedupe_records(records)
    scores = {
        "baseline(no LLM)": ModelScore("baseline(no LLM)"),
        "llm-judge(raw)": ModelScore("llm-judge(raw)"),
        "llm-blend(final)": ModelScore("llm-blend(final)"),
        "market(de-vigged odds)": ModelScore("market(de-vigged odds)"),
    }

    def tally(name: str, p: Probs, actual) -> None:
        s = scores[name]
        s.n += 1
        s.brier_sum += brier_score(p[0], p[1], p[2], actual)
        if _argmax_outcome(*p) == actual:
            s.hits += 1

    for rec in records:
        actual = outcome_from_score(rec["hg"], rec["ag"])
        if rec.get("base"):
            tally("baseline(no LLM)", tuple(rec["base"]), actual)
        if rec.get("llm") and rec.get("judge"):
            tally("llm-judge(raw)", tuple(rec["judge"]), actual)
        if rec.get("llm") and rec.get("blend"):
            tally("llm-blend(final)", tuple(rec["blend"]), actual)
        if rec.get("odds"):
            tally("market(de-vigged odds)", devig_odds(*rec["odds"]), actual)

    return {k: v for k, v in scores.items() if v.n}


def fit_judge_weight(records: list[dict], step: float = 0.05) -> tuple[float, list[tuple[float, float]]]:
    """Grid-search the blend weight over all recorded LLM judge reads (no LLM
    spend — reads are already logged). Returns (best_weight, [(w, mean_brier)…]).
    w=0 is pure baseline, w=1 is pure judge."""
    usable = [r for r in dedupe_records(records)
              if r.get("llm") and r.get("judge") and r.get("base")]
    if not usable:
        return 0.0, []
    curve: list[tuple[float, float]] = []
    w = 0.0
    while w <= 1.0 + 1e-9:
        total = 0.0
        for rec in usable:
            p = blend(tuple(rec["judge"]), tuple(rec["base"]), w)
            total += brier_score(p[0], p[1], p[2], outcome_from_score(rec["hg"], rec["ag"]))
        curve.append((round(w, 2), total / len(usable)))
        w += step
    best_w = min(curve, key=lambda x: x[1])[0]
    return best_w, curve
