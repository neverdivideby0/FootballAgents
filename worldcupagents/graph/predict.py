"""Predictor — the entry point, analog of TradingAgents' TradingAgentsGraph.propagate.

Builds the graph, runs one fixture, persists the run and appends a 'pending' entry
to the prediction log (the learning-loop scaffold; M3 resolves it post-match).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from worldcupagents.agents.schemas import Fixture, MatchVerdict
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.graph.setup import build_graph
from worldcupagents.llm_clients.factory import create_llm

_ENTRY_SEP = "\n\n<!-- ENTRY_END -->\n\n"


class Predictor:
    """Entry-point for a single fixture prediction.

    After each call to predict(), ``self.last_usage`` holds the raw token counts
    {"input": int, "output": int} and ``self.last_cost`` holds the estimated USD
    spend (or None if the model isn't in the pricing table).
    """

    def __init__(self, config: dict | None = None, deep_llm=None, quick_llm=None):
        """``deep_llm`` / ``quick_llm`` can be injected (tests, custom clients).
        When omitted and ``use_llm`` is set, they're built from the LLM factory."""
        self.config = dict(config or DEFAULT_CONFIG)
        self._deep_model  = self.config.get("deep_think_llm",  "")
        self._quick_model = self.config.get("quick_think_llm", "")
        self.last_usage: dict = {"input": 0, "output": 0}
        self.last_cost: float | None = None

        if self.config.get("use_llm") and (deep_llm is None or quick_llm is None):
            try:
                deep_llm  = deep_llm  or create_llm(self.config["llm_provider"], self._deep_model)
                quick_llm = quick_llm or create_llm(self.config["llm_provider"], self._quick_model)
            except Exception as e:  # noqa: BLE001 — missing key/SDK shouldn't crash; degrade to baseline
                logger.warning(
                    "use_llm is set but the LLM client is unavailable (%s); running baseline-only. "
                    "Set %s_API_KEY and install the provider extra.",
                    e, self.config.get("llm_provider", "").upper(),
                )
                deep_llm = quick_llm = None

        # usage_acc is a mutable dict shared by reference with all agent closures.
        self._usage_acc: dict = {"input": 0, "output": 0}
        self.graph = build_graph(self.config, deep_llm, quick_llm, self._usage_acc)

    def predict(self, fixture: Fixture, persist: bool = True):
        """``persist=False`` skips the run file + pending log entry — used by the
        evaluation harness, which scores known results and must not pollute the
        learning loop with fake pending predictions."""
        # Reset per-run counters.
        self._usage_acc["input"]  = 0
        self._usage_acc["output"] = 0

        final = self.graph.invoke(self._init_state(fixture))
        if persist:
            self._persist(fixture, final)
        return self._finish(final)

    def predict_stream(self, fixture: Fixture, on_event=None):
        """Like predict(), but streams node-by-node for a live UI.

        ``on_event(node_name, delta)`` is called after each graph node completes
        with that node's state delta. Returns (final_state, verdict) like predict().
        MatchState uses plain overwrite channels, so merging deltas reproduces
        graph.invoke()'s final state exactly.
        """
        self._usage_acc["input"] = 0
        self._usage_acc["output"] = 0

        final: dict = self._init_state(fixture)
        for chunk in self.graph.stream(final, stream_mode="updates"):
            for node_name, delta in chunk.items():
                if delta:
                    final.update(delta)
                if on_event is not None:
                    try:
                        on_event(node_name, delta or {})
                    except Exception as e:  # noqa: BLE001 — UI must never break a prediction
                        logger.warning("stream callback error (%s)", e)
        self._persist(fixture, final)
        return self._finish(final)

    def _init_state(self, fixture: Fixture) -> dict:
        return {
            "fixture": fixture,
            "debate_state": {
                "history": "", "home_history": "", "away_history": "",
                "current_response": "", "count": 0,
            },
            # Seeded unconditionally (like debate_state) — harmless when the
            # scenario layer is disabled.
            "scenario_debate_state": {
                "history": "", "upside_history": "", "downside_history": "",
                "neutral_history": "", "latest_speaker": "",
                "current_upside_response": "", "current_downside_response": "",
                "current_neutral_response": "", "count": 0,
            },
            "past_context": self._recall(fixture),
            "calibration_note": self._calibration_note(),
        }

    def _recall(self, fixture: Fixture) -> str:
        """Pull tactical history + resolved-prediction lessons from memory
        (graceful if absent) — TA's get_past_context injection point."""
        try:
            from worldcupagents.recall import past_context_for
            return past_context_for(fixture.home, fixture.away, self.config)
        except Exception as e:  # noqa: BLE001 — memory issues must not break a prediction
            logger.warning("recall failed (%s); proceeding without memory context", e)
            return ""

    def _calibration_note(self) -> str:
        """Recency-weighted correction from the system's own resolved track record,
        fed to the Judge + Final Pundit. "" when there is no resolved history."""
        try:
            from worldcupagents.calibration import calibration_note
            return calibration_note(self.config)
        except Exception as e:  # noqa: BLE001 — calibration must not break a prediction
            logger.warning("calibration note failed (%s); proceeding without it", e)
            return ""

    def _finish(self, final: dict):

        # Snapshot usage after the run.
        self.last_usage = dict(self._usage_acc)
        self.last_cost  = _compute_cost(
            self._deep_model, self._quick_model, self.last_usage
        )
        return final, final["verdict"]

    def _persist(self, fixture: Fixture, final: dict) -> None:
        verdict: MatchVerdict = final["verdict"]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

        runs = Path(self.config["results_dir"])
        runs.mkdir(parents=True, exist_ok=True)
        provisional = final.get("provisional_verdict")
        (runs / f"{fixture.home}_vs_{fixture.away}_{stamp}.json").write_text(
            json.dumps(
                {
                    "fixture": fixture.model_dump(mode="json"),
                    "verdict": verdict.model_dump(mode="json"),
                    "provisional_verdict": provisional.model_dump(mode="json") if provisional else None,
                    "debate": final["debate_state"]["history"],
                    "scenario_debate": (final.get("scenario_debate_state") or {}).get("history", ""),
                    "reports": {
                        "form": final.get("form_report", ""),
                        "tactical": final.get("tactical_report", ""),
                        "player": final.get("player_report", ""),
                    },
                },
                indent=2, default=str,
            ),
            encoding="utf-8",
        )

        # Append-only prediction log (markdown, Git-diffable) — TradingAgents memory pattern.
        log = Path(self.config["prediction_log_path"])
        log.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).date()
        tag = f"[{today} | {fixture.home} vs {fixture.away} | {verdict.outcome.value} {verdict.scoreline} | pending]"
        signals = _signals_present(final)
        entry = (
            f"{tag}\n\nPREDICTION:\n{verdict.rationale}\n"
            f"(p_home={verdict.p_home:.3f}, p_draw={verdict.p_draw:.3f}, p_away={verdict.p_away:.3f})\n"
            f"SIGNALS: {', '.join(signals) if signals else '(none)'}"
            f"{_ENTRY_SEP}"
        )
        with open(log, "a", encoding="utf-8") as f:
            f.write(entry)


