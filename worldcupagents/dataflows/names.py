"""Team-name normalization + aliases.

User input ("USA"), ranking tables ("United States"), and vendor feeds
("Korea Republic") all spell nations differently. Everything funnels through
``normalize_key`` so lookups are robust; ``canonical_name`` gives a display form.
"""

from __future__ import annotations


def normalize_key(name: str) -> str:
    """Lowercase, trim, collapse internal whitespace. The matching key everywhere."""
    try:
        from worldcupagents.dataflows.entities import normalize_entity_key
        return normalize_entity_key(name)
    except Exception:
        return " ".join((name or "").strip().lower().split())


# normalized-input -> canonical display name. Add rows as feeds surprise you.
_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "united states of america": "United States",
    "south korea": "Korea Republic",
    "korea": "Korea Republic",
    "korea south": "Korea Republic",
    "north korea": "Korea DPR",
    "ir iran": "Iran",
    "iran": "Iran",
    "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
    "czech republic": "Czechia",
    "turkey": "Türkiye",
    "turkiye": "Türkiye",
    "holland": "Netherlands",
    "the netherlands": "Netherlands",
    "bosnia": "Bosnia and Herzegovina",
    "uae": "United Arab Emirates",
    "cape verde": "Cabo Verde",
    "drc": "DR Congo",
    "congo dr": "DR Congo",
}


def canonical_name(name: str) -> str:
    """Map a spelling to a canonical display name (identity if unknown)."""
    try:
        from worldcupagents.dataflows.entities import canonical_team_name
        resolved = canonical_team_name(name, kind="national")
        if resolved:
            return resolved
    except Exception:
        pass
    return _ALIASES.get(normalize_key(name), (name or "").strip())
