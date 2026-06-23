"""SQLite match store (DATA_PLAN M1.1) — the derived results/xG database.

Distinct from the human-readable markdown memory: this is queryable, machine-only
data the strength model (M1.2) fits on. Single file at ``data/football.db``,
git-ignored, rebuilt anytime via ``worldcupagents fetch-data``.

Rows are keyed by date|home|away so re-ingesting is idempotent (INSERT OR REPLACE).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_key TEXT PRIMARY KEY,   -- "{date}|{home}|{away}"
    date      TEXT,
    comp      TEXT,
    home      TEXT NOT NULL,
    away      TEXT NOT NULL,
    hg        INTEGER NOT NULL,
    ag        INTEGER NOT NULL,
    xg_home   REAL,
    xg_away   REAL,
    odds_h    REAL,
    odds_d    REAL,
    odds_a    REAL,
    sh_home   INTEGER,  -- shots
    sh_away   INTEGER,
    sot_home  INTEGER,  -- shots on target
    sot_away  INTEGER,
    fouls_home INTEGER,
    fouls_away INTEGER,
    corners_home INTEGER,
    corners_away INTEGER,
    yellow_home INTEGER,
    yellow_away INTEGER,
    red_home  INTEGER,
    red_away  INTEGER,
    source    TEXT
);
"""

_STAT_COLS = ("sh_home", "sh_away", "sot_home", "sot_away", "fouls_home", "fouls_away",
              "corners_home", "corners_away", "yellow_home", "yellow_away",
              "red_home", "red_away")
_COLS = ("date", "comp", "home", "away", "hg", "ag", "xg_home", "xg_away",
         "odds_h", "odds_d", "odds_a") + _STAT_COLS + ("source",)
# matches-table columns added after first release — migrated onto existing DBs.
_MATCH_MIGRATIONS = (("odds_h", "REAL"), ("odds_d", "REAL"), ("odds_a", "REAL")) + \
    tuple((c, "INTEGER") for c in _STAT_COLS)

_PLAYER_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_stats (
    pkey          TEXT PRIMARY KEY,   -- "{comp}|{player}|{team}"
    comp          TEXT,
    player        TEXT NOT NULL,
    team          TEXT,
    goals         INTEGER,
    assists       INTEGER,
    penalties     INTEGER,
    matches       INTEGER,
    pass_accuracy REAL,
    key_passes    INTEGER,
    minutes       INTEGER,
    rating        REAL,
    source        TEXT
);
"""
_PCOLS = ("comp", "player", "team", "goals", "assists", "penalties", "matches",
          "pass_accuracy", "key_passes", "minutes", "rating", "shots", "xg", "xa",
          "xg_buildup", "source")
# columns added after the first release — migrated onto existing DBs at open time
_PLAYER_MIGRATIONS = (("pass_accuracy", "REAL"), ("key_passes", "INTEGER"),
                      ("minutes", "INTEGER"), ("rating", "REAL"),
                      ("shots", "INTEGER"), ("xg", "REAL"), ("xa", "REAL"),
                      ("xg_buildup", "REAL"))

_SITUATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_situations (
    skey    TEXT PRIMARY KEY,   -- "{comp}|{season}|{team}"
    comp    TEXT,
    season  TEXT,
    team    TEXT NOT NULL,
    data    TEXT,               -- JSON: situation breakdown (corners/set pieces/pens…)
    xi      TEXT,               -- JSON: most-used XI by minutes [{name,pos,minutes,…}]
    source  TEXT
);
"""
_SITU_MIGRATIONS = (("xi", "TEXT"),)

