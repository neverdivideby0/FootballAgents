"""Injury / availability overlay.

Player availability isn't in any free real-time feed, so it comes from two places:
a **manual** overlay you control (`note-injury`, always authoritative) and a
**best-effort** harvest of players flagged in match-punditry digests. Both land in the
`injuries` store table; the overlay then sets each `Player.status` and drops unavailable
players (injured/suspended) from the probable XI so the debate stops projecting someone
who's out (the Víctor Muñoz case). Manual rows are never overwritten by the harvest.
"""

from __future__ import annotations

import logging

from worldcupagents.dataflows.match_store import MatchStore, db_path
from worldcupagents.dataflows.names import normalize_key

logger = logging.getLogger(__name__)

_OUT = {"injured", "suspended"}      # dropped from the XI; "doubt" stays but is flagged


def apply_injuries(profile, config: dict):
    """Overlay availability: set each squad Player.status from the injuries store and
    drop injured/suspended players from probable_xi. Mutates + returns the profile."""
    if not db_path(config).exists():
        return profile
    # Best-effort Guardian extract first (manual rows are never overwritten).
    if config.get("harvest_punditry_injuries", True):
        try:
            harvest_punditry_injuries(profile, config)
        except Exception:  # noqa: BLE001
            pass
    store = MatchStore.from_config(config)
    try:
        rows = store.injuries_for_team(profile.team)
    finally:
        store.close()
    if not rows:
        return profile
    status = {normalize_key(r["player"]): r["status"] for r in rows
              if r["status"] in ("injured", "suspended", "doubt")}
    for p in profile.squad:
        st = status.get(normalize_key(p.name))
        if st:
            p.status = st
    if profile.probable_xi:
        out = {k for k, st in status.items() if st in _OUT}
        profile.probable_xi = [n for n in profile.probable_xi if normalize_key(n) not in out]
    return profile


def injury_summary(team: str, config: dict) -> str:
    """One-line availability summary from the injuries store ('' when none)."""
    if not db_path(config).exists():
        return ""
    store = MatchStore.from_config(config)
    try:
        rows = store.injuries_for_team(team)
    finally:
        store.close()
    if not rows:
        return ""
    parts = [f"{r['player']} ({r['status']}" + (f", {r['source']}" if r.get("source") else "") + ")"
             for r in rows]
    return "Availability — OUT/DOUBT: " + "; ".join(parts) + " [source: injuries store]"


def harvest_punditry_injuries(profile, config: dict) -> int:
    """Best-effort: flag squad players named in this team's punditry `fatigue_injuries`
    as 'doubt' (source guardian:punditry). Never overrides a manual row. Returns the
    number of new rows written. Surname-match within the punditry prose — imperfect by
    design (the post-match note is a weak pre-match signal)."""
    try:
        from worldcupagents.recall import punditry_for_team
        digests = punditry_for_team(profile.team, config)
    except Exception:  # noqa: BLE001
        return 0
    tk = normalize_key(profile.team)
    blobs: list[str] = []
    for d in digests:
        read = d.home_read if normalize_key(d.home) == tk else d.away_read
        blobs.extend(read.fatigue_injuries or [])
    text = " ".join(blobs).lower()
    if not text or not profile.squad:
        return 0
    store = MatchStore.from_config(config)
    written = 0
    try:
        for p in profile.squad:
            surname = (p.name or "").split()[-1].lower() if p.name else ""
            if len(surname) >= 4 and surname in text:
                if store.upsert_injury(profile.team, p.name, "doubt",
                                       note="flagged in match punditry",
                                       source="guardian:punditry", overwrite=False):
                    written += 1
    finally:
        store.close()
    return written