# ── Helpers ────────────────────────────────────────────────────────────────

# Marker → signal name. Each "extra" signal beyond the statistical baseline that a
# prediction MAY have carried; recorded on the log entry so `credit` can later ask
# "did predictions with this signal score better?" (simple, explainable attribution).
_SIGNAL_MARKERS = {
    "PUNDITRY SIGNALS": "punditry",
    "PRE-MATCH TACTICAL BRIEF": "tactical",
    "LESSONS FROM PAST PREDICTIONS": "lessons",
    "qualitative notes": "qualitative",
}


def _signals_present(final: dict) -> list[str]:
    """Which extra signals fed this prediction (from the assembled state)."""
    pc = final.get("past_context") or ""
    out = [name for marker, name in _SIGNAL_MARKERS.items() if marker in pc]
    if (final.get("matchup_context") or {}).get("market"):
        out.append("market")
    if final.get("calibration_note"):
        out.append("calibration")
    return sorted(out)


def _compute_cost(deep_model: str, quick_model: str, usage: dict) -> float | None:
    """Best-effort cost estimate: attribute all output to the deep model (judge) and
    split input roughly 80/20 quick/deep (most input tokens come from advocates)."""
    from worldcupagents.llm_clients.model_catalog import estimate_cost

    # Heuristic split: ~70 % of input tokens → quick model (advocates + scenario
    # pundits, many short calls), ~30 % → deep model (judge + final pundit, 1–2
    # calls with large prompts). We can't separate output streams, so apportion.
    in_tok  = usage.get("input",  0)
    out_tok = usage.get("output", 0)

    quick_cost = estimate_cost(quick_model, int(in_tok * 0.7), int(out_tok * 0.65))
    deep_cost  = estimate_cost(deep_model,  int(in_tok * 0.3), int(out_tok * 0.35))

    if quick_cost is None and deep_cost is None:
        return None
    return (quick_cost or 0.0) + (deep_cost or 0.0)
