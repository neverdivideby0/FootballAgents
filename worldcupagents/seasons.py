"""Season utilities (WS-C) — one canonical season form, many spellings.

Canonical: "2025-26". Accepts "2025–26" (en-dash), "2526" (football-data.co.uk),
"2025/26", "2025". A European club season runs 1 July → 30 June.

Semantics used across the app for a selected season S:
  * cutoff   — nothing AFTER S's end is visible (no future leakage when
               examining a past season): records, H2H, strength fits.
  * window   — recent FORM is scoped to within S itself.
"""

from __future__ import annotations

import re


def normalize_season(s: str) -> str:
    """Any accepted spelling -> '2025-26'. Raises ValueError on nonsense."""
    raw = (s or "").strip().replace("–", "-").replace("/", "-")
    m = re.fullmatch(r"(\d{4})-(\d{2})", raw)
    if m:
        start, yy = int(m.group(1)), m.group(2)
        if int(yy) != (start + 1) % 100:
            raise ValueError(f"Season years not consecutive: {s!r}")
        return f"{start}-{yy}"
    m = re.fullmatch(r"(\d{2})(\d{2})", raw)  # ambiguous 4 digits: fdcouk code or a year
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b == (a + 1) % 100:                # "2526" -> consecutive pair = fdcouk code
            return f"20{m.group(1)}-{m.group(2)}"
        year = int(raw)
        if 1900 <= year <= 2099:              # "2025" -> a start year
            return f"{year}-{(year + 1) % 100:02d}"
    raise ValueError(f"Unrecognised season {s!r} (try '2025-26')")


def season_range(season: str) -> tuple[str, str]:
    """'2025-26' -> ('2025-07-01', '2026-06-30') — ISO dates, string-comparable."""
    s = normalize_season(season)
    start = int(s[:4])
    return f"{start}-07-01", f"{start + 1}-06-30"


def season_cutoff(season: str) -> str:
    """The no-future-leakage date: the season's last day."""
    return season_range(season)[1]


def season_to_fdcouk(season: str) -> str:
    """'2025-26' -> '2526' (football-data.co.uk season code)."""
    s = normalize_season(season)
    return s[2:4] + s[5:7]


def season_dash(season: str) -> str:
    """'2025-26' -> '2025–26' (EN-dash, as Wikipedia titles use)."""
    s = normalize_season(season)
    return s.replace("-", "–")
