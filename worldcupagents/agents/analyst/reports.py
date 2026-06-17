"""Analyst report nodes — analog of TradingAgents' analyst team (Market/News/
Fundamentals analysts writing reports into state before the debate).

Football twist: our data is pre-fetched deterministically (Build Dossiers /
Matchup Context / the SQLite store), so these analysts are ZERO-COST digest
builders by default — no tool-loop needed. Set ``analyst_reports_llm=True`` for
a quick-LLM prose polish of each report (3 extra calls); LLM errors degrade to
the raw digest, never a crash.

Reports land in state as ``form_report`` / ``tactical_report`` / ``player_report``
and are read by the advocates, the judge, and the scenario pundits.
"""

from __future__ import annotations

import logging

from worldcupagents.agents.briefs import profile_brief
from worldcupagents.graph.state import MatchState

logger = logging.getLogger(__name__)


def make_form_analyst(config: dict, llm=None, usage_acc: dict | None = None):
    """Quantitative digest: profiles, home/H2H records, expected-goals read."""

    def form_analyst(state: MatchState) -> dict:
        home, away = state["home_profile"], state["away_profile"]
        ctx = state.get("matchup_context") or {}
        lines = [
            f"{home.team} (home): {profile_brief(home)}",
            f"{away.team} (away): {profile_brief(away)}",
        ]
        # Dated, sourced result lines — citable evidence, not vibes.
        for p in (home, away):
            dated = _dated_results(p)
            if dated:
                lines.append(f"{p.team} recent (dated): {dated}")
        if ctx.get("records"):
            lines.append(f"Records: {ctx['records']} [source: match store]")
        for p in (home, away):  # set-piece / situation punditry (Understat, if fetched)
            situ = _situations_line(config, p.team)
            if situ:
                lines.append(situ)
        for p in (home, away):  # most-used XI by minutes (data-driven probable lineup)
            xi = _xi_line(config, p.team)
            if xi:
                lines.append(xi)
        if config.get("league_kind") != "league":  # warehouse taps for internationals
            for p in (home, away):
                intl = _intl_form_line(config, p.team)
                if intl:
                    lines.append(intl)
            h2h = _intl_h2h_line(config, home.team, away.team)
            if h2h:
                lines.append(h2h)
            for p in (home, away):
                wc = _wc_situations_line(config, p.team)
                if wc:
                    lines.append(wc)
            for p in (home, away):  # StatsBomb style fingerprint (possession, pairs, zones)
                style = _style_line(config, p.team)
                if style:
                    lines.append(style)
        for p in (home, away):  # attack/defense forte + tempo & discipline (fdcouk stats)
            forte = _forte_line(config, p.team)
            if forte:
                lines.append(forte)
            tempo = _tempo_line(config, p.team)
            if tempo:
                lines.append(tempo)
        for p in (home, away):  # head coach: style & pedigree (qualitative)
            coach = _coach_line(config, p)
            if coach:
                lines.append(coach)
        for p, opp in ((home, away.team), (away, home.team)):  # data-backed soft spots
            weak = _weakness_line(config, p, opp)
            if weak:
                lines.append(weak)
        lam = _lambda_digest(config, home, away)
        if lam:
            lines.append(lam)
        src = _result_sources(home, away)
        if src:
            lines.append(f"Sources: {src}")
        digest = "\n".join(lines)
        return {"form_report": _maybe_polish(config, llm, usage_acc, "form", digest)}

    return form_analyst


def _situations_line(config: dict, team: str) -> str:
    """Stored Understat situation breakdown (fetched via `fetch-data --xg`) —
    the 'scores from set pieces' punditry signal, sourced. Offline at predict time."""
    try:
        from worldcupagents.dataflows.match_store import MatchStore, db_path
        from worldcupagents.dataflows.providers.understat import situations_digest
        if not db_path(config).exists():
            return ""
        season = config.get("season")
        comp = config.get("fd_competition")
        if not season or not comp:
            return ""
        store = MatchStore.from_config(config)
        try:
            hit = store.situations(comp, season, team)
        finally:
            store.close()
        if not hit:
            return ""
        sit, url = hit
        return f"Situations — {situations_digest(sit, team)} [source: {url}]"
    except Exception as e:  # noqa: BLE001 — punditry must not break predict
        logger.warning("situations line failed for %r (%s)", team, e)
        return ""


