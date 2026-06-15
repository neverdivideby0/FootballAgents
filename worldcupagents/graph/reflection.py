"""Post-match scoring + reflection (the learning loop, roadmap M3).

When a predicted match is played, ``resolve_prediction`` finds the matching
``pending`` entry in ``prediction_log.md``, scores it with the multiclass Brier
score, rewrites the entry to ``resolved`` with the result + score, and appends a
one-line lesson to each team's dossier (``memory/teams/<TEAM>.md``).

Brier score (lower = better): sum over {home, draw, away} of (p - outcome)².
  * confident & correct  -> 0.0
  * confident & wrong    -> 2.0
  * uniform 1/3 guess    -> ~0.667  (so < 0.667 beats a coin-flip)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.schemas import Outcome
from worldcupagents.dataflows.names import canonical_name, normalize_key
from worldcupagents.graph.predict import _ENTRY_SEP  # keep the log format in one place

logger = logging.getLogger(__name__)

_PROBS_RE = re.compile(r"p_home=([\d.]+),\s*p_draw=([\d.]+),\s*p_away=([\d.]+)")
_ONE_HOT = {
    Outcome.HOME_WIN: (1.0, 0.0, 0.0),
    Outcome.DRAW: (0.0, 1.0, 0.0),
    Outcome.AWAY_WIN: (0.0, 0.0, 1.0),
}


def brier_score(p_home: float, p_draw: float, p_away: float, actual: Outcome) -> float:
    oh, od, oa = _ONE_HOT[actual]
    return (p_home - oh) ** 2 + (p_draw - od) ** 2 + (p_away - oa) ** 2


def outcome_from_score(home_goals: int, away_goals: int) -> Outcome:
    if home_goals > away_goals:
        return Outcome.HOME_WIN
    if home_goals < away_goals:
        return Outcome.AWAY_WIN
    return Outcome.DRAW


def quality_label(brier: float) -> str:
    if brier < 0.30:
        return "strong"
    if brier < 0.50:
        return "decent"
    if brier < 0.667:
        return "beats coin-flip"
    return "poor"


def resolve_prediction(
    home: str,
    away: str,
    actual: Outcome,
    config: dict,
    actual_scoreline: str | None = None,
    reflect_llm=None,
) -> dict:
    """Score the latest pending prediction for home-vs-away and mark it resolved.

    ``reflect_llm`` (optional): an LLM used to write a 2–4 sentence reflection —
    TradingAgents' Reflector pattern. The reflection is stored in the log entry's
    REFLECTION block and read back into future Judge/Final Pundit prompts via
    ``recall.prediction_lessons``. LLM errors degrade to no reflection.

    Returns {"found": bool, "brier": float|None, "predicted": str|None,
    "actual": str, "reflection": str|None}. Never raises on a missing log.
    """
    log_path = Path(config.get("prediction_log_path", "memory/prediction_log.md"))
    miss = {"found": False, "brier": None, "predicted": None, "actual": actual.value, "reflection": None}
    if not log_path.exists():
        logger.warning("reflection: no prediction log at %s", log_path)
        return miss

    text = log_path.read_text(encoding="utf-8")
    entries = text.split(_ENTRY_SEP)
    needle = f"{home} vs {away}".lower()

    target = None
    for i, e in enumerate(entries):
        first = e.strip().splitlines()[0] if e.strip() else ""
        if needle in first.lower() and "| pending]" in first:
            target = i  # keep the most recent matching pending entry
    if target is None:
        logger.warning("reflection: no pending entry for %s vs %s", home, away)
        return miss

    entry = entries[target]
    m = _PROBS_RE.search(entry)
    if not m:
        logger.warning("reflection: could not parse probabilities for %s vs %s", home, away)
        return miss

    p_home, p_draw, p_away = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    brier = brier_score(p_home, p_draw, p_away, actual)
    predicted = _predicted_outcome(entry)

    reflection = _llm_reflection(
        reflect_llm, entry, home, away, predicted, actual, actual_scoreline, brier
    )

    score_str = f" {actual_scoreline}" if actual_scoreline else ""
    resolved_entry = entry.replace(
        "| pending]", f"| resolved: {actual.value}{score_str} Brier={brier:.3f}]", 1
    )
    resolved_entry += (
        f"\nRESULT: {actual.value}{score_str}. "
        f"Predicted {predicted or '?'} → Brier={brier:.3f} ({quality_label(brier)})."
    )
    if reflection:
        resolved_entry += f"\nREFLECTION: {reflection}"
    entries[target] = resolved_entry
    log_path.write_text(_ENTRY_SEP.join(entries), encoding="utf-8")

    _append_dossier_lessons(home, away, predicted, actual, brier, actual_scoreline, config, reflection)

    return {"found": True, "brier": brier, "predicted": predicted,
            "actual": actual.value, "reflection": reflection}


def _llm_reflection(llm, entry, home, away, predicted, actual, scoreline, brier) -> str | None:
    """TA Reflector analog: one quick-LLM call → 2–4 plain sentences, or None."""
    if llm is None:
        return None
    try:
        prompt = f"""A football prediction has been resolved. Reflect on it in 2–4 plain sentences
