"""Commentary-provider registry — parallel to dataflows.interface.

Kept separate because CommentaryProvider has a different shape than
FootballDataProvider. Same resilience contract: if the configured vendor can't
be built (e.g. guardian with no key), log and fall back to the offline
placeholder so the pipeline never hard-crashes.
"""

from __future__ import annotations

import logging
from typing import Callable

from worldcupagents.dataflows.commentary.base import CommentaryProvider
from worldcupagents.dataflows.commentary.guardian import GuardianCommentaryProvider
from worldcupagents.dataflows.commentary.placeholder import PlaceholderCommentaryProvider

logger = logging.getLogger(__name__)

CommentaryFactory = Callable[[dict], CommentaryProvider]

_FACTORIES: dict[str, CommentaryFactory] = {
    "placeholder": lambda config: PlaceholderCommentaryProvider(),
    "guardian": lambda config: GuardianCommentaryProvider.from_config(config),
}
_INSTANCE_CACHE: dict[str, CommentaryProvider] = {}


def register_commentary_factory(name: str, factory: CommentaryFactory) -> None:
    _FACTORIES[name] = factory
    _INSTANCE_CACHE.pop(name, None)


def clear_commentary_cache() -> None:
    _INSTANCE_CACHE.clear()


def available_commentary_providers() -> list[str]:
    return sorted(_FACTORIES)


def _resolve_name(config: dict) -> str:
    return (
        config.get("tool_vendors", {}).get("commentary")
        or config.get("data_vendors", {}).get("commentary")
        or "placeholder"
    )


def get_commentary_provider(config: dict) -> CommentaryProvider:
    name = _resolve_name(config)
    if name not in _FACTORIES:
        logger.warning("Unknown commentary vendor '%s'; using placeholder.", name)
        name = "placeholder"
    if name not in _INSTANCE_CACHE:
        try:
            _INSTANCE_CACHE[name] = _FACTORIES[name](config)
        except Exception as e:  # noqa: BLE001 — degrade gracefully
            if name == "placeholder":
                raise
            logger.warning("Commentary vendor '%s' unavailable (%s); falling back to placeholder.", name, e)
            _INSTANCE_CACHE[name] = PlaceholderCommentaryProvider()
    return _INSTANCE_CACHE[name]