def _xi_line(config: dict, team: str) -> str:
    """Stored Understat most-used XI (fetched via `fetch-data --xg`) — a data-driven
    probable lineup (by minutes), sourced. Offline at predict time."""
    try:
        from worldcupagents.dataflows.match_store import MatchStore, db_path
        from worldcupagents.dataflows.providers.understat import xi_digest
        if not db_path(config).exists():
            return ""
        season, comp = config.get("season"), config.get("fd_competition")
        if not season or not comp:
            return ""
        store = MatchStore.from_config(config)
        try:
            hit = store.team_xi(comp, season, team)
        finally:
            store.close()
        if not hit:
            return ""
        xi, url = hit
        return f"Likely XI ({team}, most-used by minutes) — {xi_digest(xi)} [source: {url}]"
    except Exception as e:  # noqa: BLE001 — lineup hint must not break predict
        logger.warning("xi line failed for %r (%s)", team, e)
        return ""


def _wh_team_id(config: dict, team: str) -> str | None:
    """Resolve a team to its warehouse team_id (national registry), or None."""
    try:
        from worldcupagents.dataflows.entities import resolve_team
        res = resolve_team(team, kind="national", config=config)
        return res.team_id
    except Exception as e:  # noqa: BLE001
        logger.warning("warehouse team resolution failed for %r (%s)", team, e)
        return None


def _wh_store(config: dict):
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return None
    return MatchStore.from_config(config)


def _fmt_wh_match(m: dict, team: str) -> str:
    """'W 3-0 v Chile (2026-03-24, WC qualification)' from the team's perspective."""
    is_home = m["home_team"] == team
    gf, ga = (m["home_score"], m["away_score"]) if is_home else (m["away_score"], m["home_score"])
    opp = m["away_team"] if is_home else m["home_team"]
    wdl = "W" if gf > ga else ("L" if gf < ga else "D")
    tour = f", {m['tournament']}" if m.get("tournament") else ""
    return f"{wdl} {gf}-{ga} v {opp} ({m.get('date') or '?'}{tour})"


def _intl_form_line(config: dict, team: str) -> str:
    """Recent internationals from the warehouse (`hoard-data --source
    international-results`) — fills the pre-tournament form gap, sourced."""
    try:
        tid = _wh_team_id(config, team)
        store = _wh_store(config)
        if not tid or store is None:
            return ""
        try:
            matches = store.wh_team_matches(tid, limit=5)
        finally:
            store.close()
        if not matches:
            return ""
        parts = "; ".join(_fmt_wh_match(m, team) for m in matches)
        src = matches[0].get("source_id") or "warehouse"
        return f"{team} recent internationals: {parts} [source: {src}]"
    except Exception as e:  # noqa: BLE001 — warehouse taps must not break predict
        logger.warning("intl form line failed for %r (%s)", team, e)
        return ""


def _intl_h2h_line(config: dict, home: str, away: str) -> str:
    """Head-to-head internationals between the two sides, from the warehouse."""
    try:
        tid_h, tid_a = _wh_team_id(config, home), _wh_team_id(config, away)
        store = _wh_store(config)
        if not (tid_h and tid_a) or store is None:
            return ""
        try:
            meetings = store.wh_h2h(tid_h, tid_a, limit=5)
        finally:
            store.close()
        if not meetings:
            return ""
        parts = "; ".join(
            f"{m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']} "
            f"({m.get('date') or '?'}{', ' + m['tournament'] if m.get('tournament') else ''})"
            for m in meetings)
        src = meetings[0].get("source_id") or "warehouse"
        return f"H2H (international): {parts} [source: {src}]"
    except Exception as e:  # noqa: BLE001
        logger.warning("intl h2h line failed for %s vs %s (%s)", home, away, e)
        return ""


def _wc_situations_line(config: dict, team: str) -> str:
    """Past-World-Cup shot profile from StatsBomb open data (`hoard-data --source
    statsbomb`) — the national-team analog of the Understat situations line."""
    try:
        store = _wh_store(config)
        if store is None:
            return ""
        try:
            hit = store.latest_situations("WC", team)
        finally:
            store.close()
        if not hit:
            return ""
        data, source, season = hit
        parts = []
        for pattern, agg in sorted(data.items()):
            if not isinstance(agg, dict) or not agg.get("shots"):
                continue
            xg = f", xG {float(agg['xG']):.1f}" if agg.get("xG") is not None else ""
            parts.append(f"{pattern}: {agg.get('goals', 0)}g/{agg['shots']}sh{xg}")
        if not parts:
            return ""
        return (f"{team} shot profile (WC {season}): " + "; ".join(parts[:6])
                + f" [source: {source}]")
    except Exception as e:  # noqa: BLE001
        logger.warning("wc situations line failed for %r (%s)", team, e)
        return ""


