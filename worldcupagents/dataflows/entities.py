"""Entity identity and naming resolution.

This is the single resolver for source-specific football team spellings. It
preserves raw source text in data rows while mapping names to stable internal
IDs such as ``national:united_states`` and ``club:manchester_city_fc``.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from worldcupagents.dataflows.match_store import MatchStore, db_path


@dataclass(frozen=True)
class EntityResolution:
    raw_name: str
    team_id: str | None
    canonical_name: str
    kind: str
    status: str
    source_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
    candidates: tuple[str, ...] = ()


def normalize_entity_key(name: str) -> str:
    """Accent-insensitive, punctuation-light key for deterministic alias lookup."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def entity_slug(name: str) -> str:
    key = normalize_entity_key(name)
    return "_".join(key.split()) or "unknown"


_NATIONAL_ALIAS_SEEDS = {
    "United States": ["USA", "US", "United States of America"],
    "Korea Republic": ["South Korea", "Korea", "Korea South"],
    "Korea DPR": ["North Korea"],
    "Iran": ["IR Iran"],
    "Côte d'Ivoire": ["Ivory Coast", "Cote d'Ivoire"],
    "Czechia": ["Czech Republic"],
    "Türkiye": ["Turkey", "Turkiye"],
    "Netherlands": ["Holland", "The Netherlands"],
    "Bosnia and Herzegovina": ["Bosnia", "Bosnia-Herzegovina", "Bosnia & Herzegovina"],
    "United Arab Emirates": ["UAE"],
    "Cabo Verde": ["Cape Verde", "Cape Verde Islands"],
    "DR Congo": ["DRC", "Congo DR", "Belgian Congo"],
}

_CLUB_ALIAS_SEEDS = {
    "Arsenal FC": ["Arsenal"],
    "Aston Villa FC": ["Aston Villa"],
    "AFC Bournemouth": ["Bournemouth"],
    "Brentford FC": ["Brentford"],
    "Brighton & Hove Albion FC": ["Brighton"],
    "Burnley FC": ["Burnley"],
    "Chelsea FC": ["Chelsea"],
    "Crystal Palace FC": ["Crystal Palace"],
    "Everton FC": ["Everton"],
    "Fulham FC": ["Fulham"],
    "Ipswich Town FC": ["Ipswich"],
    "Leeds United FC": ["Leeds", "Leeds United"],
    "Leicester City FC": ["Leicester"],
    "Liverpool FC": ["Liverpool"],
    "Luton Town FC": ["Luton"],
    "Manchester City FC": ["Man City", "Manchester City"],
    "Manchester United FC": ["Man United", "Manchester United"],
    "Newcastle United FC": ["Newcastle", "Newcastle United"],
    "Norwich City FC": ["Norwich"],
    "Nottingham Forest FC": ["Nott'm Forest", "Nottingham Forest"],
    "Sheffield United FC": ["Sheffield United"],
    "Southampton FC": ["Southampton"],
    "Sunderland AFC": ["Sunderland"],
    "Tottenham Hotspur FC": ["Tottenham", "Tottenham Hotspur"],
    "Watford FC": ["Watford"],
    "West Bromwich Albion FC": ["West Brom"],
    "West Ham United FC": ["West Ham", "West Ham United"],
    "Wolverhampton Wanderers FC": ["Wolves", "Wolverhampton Wanderers"],
}


def stable_team_id(name: str, kind: str = "unknown") -> str:
    return f"{kind}:{entity_slug(name)}"


def _seed_aliases(kind: str | None = None) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if kind in (None, "national", "unknown"):
        for canonical, aliases in _NATIONAL_ALIAS_SEEDS.items():
            out.append(("national", canonical, canonical))
            out.extend(("national", canonical, a) for a in aliases)
    if kind in (None, "club", "unknown"):
        for canonical, aliases in _CLUB_ALIAS_SEEDS.items():
            out.append(("club", canonical, canonical))
            out.extend(("club", canonical, a) for a in aliases)
    return out


def _fallback_canonical(name: str, kind: str | None = None) -> tuple[str, str]:
    if kind == "club":
        for k, canonical, alias in _seed_aliases("club"):
            if normalize_entity_key(name) == normalize_entity_key(alias):
                return canonical, k
        return (name or "").strip(), "club"
    if kind == "national" or kind is None:
        for k, seeded, alias in _seed_aliases("national"):
            if normalize_entity_key(name) == normalize_entity_key(alias):
                return seeded, k
        return (name or "").strip(), "national" if kind == "national" else "unknown"
    return (name or "").strip(), kind or "unknown"


def _matches_kind(candidate_kind: str | None, requested: str | None) -> bool:
    return requested in (None, "unknown") or candidate_kind in (requested, None, "unknown")


@lru_cache(maxsize=16)
def _cached_alias_rows(path: str, mtime_ns: int) -> tuple[dict, ...]:
    store = MatchStore(path)
    try:
        return tuple(store.team_alias_rows())
    finally:
        store.close()


def _alias_rows(config: dict | None) -> list[dict]:
    if not config:
        return []
    try:
        path = db_path(config)
        if not path.exists():
            return []
        return list(_cached_alias_rows(str(path), path.stat().st_mtime_ns))
    except Exception:
        return []


def clear_entity_cache() -> None:
    _cached_alias_rows.cache_clear()


