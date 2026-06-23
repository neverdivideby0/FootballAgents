"""Per-signal credit — "which signals actually helped?", kept deliberately simple.

Every shipped prediction records a ``SIGNALS:`` line (the extra inputs it carried
beyond the statistical baseline — punditry, market, tactical history, lessons,
qualitative notes, the calibration note). Once predictions resolve with a Brier
score, ``signal_credit`` asks the simplest honest question for each signal:

    predictions that HAD this signal averaged Brier X (n=a)
    predictions that did NOT averaged Brier Y (n=b)
    → Δ = X − Y   (negative = the signal's group scored better)

This is an **association, not proof**: signals cluster on bigger games, and we are
not controlling for that. It's a readable scoreboard to watch as the tournament
fills in — gated so it stays quiet until there's enough resolved history.
"""

from __future__ import annotations

import re
from pathlib import Path

# The full signal vocabulary (so a signal that's always/never present still shows up).
SIGNALS = ("punditry", "tactical", "lessons", "qualitative", "market", "calibration")

_TAG_BRIER = re.compile(r"\| resolved:.*?Brier=([\d.]+)\]")
_MIN_PER_GROUP = 3  # below this, a with/without split is noise — report "insufficient"


def _resolved_with_signals(config: dict) -> list[tuple[float, set[str]]]:
    """(brier, signals) for every resolved prediction that carries a SIGNALS line.

    Predictions made before SIGNALS tagging shipped simply have no line and are
    skipped — the scoreboard is forward-looking by construction."""
    from worldcupagents.graph.predict import _ENTRY_SEP

    log = Path(config.get("prediction_log_path", "memory/prediction_log.md"))
    if not log.exists():
        return []
    out: list[tuple[float, set[str]]] = []
    for entry in log.read_text(encoding="utf-8").split(_ENTRY_SEP):
        first = entry.strip().splitlines()[0] if entry.strip() else ""
        m = _TAG_BRIER.search(first)
        sig_line = next((ln for ln in entry.splitlines() if ln.startswith("SIGNALS:")), None)
        if not m or sig_line is None:
            continue
        raw = sig_line[len("SIGNALS:"):].strip()
        signals = set() if raw in ("", "(none)") else {s.strip() for s in raw.split(",") if s.strip()}
        out.append((float(m.group(1)), signals))
    return out


def signal_credit(config: dict) -> dict:
    """Per-signal with/without mean Brier. Returns ``{"n": total, "signals": {name:
    {with_brier, with_n, without_brier, without_n, delta} | None}}`` (None = not
    enough data on one side for that signal)."""
    rows = _resolved_with_signals(config)
    out: dict = {"n": len(rows), "signals": {}}

    def mean(vals: list[float]) -> float:
        return sum(vals) / len(vals)

    for sig in SIGNALS:
        have = [b for b, s in rows if sig in s]
        lack = [b for b, s in rows if sig not in s]
        if len(have) < _MIN_PER_GROUP or len(lack) < _MIN_PER_GROUP:
            out["signals"][sig] = None
            continue
        wb, ob = mean(have), mean(lack)
        out["signals"][sig] = {
            "with_brier": round(wb, 3), "with_n": len(have),
            "without_brier": round(ob, 3), "without_n": len(lack),
            "delta": round(wb - ob, 3),
        }
    return out


def credit_report(config: dict) -> str:
    """A plain-language scoreboard. Honest about being correlational + n-gated."""
    c = signal_credit(config)
    if c["n"] == 0:
        return ("No signal-tagged resolved predictions yet. Ship a few predictions "
                "(they now record a SIGNALS line), let them resolve, then re-run `credit`.")

    lines = [f"SIGNAL CREDIT — {c['n']} resolved prediction(s) with signal tags",
             "(association, not proof — signals cluster on bigger games; lower Brier = better)", ""]
    for sig in SIGNALS:
        d = c["signals"][sig]
        if d is None:
            lines.append(f"  {sig:<12} insufficient data (need ≥{_MIN_PER_GROUP} on each side)")
            continue
        verdict = "helped" if d["delta"] < -0.02 else "hurt" if d["delta"] > 0.02 else "no clear effect"
        lines.append(
            f"  {sig:<12} with {d['with_brier']:.3f} (n={d['with_n']}) · "
            f"without {d['without_brier']:.3f} (n={d['without_n']}) · "
            f"Δ {d['delta']:+.3f} → {verdict}")
    return "\n".join(lines)
