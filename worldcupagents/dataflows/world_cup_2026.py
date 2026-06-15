"""FIFA World Cup 2026 — teams, venues, and elimination state.

Single source of truth for the 48 qualified nations and 16 host venues.
Elimination state is persisted to ``memory/wc2026_state.json`` so it
survives CLI sessions and can be updated as the tournament progresses
via ``worldcupagents eliminate TEAM [TEAM…]``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 48 Qualified Teams ─────────────────────────────────────────────────────
# Grouped by confederation for a clean presentation in the arrow-key picker.
# Sources: FIFA, per-confederation qualifiers — verified to qualification end 2025.

TEAMS_BY_CONF: dict[str, list[str]] = {
    "UEFA (Europe)": [
        "Austria",
        "Belgium",
        "Bosnia and Herzegovina",
        "Croatia",
        "Czech Republic",
        "England",
        "France",
        "Germany",
        "Netherlands",
        "Norway",
        "Portugal",
        "Scotland",
        "Spain",
        "Sweden",
        "Switzerland",
        "Turkey",
    ],
    "CONMEBOL (S. America)": [
        "Argentina",
        "Brazil",
        "Colombia",
        "Ecuador",
        "Paraguay",
        "Uruguay",
    ],
    "CONCACAF": [
        "Canada",
        "Curaçao",
        "Haiti",
        "Mexico",
        "Panama",
        "United States",
    ],
    "CAF (Africa)": [
        "Algeria",
        "Cape Verde",
        "DR Congo",
        "Egypt",
        "Ghana",
        "Ivory Coast",
        "Morocco",
        "Senegal",
        "South Africa",
        "Tunisia",
    ],
    "AFC (Asia)": [
        "Australia",
        "Iran",
        "Iraq",
        "Japan",
        "Jordan",
        "Qatar",
        "Saudi Arabia",
        "South Korea",
        "Uzbekistan",
    ],
    "OFC (Oceania)": [
        "New Zealand",
    ],
}

# Flat alphabetical list (used for name lookup / validation)
WC2026_TEAMS: list[str] = sorted(
    t for teams in TEAMS_BY_CONF.values() for t in teams
)


# ── 16 Host Venues ─────────────────────────────────────────────────────────
# display_name -> {"stadium": ..., "country": ..., "note": ...}
# note is shown in the judge's x-factor prompt; align keys with pundit._VENUE_NOTES.

WC2026_VENUES: dict[str, dict] = {
    "Atlanta": {
        "stadium": "Mercedes-Benz Stadium",
        "country": "USA",
        "note": "semi-final venue",
    },
    "Boston": {
        "stadium": "Gillette Stadium",
        "country": "USA",
        "note": "",
    },
    "Dallas": {
        "stadium": "AT&T Stadium",
        "country": "USA",
        "note": "semi-final venue; heat (roofed)",
    },
    "Guadalajara": {
        "stadium": "Estadio Akron",
        "country": "Mexico",
        "note": "altitude (~1560m)",
    },
    "Houston": {
        "stadium": "NRG Stadium",
        "country": "USA",
        "note": "heat & humidity (roofed)",
    },
    "Kansas City": {
        "stadium": "Arrowhead Stadium",
        "country": "USA",
        "note": "",
    },
    "Los Angeles": {
        "stadium": "SoFi Stadium",
        "country": "USA",
        "note": "",
    },
    "Mexico City": {
        "stadium": "Estadio Azteca",
        "country": "Mexico",
        "note": "high altitude (~2240m); opening match venue",
    },
    "Miami": {
        "stadium": "Hard Rock Stadium",
        "country": "USA",
        "note": "heat & humidity",
    },
    "Monterrey": {
        "stadium": "Estadio BBVA",
        "country": "Mexico",
        "note": "summer heat",
    },
    "New York/New Jersey": {
        "stadium": "MetLife Stadium",
        "country": "USA",
        "note": "FINAL venue",
    },
    "Philadelphia": {
        "stadium": "Lincoln Financial Field",
        "country": "USA",
        "note": "",
    },
    "San Francisco": {
        "stadium": "Levi's Stadium",
        "country": "USA",
        "note": "",
    },
    "Seattle": {
        "stadium": "Lumen Field",
        "country": "USA",
        "note": "",
    },
    "Toronto": {
        "stadium": "BMO Field",
        "country": "Canada",
        "note": "",
    },
    "Vancouver": {
        "stadium": "BC Place",
        "country": "Canada",
        "note": "semi-final venue",
    },
}

# Venue notes keyed by city (for pundit x-factor injection)
VENUE_NOTES: dict[str, str] = {
    city: info["note"]
    for city, info in WC2026_VENUES.items()
    if info["note"]
}


# ── Elimination State ──────────────────────────────────────────────────────

def _state_path(config: dict | None = None) -> Path:
    if config:
        return Path(config.get("wc2026_state_path", "memory/wc2026_state.json"))
    return Path("memory/wc2026_state.json")


def load_eliminated(config: dict | None = None) -> set[str]:
    """Return the set of currently eliminated teams (empty before the tournament)."""
    p = _state_path(config)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("eliminated", []))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read WC2026 state file (%s): %s — using empty eliminated set", p, e)
        return set()


def save_eliminated(eliminated: set[str], config: dict | None = None) -> None:
    """Persist the current eliminated set to disk."""
    p = _state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "eliminated": sorted(eliminated),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def add_eliminated(teams: list[str], config: dict | None = None) -> set[str]:
    """Add teams to the eliminated set; returns the updated set."""
    current = load_eliminated(config)
    current.update(teams)
    save_eliminated(current, config)
    return current


def remove_eliminated(teams: list[str], config: dict | None = None) -> set[str]:
    """Remove teams from the eliminated set; returns the updated set."""
    current = load_eliminated(config)
    current.difference_update(teams)
    save_eliminated(current, config)
    return current


def reset_eliminated(config: dict | None = None) -> None:
    """Clear all eliminated teams."""
    save_eliminated(set(), config)
