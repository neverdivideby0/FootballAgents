"""Curated FIFA-ranking snapshot used by the ensemble Elo baseline.

⚠️ This is a MANUALLY-CURATED APPROXIMATION, not an official live feed —
football-data.org does not expose FIFA rankings. It exists so the baseline has a
strength prior. Swap in a real ranking source (or replace the baseline with a
results-driven Elo) in M4. Order ≈ men's ranking as of early 2026.
"""

from __future__ import annotations

from worldcupagents.dataflows.names import canonical_name, normalize_key

RANKING_AS_OF = "2026-04 (curated approximation — NOT an official feed)"

# index + 1 == rank
_RANKING = [
    "Argentina", "France", "Spain", "England", "Brazil",
    "Portugal", "Netherlands", "Belgium", "Italy", "Germany",
    "Croatia", "Morocco", "Colombia", "Uruguay", "United States",
    "Mexico", "Switzerland", "Senegal", "Japan", "Denmark",
    "Iran", "Korea Republic", "Australia", "Ecuador", "Austria",
    "Ukraine", "Sweden", "Wales", "Poland", "Serbia",
    "Egypt", "Hungary", "Nigeria", "Peru", "Czechia",
    "Norway", "Scotland", "Canada", "Tunisia", "Costa Rica",
    "Algeria", "Cameroon", "Ghana", "Paraguay", "Saudi Arabia",
    "Qatar", "Côte d'Ivoire", "Panama", "Greece", "Türkiye",
]

# Beyond the top-50 list: explicit ranks for the remaining WC2026 qualifiers
# (and a few neighbours), so no qualified nation is ever modelled as unranked —
# previously these fell back to a mid-table prior (≈ rank 51), which made e.g.
# France vs Curaçao look like France vs a top-50 side.
_EXTENDED_RANKS = {
    "Uzbekistan": 55,
    "South Africa": 57,
    "Iraq": 58,
    "DR Congo": 60,
    "Jordan": 64,
    "Bosnia and Herzegovina": 68,
    "Cabo Verde": 71,
    "Curaçao": 82,
    "New Zealand": 86,
    "Haiti": 88,
}

_RANK_BY_KEY = {normalize_key(n): i + 1 for i, n in enumerate(_RANKING)}
_RANK_BY_KEY.update({normalize_key(n): r for n, r in _EXTENDED_RANKS.items()})


def get_rank(team: str) -> int | None:
    """Return the curated rank for ``team`` (alias-aware), or None if unranked."""
    return _RANK_BY_KEY.get(normalize_key(canonical_name(team)))
