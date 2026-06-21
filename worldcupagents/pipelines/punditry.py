"""analyze_punditry — the post-game PUNDITRY pipeline (structured-signal sibling of
analyze_match).

    A. fetch    Guardian articles for the match (report + tactical columns)
    B. raw      save each article to data/punditry/<id>/NN.json  (provenance)
    C. extract  make_punditry_extractor(config, llm)  -> PunditryDigest
    store       memory/punditry/<id>.json             (debate-facing, distilled)

Offline by default (placeholder extractor, no spend). Set use_llm — e.g.
``footballagents watch ... --provider openai`` — to run the real analyst. Degrades
gracefully at every step: a missing key, a failed fetch, or an LLM error never
crashes; you get a placeholder-grade digest instead.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from worldcupagents.agents.analyst.punditry import make_punditry_extractor
from worldcupagents.agents.analyst.tactical import make_match_id
from worldcupagents.agents.schemas import PunditryDigest
from worldcupagents.config import DEFAULT_CONFIG
from worldcupagents.llm_clients.factory import create_llm
from worldcupagents.llm_clients.model_catalog import estimate_cost

logger = logging.getLogger(__name__)


@dataclass
class PunditryOutcome:
    digest: PunditryDigest
    n_articles: int
    usage: dict
    cost: float | None
    model: str | None
    json_path: Path | None


def analyze_punditry(
    home: str,
    away: str,
    date: str | None = None,
    config: dict | None = None,
    llm=None,
    persist: bool = True,
    force: bool = False,
    fetch_articles=None,
) -> PunditryOutcome:
    """Distil the match's punditry into a structured PunditryDigest.

    force=False (default): if a populated digest already exists, return it instead
    of overwriting (an accidental offline re-run won't clobber a good LLM digest).
    ``fetch_articles`` (test injection): a callable(home, away, date) -> list[dict];
    when None, the configured commentary provider's fetch_articles is used.
    """
    config = dict(config or DEFAULT_CONFIG)
    usage = {"input": 0, "output": 0}
    model = config.get("quick_think_llm", "") if config.get("use_llm") else None

    if persist and not force:
        existing = _load_existing(home, away, date, config)
        if existing is not None:
            logger.info("analyze_punditry: existing digest for %s vs %s (force=True to overwrite)", home, away)
            return PunditryOutcome(existing, 0, {}, None, None, _digest_path(home, away, date, config))

    if config.get("use_llm") and llm is None:
        try:
            llm = create_llm(config["llm_provider"], model)
        except Exception as e:  # noqa: BLE001
            logger.warning("punditry analyst LLM unavailable (%s); using placeholder.", e)
            llm, model = None, None

    # A. fetch  ->  B. raw snapshot  ->  C. extract
    articles = _fetch(home, away, date, config, fetch_articles)
    if persist:
        _save_raw(home, away, date, articles, config)
    extract = make_punditry_extractor(config, llm, usage)
    digest = extract(articles, home, away, date)

    cost = estimate_cost(model, usage["input"], usage["output"]) if model else None
    json_path = _persist(digest, config) if persist else None
    return PunditryOutcome(digest, len(articles), dict(usage), cost, model, json_path)


# ── helpers ──────────────────────────────────────────────────────────────────

def _fetch(home, away, date, config, fetch_articles) -> list[dict]:
    if fetch_articles is None:
        from worldcupagents.dataflows.commentary.registry import get_commentary_provider
        provider = get_commentary_provider(config)
        fetch_articles = getattr(provider, "fetch_articles", None)
    if fetch_articles is None:  # placeholder provider has no article search
        return []
    try:
        return fetch_articles(home, away, date) or []
    except Exception as e:  # noqa: BLE001 — a fetch failure must not crash the tick
        logger.warning("punditry fetch failed for %s v %s (%s)", home, away, e)
        return []


def _punditry_dir(config: dict) -> Path:
    return Path(config.get("memory_dir", "memory")) / "punditry"


def _digest_path(home, away, date, config) -> Path:
    return _punditry_dir(config) / f"{make_match_id(home, away, date)}.json"


def _raw_dir(home, away, date, config) -> Path:
    return Path(config.get("data_dir", "data")) / "punditry" / make_match_id(home, away, date)


def _has_content(d: PunditryDigest) -> bool:
    return any(r.tactical_shape or r.standout_players or r.fatigue_injuries
              for r in (d.home_read, d.away_read))


def _load_existing(home, away, date, config) -> PunditryDigest | None:
    path = _digest_path(home, away, date, config)
    if not path.exists():
        return None
    try:
        d = PunditryDigest.model_validate_json(path.read_text(encoding="utf-8"))
        return d if _has_content(d) else None
    except Exception:  # noqa: BLE001
        return None


def _save_raw(home, away, date, articles: list[dict], config: dict) -> None:
    if not articles:
        return
    out = _raw_dir(home, away, date, config)
    out.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, a in enumerate(articles):
        (out / f"{i:02d}.json").write_text(
            json.dumps({**a, "fetched_at": fetched_at}, indent=2), encoding="utf-8")


def _persist(digest: PunditryDigest, config: dict) -> Path:
    out = _punditry_dir(config)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{digest.match_id}.json"
    path.write_text(json.dumps(digest.model_dump(mode="json"), indent=2), encoding="utf-8")
    return path