def _style_line(config: dict, team: str) -> str:
    """StatsBomb style fingerprint (most recent stored WC): possession share,
    pass accuracy, directness, favourite pass pairs, build-up zones — pundit
    language derived from event coordinates (never raw X,Y)."""
    try:
        store = _wh_store(config)
        if store is None:
            return ""
        try:
            hit = store.latest_situations("WC", team)
        finally:
            store.close()
        if not hit:
            return ""
        data, source, season = hit
        style = data.get("style")
        if not isinstance(style, dict):
            return ""
        bits = [f"possession {style['possession_share']:.0%}" if style.get("possession_share") else "",
                f"pass accuracy {style['pass_pct']:.0f}%" if style.get("pass_pct") else "",
                f"directness {style['directness']:.2f} prog passes/pass" if style.get("directness") else ""]
        if style.get("top_pass_pairs"):
            bits.append("favourite passing lanes: " + ", ".join(style["top_pass_pairs"][:2]))
        if style.get("build_up_zones"):
            bits.append("builds up via " + ", ".join(style["build_up_zones"][:2]))
        bits = [b for b in bits if b]
        if not bits:
            return ""
        return f"{team} style (WC {season}): " + "; ".join(bits) + f" [source: {source}]"
    except Exception as e:  # noqa: BLE001
        logger.warning("style line failed for %r (%s)", team, e)
        return ""


def _player_notes_line(config: dict, profile) -> str:
    """User-authored scouting/style notes for CURRENT-squad players (typed via
    `note-player` or the explorer's Player Notes tab) — the qualitative player
    layer that data can't capture."""
    try:
        from worldcupagents.dataflows.match_store import MatchStore, db_path
        if not db_path(config).exists():
            return ""
        store = MatchStore.from_config(config)
        try:
            notes = store.player_notes_for_team(profile.team)
        finally:
            store.close()
        if not notes:
            return ""
        squad = {_fold_name(p.name) for p in profile.squad}
        kept = [n for n in notes if not squad or _fold_name(n["player"]) in squad]
        if not kept:
            return ""
        parts = "; ".join(f"{n['player']} — {n['note']}" for n in kept)
        srcs = sorted({n.get("source") or "manual" for n in kept})
        return f"Scouting notes ({profile.team}): {parts} [source: {', '.join(srcs)}]"
    except Exception as e:  # noqa: BLE001
        logger.warning("player notes line failed for %r (%s)", profile.team, e)
        return ""


