"""WC2026 tournament simulator (roadmap D1) — Monte-Carlo on the Poisson engine.

Reads the REAL fixture list (football-data.org: 12 groups, played results locked
in as fact), simulates every unplayed match by sampling Poisson goals from the
same λ machinery the predictor uses, plays out the 2026 format (top 2 of each
group + best thirds fill the 32-team knockout), and tallies how often each team
reaches each round. No LLM calls — 10k tournaments cost seconds, not dollars.

Honest limits, stated in the output:
  * Knockout pairings: until the groups conclude, the feed's LAST_32 slots are
    TBD, so the bracket uses a SEEDED APPROXIMATION (qualifiers ranked by group
    performance, 1v32/2v31 …) — not FIFA's official thirds-allocation table.
    Once football-data.org fills the real pairings they are used automatically.
  * λ for national teams comes from fitted strengths where store data exists,
    else the rank-Elo baseline (most WC teams pre-tournament).
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.dataflows.names import canonical_name

logger = logging.getLogger(__name__)

_ROUNDS = ("r32", "r16", "qf", "sf", "final", "champion")
_ROUND_BY_SIZE = {32: "r32", 16: "r16", 8: "qf", 4: "sf", 2: "final"}


@dataclass
class SimResult:
    n: int
    teams: dict[str, dict] = field(default_factory=dict)   # team -> counters
    groups: dict[str, list[str]] = field(default_factory=dict)
    bracket_source: str = "seeded approximation (official R32 pairings TBD)"
    played: int = 0
    remaining: int = 0

    def share(self, team: str, key: str) -> float:
        return self.teams.get(team, {}).get(key, 0) / self.n if self.n else 0.0


def load_wc_fixtures(config: dict | None = None) -> list[dict]:
    """All World Cup fixtures (played + scheduled) with stage/group labels,
    canonical team names. Empty on any error — the simulator then has nothing
    to chew on and says so."""
    config = dict(config or DEFAULT_CONFIG)
    config.setdefault("fd_competition", "WC")
    try:
        from worldcupagents.dataflows.providers.football_data_org import BASE, FootballDataOrgProvider
        prov = FootballDataOrgProvider.from_config(config)
        data = prov.http.get_json(f"{BASE}/competitions/WC/matches", prov._headers, ttl=3_600)
    except Exception as e:  # noqa: BLE001 — no token/network → empty, never crash
        logger.warning("simulate: fixtures unavailable (%s)", e)
        return []
    out = []
    for m in data.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        home = (m.get("homeTeam") or {}).get("name")
        away = (m.get("awayTeam") or {}).get("name")
        out.append({
            "stage": m.get("stage"),
            "group": (m.get("group") or "").replace("GROUP_", "") or None,
            "status": m.get("status"),
            "home": canonical_name(home) if home else None,
            "away": canonical_name(away) if away else None,
            "hg": ft.get("home"), "ag": ft.get("away"),
            "date": (m.get("utcDate") or "")[:10],
        })
    return out


# ── goal sampling ─────────────────────────────────────────────────────────────

def _default_lambdas(config: dict):
    """(home, away) -> (λh, λa) using the predictor's own machinery: fitted
    strengths where the store knows both teams, rank-Elo otherwise."""
    from worldcupagents.dataflows import fifa_rankings
    from worldcupagents.ensemble.strength import load_strength_model, team_lambdas
    try:
        strength = load_strength_model({**config, "fd_competition": "WC"})
    except Exception:  # noqa: BLE001
        strength = None

    def lambdas(home: str, away: str):
        return team_lambdas(home, away,
                            fifa_rankings.get_rank(home), fifa_rankings.get_rank(away),
                            strength)
    return lambdas


def _poisson_sample(lam: float, rng: random.Random) -> int:
    """Knuth's algorithm — dependency-free Poisson draw."""
    threshold = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


# ── tournament mechanics ──────────────────────────────────────────────────────

def _rank_table(rows: dict[str, list], rng: random.Random) -> list[str]:
    """Order teams by points, goal difference, goals for; coin-flip the rest
    (a Monte-Carlo stand-in for fair-play/drawing-of-lots tiebreakers)."""
    return sorted(rows, key=lambda t: (rows[t][0], rows[t][1], rows[t][2], rng.random()),
                  reverse=True)


def _qualifier_target(n_groups: int) -> int:
    """Knockout size: smallest power of two ≥ 2 per group (2026: 12 groups → 32)."""
    size = 1
    while size < 2 * n_groups:
        size *= 2
    return size


def _bracket_order(size: int) -> list[int]:
    """Classic bracket slot order so top seeds can only meet late: for 4 →
    [0,3,1,2] (1v4, 2v3 with 1 and 2 in opposite halves); same pattern for 32."""
    order = [0]
    while len(order) < size:
        m = len(order) * 2
        order = [x for pair in zip(order, (m - 1 - x for x in order)) for x in pair]
    return order