(no markdown): was the directional call right, which part of the reasoning held or failed,
and ONE concrete lesson to apply to future predictions involving these teams.

PREDICTION ENTRY:
{entry.strip()}

ACTUAL RESULT: {actual.value}{f' {scoreline}' if scoreline else ''} (we predicted {predicted or '?'};
Brier {brier:.3f}, lower is better, 0.667 = coin-flip)."""
        msg = llm.invoke(prompt)
        text = (getattr(msg, "content", "") or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001 — reflection is best-effort
        logger.warning("reflection LLM error (%s); resolving without reflection", e)
        return None


def sync_pending(config: dict, reflect_llm=None) -> list[dict]:
    """Auto-resolve every pending prediction whose result already sits in the
    match store — the ever-learning loop without manual `resolve` calls.

    For each pending entry, finds the EARLIEST store match between the same two
    teams on/after the prediction date (the fixture that was being predicted)
    and resolves it with that score. Returns one summary dict per resolution.
    """
    log_path = Path(config.get("prediction_log_path", "memory/prediction_log.md"))
    if not log_path.exists():
        return []
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        matches = store.all_matches()
    finally:
        store.close()

    def key(name: str) -> str:
        return normalize_key(canonical_name(name))

    resolved = []
    for tag in _pending_tags(log_path):
        cands = [m for m in matches
                 if m.get("date") and m["date"] >= tag["date"]
                 and key(m["home"]) == key(tag["home"]) and key(m["away"]) == key(tag["away"])]
        if not cands:
            continue
        m = min(cands, key=lambda x: x["date"])
        actual = outcome_from_score(m["hg"], m["ag"])
        res = resolve_prediction(tag["home"], tag["away"], actual, config,
                                 actual_scoreline=f"{m['hg']}-{m['ag']}", reflect_llm=reflect_llm)
        if res["found"]:
            resolved.append({**res, "home": tag["home"], "away": tag["away"],
                             "match_date": m["date"]})
    return resolved


def _pending_tags(log_path: Path) -> list[dict]:
    """Parse pending entry tags '[date | A vs B | OUTCOME SCORE | pending]'."""
    text = log_path.read_text(encoding="utf-8")
    out = []
    for e in text.split(_ENTRY_SEP):
        first = e.strip().splitlines()[0] if e.strip() else ""
        if "| pending]" not in first:
            continue
        parts = [p.strip() for p in first.strip("[]").split("|")]
        if len(parts) >= 2 and " vs " in parts[1]:
            home, away = parts[1].split(" vs ", 1)
            out.append({"date": parts[0], "home": home.strip(), "away": away.strip()})
    return out


# ── internals ────────────────────────────────────────────────────────────────

def _predicted_outcome(entry: str) -> str | None:
    """Pull the predicted OUTCOME from the tag '[date | A vs B | OUTCOME SCORE | …]'."""
    first = entry.strip().splitlines()[0]
    parts = [p.strip() for p in first.strip("[]").split("|")]
    if len(parts) >= 3:
        return parts[2].split()[0] if parts[2].split() else None
    return None


def _append_dossier_lessons(home, away, predicted, actual, brier, score_str, config,
                            reflection: str | None = None) -> None:
    teams_dir = Path(config.get("memory_dir", "memory")) / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    line = (
        f"- {today}: {home} vs {away} → {actual.value}{(' ' + score_str) if score_str else ''} "
        f"(we predicted {predicted or '?'}, Brier {brier:.3f})"
        f"{(' — ' + reflection) if reflection else ''}\n"
    )
    for team in (home, away):
        f = teams_dir / f"{normalize_key(canonical_name(team))}.md"
        if not f.exists():
            f.write_text(f"# {canonical_name(team)} — prediction lessons\n\n", encoding="utf-8")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(line)
