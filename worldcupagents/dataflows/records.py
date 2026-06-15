"""Home + head-to-head records from the match store (the 'Chelsea unbeaten at
home vs Leeds in 20' signal).

Computed from real results and fed to the advocate/judge PROMPTS — not into the
Poisson λ, where sparse head-to-head samples would be too noisy. Competition-
scoped (uses config['fd_competition']) so an EPL record isn't polluted by other
comps. Empty string when the store has nothing → no effect on WC/offline runs.
"""

from __future__ import annotations

from worldcupagents.dataflows.entities import same_team
from worldcupagents.dataflows.match_store import MatchStore, db_path


def _store_matches(config: dict) -> list[dict]:
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        rows = store.all_matches()
    finally:
        store.close()
    comp = config.get("fd_competition")
    if comp is not None:
        rows = [r for r in rows if r.get("comp") == comp]
    # Season cutoff: records/H2H span PAST seasons (that's their value), but a
    # selected season must never see matches played after its end (no leakage
    # when examining a historical season).
    season = config.get("season")
    if season:
        from worldcupagents.seasons import season_cutoff
        hi = season_cutoff(season)
        rows = [r for r in rows if not r.get("date") or r["date"] <= hi]
    return rows


def home_record(team: str, config: dict) -> tuple[int, int, int]:
    """(W, D, L) for ``team`` when playing at home, in the active competition."""
    w = d = loss = 0
    for r in _store_matches(config):
        if same_team(team, r["home"], config=config):
            if r["hg"] > r["ag"]:
                w += 1
            elif r["hg"] == r["ag"]:
                d += 1
            else:
                loss += 1
    return w, d, loss


def h2h_home_record(home: str, away: str, config: dict) -> tuple[int, int, int, int]:
    """(W, D, L, N) for ``home`` hosting ``away`` specifically."""
    w = d = loss = 0
    for r in _store_matches(config):
        if same_team(home, r["home"], config=config) and same_team(away, r["away"], config=config):
            if r["hg"] > r["ag"]:
                w += 1
            elif r["hg"] == r["ag"]:
                d += 1
            else:
                loss += 1
    return w, d, loss, (w + d + loss)


def records_summary(home: str, away: str, config: dict) -> str:
    """One-line home + head-to-head-at-home digest, or '' when no data."""
    parts: list[str] = []

    hw, hd, hl = home_record(home, config)
    if hw + hd + hl > 0:
        parts.append(f"{home} home record: {hw}W-{hd}D-{hl}L")

    w, d, loss, n = h2h_home_record(home, away, config)
    if n > 0:
        rec = f"{w}W-{d}D-{loss}L"
        if loss == 0 and n >= 2:
            parts.append(f"{home} UNBEATEN at home vs {away} ({rec} in last {n})")
        else:
            parts.append(f"{home} at home vs {away}: {rec} in {n}")

    return "; ".join(parts)
