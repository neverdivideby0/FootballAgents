"""Pre-match dossier — the unified lookup that assembles everything the model
knows about a fixture into one structured, squad-scoped, recency-bounded view.

This is the `footballagents dossier HOME AWAY` command. It does NOT call an LLM;
it gathers and formats the same data the debate runs on, so you can eyeball
exactly what the agents will see before spending tokens. Every block is sourced.

Design rules from the owner (2026-06):
  * Only show player stats for players in the CURRENT squad.
  * No games older than 5 years.
  * Show line-up, player stats, recent scores+stats, style of play (attack/defense
    forte + set pieces + tempo/discipline), and learnings from past predictions.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from worldcupagents.dataflows.enrich import enrich_profile
from worldcupagents.dataflows.interface import get_provider
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

RECENCY_YEARS = 5


def _since_date() -> str:
    return (date.today() - timedelta(days=365 * RECENCY_YEARS)).isoformat()


def _fold(name: str) -> str:
    return normalize_key(canonical_name(name or ""))


def _squad_player_stats(team: str, squad_names: set[str], config: dict, limit: int = 8) -> list[dict]:
    """Player-stat rows for CURRENT-squad members only, richest+highest-contribution
    first. Accent-insensitive squad match (Gyökeres == Gyokeres)."""
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return []
    store = MatchStore.from_config(config)
    try:
        rows = store.players(comp=config.get("fd_competition"))
    finally:
        store.close()
    from worldcupagents.dataflows.entities import same_team
    mine = [r for r in rows if same_team(team, r.get("team") or "", config=config)]
    if squad_names:
        mine = [r for r in mine if _fold(r["player"]) in squad_names]
    # de-dupe accent variants, keep the richer row
    best: dict[str, dict] = {}
    for r in mine:
        k = _fold(r["player"])
        richness = sum(1 for v in r.values() if v is not None)
        if k not in best or richness > sum(1 for v in best[k].values() if v is not None):
            best[k] = r
    out = sorted(best.values(),
                 key=lambda r: (r.get("goals") or 0) + (r.get("assists") or 0), reverse=True)
    # National teams have no club stats under comp=WC — fall back to each squad
    # player's CLUB form (matched by name across leagues), so the table isn't empty.
    if not out and config.get("league_kind") != "league" and squad_names:
        from worldcupagents.recall import squad_club_stats
        cs = squad_club_stats(config, list(squad_names), n=limit)
        return [{**c.model_dump(), "club": c.team} for c in cs]
    return out[:limit]


def _career_totals(config: dict, store, team: str, squad_names=None) -> list[dict]:
    """Career caps/goals (Wikipedia infoboxes) for CURRENT-SQUAD players only —
    leading scorers first; retired legends are filtered out."""
    try:
        from worldcupagents.dataflows.entities import resolve_team
        tid = resolve_team(team, kind="national", config=config).team_id
        if not tid:
            return []
        rows = store.career_totals_for_team(tid, limit=60)
        if squad_names:
            import unicodedata
            af = lambda s: unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()  # noqa: E731
            keys = {af(s) for s in squad_names}
            rows = [r for r in rows if af(r.get("player", "")) in keys]
        return rows[:6]
    except Exception:  # noqa: BLE001
        return []


def _recent_form(profile, since: str, n: int = 6) -> list:
    """Form results within the recency window (profile.form is already season/recency
    aware; this is the belt-and-braces date filter)."""
    out = [r for r in profile.form if not r.date or r.date >= since]
    return out[:n]


def _team_block(team: str, config: dict, profile, strength, since: str) -> dict:
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    from worldcupagents.ensemble.strength import team_forte

    squad_names = {_fold(p.name) for p in profile.squad}
    block = {
        "team": team,
        "fifa_rank": profile.fifa_rank,
        "formation": profile.formation,
        "squad_size": len(profile.squad),
        "probable_xi": list(profile.probable_xi),
        "players": _squad_player_stats(team, squad_names, config),
        "form": [{"opponent": r.opponent, "gf": r.goals_for, "ga": r.goals_against,
                  "date": r.date, "source": r.source} for r in _recent_form(profile, since)],
        "xg_for": profile.xg_for, "xg_against": profile.xg_against,
        "forte": team_forte(strength, team),
        "tempo": None, "set_pieces": None, "style_note": profile.style or None,
        "recent": [], "style_fingerprint": None, "qual_notes": None,
        "sources": list(profile.sources),
    }
    comp, season = config.get("fd_competition"), config.get("season")
    if db_path(config).exists():
        store = MatchStore.from_config(config)
        try:
            block["career"] = _career_totals(config, store, team, [p.name for p in profile.squad])
            block["tempo"] = store.team_stat_profile(team, comp=comp, since=since)
            block["recent"] = store.recent_team_matches(team, comp=comp, since=since, limit=6)
            if season:
                hit = store.situations(comp, season, team)
            else:
                hit = None
                latest = store.latest_situations(comp or "WC", team)
                if latest:
                    hit = (latest[0], latest[1])
            if hit:
                block["set_pieces"] = {"data": hit[0], "source": hit[1]}
                style = (hit[0] or {}).get("style")  # StatsBomb playing-style fingerprint
                if isinstance(style, dict):
                    block["style_fingerprint"] = style
        finally:
            store.close()
    # Team playing style in prose (manual / scraped notes from the warehouse).
    block["qual_notes"] = _safe(_team_qual_brief, config, team)
    # Head coach: name (data vendor) + style & pedigree (Guardian Experts' guide).
    try:
        from worldcupagents.dataflows.coach import coach_brief
        block["coach"] = coach_brief(config, team, profile)
    except Exception as e:  # noqa: BLE001
        logger.warning("dossier coach brief failed for %s (%s)", team, e)
        block["coach"] = None
    return block


def build_dossier(home: str, away: str, config: dict) -> dict:
    """Assemble the full pre-match dossier dict (no LLM)."""
    from worldcupagents.dataflows.records import records_summary
    from worldcupagents.dataflows.weaknesses import find_weaknesses
    from worldcupagents.ensemble.strength import load_strength_model
    from worldcupagents.recall import prediction_lessons, qualitative_brief

    provider = get_provider(config, "squads")
    since = _since_date()
    home_profile = enrich_profile(provider.get_team_profile(home), config)
    away_profile = enrich_profile(provider.get_team_profile(away), config)
    try:
        strength = load_strength_model(config)
    except Exception:  # noqa: BLE001
        strength = None

    home_block = _team_block(home, config, home_profile, strength, since)
    away_block = _team_block(away, config, away_profile, strength, since)
    home_block["weaknesses"] = _weaknesses(config, home_profile, away)
    away_block["weaknesses"] = _weaknesses(config, away_profile, home)
    home_block["player_notes"] = _player_notes(config, home_profile)
    away_block["player_notes"] = _player_notes(config, away_profile)
    dossier = {
        "home": home_block,
        "away": away_block,
        "records": records_summary(home, away, config),
        "learnings": _safe(prediction_lessons, home, away, config),
        "notes": _safe(qualitative_brief, home, away, config),
        "since": since,
    }
    # The honest counterweight, baseline-only (no LLM): the model's call + the
    # live alternative, so the dossier never reads like a coronation.
    dossier["verdict"] = _baseline_verdict(config, home, away, home_profile, away_profile)
    try:
        from worldcupagents.dataflows.market import market_read
        dossier["market"] = market_read(config, home, away)
    except Exception:  # noqa: BLE001
        dossier["market"] = None
    return dossier


def _baseline_verdict(config, home, away, home_profile, away_profile):
    """A no-LLM verdict (with its upset watch) for the dossier view."""
    try:
        from worldcupagents.agents.schemas import Fixture, Stage
        from worldcupagents.ensemble.alternative import upset_factors
        from worldcupagents.ensemble.verdict import assemble_verdict
        knockout = config.get("league_kind") != "league" and config.get("neutral_venue", True)
        fx = Fixture(home=home, away=away, stage=Stage.QF if knockout else Stage.GROUP)
        v = assemble_verdict(config, fx, home_profile, away_profile, None,
                             config.get("ensemble_judge_weight", 0.6))
        if v.alternative:
            v.alternative.swing_factors = upset_factors(config, fx, home_profile, away_profile, v.alternative)
        return v
    except Exception as e:  # noqa: BLE001
        logger.warning("dossier baseline verdict failed (%s)", e)
        return None


def _safe(fn, *args):
    try:
        return (fn(*args) or "").strip()
    except Exception as e:  # noqa: BLE001 — a missing brief must not break the dossier
        logger.warning("dossier: %s failed (%s)", getattr(fn, "__name__", fn), e)
        return ""


def _weaknesses(config: dict, profile, opponent: str) -> list[str]:
    from worldcupagents.dataflows.weaknesses import find_weaknesses
    try:
        return find_weaknesses(config, profile, opponent)
    except Exception as e:  # noqa: BLE001
        logger.warning("dossier weaknesses failed for %s (%s)", profile.team, e)
        return []


def _team_qual_brief(config: dict, team: str) -> str:
    """That team's qualitative/style notes from the warehouse (manual + scraped)."""
    from worldcupagents.recall import qualitative_brief
    return qualitative_brief(team, team, config, per_team=3)


