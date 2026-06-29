"""Data-backed weaknesses — the 'where is this team gettable?' read.

Every weakness here is computed from stored data and ONLY surfaced when a real
threshold trips (no manufactured flaws). Sourced, recency-bounded, and framed for
the opponent to exploit. Feeds both the dossier and the form analyst (so the
debate can attack concrete soft spots, not vibes).

Covers: set-piece vulnerability, soft home / poor away record, a bogey opponent,
falling short on penalties (extra time), goal over-reliance on one player, a form
slump, indiscipline, a leaky defence, and blunt finishing.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_RECENCY_YEARS = 5


def _since() -> str:
    return (date.today() - timedelta(days=365 * _RECENCY_YEARS)).isoformat()


def find_weaknesses(config: dict, profile, opponent: str | None = None,
                    max_items: int = 5) -> list[str]:
    """Up to ``max_items`` concrete, sourced weaknesses for ``profile.team``.
    Reads the match store defensively — returns [] when data is thin."""
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    team = profile.team
    out: list[str] = []
    if not db_path(config).exists():
        return out
    comp, season, since = config.get("fd_competition"), config.get("season"), _since()
    store = MatchStore.from_config(config)
    try:
        # 1) Bogey / can't-beat opponent — most relevant: record vs THIS opponent.
        if opponent:
            h = store.h2h_vs(team, opponent, comp=comp, since=since)
            w, d, lost = h["wdl"]
            n = h["n"]
            if n >= 4 and w <= max(1, n * 0.25) and lost >= w:
                wdl = f"{w}W-{d}D-{lost}L"
                if lost >= 3 and lost > w:
                    out.append(f"bogey side — lost {lost} of the last {n} vs {opponent} "
                               f"({wdl}) [source: match store]")
                else:
                    out.append(f"struggles against {opponent} — {w} win"
                               f"{'s' if w != 1 else ''} in the last {n} ({wdl}) [source: match store]")

        # (Shootout record removed 2026-06 — the warehouse shootout data was too sparse
        # and noisy to be a reliable weakness signal.)

        # 3) Set-piece vulnerability (conceded from dead balls).
        sp = _set_piece_conceded(store, comp, season, team)
        if sp:
            out.append(f"vulnerable from set pieces — {sp} [source: Understat]")

        # 4) Soft home record / poor away form.
        rec = store.venue_record(team, comp=comp, since=since)
        for at, label, thresh in (("home", "soft at home", 0.30), ("away", "poor on the road", 0.45)):
            w, dd, lost = rec[at]
            n = w + dd + lost
            if n >= 8 and lost / n >= thresh:
                out.append(f"{label} — {lost} losses in {n} {at} games "
                           f"({lost/n:.0%}) [source: match store]")

        # 5) Goal over-reliance on one player + blunt/leaky/regression from stats.
        out += _stat_weaknesses(store, config, profile)
    finally:
        store.close()

    # 6) Form slump (from the profile — no store needed).
    slump = _form_slump(profile)
    if slump:
        out.append(slump)

    return out[:max_items]


# ── components ────────────────────────────────────────────────────────────────

def _set_piece_conceded(store, comp, season, team) -> str:
    hit = store.situations(comp, season, team) if season else None
    if not hit:
        latest = store.latest_situations(comp or "WC", team)
        hit = (latest[0], latest[1]) if latest else None
    if not hit:
        return ""
    data = hit[0]
    conceded = 0
    for key in ("FromCorner", "SetPiece", "From Corner"):
        agg = data.get(key)
        if isinstance(agg, dict):
            against = agg.get("against") or {}
            conceded += against.get("goals") or 0
    return f"{conceded} goals conceded from corners/set pieces" if conceded >= 6 else ""


def _stat_weaknesses(store, config: dict, profile) -> list[str]:
    out: list[str] = []
    try:
        from worldcupagents.dataflows.entities import same_team
        rows = [r for r in store.players(comp=config.get("fd_competition"))
                if same_team(profile.team, r.get("team") or "", config=config)]
        squad = {p.name for p in profile.squad}
        if squad:
            import unicodedata
            fold = lambda s: unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()  # noqa: E731
            keys = {fold(s) for s in squad}
            rows = [r for r in rows if fold(r["player"]) in keys]
        scorers = [(r["player"], r.get("goals") or 0) for r in rows if (r.get("goals") or 0) > 0]
        total = sum(g for _, g in scorers)
        if total >= 10 and len(scorers) >= 3:
            top_name, top_g = max(scorers, key=lambda x: x[1])
            if top_g / total >= 0.40:
                out.append(f"over-reliant on {top_name} for goals ({top_g}/{total} = "
                           f"{top_g/total:.0%} of the squad's league goals) [source: Understat]")
    except Exception as e:  # noqa: BLE001
        logger.warning("stat weaknesses failed for %s (%s)", profile.team, e)

    # Tempo-based: leaky defence, blunt finishing, indiscipline.
    try:
        from datetime import date, timedelta
        tp = store.team_stat_profile(profile.team, comp=config.get("fd_competition"),
                                     since=(date.today() - timedelta(days=365 * _RECENCY_YEARS)).isoformat())
        if tp:
            if tp.get("shots_a", 0) >= 13:
                out.append(f"leaky — concedes {tp['shots_a']} shots/game [source: football-data.co.uk]")
            if tp.get("sot", 99) <= 4.0:
                out.append(f"blunt up top — only {tp['sot']} shots on target/game [source: football-data.co.uk]")
            if tp.get("fouls", 0) >= 12 or tp.get("yellow", 0) >= 2.2:
                out.append(f"indisciplined — {tp['fouls']} fouls, {tp['yellow']} yellows/game "
                           f"(card & set-piece risk) [source: football-data.co.uk]")
    except Exception as e:  # noqa: BLE001
        logger.warning("tempo weaknesses failed for %s (%s)", profile.team, e)
    return out


def _form_slump(profile) -> str:
    recent = profile.form[:5]
    if len(recent) < 4:
        return ""
    losses = sum(1 for r in recent if r.goals_for < r.goals_against)
    if losses >= 3:
        wdl = "".join("W" if r.goals_for > r.goals_against
                      else "L" if r.goals_for < r.goals_against else "D" for r in recent)
        return f"out of form — {wdl} in the last {len(recent)} [source: match store]"
    return ""
