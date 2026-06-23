"""Fixture stage resolution — derive a match's stage from the WC feed.

`Fixture.stage` used to default to GROUP, so a knockout predicted without `--stage`
was silently treated as a group game (wrong draw handling). The football-data feed
already carries the true stage per fixture; this resolves it so the draw uplift and
the knockout draw-fold key off a reliable flag. Precedence is enforced by the caller:
explicit user choice > feed-derived > group fallback.
"""

from __future__ import annotations

import logging

from worldcupagents.agents.schemas import Stage
from worldcupagents.dataflows.names import canonical_name, normalize_key

logger = logging.getLogger(__name__)

# football-data.org `stage` strings → our Stage enum. Anything not GROUP_STAGE is a
# knockout (3rd-place play-off included — it's a one-off winner-takes-it game).
_FEED_STAGE = {
    "GROUP_STAGE": Stage.GROUP, "GROUP": Stage.GROUP,
    "LAST_32": Stage.R32, "ROUND_OF_32": Stage.R32,
    "LAST_16": Stage.R16, "ROUND_OF_16": Stage.R16,
    "QUARTER_FINALS": Stage.QF, "QUARTER_FINAL": Stage.QF,
    "SEMI_FINALS": Stage.SF, "SEMI_FINAL": Stage.SF,
    "FINAL": Stage.FINAL, "3RD_PLACE": Stage.FINAL, "THIRD_PLACE": Stage.FINAL,
}


def map_feed_stage(s: str | None) -> Stage | None:
    """Map a football-data `stage` string to our Stage enum (None if unrecognised)."""
    return _FEED_STAGE.get((s or "").upper().strip()) if s else None


def resolve_stage(home: str, away: str, date: str | None, config: dict) -> tuple[Stage | None, str]:
    """(stage, source) for a fixture, from the live WC feed.

    Returns ``(Stage, "feed")`` when the fixture is found and its stage maps cleanly;
    ``(None, "absent")`` when the fixture isn't in the feed (hypothetical matchup, no
    token, or offline) — the caller then keeps the user's flag or falls back to group.
    Orientation-agnostic (home/away may be swapped) and date-filtered when given.
    """
    try:
        from worldcupagents.pipelines.simulate import load_wc_fixtures
        fixtures = load_wc_fixtures(config)
    except Exception as e:  # noqa: BLE001 — feed issues must not break predict
        logger.info("stage resolve: fixtures unavailable (%s)", e)
        return None, "absent"

    want = {normalize_key(canonical_name(home)), normalize_key(canonical_name(away))}
    for f in fixtures:
        fh, fa = f.get("home"), f.get("away")
        if not fh or not fa:
            continue
        if {normalize_key(fh), normalize_key(fa)} != want:
            continue
        if date and f.get("date") and f["date"] != date:
            continue
        st = map_feed_stage(f.get("stage"))
        if st is not None:
            return st, "feed"
    return None, "absent"
