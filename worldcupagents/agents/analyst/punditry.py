"""Punditry analyst — the prose→structured step for post-match ARTICLES.

The tactical analyst (``agents/analyst/tactical.py``) already distills minute-by-
minute LIVEBLOG commentary into a structured ``MatchTacticalReport``. This module
does the same job for the surrounding punditry — the match report and tactical
columns — distilling them into a ``PunditryDigest`` (tactical shape, key-player
verdicts, fatigue/injury flags, momentum) so the debate sees clean signals, not
raw article text.

Same patterns as tactical.py / pundit.py:
  * structured output via with_structured_output(..., include_raw=True)
  * token accumulation into a shared usage_acc dict
  * graceful degradation — use_llm off OR an LLM error yields a deterministic
    placeholder digest, never a crash.
"""

from __future__ import annotations

import logging

from worldcupagents.agents.analyst.tactical import make_match_id
from worldcupagents.agents.schemas import PunditryDigest, TeamPunditryRead

logger = logging.getLogger(__name__)

_MAX_BODY_CHARS = 6000   # cap the concatenated article text fed to the LLM


def make_punditry_extractor(config: dict, llm=None, usage_acc: dict | None = None):
    """Return a callable: (articles, home, away, date) -> PunditryDigest.

    ``articles`` is a list of {title, url, body} dicts (from
    ``GuardianCommentaryProvider.fetch_articles``). usage_acc: optional mutable
    {"input": int, "output": int} for token tracking.
    """
    use_llm = bool(config.get("use_llm")) and llm is not None

    def extract(articles: list[dict], home: str, away: str, date: str | None = None) -> PunditryDigest:
        sources = [a.get("url", "guardian") for a in articles]
        if not articles or not use_llm:
            return _placeholder_digest(home, away, date, articles)
        try:
            return _llm_digest(llm, articles, home, away, date, usage_acc)
        except Exception as e:  # noqa: BLE001 — visible degrade, never crash the pipeline
            logger.warning("Punditry analyst LLM error for %s v %s (%s); placeholder", home, away, e)
            return _placeholder_digest(home, away, date, articles)

    return extract


# ── internals ────────────────────────────────────────────────────────────────

def _articles_brief(articles: list[dict]) -> str:
    """Concatenate the article bodies into capped prompt text, with titles as
    lightweight separators so the model can tell the report from the columns."""
    parts: list[str] = []
    for a in articles:
        title = a.get("title", "").strip()
        body = (a.get("body") or "").strip()
        if not body:
            continue
        parts.append(f"--- {title} ---\n{body}")
    return "\n\n".join(parts)[:_MAX_BODY_CHARS]


def _llm_digest(llm, articles: list[dict], home: str, away: str,
                date: str | None, usage_acc: dict | None) -> PunditryDigest:
    prompt = f"""You are a football analyst distilling POST-MATCH PUNDITRY (the match \
report and tactical columns) on {home} (home) vs {away} (away) into structured signals.

PUNDITRY:
{_articles_brief(articles)}

Working ONLY from the text above (do not invent anything it does not support), fill a
read for EACH team — home ({home}) and away ({away}):
- tactical_shape: the formation/approach the pundits actually describe.
- standout_players: named player verdicts/ratings the punditry gives.
- fatigue_injuries: any fatigue, knocks, injuries or suspensions mentioned.
- momentum: one line on the team's morale/narrative going forward.
Return an empty list for any field the punditry does not support. Attribute each
read to the correct team; do not mix them up."""

    chain = llm.with_structured_output(PunditryDigest, include_raw=True)
    result = chain.invoke(prompt)

    raw = result.get("raw") if isinstance(result, dict) else None
    if usage_acc is not None and raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta:
            usage_acc["input"] += meta.get("input_tokens", 0)
            usage_acc["output"] += meta.get("output_tokens", 0)

    digest = result.get("parsed") if isinstance(result, dict) else result
    # Authoritative identity/provenance — never trust the model for these.
    digest.match_id = make_match_id(home, away, date)
    digest.home, digest.away, digest.date = home, away, date
    digest.home_read.team, digest.away_read.team = home, away
    digest.sources = [a.get("url", "guardian") for a in articles]
    return digest


def _placeholder_digest(home: str, away: str, date: str | None,
                        articles: list[dict]) -> PunditryDigest:
    """Deterministic offline digest — visible, sourced, no LLM."""
    n = len(articles)
    note = (f"[placeholder] {n} punditry article(s) collected; enable use_llm for extraction."
            if n else "[placeholder] no punditry articles found.")
    return PunditryDigest(
        match_id=make_match_id(home, away, date),
        home=home, away=away, date=date,
        home_read=TeamPunditryRead(team=home, momentum=note),
        away_read=TeamPunditryRead(team=away, momentum=note),
        sources=[a.get("url", "guardian") for a in articles],
    )
