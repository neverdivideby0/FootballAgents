"""Live prediction TUI — analog of TradingAgents' rich Live CLI display.

While the graph runs we show: an agent progress table (Team | Agent | Status),
a rolling message buffer (latest debate/report snippets), the current report
panel, and a stats footer (elapsed | tokens | est. cost). Driven by
Predictor.predict_stream()'s per-node events; falls back to the plain
predict() path in non-TTY contexts (handled by the caller).
"""

from __future__ import annotations

import time
from collections import deque

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_PENDING, _RUNNING, _DONE = "[dim]pending[/dim]", "[yellow]in progress…[/yellow]", "[green]completed[/green]"


def _stages(config: dict) -> list[tuple[str, str]]:
    """(team, node_name) rows in execution order, mirroring the graph wiring."""
    rows = [("Scouts", "Build Dossiers"), ("Scouts", "Matchup Context")]
    if config.get("enable_analyst_reports", True):
        rows += [("Analysts", "Form Analyst"), ("Analysts", "Tactical Analyst"),
                 ("Analysts", "Player Analyst")]
    rows += [("Advocates", "Home Advocate"), ("Advocates", "Away Advocate"),
             ("Research", "Judge")]
    if config.get("enable_scenario_debate", False):
        rows += [("Risk", "Upside Pundit"), ("Risk", "Downside Pundit"),
                 ("Risk", "Neutral Pundit"), ("Final", "Final Pundit")]
    return rows


def _snippet(node: str, delta: dict) -> str | None:
    """One-line message for the buffer from a node's state delta."""
    if "debate_state" in delta:
        return (delta["debate_state"].get("current_response") or "").strip() or None
    if "scenario_debate_state" in delta:
        sd = delta["scenario_debate_state"]
        speaker = sd.get("latest_speaker", "")
        key = f"current_{speaker.lower()}_response" if speaker else ""
        return (sd.get(key) or "").strip() or None
    for key in ("form_report", "tactical_report", "player_report"):
        if key in delta:
            return f"{node} report ready ({len(delta[key])} chars)"
    if "provisional_verdict" in delta and "verdict" in delta:
        v = delta["verdict"]
        return f"Provisional verdict: {v.outcome.value} {v.scoreline} (H {v.p_home:.0%}/D {v.p_draw:.0%}/A {v.p_away:.0%})"
    if "verdict" in delta:
        v = delta["verdict"]
        return f"FINAL verdict: {v.outcome.value} {v.scoreline} (H {v.p_home:.0%}/D {v.p_draw:.0%}/A {v.p_away:.0%})"
    return None


def _report_panel_content(node: str, delta: dict, current: str) -> str:
    """The 'current report' panel follows whatever substantial text just landed."""
    for key, label in (("form_report", "Form report"), ("tactical_report", "Tactical report"),
                       ("player_report", "Player report")):
        if key in delta and delta[key]:
            return f"[bold]{label}[/bold]\n{delta[key]}"
    if "debate_state" in delta:
        turn = (delta["debate_state"].get("current_response") or "").strip()
        if turn:
            return turn
    if "scenario_debate_state" in delta:
        sd = delta["scenario_debate_state"]
        speaker = sd.get("latest_speaker", "")
        turn = (sd.get(f"current_{speaker.lower()}_response") or "").strip()
        if turn:
            return turn
    if "verdict" in delta:
        v = delta["verdict"]
        return f"[bold]{v.outcome.value} {v.scoreline}[/bold]\n{v.rationale}"
    return current


def run_predict_live(predictor, fixture, console: Console):
    """Run predict_stream under a rich Live display. Returns (final, verdict)."""
    config = predictor.config
    stages = _stages(config)
    status = {node: _PENDING for _, node in stages}
    turns: dict[str, int] = {}
    messages: deque[str] = deque(maxlen=10)
    started = time.time()
    state = {"report": "(waiting for the first agent…)", "llm_calls": 0}
    if stages:
        status[stages[0][1]] = _RUNNING

    def render() -> Group:
        t = Table(show_header=True, header_style="bold", expand=True, box=None)
        t.add_column("Team", width=12)
        t.add_column("Agent", width=18)
        t.add_column("Status")
        for team, node in stages:
            extra = f" [dim]×{turns[node]}[/dim]" if turns.get(node, 0) > 1 else ""
            t.add_row(team, node, status[node] + extra)

        msg_text = "\n".join(f"[dim]·[/dim] {m[:140]}" for m in messages) or "[dim](no messages yet)[/dim]"
        usage = predictor._usage_acc
        from worldcupagents.graph.predict import _compute_cost
        cost = _compute_cost(predictor._deep_model, predictor._quick_model, usage)
        cost_s = f"  ≈ ${cost:.4f}" if cost else ""
        footer = Text.from_markup(
            f"[dim]elapsed {time.time() - started:4.1f}s   "
            f"tokens {usage['input']:,} in / {usage['output']:,} out{cost_s}[/dim]"
        )
        return Group(
            Panel(t, title="Agents", border_style="blue"),
            Panel(msg_text, title="Messages", border_style="magenta"),
            Panel(state["report"], title="Current report", border_style="cyan"),
            footer,
        )

    def mark_progress(node: str) -> None:
        if node in status:
            status[node] = _DONE
            turns[node] = turns.get(node, 0) + 1
        # mark the next pending stage as running
        for _, n in stages:
            if status[n] == _PENDING:
                status[n] = _RUNNING
                break

    with Live(render(), console=console, refresh_per_second=8) as live:
        def on_event(node: str, delta: dict) -> None:
            mark_progress(node)
            snip = _snippet(node, delta)
            if snip:
                messages.append(snip.replace("\n", " "))
            state["report"] = _report_panel_content(node, delta, state["report"])
            live.update(render())

        final, verdict = predictor.predict_stream(fixture, on_event=on_event)
        # final frame: everything completed
        for _, n in stages:
            if status[n] != _DONE:
                status[n] = _DONE
        live.update(render())

    return final, verdict