def _player_notes(config: dict, profile) -> list[dict]:
    """User-authored per-player scouting notes for current-squad members."""
    from worldcupagents.dataflows.match_store import MatchStore, db_path
    if not db_path(config).exists():
        return []
    import unicodedata
    af = lambda s: unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()  # noqa: E731
    squad = {af(p.name) for p in profile.squad}
    store = MatchStore.from_config(config)
    try:
        notes = store.player_notes_for_team(profile.team)
    finally:
        store.close()
    return [n for n in notes if not squad or af(n["player"]) in squad]


def dossier_markdown(doss: dict) -> str:
    """Render the dossier dict as readable markdown — embedded in the match report
    so the reader can see the raw data the agents worked from and form their own view."""
    out: list[str] = []

    def team_md(b: dict) -> None:
        nonlocal out
        rank = f" (FIFA #{b['fifa_rank']})" if b.get("fifa_rank") else ""
        out.append(f"### {b['team']}{rank}")
        if b.get("formation"):
            out.append(f"- **Formation:** {b['formation']}")
        co = b.get("coach")
        if co:
            from worldcupagents.dataflows.coach import coach_digest
            digest = coach_digest(co)
            if digest:
                out.append(f"- **Coach:** {digest}")
        fo = b.get("forte")
        if fo:
            out.append(f"- **Forte:** {fo['label']} (attack {fo['attack']}, solidity {fo['solidity']})")
        tp = b.get("tempo")
        if tp:
            out.append(f"- **Tempo/discipline** (last {tp['n']}): {tp['shots']} shots "
                       f"({tp['sot']} on target), {tp['corners']} corners, {tp['fouls']} fouls, "
                       f"{tp['yellow']}🟨 {tp['red']}🟥 per game")
        sf = b.get("style_fingerprint")
        if sf:
            sbits = []
            if sf.get("possession_share"):
                sbits.append(f"possession {sf['possession_share']:.0%}")
            if sf.get("directness"):
                sbits.append(f"directness {sf['directness']:.2f}")
            if sf.get("top_pass_pairs"):
                sbits.append("key combos: " + ", ".join(sf["top_pass_pairs"][:2]))
            if sf.get("build_up_zones"):
                sbits.append("builds via " + ", ".join(sf["build_up_zones"][:2]))
            if sbits:
                out.append(f"- **Playing style:** {'; '.join(sbits)}")
        sp = b.get("set_pieces")
        if sp:
            from worldcupagents.dataflows.providers.understat import situations_digest
            try:
                out.append(f"- **Set pieces:** {situations_digest(sp['data'], b['team'])[:240]}")
            except Exception:  # noqa: BLE001
                pass
        if b.get("probable_xi"):
            out.append(f"- **Likely XI** (most-used): {', '.join(b['probable_xi'][:11])}")
        if b.get("players"):
            club = any(p.get("club") for p in b["players"])
            hdr = "| Player | " + ("Club | " if club else "") + "G | A | Sh | xG | xA | build-up xG | KeyP | Min |"
            sep = "|---|" + ("---|" if club else "") + "---|---|---|---|---|---|---|---|"
            out.extend(["", hdr, sep])
            for p in b["players"][:8]:
                cells = [p["player"]] + ([p.get("club") or "—"] if club else [])
                cells += [str(p.get("goals") or 0), str(p.get("assists") or 0),
                          str(p.get("shots") or "—"),
                          f"{p['xg']:.1f}" if p.get("xg") is not None else "—",
                          f"{p['xa']:.1f}" if p.get("xa") is not None else "—",
                          f"{p['xg_buildup']:.1f}" if p.get("xg_buildup") is not None else "—",
                          str(p.get("key_passes") or "—"), str(p.get("minutes") or "—")]
                out.append("| " + " | ".join(cells) + " |")
            out.append("")
        rec = b.get("recent") or []
        if rec:
            has_stats = any(m.get("shots") is not None for m in rec)
            out.append("")
            if has_stats:
                out.append("| Date | | Opponent | Score | Shots (OT) | Corners | Fouls | Cards | xG | xGA |")
                out.append("|---|---|---|---|---|---|---|---|---|---|")
            else:
                out.append("| Date | | Opponent | Score | xG | xGA |")
                out.append("|---|---|---|---|---|---|")
            for m in rec:
                base = [m["date"], m["venue"], m["opponent"], f"{m['result']} {m['gf']}-{m['ga']}"]
                if has_stats:
                    cards = f"{int(m['yellow'] or 0)}Y" + (f"/{int(m['red'])}R" if m.get("red") else "")
                    base += [f"{m['shots'] or '—'} ({m['sot'] or '—'})", str(m.get("corners") or "—"),
                             str(m.get("fouls") or "—"), cards]
                base += [f"{m['xg']:.1f}" if m.get("xg") is not None else "—",
                         f"{m['xga']:.1f}" if m.get("xga") is not None else "—"]
                out.append("| " + " | ".join(base) + " |")
            out.append("")
        if b.get("career"):
            out.append("- **Career caps/goals:** " + "; ".join(
                f"{c['player']} {c['caps']}/{c['goals'] or 0}" for c in b["career"]))
        if b.get("form"):
            out.append("- **Recent (≤5y):** " + "; ".join(
                f"{'W' if f['gf']>f['ga'] else 'L' if f['gf']<f['ga'] else 'D'} "
                f"{f['gf']}-{f['ga']} v {f['opponent']}" for f in b["form"][:6]))
        if b.get("weaknesses"):
            out.append("- **Weaknesses:** " + "; ".join(b["weaknesses"]))
        if b.get("qual_notes"):
            out.append("- **Style notes:** " + b["qual_notes"].replace("\n", " ")[:400])
        if b.get("player_notes"):
            out.append("- **Scouting notes (yours):**")
            for n in b["player_notes"]:
                note = " ".join((n["note"] or "").split())
                out.append(f"  - {n['player']} — {note}")
        out.append("")

    team_md(doss["home"])
    team_md(doss["away"])
    if doss.get("market"):
        from worldcupagents.dataflows.market import market_digest
        out.append(f"**{market_digest(doss['market'])}**\n")
    if doss.get("records"):
        out.append(f"**Head-to-head / records:** {doss['records']}\n")
    return "\n".join(out).strip()
