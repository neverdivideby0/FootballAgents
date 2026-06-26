"""Public football data hoarding pipeline.

First wave: martj42/international_results (CC0 men's full internationals).
The pipeline keeps raw source snapshots under data/raw/ and normalizes into
warehouse tables while still feeding the existing model-facing summary tables.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.match_store import MatchStore
from worldcupagents.dataflows.entities import (
    canonical_team_name,
    normalize_entity_key,
    resolve_team,
    seed_identity_registry,
    stable_team_id,
)

SOURCE_INTERNATIONAL_RESULTS = "international_results"
SOURCE_WIKIPEDIA_PLAYER_TOTALS = "wikipedia_player_totals"
SOURCE_STATSBOMB_OPEN_DATA = "statsbomb_open_data"
_BASE = "https://raw.githubusercontent.com/martj42/international_results/master"
_FILES = ("results.csv", "goalscorers.csv", "former_names.csv")
_STATSBOMB_BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


@dataclass
class HoardResult:
    source: str
    snapshot: str
    raw_dir: str
    counts: dict


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _snapshot_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _slug(value: str) -> str:
    key = normalize_entity_key(canonical_team_name(value, kind="national"))
    return "".join(ch if ch.isalnum() else "_" for ch in key).strip("_") or "unknown"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_csv(path: Path, limit: int | None = None) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[:limit] if limit else rows


def _fetch_file(url: str, dest: Path, refresh: bool) -> None:
    if dest.exists() and not refresh:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "WorldCupAgents/0.2 public-data-hoarder"})
    with urlopen(req, timeout=30) as r, dest.open("wb") as f:  # noqa: S310 - fixed public URLs
        shutil.copyfileobj(r, f)


def _fetch_text(url: str, retries: int = 3) -> str:
    """Fetch with simple retry/backoff — multi-MB event files over flaky links
    were killing whole ingestion runs on a single read timeout."""
    req = Request(url, headers={
        "User-Agent": "WorldCupAgents/0.2 public-data-hoarder (personal research; polite cache)",
        "Accept": "application/json,text/plain,*/*",
    })
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=60) as r:  # noqa: S310 - fixed public URLs / caller-provided public page
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 — timeouts/resets: back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def _fetch_json(url: str) -> dict | list:
    return json.loads(_fetch_text(url))


def _team_id(name: str) -> str:
    return stable_team_id(canonical_team_name(name, kind="national"), "national")


def _competition_id(name: str) -> str:
    return f"competition:{_slug(name)}"


def _match_id(row: dict) -> str:
    return "|".join([
        row.get("date") or "",
        _team_id(row.get("home_team") or ""),
        _team_id(row.get("away_team") or ""),
        str(row.get("home_score") or ""),
        str(row.get("away_score") or ""),
        _competition_id(row.get("tournament") or "International"),
    ])


def _match_lookup_key(date: str | None, home: str | None, away: str | None) -> str:
    return f"{date or ''}|{_team_id(home or '')}|{_team_id(away or '')}"


def _team_resolution(name: str):
    return resolve_team(name or "", kind="national", source_id=SOURCE_INTERNATIONAL_RESULTS)


def _boolish(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return 1 if str(value).strip().lower() in ("true", "1", "yes") else 0


def _completed(row: dict) -> bool:
    return row.get("home_score") not in ("", "NA", None) and row.get("away_score") not in ("", "NA", None)


def _source_file_id(source: str, snapshot: str, name: str) -> str:
    return f"{source}:{snapshot}:{name}"


def hoard_data(
    config: dict | None = None,
    source: str = SOURCE_INTERNATIONAL_RESULTS,
    refresh: bool = False,
    populate_summary: bool = True,
    limit_source: int | None = None,
) -> HoardResult:
    if source == "all":
        # Keep "all" conservative: public open-file match history only. Richer
        # sources can be large and are run explicitly.
        source = SOURCE_INTERNATIONAL_RESULTS
    if source == SOURCE_INTERNATIONAL_RESULTS:
        return hoard_international_results(
            config=config,
            refresh=refresh,
            populate_summary=populate_summary,
            limit_source=limit_source,
        )
    if source == SOURCE_WIKIPEDIA_PLAYER_TOTALS:
        return hoard_wikipedia_player_totals(
            config=config,
            refresh=refresh,
            populate_summary=populate_summary,
            limit_source=limit_source,
        )
    if source == SOURCE_STATSBOMB_OPEN_DATA:
        return hoard_statsbomb_open_data(
            config=config,
            refresh=refresh,
            populate_summary=populate_summary,
            limit_source=limit_source,
        )
    raise ValueError(f"Unsupported hoard source: {source}")


def hoard_international_results(
    config: dict | None = None,
    refresh: bool = False,
    populate_summary: bool = True,
    limit_source: int | None = None,
) -> HoardResult:
    config = dict(config or DEFAULT_CONFIG)
    source_id = SOURCE_INTERNATIONAL_RESULTS
    snapshot = _snapshot_id()
    raw_dir = Path(config.get("data_dir", "data")) / "raw" / source_id / snapshot
    started = _now()

    for name in _FILES:
        _fetch_file(f"{_BASE}/{name}", raw_dir / name, refresh=refresh)

    store = MatchStore.from_config(config)
    counts: dict[str, int] = {}
    try:
        counts.update(seed_identity_registry(config))
        store.upsert_wh_source({
            "source_id": source_id,
            "name": "martj42/international_results",
            "homepage": "https://github.com/martj42/international_results",
            "license": "CC0-1.0",
            "notes": "Men's full international football results, goalscorers, and former names.",
        })
        fetched_at = _now()
        for name in _FILES:
            path = raw_dir / name
            store.upsert_wh_source_file({
                "file_id": _source_file_id(source_id, snapshot, name),
                "source_id": source_id,
                "snapshot": snapshot,
                "path": str(path),
                "url": f"{_BASE}/{name}",
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "fetched_at": fetched_at,
            })

        result_rows = [r for r in _read_csv(raw_dir / "results.csv", limit_source) if _completed(r)]
        goal_rows = _read_csv(raw_dir / "goalscorers.csv", limit_source)
        former_rows = _read_csv(raw_dir / "former_names.csv", limit_source)

        store.delete_wh_snapshot_facts(source_id, snapshot)

        teams = {}
        aliases = {}
        comps = {}
        wh_matches = []
        wh_match_sources = []
        for idx, r in enumerate(result_rows, start=1):
            for side in ("home_team", "away_team"):
                res = _team_resolution(r[side])
                name = res.canonical_name
                tid = res.team_id or _team_id(name)
                teams[tid] = {"team_id": tid, "name": name, "kind": res.kind,
                              "source_id": source_id, "source_name": r[side]}
                alias_norm = normalize_entity_key(r[side])
                aliases[f"{source_id}:{alias_norm}:{tid}"] = {
                    "alias_key": f"{source_id}:{alias_norm}:{tid}",
                    "team_id": tid, "alias": r[side], "source_id": source_id,
                    "alias_norm": alias_norm, "confidence": res.confidence,
                    "status": "active", "notes": res.reason,
                }
            tournament = r.get("tournament") or "International"
            cid = _competition_id(tournament)
            comps[cid] = {"competition_id": cid, "name": tournament,
                          "kind": "international", "source_id": source_id}
            mid = _match_id(r)
            wh_matches.append({
                "wh_match_id": mid, "date": r.get("date"), "competition_id": cid,
                "tournament": tournament, "home_team_id": _team_resolution(r["home_team"]).team_id,
                "away_team_id": _team_resolution(r["away_team"]).team_id,
                "home_team": _team_resolution(r["home_team"]).canonical_name,
                "away_team": _team_resolution(r["away_team"]).canonical_name,
                "home_score": int(r["home_score"]), "away_score": int(r["away_score"]),
                "city": r.get("city"), "country": r.get("country"),
                "neutral": _boolish(r.get("neutral")),
                "source_id": source_id, "snapshot": snapshot,
            })
            wh_match_sources.append({
                "wh_match_id": mid, "source_id": source_id,
                "file_id": _source_file_id(source_id, snapshot, "results.csv"),
                "source_row": idx,
            })

        for r in former_rows:
            current = r.get("current") or r.get("current_name") or r.get("country") or ""
            former = r.get("former") or r.get("former_name") or r.get("name") or ""
            if not current or not former:
                continue
            res = _team_resolution(current)
            tid = res.team_id or _team_id(current)
            teams.setdefault(tid, {"team_id": tid, "name": res.canonical_name,
                                   "kind": res.kind, "source_id": source_id,
                                   "source_name": current})
            alias_norm = normalize_entity_key(former)
            aliases[f"{source_id}:{alias_norm}:{tid}"] = {
                "alias_key": f"{source_id}:{alias_norm}:{tid}",
                "team_id": tid, "alias": former, "source_id": source_id,
                "alias_norm": alias_norm, "confidence": 1.0,
                "status": "active", "notes": "former name",
            }

        matches_by_key = {
            _match_lookup_key(r.get("date"), r.get("home_team"), r.get("away_team")): _match_id(r)
            for r in result_rows
        }
        wh_goals = []
        player_agg: dict[tuple[str, str], dict] = {}
        for idx, r in enumerate(goal_rows, start=1):
            if not r.get("scorer") or r.get("scorer") == "NA":
                continue
            # goalscorers.csv does not carry score/tournament; match by date + teams.
            mid = matches_by_key.get(_match_lookup_key(r.get("date"), r.get("home_team"), r.get("away_team")))
            res = _team_resolution(r.get("team") or "")
            tid = res.team_id or _team_id(r.get("team") or "")
            teams.setdefault(tid, {"team_id": tid, "name": res.canonical_name,
                                   "kind": res.kind, "source_id": source_id,
                                   "source_name": r.get("team")})
            goal_id = f"{source_id}:{snapshot}:goal:{idx}"
            wh_goals.append({
                "goal_id": goal_id, "wh_match_id": mid, "date": r.get("date"),
                "team_id": tid, "team": res.canonical_name,
                "scorer": r.get("scorer"), "minute": r.get("minute"),
                "own_goal": _boolish(r.get("own_goal")),
                "penalty": _boolish(r.get("penalty")),
                "source_id": source_id, "snapshot": snapshot, "source_row": idx,
            })
            if _boolish(r.get("own_goal")):
                continue
            key = (r.get("scorer") or "", res.canonical_name)
            item = player_agg.setdefault(key, {
                "comp": "INT", "player": key[0], "team": key[1],
                "goals": 0, "assists": 0, "penalties": 0, "matches": set(),
                "source": "martj42/international_results:goalscorers.csv",
            })
            item["goals"] += 1
            if _boolish(r.get("penalty")):
                item["penalties"] += 1
            if mid:
                item["matches"].add(mid)

        counts["wh_teams"] = store.upsert_wh_rows("wh_teams", list(teams.values()))
        counts["wh_team_aliases"] = store.upsert_wh_rows("wh_team_aliases", list(aliases.values()))
        counts["wh_competitions"] = store.upsert_wh_rows("wh_competitions", list(comps.values()))
        counts["wh_matches"] = store.upsert_wh_rows("wh_matches", wh_matches)
        counts["wh_match_sources"] = store.upsert_wh_rows("wh_match_sources", wh_match_sources)
        counts["wh_goals"] = store.upsert_wh_rows("wh_goals", wh_goals)

        if populate_summary:
            existing = {
                f"{r.get('date') or ''}|{r['home']}|{r['away']}": r
                for r in store.all_matches()
            }
            summary_matches = []
            for r in result_rows:
                row = {
                    "date": r["date"], "comp": "INT",
                    "home": _team_resolution(r["home_team"]).canonical_name,
                    "away": _team_resolution(r["away_team"]).canonical_name,
                    "hg": int(r["home_score"]), "ag": int(r["away_score"]),
                    "xg_home": None, "xg_away": None,
                    "odds_h": None, "odds_d": None, "odds_a": None,
                    "source": "martj42/international_results:results.csv",
                }
                old = existing.get(f"{row['date']}|{row['home']}|{row['away']}")
                if old:
                    row["comp"] = old.get("comp") or row["comp"]
                    for field in ("xg_home", "xg_away", "odds_h", "odds_d", "odds_a"):
                        row[field] = old.get(field) if old.get(field) is not None else row[field]
                    old_source = old.get("source") or ""
                    if old_source and old_source != row["source"]:
                        row["source"] = f"{old_source}; {row['source']}"
                summary_matches.append(row)
            counts["summary_matches"] = store.upsert(summary_matches)
            player_rows = []
            for item in player_agg.values():
                player_rows.append({
                    **{k: v for k, v in item.items() if k != "matches"},
                    "matches": len(item["matches"]),
                })
            counts["summary_player_stats"] = store.upsert_players(player_rows)
        else:
            counts["summary_matches"] = 0
            counts["summary_player_stats"] = 0

        store.upsert_wh_ingestion_run({
            "run_id": f"{source_id}:{snapshot}:{started}",
            "source_id": source_id, "snapshot": snapshot, "started_at": started,
            "finished_at": _now(), "status": "ok", "counts_json": counts,
        })
    except Exception:
        store.upsert_wh_ingestion_run({
            "run_id": f"{source_id}:{snapshot}:{started}",
            "source_id": source_id, "snapshot": snapshot, "started_at": started,
            "finished_at": _now(), "status": "error", "counts_json": counts,
        })
        raise
    finally:
        store.close()

    return HoardResult(source=source_id, snapshot=snapshot, raw_dir=str(raw_dir), counts=counts)


def _player_id(name: str) -> str:
    return f"player:{normalize_entity_key(name).replace(' ', '_')}"


def _wiki_api_url(title: str) -> str:
    return (
        "https://en.wikipedia.org/w/api.php?action=query&prop=revisions|pageprops"
        f"&titles={quote(title)}&rvslots=main&rvprop=content&format=json&formatversion=2"
    )


def _newest_cached(base_dir: Path, filename: str) -> Path | None:
    """Newest existing copy of a raw file across ALL snapshot dirs — so a daily
    re-run reuses yesterday's downloads instead of re-fetching (the snapshot id
    is date-based)."""
    if not base_dir.exists():
        return None
    hits = sorted(base_dir.glob(f"*/{filename}"), key=lambda p: p.parent.name, reverse=True)
    return hits[0] if hits else None


def _ensure_raw(base_dir: Path, raw_dir: Path, filename: str, url: str,
                refresh: bool = False) -> Path:
    """Return today's snapshot path for ``filename``, reusing a prior snapshot's
    copy (immutable historical files never change) instead of re-downloading —
    this is what stops StatsBomb's 337 MB from being re-fetched every calendar
    day. Falls back to a network fetch only when no cached copy exists."""
    dest = raw_dir / filename
    if dest.exists() and not refresh:
        return dest
    if not refresh:
        cached = _newest_cached(base_dir, filename)
        if cached is not None and cached != dest:
            shutil.copyfile(cached, dest)
            return dest
    dest.write_text(json.dumps(_fetch_json(url)), encoding="utf-8")
    return dest


def _wiki_titles_from_store(store: MatchStore, limit: int | None) -> list[str]:
    rows = store.conn.execute(
        "SELECT player, SUM(COALESCE(goals, 0)) AS goals FROM player_stats "
        "WHERE comp = 'INT' GROUP BY player ORDER BY goals DESC, player"
    ).fetchall()
    names = [r["player"] for r in rows if r["player"]]
    return names[:limit] if limit else names


def _wc2026_squad_names(config: dict) -> list[str]:
    """Every player in a current WC2026 squad (competition feed, cached) — the
    names the agent most needs career context for. Graceful: [] without a token."""
    try:
        from worldcupagents.dataflows.interface import get_provider
        from worldcupagents.dataflows.world_cup_2026 import WC2026_TEAMS
        provider = get_provider(config)
        names: list[str] = []
        for team in WC2026_TEAMS:
            try:
                profile = provider.get_team_profile(team)
            except Exception:  # noqa: BLE001 — one team must not sink the list
                continue
            for p in getattr(profile, "squad", []) or []:
                if p.name and p.name not in names:
                    names.append(p.name)
        return names
    except Exception as e:  # noqa: BLE001
        return []


def _wiki_titles(store: MatchStore, config: dict, limit: int | None) -> list[str]:
    """Priority order: current WC2026 squad players (the agent's live subjects),
    then all-time INT top scorers. De-duplicated, then capped."""
    names = _wc2026_squad_names(config)
    seen = {normalize_entity_key(n) for n in names}
    for n in _wiki_titles_from_store(store, None):
        if normalize_entity_key(n) not in seen:
            names.append(n)
            seen.add(normalize_entity_key(n))
    return names[:limit] if limit else names


def _clean_wiki_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<!--.*?-->", "", value)
    text = re.sub(r"<ref[^>/]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"\{\{[Ff]lagicon\|[^}]+\}\}", "", text)
    text = re.sub(r"\{\{sortname\|([^|}]+)\|([^|}]+).*?\}\}", r"\1 \2", text)
    text = re.sub(r"\[\[[^|\]]+\|([^]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    return " ".join(text.strip().split())


def _extract_template_field(text: str, base: str, idx: int) -> str | None:
    m = re.search(rf"^\s*\|\s*{re.escape(base)}{idx}\s*=\s*(.*?)\s*$", text, flags=re.M)
    return _clean_wiki_value(m.group(1)) if m else None


def _int_from_wiki(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value.replace(",", ""))
    return int(m.group(0)) if m else None


def _years_from_wiki(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    nums = [int(x) for x in re.findall(r"\d{4}", value)]
    if not nums:
        return None, None
    return nums[0], nums[-1] if len(nums) > 1 else None


def _parse_wikipedia_player_totals(title: str, wikitext: str, source_url: str, snapshot: str) -> list[dict]:
    out = []
    for i in range(1, 20):
        team = _extract_template_field(wikitext, "nationalteam", i)
        if not team:
            continue
        caps = _int_from_wiki(_extract_template_field(wikitext, "nationalcaps", i))
        goals = _int_from_wiki(_extract_template_field(wikitext, "nationalgoals", i))
        start, end = _years_from_wiki(_extract_template_field(wikitext, "nationalyears", i))
        if caps is None and goals is None:
            continue
        res = resolve_team(team, kind="national", source_id=SOURCE_WIKIPEDIA_PLAYER_TOTALS)
        out.append({
            "total_id": f"{SOURCE_WIKIPEDIA_PLAYER_TOTALS}:{_player_id(title)}:{res.team_id or normalize_entity_key(team)}:{i}",
            "player_id": _player_id(title),
            "player": title,
            "team_id": res.team_id,
            "team": res.canonical_name,
            "scope": "national_team_infobox",
            "caps": caps,
            "goals": goals,
            "start_year": start,
            "end_year": end,
            "source_id": SOURCE_WIKIPEDIA_PLAYER_TOTALS,
            "source_url": source_url,
            "snapshot": snapshot,
            "confidence": 0.72,
            "notes": "Parsed from English Wikipedia football biography infobox; verify conflicts against stronger sources.",
        })
    return out


def _is_senior_national_team(name: str | None) -> bool:
    text = name or ""
    return not re.search(r"\bU-?\d{2}\b|under-?\d{2}|Olympic", text, flags=re.I)


def hoard_wikipedia_player_totals(
    config: dict | None = None,
    refresh: bool = False,
    populate_summary: bool = True,
    limit_source: int | None = 50,
) -> HoardResult:
    config = dict(config or DEFAULT_CONFIG)
    source_id = SOURCE_WIKIPEDIA_PLAYER_TOTALS
    snapshot = _snapshot_id()
    raw_dir = Path(config.get("data_dir", "data")) / "raw" / source_id / snapshot
    started = _now()
    store = MatchStore.from_config(config)
    counts: dict[str, int] = {}
    try:
        counts.update(seed_identity_registry(config))
        store.upsert_wh_source({
            "source_id": source_id,
            "name": "English Wikipedia football biography infoboxes",
            "homepage": "https://en.wikipedia.org/wiki/Main_Page",
            "license": "CC BY-SA",
            "notes": "Player national-team career caps/goals parsed from public article wikitext.",
        })
        titles = _wiki_titles(store, config, limit_source)
        raw_dir.mkdir(parents=True, exist_ok=True)
        base_dir = Path(config.get("data_dir", "data")) / "raw" / source_id
        players, aliases, totals = [], [], []
        for title in titles:
            raw_path = raw_dir / f"{_slug(title)}.json"
            cached = _newest_cached(base_dir, f"{_slug(title)}.json") if not refresh else None
            if cached is not None:
                data = json.loads(cached.read_text(encoding="utf-8"))
                if cached != raw_path:  # promote yesterday's download into today's snapshot
                    raw_path.write_text(json.dumps(data), encoding="utf-8")
            else:
                try:
                    data = _fetch_json(_wiki_api_url(title))
                    pages0 = data.get("query", {}).get("pages", []) if isinstance(data, dict) else []
                    if not pages0 or pages0[0].get("missing"):
                        # common-name miss → try the standard disambiguator
                        data = _fetch_json(_wiki_api_url(f"{title} (footballer)"))
                except Exception:
                    counts["fetch_errors"] = counts.get("fetch_errors", 0) + 1
                    continue
                raw_path.write_text(json.dumps(data), encoding="utf-8")
                time.sleep(0.4)
            store.upsert_wh_source_file({
                "file_id": _source_file_id(source_id, snapshot, raw_path.name),
                "source_id": source_id,
                "snapshot": snapshot,
                "path": str(raw_path),
                "url": _wiki_api_url(title),
                "sha256": _sha256(raw_path),
                "bytes": raw_path.stat().st_size,
                "fetched_at": _now(),
            })
            pages = data.get("query", {}).get("pages", []) if isinstance(data, dict) else []
            if not pages or pages[0].get("missing"):
                continue
            page_title = pages[0].get("title") or title
            revisions = pages[0].get("revisions") or []
            content = (((revisions[0] or {}).get("slots") or {}).get("main") or {}).get("content", "") if revisions else ""
            source_url = f"https://en.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'))}"
            pid = _player_id(page_title)
            players.append({"player_id": pid, "name": page_title, "source_id": source_id, "source_name": title})
            alias_norm = normalize_entity_key(title)
            aliases.append({
                "alias_key": f"{source_id}:{alias_norm}:{pid}",
                "player_id": pid,
                "alias": title,
                "alias_norm": alias_norm,
                "source_id": source_id,
                "confidence": 0.95,
                "status": "active",
                "notes": "Wikipedia page title/search alias",
            })
            totals.extend(_parse_wikipedia_player_totals(page_title, content, source_url, snapshot))
        counts["wh_players"] = store.upsert_wh_rows("wh_players", players)
        counts["wh_player_aliases"] = store.upsert_wh_rows("wh_player_aliases", aliases)
        counts["wh_player_career_totals"] = store.upsert_wh_rows("wh_player_career_totals", totals)
        if populate_summary:
            store.conn.execute(
                "DELETE FROM player_stats WHERE comp = 'INT_CAREER' AND source LIKE 'wikipedia_player_totals:%'"
            )
            store.conn.commit()
            rows = [{
                "comp": "INT_CAREER",
                "player": r["player"],
                "team": r["team"],
                "goals": r.get("goals"),
                "assists": 0,
                "penalties": None,
                "matches": r.get("caps"),
                "source": f"{source_id}:{r.get('source_url')}",
            } for r in totals if r.get("team") and _is_senior_national_team(r.get("team"))]
            counts["summary_player_stats"] = store.upsert_players(rows)
        else:
            counts["summary_player_stats"] = 0
        store.upsert_wh_ingestion_run({
            "run_id": f"{source_id}:{snapshot}:{started}",
            "source_id": source_id,
            "snapshot": snapshot,
            "started_at": started,
            "finished_at": _now(),
            "status": "ok",
            "counts_json": counts,
        })
    finally:
        store.close()
    return HoardResult(source=source_id, snapshot=snapshot, raw_dir=str(raw_dir), counts=counts)


def _statsbomb_competitions() -> list[dict]:
    data = _fetch_json(f"{_STATSBOMB_BASE}/competitions.json")
    return data if isinstance(data, list) else []


def _statsbomb_wc_pairs() -> list[tuple[int, int]]:
    return [
        (int(r["competition_id"]), int(r["season_id"]))
        for r in _statsbomb_competitions()
        if r.get("competition_name") == "FIFA World Cup"
    ]


def _statsbomb_team_id(name: str) -> str:
    return stable_team_id(canonical_team_name(name, kind="national"), "national")


def _statsbomb_match_id(row: dict) -> str:
    return f"statsbomb:{row.get('match_id')}"


def _is_progressive(start, end) -> bool:
    """Deliberately simple progressive definition (comprehensible beats official):
    the action ends ≥25% closer to the opponent's goal than it started, or it
    enters the final third (crosses x=80 on StatsBomb's 120-long pitch)."""
    if not start or not end:
        return False
    try:
        sx, ex = float(start[0]), float(end[0])
    except (TypeError, ValueError, IndexError):
        return False
    return (120.0 - ex) <= 0.75 * (120.0 - sx) or (sx < 80.0 <= ex)


def _collect_event_stats(events: list[dict]) -> dict:
    """One pass over a match's events → per-player and per-team aggregates of
    Pass / Carry / Shot actions, plus pass pairs and pass-origin zones (the raw
    material for style fingerprints). No raw streams are stored — only these
    aggregates reach SQLite; coordinates become zone labels (pitch_zones)."""
    from collections import Counter, defaultdict

    from worldcupagents.dataflows.pitch_zones import zone_label

    players: dict[tuple[str, str], dict] = {}
    teams: dict[str, dict] = {}
    pairs: dict[str, Counter] = {}
    zones: dict[str, Counter] = {}

    for ev in events:
        etype = (ev.get("type") or {}).get("name")
        if etype not in ("Pass", "Carry", "Shot"):
            continue
        team = (ev.get("team") or {}).get("name") or ""
        player = (ev.get("player") or {}).get("name") or ""
        if not team:
            continue
        t = teams.setdefault(team, defaultdict(float))
        p = players.setdefault((team, player), defaultdict(float)) if player else None
        loc = ev.get("location")

        if etype == "Pass":
            pas = ev.get("pass") or {}
            end = pas.get("end_location")
            complete = not pas.get("outcome")  # StatsBomb: no outcome = completed
            for c in (t, p):
                if c is None:
                    continue
                c["passes"] += 1
                c["passes_completed"] += 1 if complete else 0
                # progressive passes count COMPLETED passes only (FBref convention)
                if complete and _is_progressive(loc, end):
                    c["progressive_passes"] += 1
                if complete and loc and end and float(loc[0]) < 80.0 <= float(end[0]):
                    c["final_third_entries"] += 1
            recipient = (pas.get("recipient") or {}).get("name")
            if complete and player and recipient:
                pairs.setdefault(team, Counter())[(player, recipient)] += 1
            if loc:
                z = zone_label(loc[0], loc[1])
                if z:
                    zones.setdefault(team, Counter())[z] += 1
        elif etype == "Carry":
            end = (ev.get("carry") or {}).get("end_location")
            for c in (t, p):
                if c is None:
                    continue
                if _is_progressive(loc, end):
                    c["progressive_carries"] += 1
                if loc and end and float(loc[0]) < 80.0 <= float(end[0]):
                    c["final_third_entries"] += 1
        else:  # Shot
            shot = ev.get("shot") or {}
            xg = float(shot.get("statsbomb_xg") or 0)
            goal = (shot.get("outcome") or {}).get("name") == "Goal"
            for c in (t, p):
                if c is None:
                    continue
                c["shots"] += 1
                c["xg"] += xg
                c["goals"] += 1 if goal else 0

    return {"players": players, "teams": teams, "pairs": pairs, "zones": zones}


def _style_fingerprint(fp: dict) -> dict:
    """Season-level style summary a pundit can quote: possession share,
    directness (progressive passes per pass), favourite pass pairs, build-up zones."""
    passes = fp["passes"] or 1
    style = {
        "matches": fp["matches"],
        "possession_share": round(fp["passes"] / max(fp["passes"] + fp["opp_passes"], 1), 3),
        "pass_pct": round(100.0 * fp["passes_completed"] / passes, 1),
        "directness": round(fp["progressive_passes"] / passes, 3),
        "top_pass_pairs": [f"{a} → {b} ({n})" for (a, b), n in fp["pairs"].most_common(3)],
        "build_up_zones": [f"{z} ({n})" for z, n in fp["zones"].most_common(3)],
    }
    return style


def hoard_statsbomb_open_data(
    config: dict | None = None,
    refresh: bool = False,
    populate_summary: bool = True,
    limit_source: int | None = None,
) -> HoardResult:
    config = dict(config or DEFAULT_CONFIG)
    source_id = SOURCE_STATSBOMB_OPEN_DATA
    snapshot = _snapshot_id()
    raw_dir = Path(config.get("data_dir", "data")) / "raw" / source_id / snapshot
    started = _now()
    store = MatchStore.from_config(config)
    counts: dict[str, int] = {}
    try:
        counts.update(seed_identity_registry(config))
        store.upsert_wh_source({
            "source_id": source_id,
            "name": "StatsBomb Open Data",
            "homepage": "https://github.com/statsbomb/open-data",
            "license": "StatsBomb Open Data terms",
            "notes": "Public event, lineup, and match data for selected competitions including past FIFA World Cups.",
        })
        raw_dir.mkdir(parents=True, exist_ok=True)
        base_dir = Path(config.get("data_dir", "data")) / "raw" / source_id
        competitions_path = raw_dir / "competitions.json"
        if not competitions_path.exists() or refresh:
            competitions_path.write_text(json.dumps(_statsbomb_competitions()), encoding="utf-8")
        store.upsert_wh_source_file({
            "file_id": _source_file_id(source_id, snapshot, "competitions.json"),
            "source_id": source_id,
            "snapshot": snapshot,
            "path": str(competitions_path),
            "url": f"{_STATSBOMB_BASE}/competitions.json",
            "sha256": _sha256(competitions_path),
            "bytes": competitions_path.stat().st_size,
            "fetched_at": _now(),
        })
        teams, comps, wh_matches, wh_events, wh_lineups = {}, {}, [], [], []
        wh_player_stats: list[dict] = []
        wh_team_stats: list[dict] = []
        situations: dict[tuple[str, str], dict] = {}
        from collections import Counter
        fingerprints: dict[tuple[str, str], dict] = {}  # (season, team) -> accumulators
        match_count = 0
        for comp_id, season_id in _statsbomb_wc_pairs():
            matches_url = f"{_STATSBOMB_BASE}/matches/{comp_id}/{season_id}.json"
            matches_path = _ensure_raw(base_dir, raw_dir, f"matches_{comp_id}_{season_id}.json",
                                       matches_url, refresh)
            store.upsert_wh_source_file({
                "file_id": _source_file_id(source_id, snapshot, matches_path.name),
                "source_id": source_id,
                "snapshot": snapshot,
                "path": str(matches_path),
                "url": matches_url,
                "sha256": _sha256(matches_path),
                "bytes": matches_path.stat().st_size,
                "fetched_at": _now(),
            })
            for m in json.loads(matches_path.read_text(encoding="utf-8")):
                if limit_source and match_count >= limit_source:
                    break
                match_count += 1
                home = (m.get("home_team") or {}).get("home_team_name") or ""
                away = (m.get("away_team") or {}).get("away_team_name") or ""
                home_id, away_id = _statsbomb_team_id(home), _statsbomb_team_id(away)
                for tid, name in ((home_id, home), (away_id, away)):
                    teams[tid] = {"team_id": tid, "name": canonical_team_name(name, kind="national"),
                                  "kind": "national", "source_id": source_id, "source_name": name}
                season = (m.get("season") or {}).get("season_name") or str(season_id)
                cid = f"competition:statsbomb:{comp_id}:{season_id}"
                comps[cid] = {"competition_id": cid, "name": f"FIFA World Cup {season}",
                              "kind": "international", "source_id": source_id}
                mid = _statsbomb_match_id(m)
                wh_matches.append({
                    "wh_match_id": mid,
                    "date": m.get("match_date"),
                    "competition_id": cid,
                    "tournament": f"FIFA World Cup {season}",
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "home_team": canonical_team_name(home, kind="national"),
                    "away_team": canonical_team_name(away, kind="national"),
                    "home_score": int(m.get("home_score") or 0),
                    "away_score": int(m.get("away_score") or 0),
                    "city": (m.get("stadium") or {}).get("name"),
                    "country": ((m.get("stadium") or {}).get("country") or {}).get("name"),
                    "neutral": None,
                    "source_id": source_id,
                    "snapshot": snapshot,
                })
                events_url = f"{_STATSBOMB_BASE}/events/{m['match_id']}.json"
                events_path = _ensure_raw(base_dir, raw_dir, f"events_{m['match_id']}.json",
                                          events_url, refresh)
                lineups_url = f"{_STATSBOMB_BASE}/lineups/{m['match_id']}.json"
                lineups_path = _ensure_raw(base_dir, raw_dir, f"lineups_{m['match_id']}.json",
                                           lineups_url, refresh)
                for path, url in ((events_path, events_url), (lineups_path, lineups_url)):
                    store.upsert_wh_source_file({
                        "file_id": _source_file_id(source_id, snapshot, path.name),
                        "source_id": source_id,
                        "snapshot": snapshot,
                        "path": str(path),
                        "url": url,
                        "sha256": _sha256(path),
                        "bytes": path.stat().st_size,
                        "fetched_at": _now(),
                    })
                from worldcupagents.dataflows.pitch_zones import zone_label
                events_list = json.loads(events_path.read_text(encoding="utf-8"))
                for ev in events_list:
                    if (ev.get("type") or {}).get("name") != "Shot":
                        continue
                    team_name = (ev.get("team") or {}).get("name") or ""
                    tid = _statsbomb_team_id(team_name) if team_name else None
                    shot = ev.get("shot") or {}
                    pattern = (ev.get("play_pattern") or {}).get("name") or "Unknown"
                    key = (season, canonical_team_name(team_name, kind="national"))
                    bucket = situations.setdefault(key, {})
                    item = bucket.setdefault(pattern, {"shots": 0, "goals": 0, "xG": 0.0})
                    item["shots"] += 1
                    item["xG"] = round(float(item["xG"]) + float(shot.get("statsbomb_xg") or 0), 4)
                    if (shot.get("outcome") or {}).get("name") == "Goal":
                        item["goals"] += 1
                    loc = ev.get("location") or [None, None]
                    wh_events.append({
                        "event_id": f"{source_id}:{ev.get('id')}",
                        "wh_match_id": mid,
                        "team_id": tid,
                        "player": (ev.get("player") or {}).get("name"),
                        "minute": ev.get("minute"),
                        "event_type": "shot",
                        "data_json": json.dumps({
                            "play_pattern": pattern,
                            "xg": shot.get("statsbomb_xg"),
                            "outcome": (shot.get("outcome") or {}).get("name"),
                            "body_part": (shot.get("body_part") or {}).get("name"),
                            "location": ev.get("location"),
                            "zone": zone_label(loc[0], loc[1]),
                        }, sort_keys=True),
                        "source_id": source_id,
                        "snapshot": snapshot,
                    })

                # Pass/Carry/Shot aggregation (B3): per player-match and
                # team-match rows + season style fingerprints. Aggregates only —
                # the raw stream stays in the snapshot files on disk.
                agg = _collect_event_stats(events_list)
                for (team_name, player), c in agg["players"].items():
                    stats = dict(c)
                    if stats.get("passes"):
                        stats["pass_pct"] = round(100.0 * stats.get("passes_completed", 0)
                                                  / stats["passes"], 1)
                    tid = _statsbomb_team_id(team_name)
                    for stat_name, val in stats.items():
                        wh_player_stats.append({
                            "stat_id": f"{source_id}:{m['match_id']}:{player}:{stat_name}",
                            "wh_match_id": mid, "team_id": tid, "player": player,
                            "stat_name": stat_name, "stat_value": round(float(val), 3),
                            "source_id": source_id, "snapshot": snapshot,
                        })
                team_passes = {tn: c.get("passes", 0) for tn, c in agg["teams"].items()}
                for team_name, c in agg["teams"].items():
                    tid = _statsbomb_team_id(team_name)
                    stats = dict(c)
                    if stats.get("passes"):
                        stats["pass_pct"] = round(100.0 * stats.get("passes_completed", 0)
                                                  / stats["passes"], 1)
                    for stat_name, val in stats.items():
                        wh_team_stats.append({
                            "stat_id": f"{source_id}:{m['match_id']}:{team_name}:{stat_name}",
                            "wh_match_id": mid, "team_id": tid,
                            "stat_name": stat_name, "stat_value": round(float(val), 3),
                            "source_id": source_id, "snapshot": snapshot,
                        })
                    fkey = (season, canonical_team_name(team_name, kind="national"))
                    fp = fingerprints.setdefault(fkey, {
                        "matches": 0, "passes": 0, "passes_completed": 0,
                        "progressive_passes": 0, "opp_passes": 0,
                        "pairs": Counter(), "zones": Counter(),
                    })
                    fp["matches"] += 1
                    fp["passes"] += c.get("passes", 0)
                    fp["passes_completed"] += c.get("passes_completed", 0)
                    fp["progressive_passes"] += c.get("progressive_passes", 0)
                    fp["opp_passes"] += sum(n for tn, n in team_passes.items() if tn != team_name)
                    fp["pairs"].update(agg["pairs"].get(team_name) or {})
                    fp["zones"].update(agg["zones"].get(team_name) or {})
                for team_block in json.loads(lineups_path.read_text(encoding="utf-8")):
                    tid = _statsbomb_team_id(team_block.get("team_name") or "")
                    for idx, player in enumerate(team_block.get("lineup") or []):
                        wh_lineups.append({
                            "lineup_id": f"{source_id}:{m['match_id']}:{player.get('player_id') or idx}",
                            "wh_match_id": mid,
                            "team_id": tid,
                            "player": player.get("player_name"),
                            "position": None,
                            "starter": None,
                            "source_id": source_id,
                            "snapshot": snapshot,
                        })
            if limit_source and match_count >= limit_source:
                break
        counts["wh_teams"] = store.upsert_wh_rows("wh_teams", list(teams.values()))
        counts["wh_competitions"] = store.upsert_wh_rows("wh_competitions", list(comps.values()))
        counts["wh_matches"] = store.upsert_wh_rows("wh_matches", wh_matches)
        counts["wh_events"] = store.upsert_wh_rows("wh_events", wh_events)
        counts["wh_lineups"] = store.upsert_wh_rows("wh_lineups", wh_lineups)
        counts["wh_player_match_stats"] = store.upsert_wh_rows("wh_player_match_stats", wh_player_stats)
        counts["wh_team_match_stats"] = store.upsert_wh_rows("wh_team_match_stats", wh_team_stats)
        if populate_summary:
            for (season, team), data in situations.items():
                fp = fingerprints.get((season, team))
                if fp:  # style fingerprint rides in the same situations JSON
                    data = {**data, "style": _style_fingerprint(fp)}
                store.upsert_situations("WC", season, team, data, "statsbomb/open-data:events")
            counts["summary_team_situations"] = len(situations)
        else:
            counts["summary_team_situations"] = 0
        counts["event_shots"] = len(wh_events)
        store.upsert_wh_ingestion_run({
            "run_id": f"{source_id}:{snapshot}:{started}",
            "source_id": source_id,
            "snapshot": snapshot,
            "started_at": started,
            "finished_at": _now(),
            "status": "ok",
            "counts_json": counts,
        })
    finally:
        store.close()
    return HoardResult(source=source_id, snapshot=snapshot, raw_dir=str(raw_dir), counts=counts)