# User-authored per-player scouting/style notes (the qualitative layer you type in).
_PLAYER_NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_notes (
    pkey       TEXT PRIMARY KEY,   -- "{team_norm}|{player_norm}"
    team       TEXT,
    player     TEXT NOT NULL,
    note       TEXT NOT NULL,
    source     TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS injuries (
    ikey       TEXT PRIMARY KEY,   -- "{team_norm}|{player_norm}"
    team       TEXT,
    player     TEXT NOT NULL,
    status     TEXT NOT NULL,      -- injured | suspended | doubt
    note       TEXT,
    source     TEXT,               -- manual | guardian:punditry
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS team_coach (
    team_key   TEXT PRIMARY KEY,   -- normalize_key(team)
    team       TEXT,
    name       TEXT,               -- head coach / manager (may be NULL if only prose)
    note       TEXT,               -- their style & pedigree, in prose
    source     TEXT,
    updated_at TEXT
);
"""
_TEAM_ALIAS_MIGRATIONS = (
    ("alias_norm", "TEXT"),
    ("confidence", "REAL"),
    ("status", "TEXT"),
    ("notes", "TEXT"),
)

_WAREHOUSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS wh_sources (
    source_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    homepage    TEXT,
    license     TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS wh_source_files (
    file_id      TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    snapshot     TEXT NOT NULL,
    path         TEXT NOT NULL,
    url          TEXT,
    sha256       TEXT,
    bytes        INTEGER,
    fetched_at   TEXT,
    FOREIGN KEY(source_id) REFERENCES wh_sources(source_id)
);

CREATE TABLE IF NOT EXISTS wh_ingestion_runs (
    run_id       TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    snapshot     TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT,
    counts_json  TEXT,
    FOREIGN KEY(source_id) REFERENCES wh_sources(source_id)
);

CREATE TABLE IF NOT EXISTS wh_teams (
    team_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT DEFAULT 'national',
    source_id    TEXT,
    source_name  TEXT
);

CREATE TABLE IF NOT EXISTS wh_team_aliases (
    alias_key    TEXT PRIMARY KEY,
    team_id      TEXT NOT NULL,
    alias        TEXT NOT NULL,
    source_id    TEXT,
    alias_norm   TEXT,
    confidence   REAL,
    status       TEXT,
    notes        TEXT,
    FOREIGN KEY(team_id) REFERENCES wh_teams(team_id)
);

CREATE TABLE IF NOT EXISTS wh_unresolved_names (
    unresolved_id TEXT PRIMARY KEY,
    raw_name      TEXT NOT NULL,
    name_norm     TEXT,
    kind          TEXT,
    source_id     TEXT,
    context       TEXT,
    reason        TEXT,
    candidates_json TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    count         INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS wh_competitions (
    competition_id TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    kind           TEXT,
    source_id      TEXT
);

CREATE TABLE IF NOT EXISTS wh_matches (
    wh_match_id     TEXT PRIMARY KEY,
    date            TEXT,
    competition_id  TEXT,
    tournament      TEXT,
    home_team_id    TEXT NOT NULL,
    away_team_id    TEXT NOT NULL,
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    home_score      INTEGER NOT NULL,
    away_score      INTEGER NOT NULL,
    city            TEXT,
    country         TEXT,
    neutral         INTEGER,
    source_id       TEXT,
    snapshot        TEXT,
    FOREIGN KEY(competition_id) REFERENCES wh_competitions(competition_id),
    FOREIGN KEY(home_team_id) REFERENCES wh_teams(team_id),
    FOREIGN KEY(away_team_id) REFERENCES wh_teams(team_id)
);

CREATE TABLE IF NOT EXISTS wh_match_sources (
    wh_match_id TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    file_id     TEXT,
    source_row  INTEGER,
    PRIMARY KEY (wh_match_id, source_id, source_row),
    FOREIGN KEY(wh_match_id) REFERENCES wh_matches(wh_match_id)
);

CREATE TABLE IF NOT EXISTS wh_goals (
    goal_id     TEXT PRIMARY KEY,
    wh_match_id TEXT,
    date        TEXT,
    team_id     TEXT,
    team        TEXT,
    scorer      TEXT,
    minute      TEXT,
    own_goal    INTEGER,
    penalty     INTEGER,
    source_id   TEXT,
    snapshot    TEXT,
    source_row  INTEGER,
    FOREIGN KEY(wh_match_id) REFERENCES wh_matches(wh_match_id),
    FOREIGN KEY(team_id) REFERENCES wh_teams(team_id)
);

CREATE TABLE IF NOT EXISTS wh_shootouts (
    shootout_id   TEXT PRIMARY KEY,
    wh_match_id   TEXT,
    date          TEXT,
    home_team_id  TEXT,
    away_team_id  TEXT,
    home_team     TEXT,
    away_team     TEXT,
    winner_team_id TEXT,
    winner        TEXT,
    first_shooter TEXT,
    source_id     TEXT,
    snapshot      TEXT,
    source_row    INTEGER,
    FOREIGN KEY(wh_match_id) REFERENCES wh_matches(wh_match_id)
);

CREATE TABLE IF NOT EXISTS wh_lineups (
    lineup_id   TEXT PRIMARY KEY,
    wh_match_id TEXT,
    team_id     TEXT,
    player      TEXT,
    position    TEXT,
    starter     INTEGER,
    source_id   TEXT,
    snapshot    TEXT
);

CREATE TABLE IF NOT EXISTS wh_events (
    event_id    TEXT PRIMARY KEY,
    wh_match_id TEXT,
    team_id     TEXT,
    player      TEXT,
    minute      REAL,
    event_type  TEXT,
    data_json   TEXT,
    source_id   TEXT,
    snapshot    TEXT
);

CREATE TABLE IF NOT EXISTS wh_team_match_stats (
    stat_id     TEXT PRIMARY KEY,
    wh_match_id TEXT,
    team_id     TEXT,
    stat_name   TEXT,
    stat_value  REAL,
    source_id   TEXT,
    snapshot    TEXT
);

CREATE TABLE IF NOT EXISTS wh_player_match_stats (
    stat_id     TEXT PRIMARY KEY,
    wh_match_id TEXT,
    team_id     TEXT,
    player      TEXT,
    stat_name   TEXT,
    stat_value  REAL,
    source_id   TEXT,
    snapshot    TEXT
);

CREATE TABLE IF NOT EXISTS wh_players (
    player_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    source_id    TEXT,
    source_name  TEXT
);

CREATE TABLE IF NOT EXISTS wh_player_aliases (
    alias_key    TEXT PRIMARY KEY,
    player_id    TEXT NOT NULL,
    alias        TEXT NOT NULL,
    alias_norm   TEXT,
    source_id    TEXT,
    confidence   REAL,
    status       TEXT,
    notes        TEXT,
    FOREIGN KEY(player_id) REFERENCES wh_players(player_id)
);

CREATE TABLE IF NOT EXISTS wh_player_career_totals (
    total_id     TEXT PRIMARY KEY,
    player_id    TEXT NOT NULL,
    player       TEXT NOT NULL,
    team_id      TEXT,
    team         TEXT,
    scope        TEXT,
    caps         INTEGER,
    goals        INTEGER,
    start_year   INTEGER,
    end_year     INTEGER,
    source_id    TEXT,
    source_url   TEXT,
    snapshot     TEXT,
    confidence   REAL,
    notes        TEXT,
    FOREIGN KEY(player_id) REFERENCES wh_players(player_id),
    FOREIGN KEY(team_id) REFERENCES wh_teams(team_id)
);

CREATE TABLE IF NOT EXISTS wh_qual_documents (
    document_id   TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    source_type   TEXT,
    title         TEXT,
    url           TEXT,
    published_at  TEXT,
    fetched_at    TEXT,
    snapshot      TEXT,
    raw_path      TEXT,
    sha256        TEXT,
    license       TEXT,
    author        TEXT,
    language      TEXT,
    text_chars    INTEGER,
    meta_json     TEXT
);

CREATE TABLE IF NOT EXISTS wh_qual_segments (
    segment_id    TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    minute        TEXT,
    heading       TEXT,
    text          TEXT NOT NULL,
    text_norm     TEXT,
    char_start    INTEGER,
    char_end      INTEGER,
    FOREIGN KEY(document_id) REFERENCES wh_qual_documents(document_id)
);

CREATE TABLE IF NOT EXISTS wh_qual_claims (
    claim_id      TEXT PRIMARY KEY,
    segment_id    TEXT NOT NULL,
    document_id   TEXT NOT NULL,
    claim_type    TEXT,
    team_id       TEXT,
    player        TEXT,
    claim_text    TEXT NOT NULL,
    confidence    REAL,
    source_id     TEXT,
    FOREIGN KEY(segment_id) REFERENCES wh_qual_segments(segment_id)
);

CREATE TABLE IF NOT EXISTS wh_qual_links (
    link_id       TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    segment_id    TEXT,
    entity_type   TEXT,
    entity_id     TEXT,
    entity_name   TEXT,
    source_id     TEXT,
    confidence    REAL,
    FOREIGN KEY(document_id) REFERENCES wh_qual_documents(document_id)
);
"""


def db_path(config: dict) -> Path:
    return Path(config.get("data_dir", "data")) / "football.db"


def _match_key(row: dict) -> str:
    return f"{row.get('date') or ''}|{row['home']}|{row['away']}"


class MatchStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.executescript(_PLAYER_SCHEMA)
        self.conn.executescript(_SITUATIONS_SCHEMA)
        self.conn.executescript(_WAREHOUSE_SCHEMA)
        self.conn.executescript(_PLAYER_NOTES_SCHEMA)
        self._migrate("player_stats", _PLAYER_MIGRATIONS)
        self._migrate("matches", _MATCH_MIGRATIONS)
        self._migrate("team_situations", _SITU_MIGRATIONS)
        self._migrate("wh_team_aliases", _TEAM_ALIAS_MIGRATIONS)
        self._backfill_team_alias_metadata()

    def _migrate(self, table: str, cols) -> None:
        """Add columns introduced after the first release to an existing DB."""
        have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols:
            if col not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.conn.commit()

    def _alias_norm(self, value: str | None) -> str:
        import unicodedata
        text = unicodedata.normalize("NFKD", value or "")
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.lower().replace("&", " and ")
        text = "".join(ch if ch.isalnum() else " " for ch in text)
        return " ".join(text.split())

    def _backfill_team_alias_metadata(self) -> None:
        rows = self.conn.execute(
            "SELECT alias_key, alias, alias_norm, confidence, status FROM wh_team_aliases "
            "WHERE alias_norm IS NULL OR confidence IS NULL OR status IS NULL"
        ).fetchall()
        for r in rows:
            self.conn.execute(
                "UPDATE wh_team_aliases SET alias_norm = COALESCE(alias_norm, ?), "
                "confidence = COALESCE(confidence, 1.0), status = COALESCE(status, 'active') "
                "WHERE alias_key = ?",
                [self._alias_norm(r["alias"]), r["alias_key"]],
            )
        if rows:
            self.conn.commit()
        legacy = self.conn.execute(
            "SELECT a.alias_key, a.team_id, t.name, COALESCE(t.kind, 'unknown') AS kind "
            "FROM wh_team_aliases a JOIN wh_teams t ON t.team_id = a.team_id "
            "WHERE COALESCE(a.status, 'active') = 'active'"
        ).fetchall()
        changed = 0
        for r in legacy:
            expected = f"{r['kind']}:{self._alias_norm(r['name']).replace(' ', '_')}"
            if r["team_id"] != expected and r["kind"] in ("national", "club", "regional"):
                self.conn.execute(
                    "UPDATE wh_team_aliases SET status = 'legacy_inactive', "
                    "notes = COALESCE(notes, 'legacy non-normalized team_id') "
                    "WHERE alias_key = ?",
                    [r["alias_key"]],
                )
                changed += 1
        if changed:
            self.conn.commit()

    @classmethod
    def from_config(cls, config: dict) -> "MatchStore":
        return cls(db_path(config))

    def upsert(self, rows: list[dict]) -> int:
        """Insert/replace rows; returns how many were written."""
        n = 0
        for r in rows:
            vals = [_match_key(r)] + [r.get(c) for c in _COLS]
            self.conn.execute(
                f"INSERT OR REPLACE INTO matches (match_key, {', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' * (len(_COLS) + 1))})",
                vals,
            )
            n += 1
        self.conn.commit()
        return n

    def team_stat_profile(self, team: str, comp: str | None = None,
                          since: str | None = None, limit: int = 38) -> dict | None:
        """Per-match averages (for AND against) of shots, shots-on-target, fouls,
        corners, cards over a team's most recent matches — the 'tempo & discipline'
        profile mined from football-data.co.uk stat columns. ``since`` bounds
        recency (ISO date); None if the team has no stat-bearing rows."""
        clauses = ["(home = ? OR away = ?)", "sh_home IS NOT NULL"]
        args: list = [team, team]
        if comp is not None:
            clauses.append("comp = ?"); args.append(comp)
        if since is not None:
            clauses.append("date >= ?"); args.append(since)
        rows = self.conn.execute(
            f"SELECT * FROM matches WHERE {' AND '.join(clauses)} "
            f"ORDER BY date DESC LIMIT ?", args + [limit],
        ).fetchall()
        if not rows:
            return None
        acc = {k: 0.0 for k in ("shots", "sot", "fouls", "corners", "yellow", "red",
                                "shots_a", "sot_a", "fouls_a", "corners_a")}
        n = 0
        for r in rows:
            home = r["home"] == team
            def fa(stat):  # (for, against) picking the team's side
                h, a = r[f"{stat}_home"], r[f"{stat}_away"]
                return (h, a) if home else (a, h)
            n += 1
            for key, stat in (("shots", "sh"), ("sot", "sot"), ("fouls", "fouls"),
                              ("corners", "corners"), ("yellow", "yellow"), ("red", "red")):
                f, a = fa(stat)
                acc[key] += f or 0
                if key in ("shots", "sot", "fouls", "corners"):
                    acc[f"{key}_a"] += a or 0
        return {"n": n, **{k: round(v / n, 1) for k, v in acc.items()}}

    def recent_team_matches(self, team: str, comp: str | None = None,
                            since: str | None = None, limit: int = 6) -> list[dict]:
        """A team's most recent matches with per-match stats from its OWN
        perspective (shots/SoT/corners/fouls/cards from football-data.co.uk + xG
        from Understat). Stats are None for competitions that don't carry them
        (e.g. internationals)."""
        clauses, args = ["(home = ? OR away = ?)"], [team, team]
        if comp is not None:
            clauses.append("comp = ?"); args.append(comp)
        if since is not None:
            clauses.append("date >= ?"); args.append(since)
        rows = self.conn.execute(
            f"SELECT * FROM matches WHERE {' AND '.join(clauses)} AND date IS NOT NULL "
            f"ORDER BY date DESC LIMIT ?", args + [limit],
        ).fetchall()
        out = []
        for r in rows:
            home = r["home"] == team
            def side(stat):
                h, a = r[f"{stat}_home"], r[f"{stat}_away"]
                return (h, a) if home else (a, h)
            gf, ga = (r["hg"], r["ag"]) if home else (r["ag"], r["hg"])
            sh, sh_a = side("sh"); sot, sot_a = side("sot")
            xg = r["xg_home"] if home else r["xg_away"]
            xga = r["xg_away"] if home else r["xg_home"]
            out.append({
                "date": r["date"], "venue": "H" if home else "A",
                "opponent": r["away"] if home else r["home"],
                "gf": gf, "ga": ga,
                "result": "W" if gf > ga else "L" if gf < ga else "D",
                "shots": sh, "shots_against": sh_a, "sot": sot, "sot_against": sot_a,
                "corners": side("corners")[0], "fouls": side("fouls")[0],
                "yellow": side("yellow")[0], "red": side("red")[0],
                "xg": xg, "xga": xga, "comp": r["comp"],
            })
        return out

    def venue_record(self, team: str, comp: str | None = None,
                     since: str | None = None) -> dict:
        """(W, D, L) split by home vs away over the team's matches — for spotting
        a soft home record or poor travelling form."""
        clauses, args = ["(home = ? OR away = ?)"], [team, team]
        if comp is not None:
            clauses.append("comp = ?"); args.append(comp)
        if since is not None:
            clauses.append("date >= ?"); args.append(since)
        rows = self.conn.execute(
            f"SELECT home, away, hg, ag FROM matches WHERE {' AND '.join(clauses)}", args,
        ).fetchall()
        rec = {"home": [0, 0, 0], "away": [0, 0, 0]}   # W, D, L
        for r in rows:
            at = "home" if r["home"] == team else "away"
            gf, ga = (r["hg"], r["ag"]) if at == "home" else (r["ag"], r["hg"])
            rec[at][0 if gf > ga else 2 if gf < ga else 1] += 1
        return rec

    def h2h_vs(self, team: str, opponent: str, comp: str | None = None,
               since: str | None = None, limit: int = 8) -> dict:
        """The team's record against ONE specific opponent (most recent first) —
        for flagging a bogey side."""
        clauses = ["((home = ? AND away = ?) OR (home = ? AND away = ?))"]
        args = [team, opponent, opponent, team]
        if comp is not None:
            clauses.append("comp = ?"); args.append(comp)
        if since is not None:
            clauses.append("date >= ?"); args.append(since)
        rows = self.conn.execute(
            f"SELECT date, home, away, hg, ag FROM matches WHERE {' AND '.join(clauses)} "
            f"ORDER BY date DESC LIMIT ?", args + [limit],
        ).fetchall()
        wdl = [0, 0, 0]
        for r in rows:
            gf, ga = (r["hg"], r["ag"]) if r["home"] == team else (r["ag"], r["hg"])
            wdl[0 if gf > ga else 2 if gf < ga else 1] += 1
        return {"n": len(rows), "wdl": wdl}

    def shootout_record(self, team_id: str) -> dict:
        """Penalty-shootout wins/losses from the warehouse — the 'falls short in
        extra time / on penalties' weakness for national teams."""
        rows = self.conn.execute(
            "SELECT home_team_id, away_team_id, winner_team_id FROM wh_shootouts "
            "WHERE home_team_id = ? OR away_team_id = ?", [team_id, team_id],
        ).fetchall()
        won = sum(1 for r in rows if r["winner_team_id"] == team_id)
        return {"n": len(rows), "won": won, "lost": len(rows) - won}

    # --- per-player scouting/style notes (user-authored qualitative layer) ---

    def upsert_player_note(self, team: str, player: str, note: str, source: str = "manual") -> None:
        from datetime import datetime, timezone
        from worldcupagents.dataflows.names import normalize_key
        pkey = f"{normalize_key(team)}|{normalize_key(player)}"
        self.conn.execute(
            "INSERT OR REPLACE INTO player_notes (pkey, team, player, note, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [pkey, team, player, note, source,
             datetime.now(timezone.utc).strftime("%Y-%m-%d")],
        )
        self.conn.commit()

    def player_notes_for_team(self, team: str) -> list[dict]:
        from worldcupagents.dataflows.names import normalize_key
        tk = normalize_key(team)
        return [dict(r) for r in self.conn.execute(
            "SELECT team, player, note, source, updated_at FROM player_notes "
            "WHERE pkey LIKE ? ORDER BY player", [f"{tk}|%"],
        ).fetchall()]

    def all_player_notes(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT team, player, note, source, updated_at FROM player_notes "
            "ORDER BY team, player").fetchall()]

    def delete_player_note(self, team: str, player: str) -> bool:
        from worldcupagents.dataflows.names import normalize_key
        cur = self.conn.execute("DELETE FROM player_notes WHERE pkey = ?",
                                [f"{normalize_key(team)}|{normalize_key(player)}"])
        self.conn.commit()
        return cur.rowcount > 0

    # --- injuries / availability (manual + best-effort extraction) ---

    def upsert_injury(self, team: str, player: str, status: str, note: str = "",
                      source: str = "manual", *, overwrite: bool = True) -> bool:
        """Record a player's availability. ``overwrite=False`` won't clobber an existing
        row (so a best-effort extract never overrides a manual entry). Returns whether a
        row was written."""
        from datetime import datetime, timezone
        from worldcupagents.dataflows.names import normalize_key
        ikey = f"{normalize_key(team)}|{normalize_key(player)}"
        if not overwrite and self.conn.execute(
                "SELECT 1 FROM injuries WHERE ikey = ?", [ikey]).fetchone():
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO injuries (ikey, team, player, status, note, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [ikey, team, player, status, note, source,
             datetime.now(timezone.utc).strftime("%Y-%m-%d")],
        )
        self.conn.commit()
        return True

    def injuries_for_team(self, team: str) -> list[dict]:
        from worldcupagents.dataflows.names import normalize_key
        return [dict(r) for r in self.conn.execute(
            "SELECT team, player, status, note, source, updated_at FROM injuries "
            "WHERE ikey LIKE ? ORDER BY player", [f"{normalize_key(team)}|%"],
        ).fetchall()]

    def delete_injury(self, team: str, player: str) -> bool:
        from worldcupagents.dataflows.names import normalize_key
        cur = self.conn.execute("DELETE FROM injuries WHERE ikey = ?",
                                [f"{normalize_key(team)}|{normalize_key(player)}"])
        self.conn.commit()
        return cur.rowcount > 0

    # --- head coach / manager (name + a style & pedigree note) ---

    def upsert_team_coach(self, team: str, name: str | None = None, note: str | None = None,
                          source: str = "manual") -> None:
        """Upsert one team's coach. Merge-friendly: a name-only call keeps an existing
        note and vice-versa, so the data vendor (name) and the Guardian guide (prose)
        can each fill their half without clobbering the other."""
        from datetime import datetime, timezone
        from worldcupagents.dataflows.names import normalize_key
        tk = normalize_key(team)
        prev = self.team_coach(team) or {}
        # Source should credit whoever supplied the substantive prose note; a
        # name-only update must not overwrite the note's provenance.
        if note:
            new_source = source
        elif prev.get("note"):
            new_source = prev.get("source")
        else:
            new_source = source or prev.get("source")
        self.conn.execute(
            "INSERT OR REPLACE INTO team_coach (team_key, team, name, note, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [tk, team, name or prev.get("name"), note or prev.get("note"),
             new_source, datetime.now(timezone.utc).strftime("%Y-%m-%d")],
        )
        self.conn.commit()

    def team_coach(self, team: str) -> dict | None:
        from worldcupagents.dataflows.names import normalize_key
        row = self.conn.execute(
            "SELECT team, name, note, source, updated_at FROM team_coach WHERE team_key = ?",
            [normalize_key(team)],
        ).fetchone()
        return dict(row) if row else None

    def all_team_coaches(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT team, name, note, source, updated_at FROM team_coach ORDER BY team").fetchall()]

    def all_matches(self) -> list[dict]:
        cur = self.conn.execute(f"SELECT {', '.join(_COLS)} FROM matches ORDER BY date")
        return [dict(row) for row in cur.fetchall()]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    def international_results(self, since: str | None = None) -> list[dict]:
        """International results from the warehouse (`wh_matches`) for fitting
        national-team strengths: date, tournament, teams, scores. ``since`` bounds
        recency (ISO date). Empty if the warehouse hasn't been hoarded."""
        q = ("SELECT date, tournament, home_team, away_team, home_score, away_score, neutral "
             "FROM wh_matches")
        args: list = []
        if since is not None:
            q += " WHERE date >= ?"; args.append(since)
        try:
            return [dict(r) for r in self.conn.execute(q, args).fetchall()]
        except Exception:  # noqa: BLE001 — wh_matches absent (warehouse not hoarded)
            return []

    def source_coverage(self) -> list[dict]:
        """Per-``source`` rollup of the matches table: rows + newest date. Cheap
        (grouped in SQL) — the freshness/coverage signal for the ``sources`` command."""
        rows = self.conn.execute(
            "SELECT COALESCE(source, '?') AS source, COUNT(*) AS rows, MAX(date) AS latest "
            "FROM matches GROUP BY source ORDER BY rows DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- player stats ---

    def upsert_players(self, rows: list[dict]) -> int:
        n = 0
        for r in rows:
            key = f"{r.get('comp', '')}|{r['player']}|{r.get('team', '')}"
            vals = [key] + [r.get(c) for c in _PCOLS]
            self.conn.execute(
                f"INSERT OR REPLACE INTO player_stats (pkey, {', '.join(_PCOLS)}) "
                f"VALUES ({', '.join('?' * (len(_PCOLS) + 1))})",
                vals,
            )
            n += 1
        self.conn.commit()
        return n

    def update_xg(self, date: str, home: str, away: str, xg_home: float, xg_away: float) -> bool:
        """Fill xG on an EXISTING match row (no new rows). True if a row matched."""
        cur = self.conn.execute(
            "UPDATE matches SET xg_home = ?, xg_away = ? WHERE match_key = ?",
            [xg_home, xg_away, f"{date}|{home}|{away}"],
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- team situation breakdowns (set pieces / corners / penalties …) ---

    def upsert_situations(self, comp: str, season: str, team: str, data: dict, source: str,
                          xi: list | None = None) -> None:
        import json as _json
        self.conn.execute(
            "INSERT OR REPLACE INTO team_situations (skey, comp, season, team, data, xi, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [f"{comp}|{season}|{team}", comp, season, team, _json.dumps(data),
             _json.dumps(xi) if xi else None, source],
        )
        self.conn.commit()

    def situations(self, comp: str, season: str, team: str) -> tuple[dict, str] | None:
        import json as _json
        row = self.conn.execute(
            "SELECT data, source FROM team_situations WHERE skey = ?",
            [f"{comp}|{season}|{team}"],
        ).fetchone()
        return (_json.loads(row["data"]), row["source"]) if row else None

    def team_xi(self, comp: str, season: str, team: str) -> tuple[list, str] | None:
        import json as _json
        row = self.conn.execute(
            "SELECT xi, source FROM team_situations WHERE skey = ?",
            [f"{comp}|{season}|{team}"],
        ).fetchone()
        if not row or not row["xi"]:
            return None
        return _json.loads(row["xi"]), row["source"]

    def situation_coverage(self) -> dict[str, dict]:
        """Per-comp counts of stored situation rows and XI rows (for the explorer)."""
        out: dict[str, dict] = {}
        for r in self.conn.execute(
            "SELECT comp, COUNT(*) AS teams, "
            "SUM(CASE WHEN xi IS NOT NULL THEN 1 ELSE 0 END) AS xis "
            "FROM team_situations GROUP BY comp"
        ).fetchall():
            out[r["comp"] or "?"] = {"situations": r["teams"], "xis": r["xis"] or 0}
        return out

    def all_player_stats(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {', '.join(_PCOLS)} FROM player_stats ORDER BY comp, team, player"
        ).fetchall()]

    def all_situations(self) -> list[dict]:
        import json as _json
        rows = []
        for r in self.conn.execute(
            "SELECT comp, season, team, data, xi, source FROM team_situations ORDER BY comp, season, team"
        ).fetchall():
            xi = None
            if r["xi"]:
                try:
                    xi = _json.loads(r["xi"])
                except Exception:
                    pass
            rows.append({
                "comp": r["comp"], "season": r["season"], "team": r["team"],
                "situations": _json.loads(r["data"]) if r["data"] else {},
                "xi": xi, "source": r["source"],
            })
        return rows

    def players(self, comp: str | None = None) -> list[dict]:
        q = f"SELECT {', '.join(_PCOLS)} FROM player_stats"
        args: list = []
        if comp is not None:
            q += " WHERE comp = ?"
            args.append(comp)
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    # --- warehouse tables (data-hoard layer) ---

    def upsert_wh_source(self, row: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO wh_sources (source_id, name, homepage, license, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            [row.get("source_id"), row.get("name"), row.get("homepage"),
             row.get("license"), row.get("notes")],
        )
        self.conn.commit()

    def upsert_wh_source_file(self, row: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO wh_source_files "
            "(file_id, source_id, snapshot, path, url, sha256, bytes, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [row.get("file_id"), row.get("source_id"), row.get("snapshot"),
             row.get("path"), row.get("url"), row.get("sha256"),
             row.get("bytes"), row.get("fetched_at")],
        )
        self.conn.commit()

    def upsert_wh_ingestion_run(self, row: dict) -> None:
        import json as _json
        counts = row.get("counts_json")
        if isinstance(counts, dict):
            counts = _json.dumps(counts, sort_keys=True)
        self.conn.execute(
            "INSERT OR REPLACE INTO wh_ingestion_runs "
            "(run_id, source_id, snapshot, started_at, finished_at, status, counts_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [row.get("run_id"), row.get("source_id"), row.get("snapshot"),
             row.get("started_at"), row.get("finished_at"), row.get("status"), counts],
        )
        self.conn.commit()

    def upsert_wh_rows(self, table: str, rows: list[dict]) -> int:
        allowed = {
            "wh_teams", "wh_team_aliases", "wh_competitions", "wh_matches",
            "wh_match_sources", "wh_goals", "wh_shootouts", "wh_lineups",
            "wh_events", "wh_team_match_stats", "wh_player_match_stats",
            "wh_unresolved_names", "wh_players", "wh_player_aliases",
            "wh_player_career_totals", "wh_qual_documents", "wh_qual_segments",
            "wh_qual_claims", "wh_qual_links",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported warehouse table: {table}")
        if not rows:
            return 0
        cols = [r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")]
        writable = [c for c in cols if c in rows[0]]
        n = 0
        for row in rows:
            vals = [row.get(c) for c in writable]
            self.conn.execute(
                f"INSERT OR REPLACE INTO {table} ({', '.join(writable)}) "
                f"VALUES ({', '.join('?' * len(writable))})",
                vals,
            )
            n += 1
        self.conn.commit()
        return n

    def delete_wh_snapshot_facts(self, source_id: str, snapshot: str) -> None:
        """Remove normalized fact rows for a source snapshot before re-importing it.

        Raw source-file metadata and dimension rows (teams/competitions/aliases)
        are intentionally preserved.
        """
        file_like = f"{source_id}:{snapshot}:%"
        self.conn.execute(
            "DELETE FROM wh_match_sources WHERE source_id = ? AND file_id LIKE ?",
            [source_id, file_like],
        )
        for table in ("wh_goals", "wh_shootouts", "wh_matches"):
            self.conn.execute(
                f"DELETE FROM {table} WHERE source_id = ? AND snapshot = ?",
                [source_id, snapshot],
            )
        self.conn.commit()

    def warehouse_counts(self) -> dict[str, int]:
        tables = [
            "wh_sources", "wh_source_files", "wh_ingestion_runs", "wh_teams",
            "wh_team_aliases", "wh_competitions", "wh_matches", "wh_match_sources",
            "wh_goals", "wh_shootouts", "wh_lineups", "wh_events",
            "wh_team_match_stats", "wh_player_match_stats", "wh_qual_documents",
            "wh_players", "wh_player_aliases", "wh_player_career_totals",
            "wh_qual_segments", "wh_qual_claims", "wh_qual_links",
        ]
        return {
            t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables
        }

    # ── warehouse reads (predict-time taps — roadmap B1) ─────────────────────

    def wh_team_matches(self, team_id: str, limit: int = 5) -> list[dict]:
        """Most recent warehouse matches (internationals) involving a team."""
        return [dict(r) for r in self.conn.execute(
            "SELECT date, tournament, home_team, away_team, home_score, away_score, "
            "home_team_id, away_team_id, source_id FROM wh_matches "
            "WHERE home_team_id = ? OR away_team_id = ? ORDER BY date DESC LIMIT ?",
            [team_id, team_id, limit],
        ).fetchall()]

    def wh_h2h(self, team_id_a: str, team_id_b: str, limit: int = 5) -> list[dict]:
        """Most recent warehouse meetings between two teams (either venue)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT date, tournament, home_team, away_team, home_score, away_score, "
            "source_id FROM wh_matches "
            "WHERE (home_team_id = ? AND away_team_id = ?) "
            "   OR (home_team_id = ? AND away_team_id = ?) "
            "ORDER BY date DESC LIMIT ?",
            [team_id_a, team_id_b, team_id_b, team_id_a, limit],
        ).fetchall()]

    def career_totals_for_team(self, team_id: str, limit: int = 4) -> list[dict]:
        """Top career cap/goal totals for a national team (most goals first)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT player, caps, goals, start_year, end_year, source_url, source_id "
            "FROM wh_player_career_totals WHERE team_id = ? AND caps IS NOT NULL "
            "ORDER BY goals DESC, caps DESC LIMIT ?",
            [team_id, limit],
        ).fetchall()]

    def wc_player_aggregates(self, team_id: str, limit: int = 3) -> list[dict]:
        """Per-player event aggregates summed across stored matches (StatsBomb
        wh_player_match_stats), leading contributors first."""
        return [dict(r) for r in self.conn.execute(
            "SELECT player, "
            "SUM(CASE WHEN stat_name='passes' THEN stat_value END) AS passes, "
            "SUM(CASE WHEN stat_name='passes_completed' THEN stat_value END) AS passes_completed, "
            "SUM(CASE WHEN stat_name='progressive_passes' THEN stat_value END) AS progressive_passes, "
            "SUM(CASE WHEN stat_name='progressive_carries' THEN stat_value END) AS progressive_carries, "
            "SUM(CASE WHEN stat_name='xg' THEN stat_value END) AS xg, "
            "SUM(CASE WHEN stat_name='goals' THEN stat_value END) AS goals "
            "FROM wh_player_match_stats WHERE team_id = ? GROUP BY player "
            "ORDER BY goals DESC, xg DESC LIMIT ?",
            [team_id, limit],
        ).fetchall()]

    def latest_situations(self, comp: str, team: str) -> tuple[dict, str, str] | None:
        """Most recent season's situation row for (comp, team) — for tournaments
        where no season is configured (e.g. StatsBomb past-WC data).
        Returns (data, source, season) or None."""
        import json as _json
        row = self.conn.execute(
            "SELECT data, source, season FROM team_situations "
            "WHERE comp = ? AND team = ? ORDER BY season DESC LIMIT 1",
            [comp, team],
        ).fetchone()
        if not row or not row["data"]:
            return None
        return _json.loads(row["data"]), row["source"], row["season"]

    def raw_snapshots(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT source_id, snapshot, COUNT(*) AS files, SUM(bytes) AS bytes, "
            "MIN(fetched_at) AS first_fetched, MAX(fetched_at) AS last_fetched "
            "FROM wh_source_files GROUP BY source_id, snapshot ORDER BY source_id, snapshot"
        ).fetchall()]

    def latest_ingestion_runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT run_id, source_id, snapshot, started_at, finished_at, status, counts_json "
            "FROM wh_ingestion_runs ORDER BY started_at DESC LIMIT ?",
            [limit],
        ).fetchall()]

    def upsert_qual_documents(self, rows: list[dict]) -> int:
        return self.upsert_wh_rows("wh_qual_documents", rows)

    def upsert_qual_segments(self, rows: list[dict]) -> int:
        return self.upsert_wh_rows("wh_qual_segments", rows)

    def upsert_qual_claims(self, rows: list[dict]) -> int:
        return self.upsert_wh_rows("wh_qual_claims", rows)

    def upsert_qual_links(self, rows: list[dict]) -> int:
        return self.upsert_wh_rows("wh_qual_links", rows)

    def latest_qual_documents(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT document_id, source_id, source_type, title, url, published_at, "
            "fetched_at, text_chars, raw_path FROM wh_qual_documents "
            "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
            [limit],
        ).fetchall()]

    def qualitative_summary(self) -> dict:
        by_source = self.conn.execute(
            "SELECT source_id, COUNT(*) AS documents, SUM(text_chars) AS text_chars "
            "FROM wh_qual_documents GROUP BY source_id ORDER BY documents DESC"
        ).fetchall()
        claim_types = self.conn.execute(
            "SELECT claim_type, COUNT(*) AS claims FROM wh_qual_claims "
            "GROUP BY claim_type ORDER BY claims DESC"
        ).fetchall()
        return {
            "documents": self.conn.execute("SELECT COUNT(*) FROM wh_qual_documents").fetchone()[0],
            "segments": self.conn.execute("SELECT COUNT(*) FROM wh_qual_segments").fetchone()[0],
            "claims": self.conn.execute("SELECT COUNT(*) FROM wh_qual_claims").fetchone()[0],
            "links": self.conn.execute("SELECT COUNT(*) FROM wh_qual_links").fetchone()[0],
            "by_source": [dict(r) for r in by_source],
            "claim_types": [dict(r) for r in claim_types],
        }

    def upsert_wh_team(self, team_id: str, name: str, kind: str = "unknown",
                       source_id: str | None = None, source_name: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO wh_teams (team_id, name, kind, source_id, source_name) "
            "VALUES (?, ?, ?, ?, ?)",
            [team_id, name, kind, source_id, source_name or name],
        )
        self.conn.commit()

    def upsert_wh_team_alias(self, team_id: str, alias: str, source_id: str | None,
                             alias_norm: str, confidence: float = 1.0,
                             status: str = "active", notes: str | None = None) -> None:
        alias_key = f"{source_id or '*'}:{alias_norm}:{team_id}"
        self.conn.execute(
            "INSERT OR REPLACE INTO wh_team_aliases "
            "(alias_key, team_id, alias, source_id, alias_norm, confidence, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [alias_key, team_id, alias, source_id, alias_norm, confidence, status, notes],
        )
        self.conn.commit()

    def team_alias_rows(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT a.alias_key, a.team_id, a.alias, a.source_id, a.alias_norm, "
            "a.confidence, a.status, a.notes, t.name, t.kind "
            "FROM wh_team_aliases a LEFT JOIN wh_teams t ON t.team_id = a.team_id"
        ).fetchall()]

    def team_rows(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT team_id, name, kind, source_id, source_name FROM wh_teams"
        ).fetchall()]

    def record_unresolved_name(self, row: dict) -> None:
        import json as _json
        candidates = row.get("candidates_json")
        if isinstance(candidates, (list, dict)):
            candidates = _json.dumps(candidates, sort_keys=True)
        self.conn.execute(
            "INSERT INTO wh_unresolved_names "
            "(unresolved_id, raw_name, name_norm, kind, source_id, context, reason, "
            "candidates_json, first_seen, last_seen, count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(unresolved_id) DO UPDATE SET "
            "last_seen = excluded.last_seen, count = wh_unresolved_names.count + 1, "
            "candidates_json = excluded.candidates_json, reason = excluded.reason",
            [row.get("unresolved_id"), row.get("raw_name"), row.get("name_norm"),
             row.get("kind"), row.get("source_id"), row.get("context"),
             row.get("reason"), candidates, row.get("first_seen"),
             row.get("last_seen"), row.get("count", 1)],
        )
        self.conn.commit()

    def unresolved_names(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT raw_name, name_norm, kind, source_id, context, reason, "
            "candidates_json, first_seen, last_seen, count "
            "FROM wh_unresolved_names ORDER BY last_seen DESC, count DESC LIMIT ?",
            [limit],
        ).fetchall()]

    def entity_resolution_summary(self) -> dict:
        ambiguous = self.conn.execute(
            "SELECT alias_norm, COUNT(DISTINCT team_id) AS n "
            "FROM wh_team_aliases WHERE COALESCE(status, 'active') = 'active' "
            "GROUP BY alias_norm HAVING n > 1"
        ).fetchall()
        by_source = self.conn.execute(
            "SELECT COALESCE(source_id, '*') AS source_id, COUNT(*) AS aliases "
            "FROM wh_team_aliases WHERE COALESCE(status, 'active') = 'active' "
            "GROUP BY COALESCE(source_id, '*') ORDER BY aliases DESC"
        ).fetchall()
        return {
            "teams": self.conn.execute("SELECT COUNT(*) FROM wh_teams").fetchone()[0],
            "aliases": self.conn.execute(
                "SELECT COUNT(*) FROM wh_team_aliases WHERE COALESCE(status, 'active') = 'active'"
            ).fetchone()[0],
            "unresolved": self.conn.execute("SELECT COUNT(*) FROM wh_unresolved_names").fetchone()[0],
            "ambiguous_aliases": len(ambiguous),
            "aliases_by_source": [dict(r) for r in by_source],
        }

    def close(self) -> None:
        self.conn.close()
