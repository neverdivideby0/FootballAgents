"""Coach / manager brief — surfaces the head coach's name (from the data vendor)
plus their style & pedigree in prose (from the Guardian Experts' Network guide).

The manager matters: a pragmatic low-block coach, a tournament-hardened tactician,
or a debutant under pressure all shift how a match plays out. This is the small,
sourced layer that lets the dossier, the analysts, and the debate weigh that.

Read at predict/dossier time only — never a network call. The note is populated by
``footballagents guardian-experts``; the name by the football-data squads provider
(``TeamProfile.coach``) or the same guide.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def coach_brief(config: dict, team: str, profile=None) -> dict | None:
    """{'name', 'note', 'source'} for a team's coach, or None if nothing is known.

    Name preference: the live profile (data vendor) first, then the stored guide.
    Note + source come from the stored coach note (the Guardian 'The coach' section).
    """
    name = getattr(profile, "coach", None) if profile is not None else None
    note = ""
    source = ""
    try:
        from worldcupagents.dataflows.match_store import MatchStore, db_path
        if db_path(config).exists():
            store = MatchStore.from_config(config)
            try:
                row = store.team_coach(team)
            finally:
                store.close()
            if row:
                name = name or row.get("name")
                note = (row.get("note") or "").strip()
                source = row.get("source") or ""
    except Exception as e:  # noqa: BLE001 — a missing coach must not break anything
        logger.warning("coach brief failed for %r (%s)", team, e)
    if not name and not note:
        return None
    return {"name": name, "note": note, "source": source}


def coach_digest(brief: dict | None, *, max_chars: int = 320) -> str:
    """One-line prompt/dossier string, e.g. 'Tuchel — pragmatic, CL-winning pedigree…'."""
    if not brief:
        return ""
    name = (brief.get("name") or "").strip()
    note = (brief.get("note") or "").strip()
    if note and len(note) > max_chars:
        note = note[:max_chars].rstrip() + "…"
    if name and note:
        return f"{name} — {note}"
    return name or note
