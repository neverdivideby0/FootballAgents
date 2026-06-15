"""football-data.co.uk multi-season results ingester (DATA_PLAN data-volume lever).

Free historical results CSVs (no key). Used to seed the match store with many
seasons so head-to-head records run deep and the strength fit has real volume.
Maps short club names to canonical via club_aliases so rows merge with the
football-data.org live feed.
"""

from __future__ import annotations

import csv
import io
import logging

import httpx

from worldcupagents.dataflows.club_aliases import canon_club

logger = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk/mmz4281"
# our competition code -> football-data.co.uk division code
COUK_CODE = {"PL": "E0", "PD": "SP1", "SA": "I1", "BL1": "D1", "FL1": "F1"}


def season_url(comp: str, season: str) -> str:
    """e.g. comp='PL', season='2324' -> .../mmz4281/2324/E0.csv"""
    return f"{BASE}/{season}/{COUK_CODE[comp]}.csv"


def _iso_date(s: str | None) -> str | None:
    """'11/08/2023' or '11/08/23' -> '2023-08-11'."""
    if not s:
        return None
    parts = s.strip().split("/")
    if len(parts) != 3:
        return None
    d, m, y = parts
    if len(y) == 2:
        y = "20" + y
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _odds(r: dict) -> tuple[float | None, float | None, float | None]:
    """1X2 closing odds — prefer Bet365, fall back to market average, then Pinnacle."""
    for h, d, a in (("B365H", "B365D", "B365A"), ("AvgH", "AvgD", "AvgA"), ("PSH", "PSD", "PSA")):
        try:
            oh, od, oa = float(r[h]), float(r[d]), float(r[a])
            if oh > 1 and od > 1 and oa > 1:
                return oh, od, oa
        except (KeyError, ValueError, TypeError):
            continue
    return None, None, None


# football-data.co.uk per-match stat columns → our match-store fields. Free,
# already in the CSV we download — covers shots, shots on target, fouls,
# corners, and cards with no scraping.
_STAT_MAP = {
    "sh_home": "HS", "sh_away": "AS", "sot_home": "HST", "sot_away": "AST",
    "fouls_home": "HF", "fouls_away": "AF", "corners_home": "HC", "corners_away": "AC",
    "yellow_home": "HY", "yellow_away": "AY", "red_home": "HR", "red_away": "AR",
}


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_csv(text: str, comp: str, season: str) -> list[dict]:
    rows: list[dict] = []
    for r in csv.DictReader(io.StringIO(text)):
        home, away = r.get("HomeTeam"), r.get("AwayTeam")
        if not home or not away:
            continue
        try:
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
        except (KeyError, ValueError, TypeError):
            continue  # postponed/blank rows
        oh, od, oa = _odds(r)
        row = {
            "date": _iso_date(r.get("Date")),
            "comp": comp,
            "home": canon_club(home),
            "away": canon_club(away),
            "hg": hg, "ag": ag, "xg_home": None, "xg_away": None,
            "odds_h": oh, "odds_d": od, "odds_a": oa,
            "source": f"fdcouk:{comp}:{season}",
        }
        row.update({field: _int(r.get(col)) for field, col in _STAT_MAP.items()})
        rows.append(row)
    return rows


def fetch_season_rows(comp: str, season: str, http_get=None) -> list[dict]:
    """Download + parse one season; [] on unsupported comp or network error."""
    if comp not in COUK_CODE:
        logger.warning("football-data.co.uk has no division for %s", comp)
        return []
    url = season_url(comp, season)
    try:
        text = http_get(url) if http_get else httpx.get(url, timeout=30, follow_redirects=True).text
    except Exception as e:  # noqa: BLE001
        logger.warning("football-data.co.uk fetch failed for %s/%s (%s)", comp, season, e)
        return []
    return parse_csv(text, comp, season)
