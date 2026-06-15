"""Long-term tactical memory recall — the bridge from analyze-match to predict.

Reads the MatchTacticalReports that ``analyze-match`` persists to
``memory/matches/`` and distills them into a ``predictive_brief`` for an upcoming
fixture. The brief is injected into the predict graph's ``past_context`` so the
advocates and judge reason from observed tactical history, not just squad lists.

Deterministic + offline by default (pure retrieval/formatting); the downstream
advocate/judge LLMs do the actual reasoning over what we surface. Degrades to an
empty brief when no history exists — predict then behaves exactly as before.
"""

from __future__ import annotations

import logging
from pathlib import Path

from worldcupagents.agents.schemas import MatchTacticalReport, PlayerStat
from worldcupagents.dataflows.entities import resolve_team, same_team
from worldcupagents.dataflows.match_store import MatchStore, db_path
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

_MAX_MATCHES_PER_TEAM = 5


def _matches_dir(config: dict) -> Path:
    return Path(config.get("memory_dir", "memory")) / "matches"


def load_reports(config: dict) -> list[MatchTacticalReport]:
    """Load every persisted tactical report (skips anything unreadable)."""
    d = _matches_dir(config)
    if not d.exists():
        return []
    reports: list[MatchTacticalReport] = []
    for f in sorted(d.glob("*.json")):
        try:
            reports.append(MatchTacticalReport.model_validate_json(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001 — a corrupt file shouldn't break recall
            logger.warning("recall: could not read %s (%s)", f.name, e)
    return reports


def reports_for_team(team: str, config: dict, limit: int = _MAX_MATCHES_PER_TEAM) -> list[MatchTacticalReport]:
    """Most-recent tactical reports featuring ``team`` (home or away)."""
    hits = [
        r for r in load_reports(config)
        if same_team(team, r.home, config=config) or same_team(team, r.away, config=config)
    ]
    hits.sort(key=lambda r: r.date or "", reverse=True)
    return hits[:limit]


def prediction_lessons(team_a: str, team_b: str, config: dict,
                       n_same: int = 5, n_cross: int = 3) -> str:
    """Lessons from RESOLVED past predictions — TradingAgents' get_past_context port.

    Parses ``prediction_log.md`` for resolved entries: up to ``n_same`` most-recent
    entries involving either team, plus ``n_cross`` recent cross-team entries (a
    lesson about over-backing favourites transfers between fixtures). Each lesson
    carries the result, our call, the Brier score, and the REFLECTION when one was
    written at resolve time. Returns "" when there is no resolved history.
    """
    from worldcupagents.graph.predict import _ENTRY_SEP

    log_path = Path(config.get("prediction_log_path", "memory/prediction_log.md"))
    if not log_path.exists():
        return ""

    keys = {normalize_key(canonical_name(team_a)), normalize_key(canonical_name(team_b))}
    same: list[str] = []
    cross: list[str] = []
    # Iterate newest-last (append-only log) then reverse for most-recent-first.
    for entry in reversed(log_path.read_text(encoding="utf-8").split(_ENTRY_SEP)):
        entry = entry.strip()
        if not entry:
            continue
        first = entry.splitlines()[0]
        if "| resolved:" not in first:
            continue
        lesson = _lesson_line(entry, first)
        if lesson is None:
            continue
        fixture_part = first.strip("[]").split("|")[1].strip() if "|" in first else ""
        involved = {normalize_key(t.strip()) for t in fixture_part.split(" vs ")} if " vs " in fixture_part else set()
        if involved & keys:
            if len(same) < n_same:
                same.append(lesson)
        elif len(cross) < n_cross:
            cross.append(lesson)

    if not same and not cross:
        return ""
    blocks = []
    if same:
        blocks.append("These teams:\n" + "\n".join(f"  - {x}" for x in same))
    if cross:
        blocks.append("Other matches (general calibration):\n" + "\n".join(f"  - {x}" for x in cross))
    return "LESSONS FROM PAST PREDICTIONS (resolved, with Brier scores — lower is better)\n\n" + "\n\n".join(blocks)


def _lesson_line(entry: str, first: str) -> str | None:
    """Compact one-liner: '<fixture tag> | RESULT…sentence | REFLECTION…'."""
    result = next((ln[len("RESULT: "):] for ln in entry.splitlines() if ln.startswith("RESULT: ")), "")
    reflection = next((ln[len("REFLECTION: "):] for ln in entry.splitlines() if ln.startswith("REFLECTION: ")), "")
    tag = first.strip("[]")
    bits = [tag]
    if result:
        bits.append(result)
    if reflection:
        bits.append(f"Lesson: {reflection}")
    return " | ".join(bits) if len(bits) > 1 else None


def past_context_for(team_a: str, team_b: str, config: dict) -> str:
    """Everything memory has for this fixture: tactical brief + prediction lessons."""
    parts = [predictive_brief(team_a, team_b, config),
             qualitative_brief(team_a, team_b, config),
             prediction_lessons(team_a, team_b, config)]
    return "\n\n".join(p for p in parts if p)


def qualitative_brief(team_a: str, team_b: str, config: dict, per_team: int = 4) -> str:
    """Compact public/manual qualitative notes linked to the two teams.

    Pulls from the SQLite qualitative warehouse. This is intentionally capped:
    articles stay in the warehouse, prompts get only short sourced snippets.
    """
    if not db_path(config).exists():
        return ""
    store = MatchStore.from_config(config)
    try:
        blocks = []
        for team in (team_a, team_b):
            res = resolve_team(team, kind="national", config=config)
            if not res.team_id:
                continue
            rows = store.conn.execute(
                """
                SELECT d.title, d.source_id, d.url, d.published_at, d.fetched_at, s.text,
                       COUNT(c.claim_id) AS claim_count
                FROM wh_qual_segments s
                JOIN wh_qual_documents d ON d.document_id = s.document_id
                JOIN wh_qual_links l ON l.document_id = d.document_id
                LEFT JOIN wh_qual_claims c ON c.segment_id = s.segment_id
                WHERE l.entity_type = 'team' AND l.entity_id = ?
                  AND (l.segment_id = s.segment_id OR d.source_id = 'manual_analysis')
                  AND (d.source_id = 'manual_analysis' OR c.claim_id IS NOT NULL)
                GROUP BY s.segment_id
                ORDER BY CASE WHEN d.source_id = 'manual_analysis' THEN 0 ELSE 1 END,
                         claim_count DESC,
                         COALESCE(d.published_at, d.fetched_at) DESC,
                         s.idx ASC
                LIMIT ?
                """,
                [res.team_id, per_team],
            ).fetchall()
            if not rows:
                continue
            lines = [f"{team} qualitative notes:"]
            for r in rows:
                snippet = " ".join((r["text"] or "").split())
                if len(snippet) > 260:
                    snippet = snippet[:257].rstrip() + "..."
                source = r["url"] or r["source_id"] or "qualitative"
                date = r["published_at"] or "undated"
                lines.append(f"  - {snippet} ({date}) [source: {r['title']} | {source}]")
            blocks.append("\n".join(lines))
    finally:
        store.close()
    if not blocks:
        return ""
    return "QUALITATIVE BRIEF (public/manual notes from warehouse)\n\n" + "\n\n".join(blocks)


def predictive_brief(team_a: str, team_b: str, config: dict) -> str:
    """Pre-match briefing from prior analysed matches for both teams.

    Returns "" when neither team has any history (keeps past_context empty so
    predict is unchanged). Otherwise a compact, sourced text block.
    """
    blocks: list[str] = []
    any_history = False
    for team in (team_a, team_b):
        reps = reports_for_team(team, config)
        if not reps:
            blocks.append(f"{team}: no analysed match history yet.")
            continue
        any_history = True
        lines = [f"{team} — {len(reps)} prior match(es) analysed:"]
        for r in reps:
            opp = r.away if same_team(team, r.home, config=config) else r.home
            lines.append(f"  vs {opp} ({r.date or 'undated'}): {_digest(r)}{_source_suffix(r)}")
        blocks.append("\n".join(lines))

    if not any_history:
        return ""
    return "PRE-MATCH TACTICAL BRIEF (from prior analysed matches)\n\n" + "\n\n".join(blocks)


def top_players(team: str, config: dict, n: int = 5, squad: list[str] | None = None) -> list[PlayerStat]:
    """A team's leading players by goal contribution, from the player-stats store.
    When ``squad`` is given (current squad member names), only those players are
    returned — so the dossier never references someone who isn't in the squad."""
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        rows = store.players(comp=config.get("fd_competition"))
    finally:
        store.close()
    mine = [r for r in rows if same_team(team, r["team"], config=config)]
    # Accent-insensitive dedupe: feeds disagree on diacritics ("Gyökeres" vs
    # "Gyokeres"); keep the richer row (more populated metric fields).
    import unicodedata

    def fold(name: str) -> str:
        return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()

    def richness(r: dict) -> int:
        return sum(1 for v in r.values() if v is not None)

    if squad:
        squad_keys = {fold(s) for s in squad}
        mine = [r for r in mine if fold(r["player"]) in squad_keys]

    by_key: dict[str, dict] = {}
    for r in mine:
        k = fold(r["player"])
        if k not in by_key or richness(r) > richness(by_key[k]):
            by_key[k] = r
    mine = sorted(by_key.values(),
                  key=lambda r: (r.get("goals") or 0) + (r.get("assists") or 0), reverse=True)
    # Drop None columns so PlayerStat's field defaults apply (the store returns
    # NULL for metrics a given source didn't fill).
    return [PlayerStat(**{k: v for k, v in r.items() if v is not None}) for r in mine[:n]]


_CLUB_COMPS = ("PL", "PD", "SA", "BL1", "FL1")


def squad_club_stats(config: dict, squad: list[str], n: int = 8) -> list[PlayerStat]:
    """CLUB form for a national-team squad: match each squad player BY NAME across
    the club leagues (not by team), keeping the richest row — so a national dossier
    shows e.g. Sabitzer's Bundesliga xG instead of an empty table. Needs the
    relevant league's Understat data (`fetch-data -L BL1 --xg`)."""
    if not squad or not db_path(config).exists():
        return []
    import unicodedata

    def fold(name: str) -> str:
        return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()

    def richness(r: dict) -> int:
        return sum(1 for v in r.values() if v is not None)

    store = MatchStore.from_config(config)
    try:
        rows = [r for r in store.players() if r.get("comp") in _CLUB_COMPS]
    finally:
        store.close()
    keys = {fold(s) for s in squad}
    by_key: dict[str, dict] = {}
    for r in rows:
        k = fold(r["player"])
        if k not in keys:
            continue
        if k not in by_key or richness(r) > richness(by_key[k]):
            by_key[k] = r
    best = sorted(by_key.values(),
                  key=lambda r: ((r.get("goals") or 0) + (r.get("assists") or 0),
                                 r.get("minutes") or 0), reverse=True)
    return [PlayerStat(**{k: v for k, v in r.items() if v is not None}) for r in best[:n]]


def players_digest(players: list[PlayerStat]) -> str:
    if not players:
        return "(no player stats on record)"
    out = []
    for p in players:
        s = f"{p.player} {p.goals}G/{p.assists}A in {p.matches}"
        if p.xg is not None and p.xa is not None:
            s += f" (xG {p.xg:.1f}, xA {p.xa:.1f})"
        if p.key_passes is not None:
            s += f", {p.key_passes} key passes"
        if p.xg_buildup is not None:
            s += f", build-up xG {p.xg_buildup:.1f}"
        if p.pass_accuracy is not None:
            s += f", {p.pass_accuracy:.0f}% pass"
        if p.rating is not None:
            s += f", {p.rating:.2f} rating"
        out.append(s)
    return "; ".join(out)


def _source_suffix(report: MatchTacticalReport) -> str:
    """Append the commentary source (a clickable Guardian URL when available) so
    every tactical claim traces to a checkable document."""
    url = next((s for s in report.sources if s.startswith("http")), None)
    if url:
        return f" [source: {url}]"
    if report.sources:
        return f" [source: {report.sources[0]}]"
    return ""


def _digest(report: MatchTacticalReport) -> str:
    """One-line tactical digest of a report's phases (rich for LLM reports,
    graceful for placeholder ones)."""
    bits: list[str] = []
    for p in report.phases:
        seg: list[str] = []
        if p.formations_blocks:
            seg.append("/".join(p.formations_blocks[:2]))
        if p.adjustments:
            seg.append(p.adjustments[0])
        if p.key_matchups:
            seg.append(f"matchup: {p.key_matchups[0]}")
        if seg:
            phase_short = p.phase.split(" ", 1)[0]  # "15-45", "Half-Time", "75-90+"
            bits.append(f"[{phase_short}] {', '.join(seg)}")
    return "; ".join(bits[:4]) if bits else "analysed (no notable tactical flags recorded)"