def _fold_name(name: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()


def _squad_only(rows: list[dict], squad_names, key: str = "player") -> list[dict]:
    """Keep only rows whose player is in the current squad (accent-insensitive).
    No squad given → keep all (back-compat)."""
    if not squad_names:
        return rows
    keys = {_fold_name(s) for s in squad_names}
    return [r for r in rows if _fold_name(r.get(key, "")) in keys]


def _wc_player_metrics_line(config: dict, team: str, squad_names=None) -> str:
    """Per-player event aggregates from past WCs (StatsBomb), CURRENT squad only —
    the granular metrics layer: pass volume/accuracy, progressive actions, xG."""
    try:
        tid = _wh_team_id(config, team)
        store = _wh_store(config)
        if not tid or store is None:
            return ""
        try:
            rows = _squad_only(store.wc_player_aggregates(tid, limit=40), squad_names)[:3]
        finally:
            store.close()
        if not rows:
            return ""
        parts = []
        for r in rows:
            seg = [r["player"]]
            if r.get("passes"):
                pct = 100.0 * (r.get("passes_completed") or 0) / r["passes"]
                seg.append(f"{int(r['passes'])} passes at {pct:.0f}%")
            prog = int((r.get("progressive_passes") or 0) + (r.get("progressive_carries") or 0))
            if prog:
                seg.append(f"{prog} progressive actions")
            if r.get("xg"):
                seg.append(f"xG {float(r['xg']):.1f}")
            if r.get("goals"):
                seg.append(f"{int(r['goals'])}g")
            parts.append(" ".join(seg[:1]) + ": " + ", ".join(seg[1:]) if len(seg) > 1 else seg[0])
        return (f"{team} WC event metrics: " + "; ".join(parts)
                + " [source: StatsBomb open data]")
    except Exception as e:  # noqa: BLE001
        logger.warning("wc player metrics line failed for %r (%s)", team, e)
        return ""


def _career_totals_line(config: dict, team: str, squad_names=None) -> str:
    """Career caps/goals for the team's leading CURRENT-SQUAD players (Wikipedia
    infoboxes via `hoard-data --source wikipedia-player-totals`), sourced. Filtered
    to the squad so retired legends (Polster, Şükür…) never appear."""
    try:
        tid = _wh_team_id(config, team)
        store = _wh_store(config)
        if not tid or store is None:
            return ""
        try:
            totals = _squad_only(store.career_totals_for_team(tid, limit=60), squad_names)[:4]
        finally:
            store.close()
        if not totals:
            return ""
        parts = "; ".join(
            f"{t['player']} {t['caps']} caps, {t['goals'] or 0} intl goals"
            + (f" ({t['start_year']}–{t['end_year'] or 'present'})" if t.get("start_year") else "")
            for t in totals)
        src = totals[0].get("source_url") or totals[0].get("source_id") or "wikipedia"
        return f"{team} career totals: {parts} [source: {src}]"
    except Exception as e:  # noqa: BLE001
        logger.warning("career totals line failed for %r (%s)", team, e)
        return ""


def _forte_line(config: dict, team: str) -> str:
    """Attack-vs-defense leaning from fitted strengths — the 'is a team better at
    defending than attacking?' signal (which suppresses goal expectancy)."""
    try:
        from worldcupagents.ensemble.strength import load_strength_model, team_forte
        fo = team_forte(load_strength_model(config), team)
        if not fo:
            return ""
        return (f"Forte — {team}: {fo['label']} (attack {fo['attack']}, "
                f"defensive solidity {fo['solidity']}) [source: fitted strength model]")
    except Exception as e:  # noqa: BLE001
        logger.warning("forte line failed for %r (%s)", team, e)
        return ""


def _coach_line(config: dict, profile) -> str:
    """Head coach style & pedigree — the manager is a real x-factor (pragmatist vs
    expansive, tournament-hardened vs debutant). Name from the data vendor, prose
    from the Guardian Experts' Network guide; sourced. Offline at predict time."""
    try:
        from worldcupagents.dataflows.coach import coach_brief, coach_digest
        brief = coach_brief(config, profile.team, profile)
        digest = coach_digest(brief)
        if not digest:
            return ""
        src = brief.get("source") or "data vendor"
        return f"Coach — {profile.team}: {digest} [source: {src}]"
    except Exception as e:  # noqa: BLE001 — coach line must not break predict
        logger.warning("coach line failed for %r (%s)", profile.team, e)
        return ""


def _weakness_line(config: dict, profile, opponent: str) -> str:
    """Concrete, data-backed soft spots for a team — so advocates can attack real
    flaws (and self-critique honestly), not vibes."""
    try:
        from worldcupagents.dataflows.weaknesses import find_weaknesses
        ws = find_weaknesses(config, profile, opponent)
        return f"Weaknesses — {profile.team}: " + "; ".join(ws) if ws else ""
    except Exception as e:  # noqa: BLE001
        logger.warning("weakness line failed for %r (%s)", profile.team, e)
        return ""


def _tempo_line(config: dict, team: str) -> str:
    """Tempo & discipline per game from football-data.co.uk stat columns."""
    try:
        from datetime import date, timedelta
        from worldcupagents.dataflows.match_store import MatchStore, db_path
        if not db_path(config).exists():
            return ""
        since = (date.today() - timedelta(days=365 * 5)).isoformat()
        store = MatchStore.from_config(config)
        try:
            p = store.team_stat_profile(team, comp=config.get("fd_competition"), since=since)
        finally:
            store.close()
        if not p:
            return ""
        return (f"Tempo/discipline — {team} (last {p['n']}): {p['shots']} shots "
                f"({p['sot']} on target), {p['corners']} corners, {p['fouls']} fouls, "
                f"{p['yellow']} yellows, {p['red']} reds per game; concedes {p['shots_a']} "
                f"shots/{p['corners_a']} corners [source: football-data.co.uk]")
    except Exception as e:  # noqa: BLE001
        logger.warning("tempo line failed for %r (%s)", team, e)
        return ""


def _dated_results(profile) -> str:
    """'2-1 v Chelsea FC (2026-05-24)' lines so claims are checkable."""
    out = []
    for r in profile.form[:5]:
        date = f" ({r.date})" if r.date else ""
        out.append(f"{r.goals_for}-{r.goals_against} v {r.opponent}{date}")
    return "; ".join(out)


def _result_sources(*profiles) -> str:
    """Distinct provenance tags behind the form data (e.g. fdcouk:PL:2425)."""
    tags: list[str] = []
    for p in profiles:
        for r in p.form:
            if r.source and r.source not in tags:
                tags.append(r.source)
    return ", ".join(tags[:6])


def make_tactical_analyst(config: dict, llm=None, usage_acc: dict | None = None):
    """Qualitative digest: tactical history mined from analysed matches (memory/)."""

    def tactical_analyst(state: MatchState) -> dict:
        pc = (state.get("past_context") or "").strip()
        digest = pc if pc else "(no analysed tactical history for either team yet)"
        return {"tactical_report": _maybe_polish(config, llm, usage_acc, "tactical", digest)}

    return tactical_analyst


def make_player_analyst(config: dict, llm=None, usage_acc: dict | None = None):
    """Per-player metrics digest: leading goal contributors for both teams."""

    def player_analyst(state: MatchState) -> dict:
        from worldcupagents.recall import players_digest, squad_club_stats, top_players

        home, away = state["home_profile"], state["away_profile"]
        is_league = config.get("league_kind") == "league"
        lines = []
        sources: list[str] = []
        for profile in (home, away):
            team = profile.team
            squad_names = [p.name for p in profile.squad]
            try:
                ps = top_players(team, config, squad=squad_names)
                # Nationals have no club stats under comp=WC — pull each squad
                # player's CLUB form by name instead of leaving the table empty.
                if not ps and not is_league:
                    ps = squad_club_stats(config, squad_names)
            except Exception as e:  # noqa: BLE001 — store issues must not break predict
                logger.warning("player analyst: top_players failed for %r (%s)", team, e)
                ps = []
            label = "club form" if (not is_league and ps and any(p.xg is not None for p in ps)) else ""
            lines.append(f"{team}{' (' + label + ')' if label else ''}: {players_digest(ps)}")
            for p in ps:
                if p.source and p.source not in sources:
                    sources.append(p.source)
            notes = _player_notes_line(config, profile)
            if notes:
                lines.append(notes)
            if config.get("league_kind") != "league":  # warehouse taps (B1/B3)
                squad_names = [p.name for p in profile.squad]
                career = _career_totals_line(config, team, squad_names)
                if career:
                    lines.append(career)
                metrics = _wc_player_metrics_line(config, team, squad_names)
                if metrics:
                    lines.append(metrics)
        if sources:
            lines.append(f"Sources: {', '.join(sources)}")
        digest = "\n".join(lines)
        return {"player_report": _maybe_polish(config, llm, usage_acc, "player", digest)}

    return player_analyst


# ── internals ────────────────────────────────────────────────────────────────

def _lambda_digest(config: dict, home, away) -> str:
    """One-line expected-goals read from the shared Poisson model."""
    try:
        from worldcupagents.ensemble.verdict import match_lambdas
        lam_h, lam_a = match_lambdas(config, home, away)
        return f"Expected goals (model): {home.team} {lam_h:.2f} — {lam_a:.2f} {away.team}"
    except Exception as e:  # noqa: BLE001
        logger.warning("form analyst: lambda digest failed (%s)", e)
        return ""


def _maybe_polish(config: dict, llm, usage_acc: dict | None, kind: str, digest: str) -> str:
    """Optional quick-LLM prose pass over a deterministic digest."""
    use_llm = bool(config.get("use_llm")) and bool(config.get("analyst_reports_llm")) and llm is not None
    if not use_llm:
        return digest
    try:
        prompt = (
            f"You are a football {kind} analyst. Rewrite the following raw data digest as a "
            f"crisp analyst report (≤120 words). Keep every number and name; do NOT invent "
            f"data not present below.\n\nDATA:\n{digest}"
        )
        msg = llm.invoke(prompt)
        meta = getattr(msg, "usage_metadata", None)
        if usage_acc is not None and meta:
            usage_acc["input"] += meta.get("input_tokens", 0)
            usage_acc["output"] += meta.get("output_tokens", 0)
        return msg.content
    except Exception as e:  # noqa: BLE001 — visible degrade, never crash
        logger.warning("%s analyst LLM error (%s); raw digest", kind, e)
        return f"[LLM unavailable] {digest}"
