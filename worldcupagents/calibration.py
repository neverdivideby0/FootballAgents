"""Calibration feedback — the adaptive half of the learning loop.

The vertical loop already *recalls* past predictions (resolved Brier + reflection
lessons injected as text). This module makes the system *adapt* to its own track
record in two concrete ways, both deterministic and offline:

  1. ``calibration_note`` — a plain-language correction ("favourites trending
     over-backed; lift draw mass") fed to the Judge + Final Pundit, computed as a
     RECENCY-WEIGHTED moving average over resolved predictions (the latest match
     carries the most weight). Emits from the first resolved match; the strength of
     its language scales with the effective sample so one game nudges, not steers.

  2. ``refit_judge_weight`` / ``effective_judge_weight`` — the blend weight adapts
     from the eval log by recency-weighted grid search, then SHRINKS toward the 0.6
     prior by effective sample size, so a short World Cup (few games) barely moves
     it while a long history leans into the fit. No hard game-count cliff.

Neutral-venue design: WC2026 is played at neutral grounds, so the fixture's
home/away slot is just listing order, NOT a venue edge. We therefore never track a
generic "home skew"; the WC-appropriate signal is FAVOURITE over-backing, with a
separate, gated carve-out only for genuine host-nation games (USA/Canada/Mexico).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

# 2026 co-hosts — the only sides with a real home advantage at a neutral-venue WC.
_HOSTS_2026 = {normalize_key(canonical_name(t)) for t in ("United States", "Canada", "Mexico")}

_PROBS_RE = re.compile(r"p_home=([\d.]+),\s*p_draw=([\d.]+),\s*p_away=([\d.]+)")
_TAG_RE = re.compile(r"\| resolved: (\w+)(?:\s+(\d+-\d+))?\s+Brier=([\d.]+)\]")

_OUTCOME_IDX = {"HOME_WIN": 0, "DRAW": 1, "AWAY_WIN": 2}

# A signal "trips" (gets a correction sentence) once the gap exceeds this.
_GAP = 0.08


# ── Resolved-prediction reader (shared with the explorer's Calibration tab) ──

def resolved_predictions(config: dict) -> list[dict]:
    """Every RESOLVED prediction parsed from the log, oldest→newest (append order).

    Each row: ``{date, fixture, predicted, actual, score, brier, p:[h,d,a]}`` — the
    same shape the data explorer's Calibration tab already consumed.
    """
    from worldcupagents.graph.predict import _ENTRY_SEP

    log = Path(config.get("prediction_log_path", "memory/prediction_log.md"))
    if not log.exists():
        return []
    rows: list[dict] = []
    for e in log.read_text(encoding="utf-8").split(_ENTRY_SEP):
        first = e.strip().splitlines()[0] if e.strip() else ""
        m, pm = _TAG_RE.search(first), _PROBS_RE.search(e)
        if not (m and pm):
            continue
        parts = [p.strip() for p in first.strip("[]").split("|")]
        predicted = parts[2].split()[0] if len(parts) > 2 and parts[2].split() else "?"
        rows.append({
            "date": parts[0] if parts else "?",
            "fixture": parts[1] if len(parts) > 1 else "?",
            "predicted": predicted,
            "actual": m.group(1), "score": m.group(2) or "",
            "brier": float(m.group(3)),
            "p": [float(pm.group(1)), float(pm.group(2)), float(pm.group(3))],
        })
    return rows


def reliability_bins(rows: list[dict]) -> list[dict]:
    """10 forecast-probability bins (predicted % vs realized %), each match
    contributing 3 forecasts (home/draw/away). Used by the explorer."""
    bins = [{"n": 0, "sum_p": 0.0, "hits": 0} for _ in range(10)]
    for r in rows:
        for p, oc in zip(r["p"], ("HOME_WIN", "DRAW", "AWAY_WIN")):
            b = bins[min(int(p * 10), 9)]
            b["n"] += 1
            b["sum_p"] += p
            b["hits"] += 1 if r["actual"] == oc else 0
    return [
        {"range": f"{i*10}–{i*10+10}%", "n": b["n"],
         "forecast": round(b["sum_p"] / b["n"], 3) if b["n"] else None,
         "realized": round(b["hits"] / b["n"], 3) if b["n"] else None}
        for i, b in enumerate(bins)
    ]


# ── Recency-weighted bias signals ──────────────────────────────────────────

def _host_side(fixture: str) -> int | None:
    """Index of the host side in a 'Home vs Away' fixture (0=home, 2=away), or None
    if neither side is a 2026 host."""
    if " vs " not in fixture:
        return None
    home, away = (t.strip() for t in fixture.split(" vs ", 1))
    if normalize_key(canonical_name(home)) in _HOSTS_2026:
        return 0
    if normalize_key(canonical_name(away)) in _HOSTS_2026:
        return 2
    return None


def calibration_summary(config: dict, *, decay: float = 0.85) -> dict:
    """Recency-weighted bias signals over resolved predictions (latest weighted
    most). Returns confidence / favourite / draw skews + a gated host carve-out,
    plus ``n`` and effective sample ``n_eff``. Empty-but-shaped when no history."""
    rows = resolved_predictions(config)
    out: dict = {"n": len(rows), "n_eff": 0.0, "mean_brier": None,
                 "confidence": None, "favourite": None, "draw": None, "host": None}
    if not rows:
        return out

    out["mean_brier"] = round(sum(r["brier"] for r in rows) / len(rows), 3)

    # Newest first → weight decay**i (i=0 is the most recent match).
    weighted = [(decay ** i, r) for i, r in enumerate(reversed(rows))]
    n_eff = sum(w for w, _ in weighted)
    out["n_eff"] = round(n_eff, 2)

    def wmean(values: list[tuple[float, float]]) -> float | None:
        wsum = sum(w for w, _ in values)
        return sum(w * v for w, v in values) / wsum if wsum else None

    # Confidence: prob put on the CHOSEN outcome vs whether it hit.
    chosen_p, chosen_hit = [], []
    # Favourite: the more-likely of home/away (draws excluded) vs whether it won.
    fav_p, fav_hit = [], []
    # Draw: prob on the draw vs whether it was a draw.
    draw_p, draw_real = [], []
    # Host carve-out (gated).
    host_p, host_hit = [], []

    for w, r in weighted:
        p = r["p"]
        idx = _OUTCOME_IDX.get(r["predicted"])
        if idx is None:
            idx = max(range(3), key=lambda k: p[k])
        chosen_p.append((w, p[idx]))
        chosen_hit.append((w, 1.0 if r["predicted"] == r["actual"] else 0.0))

        fav_idx = 0 if p[0] >= p[2] else 2
        fav_outcome = "HOME_WIN" if fav_idx == 0 else "AWAY_WIN"
        fav_p.append((w, p[fav_idx]))
        fav_hit.append((w, 1.0 if r["actual"] == fav_outcome else 0.0))

        draw_p.append((w, p[1]))
        draw_real.append((w, 1.0 if r["actual"] == "DRAW" else 0.0))

        hs = _host_side(r["fixture"])
        if hs is not None:
            host_outcome = "HOME_WIN" if hs == 0 else "AWAY_WIN"
            host_p.append((w, p[hs]))
            host_hit.append((w, 1.0 if r["actual"] == host_outcome else 0.0))

    out["confidence"] = {"forecast": wmean(chosen_p), "realized": wmean(chosen_hit),
                         "gap": _gap(wmean(chosen_p), wmean(chosen_hit))}
    out["favourite"] = {"forecast": wmean(fav_p), "realized": wmean(fav_hit),
                        "gap": _gap(wmean(fav_p), wmean(fav_hit))}
    out["draw"] = {"forecast": wmean(draw_p), "realized": wmean(draw_real),
                   "gap": _gap(wmean(draw_p), wmean(draw_real))}
    # Host carve-out only once it has its own minimum sample (raw count, not the
    # EWMA effective size — which saturates at ~1/(1-decay)).
    if len(host_p) >= 3:
        out["host"] = {"forecast": wmean(host_p), "realized": wmean(host_hit),
                       "gap": _gap(wmean(host_p), wmean(host_hit)), "n": len(host_p)}
    return out


def _gap(forecast: float | None, realized: float | None) -> float | None:
    if forecast is None or realized is None:
        return None
    return forecast - realized


# ── Plain-language correction injected into the Judge / Final Pundit ─────────

def _strength(n_eff: float) -> str:
    if n_eff < 3:
        return "tentatively"
    if n_eff < 8:
        return "trending"
    return "consistently"


def calibration_note(config: dict, *, decay: float = 0.85) -> str:
    """A 1–3 sentence, neutral-venue correction from the system's own track record,
    recency-weighted. ``""`` only when there is no resolved history."""
    s = calibration_summary(config, decay=decay)
    if not s["n"]:
        return ""
    # Recency weighting decides the bias DIRECTION; the raw count decides how
    # strongly we phrase it (the EWMA effective size saturates and never grows).
    adv = _strength(s["n"])
    fixes: list[str] = []

    fav = s["favourite"]
    if fav and fav["gap"] is not None and fav["gap"] > _GAP:
        fixes.append(
            f"favourites {adv} over-backed (forecast {fav['forecast']*100:.0f}% vs "
            f"realized {fav['realized']*100:.0f}% wins) — shade toward the field in close games")
    elif fav and fav["gap"] is not None and fav["gap"] < -_GAP:
        fixes.append(
            f"favourites {adv} under-backed (forecast {fav['forecast']*100:.0f}% vs "
            f"realized {fav['realized']*100:.0f}% wins) — trust a clear edge more")

    conf = s["confidence"]
    if conf and conf["gap"] is not None and conf["gap"] > _GAP:
        fixes.append(f"calls {adv} overconfident — flatten probabilities toward the field")
    elif conf and conf["gap"] is not None and conf["gap"] < -_GAP:
        fixes.append(f"calls {adv} underconfident — back the read more decisively")

    draw = s["draw"]
    if draw and draw["gap"] is not None and draw["gap"] < -_GAP:
        fixes.append("draws under-forecast — lift draw mass where the matchup is tight")
    elif draw and draw["gap"] is not None and draw["gap"] > _GAP:
        fixes.append("draws over-forecast — commit to a side where one is favoured")

    head = f"Calibration check (n={s['n']}, recent matches weighted most"
    head += f"; mean Brier {s['mean_brier']})" if s["mean_brier"] is not None else ")"
    if fixes:
        body = "; ".join(fixes)
        note = f"{head}: {body}. These are neutral-venue corrections — apply them to your probabilities."
    else:
        note = f"{head}: no systematic skew detected so far — keep your probabilities as the evidence dictates."

    host = s["host"]
    if host and host["gap"] is not None and abs(host["gap"]) > _GAP:
        direction = "over-rated" if host["gap"] > 0 else "under-rated"
        note += (f" Host-nation note (n={host['n']}): the genuine host edge has been "
                 f"{direction} (forecast {host['forecast']*100:.0f}% vs realized {host['realized']*100:.0f}%).")
    return note


# ── Adaptive judge_weight (recency-weighted fit + shrinkage to prior) ────────

def fitted_weights_path(config: dict) -> Path:
    return Path(config.get("data_dir", "data")) / "fitted_weights.json"


def effective_judge_weight(config: dict) -> float:
    """The blend weight to actually use: the fitted (already-shrunk) value when one
    has been persisted, else the configured prior (0.6 default). Honors
    ``use_fitted_judge_weight`` (default True) so callers can pin the prior."""
    prior = float(config.get("ensemble_judge_weight", 0.6))
    if not config.get("use_fitted_judge_weight", True):
        return prior
    path = fitted_weights_path(config)
    if not path.exists():
        return prior
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["weight"])
    except Exception as e:  # noqa: BLE001 — a bad/partial file must not break predict
        logger.warning("calibration: could not read %s (%s); using prior %.2f", path, e, prior)
        return prior


def refit_judge_weight(config: dict, *, decay: float = 0.85, k: float = 20.0) -> dict | None:
    """Recency-weighted grid-search of the blend weight over the eval log, shrunk
    toward the configured prior by effective sample size. Persists + returns
    ``{weight, w_fit, n_eff, fitted_at}``; ``None`` when there are no usable reads.

    ``final = (n·w_fit + k·prior) / (n + k)`` — with few games ``final`` barely
    leaves the prior; as reads accumulate it leans into the fit. Recency decides
    the fit DIRECTION (``w_fit``); the raw count ``n`` decides how far we move
    (the EWMA effective size saturates at ~1/(1-decay), so it can't drive shrinkage).
    """
    from worldcupagents.ensemble.baseline import blend
    from worldcupagents.graph.reflection import brier_score, outcome_from_score
    from worldcupagents.pipelines.evaluate import dedupe_records, load_eval_log

    usable = [r for r in dedupe_records(load_eval_log(config))
              if r.get("llm") and r.get("judge") and r.get("base")]
    if not usable:
        return None

    # Order by timestamp (fallback: log order) so newest gets the highest weight.
    usable.sort(key=lambda r: r.get("ts") or r.get("date") or "")
    weighted = [(decay ** i, r) for i, r in enumerate(reversed(usable))]
    n_eff = sum(w for w, _ in weighted)
    n = len(usable)

    best_w, best_score = 0.0, float("inf")
    w = 0.0
    while w <= 1.0 + 1e-9:
        total = wsum = 0.0
        for wt, rec in weighted:
            p = blend(tuple(rec["judge"]), tuple(rec["base"]), w)
            total += wt * brier_score(p[0], p[1], p[2], outcome_from_score(rec["hg"], rec["ag"]))
            wsum += wt
        mean = total / wsum if wsum else float("inf")
        if mean < best_score:
            best_score, best_w = mean, round(w, 2)
        w += 0.05

    prior = float(config.get("ensemble_judge_weight", 0.6))
    final = (n * best_w + k * prior) / (n + k)
    out = {"weight": round(final, 3), "w_fit": best_w, "n": n, "n_eff": round(n_eff, 2),
           "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = fitted_weights_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
