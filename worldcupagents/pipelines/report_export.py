"""Sectioned markdown report export — TradingAgents' complete_report.md analog.

One self-contained markdown file per prediction, sectioned by pipeline stage:
  0. Pre-Match Dossier (the raw data the agents saw — read it, form your own view)
  1. Analyst Reports (form / tactical / player)
  2. Advocate Debate
  3. Provisional Verdict (the judge, with the probability breakdown)
  4. Scenario (Risk) Debate
  5. Final Verdict (+ token usage / cost)
Sections with no content (e.g. scenario layer off) are skipped cleanly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.schemas import MatchVerdict


def build_markdown_report(fx, v: MatchVerdict, final: dict, predictor, cfg: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = (
        f"{cfg.get('llm_provider')} (deep: {cfg.get('deep_think_llm')} / quick: {cfg.get('quick_think_llm')})"
        if cfg.get("use_llm") else "baseline-only (no LLM)"
    )
    suffix = f" (via {v.decided_by.value})" if fx.knockout else ""
    lines = [
        f"# {fx.home} vs {fx.away}",
        f"_{now} · {fx.stage.value} · {fx.venue or 'neutral/TBD'} · {mode}_",
        "",
        "## Summary",
        f"**Call: {v.outcome.value}{suffix} — {v.scoreline}**  ·  "
        f"H {v.p_home:.0%} / D {v.p_draw:.0%} / A {v.p_away:.0%}  ·  {v.confidence} confidence",
    ]
    if v.alternative:
        a = v.alternative
        flag = "⚠️ Upset watch" if a.live else "Long shot"
        lines.append(f"**{flag}: {a.outcome.value} {a.scoreline} ({a.probability:.0%})** "
                     f"— {a.gap:.0%} behind the call")
    mr = (final.get("matchup_context") or {}).get("market")
    if mr:
        from worldcupagents.dataflows.market import divergence_note
        note = divergence_note(v, mr)
        if note:
            lines.append(f"_Market: {note}_")
    if v.rationale:
        lines += ["", f"> {v.rationale.strip()}"]
    lines.append("")

    # 1. Pre-match dossier — the structured data the agents worked from (no LLM),
    # so the reader can sanity-check the call and form their own opinion.
    try:
        from worldcupagents.pipelines.prematch import build_dossier, dossier_markdown
        doss_md = dossier_markdown(build_dossier(fx.home, fx.away, cfg))
        if doss_md:
            lines += ["## 1. Pre-Match Dossier", "_The data behind the call._", "", doss_md, ""]
    except Exception as e:  # noqa: BLE001 — dossier is a bonus; never break the export
        lines += [f"_(dossier unavailable: {e})_", ""]

    # 2. Analyst reports — the same data as the analysts framed it for the debate
    # (bulletised so it scans, not a wall of text).
    reports = [
        ("Form", final.get("form_report", "")),
        ("Tactical", final.get("tactical_report", "")),
        ("Player", final.get("player_report", "")),
    ]
    if any(text for _, text in reports):
        lines += ["## 2. Analyst Reports", "_How the analysts framed the dossier for the debate._", ""]
        for name, text in reports:
            if text:
                lines += [f"**{name} analyst**", "", _bullets(text), ""]

    # 3. Advocate debate
    debate = (final.get("debate_state") or {}).get("history", "").strip()
    lines += ["## 3. Advocate Debate", "", debate or "_(no debate — LLM disabled)_", ""]

    # 4. Provisional verdict (judge)
    prov = final.get("provisional_verdict")
    if prov is not None:
        lines += ["## 4. Provisional Verdict (Judge)", "", _verdict_md(prov, fx), ""]

    # 5. Scenario debate
    scenario = (final.get("scenario_debate_state") or {}).get("history", "").strip()
    if scenario:
        lines += ["## 5. Scenario (Risk) Debate", "", scenario, ""]

    # 6. Final verdict
    lines += ["## 6. Final Verdict", "", _verdict_md(v, fx)]
    if prov is not None and prov != v:
        lines += ["", f"_Adjusted from the judge's provisional read "
                      f"(H {prov.p_home:.0%} / D {prov.p_draw:.0%} / A {prov.p_away:.0%}) "
                      f"after the scenario debate._"]

    usage = getattr(predictor, "last_usage", {}) or {}
    if usage.get("input") or usage.get("output"):
        cost = getattr(predictor, "last_cost", None)
        cost_s = f" ≈ ${cost:.4f}" if cost is not None else ""
        lines += ["", "---", f"_Tokens: {usage['input']:,} in / {usage['output']:,} out{cost_s}_"]

    lines += [""]
    return "\n".join(lines)


def _bullets(text: str) -> str:
    """Turn a newline-joined analyst digest into a scannable bullet list (markdown
    collapses single newlines into one run-on paragraph otherwise)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(line if line.startswith(("#", "-", "*", ">", "|")) else f"- {line}")
    return "\n".join(out)


def _verdict_md(v: MatchVerdict, fx) -> str:
    suffix = f" (via {v.decided_by.value})" if fx.knockout else ""
    out = [
        f"**{v.outcome.value}{suffix} — {v.scoreline}**",
        "",
        f"- Probabilities: H {v.p_home:.0%} / D {v.p_draw:.0%} / A {v.p_away:.0%}",
        f"- Confidence: {v.confidence}",
    ]
    if v.exp_goals_home is not None:
        out.append(f"- Expected goals (model λ): {v.exp_goals_home:.1f}–{v.exp_goals_away:.1f}")
    if v.breakdown:
        b = v.breakdown
        out.append(
            f"- How: read {b.judge_home:.0%}/{b.judge_draw:.0%}/{b.judge_away:.0%} "
            f"⊕ baseline {b.base_home:.0%}/{b.base_draw:.0%}/{b.base_away:.0%} "
            f"(weight {b.judge_weight:.0%})"
        )
    if v.key_factors:
        out.append(f"- Key factors: {'; '.join(v.key_factors)}")
    if v.x_factors:
        out.append(f"- X-factors: {'; '.join(v.x_factors)}")
    if v.alternative:
        a = v.alternative
        flag = "⚠️ Upset watch" if a.live else "Long-shot alternative"
        out += ["", f"**{flag}: {a.outcome.value} {a.scoreline} — {a.probability:.0%}** "
                    f"(call is {a.gap:.0%} ahead)", f"> {a.narrative}"]
        out += [f"> - {f}" for f in a.swing_factors]
    out += ["", f"> {v.rationale}"]
    return "\n".join(out)


def export_markdown_report(fx, v: MatchVerdict, final: dict, predictor, cfg: dict) -> Path:
    """Write complete_report-style markdown under exports/; returns the path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = lambda s: s.replace(" ", "_").replace("/", "-")  # noqa: E731
    out_dir = Path(cfg.get("exports_dir", "exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug(fx.home)}_vs_{slug(fx.away)}_{stamp}.md"
    path.write_text(build_markdown_report(fx, v, final, predictor, cfg), encoding="utf-8")
    return path