def resolve_team(
    name: str,
    kind: str | None = None,
    source_id: str | None = None,
    config: dict | None = None,
    record_unresolved: bool = False,
    context: str | None = None,
) -> EntityResolution:
    raw = (name or "").strip()
    alias_norm = normalize_entity_key(raw)
    if not raw:
        return EntityResolution(raw, None, "", kind or "unknown", "unresolved", source_id, reason="blank")

    rows = _alias_rows(config)

    def row_ok(r: dict) -> bool:
        return (
            (r.get("alias_norm") or normalize_entity_key(r.get("alias") or "")) == alias_norm
            and (r.get("status") or "active") == "active"
            and _matches_kind(r.get("kind"), kind)
        )

    source_hits = [r for r in rows if row_ok(r) and source_id and r.get("source_id") == source_id]
    if source_id:
        global_hits = [r for r in rows if row_ok(r) and r.get("source_id") in (None, "seed")]
    else:
        seed_hits = [r for r in rows if row_ok(r) and r.get("source_id") in (None, "seed")]
        global_hits = seed_hits or [r for r in rows if row_ok(r)]
    hits = source_hits or global_hits

    if hits:
        team_ids = sorted({r["team_id"] for r in hits})
        if len(team_ids) == 1:
            best = sorted(hits, key=lambda r: r.get("confidence") or 0, reverse=True)[0]
            return EntityResolution(
                raw, best["team_id"], best.get("name") or raw, best.get("kind") or kind or "unknown",
                "resolved", source_id, float(best.get("confidence") or 1.0), "alias",
            )
        res = EntityResolution(
            raw, None, raw, kind or "unknown", "ambiguous", source_id, 0.0,
            "alias_ambiguous", tuple(team_ids),
        )
        if record_unresolved and config:
            record_unresolved_name(res, config, context=context)
        return res

    for seeded_kind, canonical, alias in _seed_aliases(kind):
        if normalize_entity_key(alias) == alias_norm:
            team_id = stable_team_id(canonical, seeded_kind)
            return EntityResolution(raw, team_id, canonical, seeded_kind, "resolved", source_id, 0.95, "seed")

    canonical, resolved_kind = _fallback_canonical(raw, kind)
    team_id = stable_team_id(canonical, resolved_kind)
    return EntityResolution(raw, team_id, canonical, resolved_kind, "created", source_id, 0.5, "fallback")


def canonical_team_name(name: str, kind: str | None = None,
                        source_id: str | None = None, config: dict | None = None) -> str:
    return resolve_team(name, kind=kind, source_id=source_id, config=config).canonical_name


def team_id_for(name: str, kind: str | None = None,
                source_id: str | None = None, config: dict | None = None) -> str | None:
    return resolve_team(name, kind=kind, source_id=source_id, config=config).team_id


def record_alias(
    team_id: str,
    alias: str,
    source_id: str | None,
    confidence: float = 1.0,
    status: str = "active",
    notes: str | None = None,
    config: dict | None = None,
) -> None:
    if not config:
        return
    store = MatchStore.from_config(config)
    try:
        store.upsert_wh_team_alias(
            team_id, alias, source_id, normalize_entity_key(alias),
            confidence=confidence, status=status, notes=notes,
        )
    finally:
        store.close()
    clear_entity_cache()


def record_unresolved_name(resolution: EntityResolution, config: dict, context: str | None = None) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    unresolved_id = "|".join([
        resolution.source_id or "*",
        resolution.kind or "unknown",
        normalize_entity_key(resolution.raw_name),
        context or "",
    ])
    store = MatchStore.from_config(config)
    try:
        store.record_unresolved_name({
            "unresolved_id": unresolved_id,
            "raw_name": resolution.raw_name,
            "name_norm": normalize_entity_key(resolution.raw_name),
            "kind": resolution.kind,
            "source_id": resolution.source_id,
            "context": context,
            "reason": resolution.reason or resolution.status,
            "candidates_json": list(resolution.candidates),
            "first_seen": now,
            "last_seen": now,
        })
    finally:
        store.close()
    clear_entity_cache()


def seed_identity_registry(config: dict) -> dict[str, int]:
    """Persist manual seed aliases into the warehouse registry."""
    store = MatchStore.from_config(config)
    teams = aliases = 0
    try:
        for kind, canonical, alias in _seed_aliases(None):
            team_id = stable_team_id(canonical, kind)
            store.upsert_wh_team(team_id, canonical, kind=kind, source_id="seed", source_name=canonical)
            store.upsert_wh_team_alias(
                team_id, alias, "seed", normalize_entity_key(alias),
                confidence=1.0, status="active", notes="manual seed",
            )
            teams += 1 if alias == canonical else 0
            aliases += 1
    finally:
        store.close()
    clear_entity_cache()
    return {"seed_teams": teams, "seed_aliases": aliases}


def same_team(a: str, b: str, kind: str | None = None, config: dict | None = None) -> bool:
    aid = team_id_for(a, kind=kind, config=config)
    bid = team_id_for(b, kind=kind, config=config)
    if aid and bid:
        return aid == bid
    return normalize_entity_key(a) == normalize_entity_key(b)


def resolution_to_json(resolution: EntityResolution) -> str:
    return json.dumps({
        "raw_name": resolution.raw_name,
        "team_id": resolution.team_id,
        "canonical_name": resolution.canonical_name,
        "kind": resolution.kind,
        "status": resolution.status,
        "source_id": resolution.source_id,
        "confidence": resolution.confidence,
        "reason": resolution.reason,
        "candidates": list(resolution.candidates),
    }, indent=2, sort_keys=True)
