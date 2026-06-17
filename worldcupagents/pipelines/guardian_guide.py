"""Guardian World Cup 2026 player guide ingester — the qualitative gold mine.

The Guardian's interactive player guide is backed by a public "docsdata" feed (a
published Google Sheet). One Teams sheet (48 nations: bio, strengths, weaknesses,
coach, key player) links to a per-team Players sheet (~26 each: prose bio,
position, club, caps, DOB, key-player tag). That's ~1,250 sourced player profiles
plus a tactical brief for every team — exactly the qualitative layer the model
couldn't get from stats.

Team rows  → the qualitative warehouse (team-linked → tactical analyst + dossier).
Player rows → per-player notes (→ player analyst + dossier, squad-scoped).
Player DOBs → a team average-age line (the previously-missing structured field).

Source: theguardian.com/football/ng-interactive/2026/jun/04/world-cup-2026-complete-player-guide
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.names import canonical_name

logger = logging.getLogger(__name__)

# The Teams docsdata id is baked into the interactive's app.js (stable per build).
TEAMS_SHEET = "1_ZAfmUkTZ4BvDgvhEGaEruakfu4aWIIjjzXaMAiT1yc"
_DOCSDATA = "https://interactive.guim.co.uk/docsdata/{sid}.json"
SOURCE = "The Guardian WC2026 player guide"


@dataclass
class GuideResult:
    teams: int = 0
    players: int = 0
    coaches: int = 0
    errors: int = 0


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(s.replace("’", "'").split())


def _age(dob: str | None) -> int | None:
    """dd/mm/yyyy → age in years (today), or None."""
    if not dob:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            d = datetime.strptime(dob.strip(), fmt).date()
            today = date.today()
            return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        except ValueError:
            continue
    return None


def _docsdata(sid: str, fetch_json=None) -> dict:
    if fetch_json is not None:
        return fetch_json(_DOCSDATA.format(sid=sid))
    from worldcupagents.pipelines.hoard_data import _fetch_json
    return _fetch_json(_DOCSDATA.format(sid=sid))


def _player_note(p: dict) -> str:
    """One readable scouting line from a player row."""
    pos = _strip_html(p.get("position"))
    club = _strip_html(p.get("club"))
    caps, gls = _strip_html(p.get("caps")), _strip_html(p.get("goals for country"))
    special = _strip_html(p.get("special player? (eg. key player, promising talent, etc) OPTIONAL"))
    age = _age(p.get("date of birth"))
    head = ", ".join(x for x in [
        pos or None, club or None,
        f"{age}y" if age else None,
        (f"{caps} caps" + (f"/{gls} gls" if gls else "")) if caps else None,
    ] if x)
    bio = _strip_html(p.get("bio"))[:600]
    parts = [head] if head else []
    if special:
        parts.append(special)
    if bio:
        parts.append(bio)
    return ". ".join(parts)


def _team_note(row: dict, avg_age: float | None) -> str:
    bits = [_strip_html(row.get("Bio"))]
    for label, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses"),
                       ("Key player", "player_pick"), ("Coach", "Coach")):
        v = _strip_html(row.get(key))
        if v:
            bits.append(f"{label}: {v}")
    if avg_age:
        bits.append(f"Average age: {avg_age:.1f}")
    return " ".join(b for b in bits if b)


def ingest_guardian_player_guide(config: dict | None = None, fetch_json=None,
                                 limit: int | None = None) -> GuideResult:
    """Populate team qualitative notes + per-player notes from the Guardian guide.
    ``fetch_json`` is injectable for tests; ``limit`` caps teams (for a quick run)."""
    config = dict(config or DEFAULT_CONFIG)
    from worldcupagents.pipelines.qualitative_data import ingest_manual_note

    res = GuideResult()
    try:
        teams = _docsdata(TEAMS_SHEET, fetch_json)["sheets"]["Teams"]
    except Exception as e:  # noqa: BLE001
        logger.warning("guardian guide: Teams sheet fetch failed (%s)", e)
        return res

    store = MatchStore.from_config(config)
    try:
        for row in teams[: limit or len(teams)]:
            raw_team = (row.get("Team") or "").strip()
            if not raw_team:
                continue
            team = canonical_name(raw_team)
            ages: list[int] = []
            # Per-team player sheet.
            sid = (row.get("spreadsheet") or "").strip()
            if sid:
                try:
                    players = _docsdata(sid, fetch_json)["sheets"].get("Players", [])
                except Exception as e:  # noqa: BLE001 — one team must not sink the run
                    logger.warning("guardian guide: players sheet failed for %s (%s)", team, e)
                    players = []
                    res.errors += 1
                for p in players:
                    name = (p.get("name") or "").strip()
                    note = _player_note(p)
                    if not name or not note:
                        continue
                    store.upsert_player_note(team, name, note, source=SOURCE)
                    res.players += 1
                    a = _age(p.get("date of birth"))
                    if a:
                        ages.append(a)
                if fetch_json is None:
                    time.sleep(0.3)  # polite between team sheets
            # Coach NAME for all 48 (the player guide covers every nation) → the
            # structured coach store, so each team has a coach line in the dossier.
            # The richer style/pedigree PROSE comes from the Experts' Network guide;
            # upsert merges name + note without clobbering either's provenance.
            coach_name = _strip_html(row.get("Coach"))
            if coach_name:
                try:
                    store.upsert_team_coach(team, name=coach_name, source=SOURCE)
                    res.coaches += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("guardian guide: coach upsert failed for %s (%s)", team, e)
                    res.errors += 1
            # Team-level qualitative note → warehouse (team-linked).
            avg_age = round(sum(ages) / len(ages), 1) if ages else None
            note = _team_note(row, avg_age)
            if note:
                try:
                    ingest_manual_note(note, config=config, teams=[team],
                                       title=f"Guardian guide: {team}", author="The Guardian")
                    res.teams += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("guardian guide: team note failed for %s (%s)", team, e)
                    res.errors += 1
    finally:
        store.close()
    return res
