"""Club-name aliases: football-data.co.uk short names -> football-data.org canonical.

So "Man City" (co.uk results CSVs) and "Manchester City FC" (the .org squad/live
feed) resolve to ONE key in the match store, strength model, and H2H records.
Unmapped names pass through unchanged (fine for internal consistency).
"""

from __future__ import annotations

from worldcupagents.dataflows.names import normalize_key

# co.uk short name -> canonical (football-data.org) name. Covers current PL + the
# clubs that have featured in recent Premier League seasons.
_CLUB_ALIASES_RAW = {
    "Arsenal": "Arsenal FC",
    "Aston Villa": "Aston Villa FC",
    "Bournemouth": "AFC Bournemouth",
    "Brentford": "Brentford FC",
    "Brighton": "Brighton & Hove Albion FC",
    "Burnley": "Burnley FC",
    "Chelsea": "Chelsea FC",
    "Crystal Palace": "Crystal Palace FC",
    "Everton": "Everton FC",
    "Fulham": "Fulham FC",
    "Ipswich": "Ipswich Town FC",
    "Leeds": "Leeds United FC",
    "Leicester": "Leicester City FC",
    "Liverpool": "Liverpool FC",
    "Luton": "Luton Town FC",
    "Man City": "Manchester City FC",
    "Man United": "Manchester United FC",
    "Newcastle": "Newcastle United FC",
    "Norwich": "Norwich City FC",
    "Nott'm Forest": "Nottingham Forest FC",
    "Sheffield United": "Sheffield United FC",
    "Southampton": "Southampton FC",
    "Sunderland": "Sunderland AFC",
    "Tottenham": "Tottenham Hotspur FC",
    "Watford": "Watford FC",
    "West Brom": "West Bromwich Albion FC",
    "West Ham": "West Ham United FC",
    "Wolves": "Wolverhampton Wanderers FC",
}

# Understat (and other sources) use full names without the FC suffix — map those too.
_FULL_NO_FC = {
    "Arsenal": "Arsenal FC", "Aston Villa": "Aston Villa FC", "Brentford": "Brentford FC",
    "Chelsea": "Chelsea FC", "Crystal Palace": "Crystal Palace FC", "Everton": "Everton FC",
    "Fulham": "Fulham FC", "Liverpool": "Liverpool FC", "Leeds United": "Leeds United FC",
    "Manchester City": "Manchester City FC", "Manchester United": "Manchester United FC",
    "Newcastle United": "Newcastle United FC", "Nottingham Forest": "Nottingham Forest FC",
    "Sunderland": "Sunderland AFC", "Tottenham Hotspur": "Tottenham Hotspur FC",
    "West Ham United": "West Ham United FC", "Wolverhampton Wanderers": "Wolverhampton Wanderers FC",
    "Bournemouth": "AFC Bournemouth", "Burnley": "Burnley FC",
}

_CLUB_ALIASES = {normalize_key(k): v for k, v in _CLUB_ALIASES_RAW.items()}
_CLUB_ALIASES.update({normalize_key(k): v for k, v in _FULL_NO_FC.items()})


# Understat uses shorter labels than the canonical names for a handful of clubs.
_UNDERSTAT_OVERRIDES = {
    "West Ham United FC": "West Ham",
    "Brighton & Hove Albion FC": "Brighton",
    "Tottenham Hotspur FC": "Tottenham",
    "Wolverhampton Wanderers FC": "Wolverhampton Wanderers",
}


def understat_name(canonical: str) -> str:
    """Canonical club name -> Understat's name (full, no FC suffix)."""
    if canonical in _UNDERSTAT_OVERRIDES:
        return _UNDERSTAT_OVERRIDES[canonical]
    for u, c in _FULL_NO_FC.items():
        if c == canonical:
            return u
    # heuristic fallback: strip a trailing FC/AFC
    n = canonical.removesuffix(" FC").removesuffix(" AFC").strip()
    return n


def canon_club(name: str) -> str:
    """Map a (possibly short) club name to its canonical form; passthrough if unknown."""
    try:
        from worldcupagents.dataflows.entities import canonical_team_name
        resolved = canonical_team_name(name, kind="club")
        if resolved:
            return resolved
    except Exception:
        pass
    return _CLUB_ALIASES.get(normalize_key(name), name)