def simulate_tournament(config: dict | None = None, n: int = 10_000, seed: int = 1,
                        fixtures: list[dict] | None = None, lambdas_fn=None) -> SimResult:
    config = dict(config or DEFAULT_CONFIG)
    fixtures = fixtures if fixtures is not None else load_wc_fixtures(config)
    group_fx = [f for f in fixtures if f.get("group") and f.get("home") and f.get("away")]
    if not group_fx:
        return SimResult(n=0)
    lambdas_fn = lambdas_fn or _default_lambdas(config)
    rng = random.Random(seed)

    groups: dict[str, list[str]] = {}
    for f in group_fx:
        for t in (f["home"], f["away"]):
            if t not in groups.setdefault(f["group"], []):
                groups[f["group"]].append(t)

    played = [f for f in group_fx if f["status"] == "FINISHED"
              and f["hg"] is not None and f["ag"] is not None]
    remaining = [f for f in group_fx if f not in played]

    # Real knockout pairings, when the feed has them (post-group-stage).
    real_r32 = [(f["home"], f["away"]) for f in fixtures
                if f.get("stage") == "LAST_32" and f.get("home") and f.get("away")]

    # λ is fixture-independent — compute once per pair, not per iteration.
    lam_cache: dict[tuple[str, str], tuple[float, float]] = {}

    def lam(home: str, away: str) -> tuple[float, float]:
        key = (home, away)
        if key not in lam_cache:
            lam_cache[key] = lambdas_fn(home, away)
        return lam_cache[key]

    result = SimResult(n=n, groups={g: list(ts) for g, ts in sorted(groups.items())},
                       played=len(played), remaining=len(remaining))
    if real_r32:
        result.bracket_source = "official LAST_32 pairings (fixtures feed)"
    counters = {t: {k: 0 for k in ("group_win",) + _ROUNDS}
                for ts in groups.values() for t in ts}

    for _ in range(n):
        # 1) Group stage: locked results + sampled remainder.
        table: dict[str, dict[str, list]] = {
            g: {t: [0, 0, 0] for t in ts} for g, ts in groups.items()}  # pts, gd, gf

        def apply(g, home, away, hg, ag):
            rows = table[g]
            rows[home][1] += hg - ag; rows[home][2] += hg
            rows[away][1] += ag - hg; rows[away][2] += ag
            if hg > ag:
                rows[home][0] += 3
            elif hg < ag:
                rows[away][0] += 3
            else:
                rows[home][0] += 1; rows[away][0] += 1

        for f in played:
            apply(f["group"], f["home"], f["away"], f["hg"], f["ag"])
        for f in remaining:
            lh, la = lam(f["home"], f["away"])
            apply(f["group"], f["home"], f["away"],
                  _poisson_sample(lh, rng), _poisson_sample(la, rng))

        # 2) Qualifiers: top 2 per group + best thirds to fill the bracket.
        winners, runners, thirds = [], [], []
        for g in sorted(table):
            order = _rank_table(table[g], rng)
            winners.append((order[0], table[g][order[0]]))
            runners.append((order[1], table[g][order[1]]))
            if len(order) > 2:
                thirds.append((order[2], table[g][order[2]]))
        for t, _stats in winners:
            counters[t]["group_win"] += 1
        target = _qualifier_target(len(groups))
        n_thirds = target - 2 * len(groups)
        thirds.sort(key=lambda x: (x[1][0], x[1][1], x[1][2], rng.random()), reverse=True)
        qualified = winners + runners + thirds[:max(n_thirds, 0)]

        # 3) Bracket: real pairings when known, else seeded 1v32/2v31 approximation.
        if real_r32:
            qset = {t for t, _ in qualified}
            field_pairs = [(h, a) for h, a in real_r32 if h in qset and a in qset]
            entrants = [t for pair in field_pairs for t in pair]
        else:
            seeds = ([t for t, _ in sorted(winners, key=lambda x: (x[1][0], x[1][1], x[1][2]), reverse=True)]
                     + [t for t, _ in sorted(runners, key=lambda x: (x[1][0], x[1][1], x[1][2]), reverse=True)]
                     + [t for t, _ in thirds[:max(n_thirds, 0)]])
            slots = [seeds[i] for i in _bracket_order(len(seeds))]
            field_pairs = [(slots[i], slots[i + 1]) for i in range(0, len(slots) - 1, 2)]
            entrants = seeds

        for t in entrants:
            counters[t][_ROUND_BY_SIZE.get(len(entrants), "r32")] += 1

        # 4) Knockouts: sample; level after 90' → winner by strength share.
        pairs = field_pairs
        while pairs:
            nxt = []
            for home, away in pairs:
                lh, la = lam(home, away)
                hg, ag = _poisson_sample(lh, rng), _poisson_sample(la, rng)
                if hg == ag:  # extra time / pens, weighted by goal expectancy
                    winner = home if rng.random() < lh / (lh + la) else away
                else:
                    winner = home if hg > ag else away
                nxt.append(winner)
            if len(nxt) == 1:
                counters[nxt[0]]["champion"] += 1
                break
            round_key = _ROUND_BY_SIZE.get(len(nxt))
            for t in nxt:
                if round_key:
                    counters[t][round_key] += 1
            pairs = [(nxt[i], nxt[i + 1]) for i in range(0, len(nxt) - 1, 2)]

    result.teams = counters
    return result


def export_simulation(result: SimResult, config: dict | None = None):
    """Persist the run for the explorer / later inspection."""
    import json
    from pathlib import Path
    config = dict(config or DEFAULT_CONFIG)
    out_dir = Path(config.get("exports_dir", "exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "wc2026_sim.json"
    path.write_text(json.dumps({
        "n": result.n, "bracket_source": result.bracket_source,
        "played": result.played, "remaining": result.remaining,
        "teams": {t: {k: round(v / result.n, 4) for k, v in c.items()}
                  for t, c in result.teams.items()},
        "groups": result.groups,
    }, indent=1), encoding="utf-8")
    return path
