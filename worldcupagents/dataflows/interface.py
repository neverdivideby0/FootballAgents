"""Vendor registry + routing — analog of TradingAgents' VENDOR_METHODS.

Providers are registered as *factories* (name -> constructor(config)) so they're
built lazily from config/env and cached per process. Adding a source = register a
factory; point a category at it in config["data_vendors"].

Resilience: if the configured provider can't be built (e.g. football_data_org with
no token), we log a warning and fall back to the placeholder so a prediction never
hard-crashes on a missing key.
"""

from __future__ import annotations

import logging
from typing import Callable

from worldcupagents.dataflows.providers.base import FootballDataProvider
from worldcupagents.dataflows.providers.football_data_org import FootballDataOrgProvider
from worldcupagents.dataflows.providers.placeholder import PlaceholderProvider

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[dict], FootballDataProvider]

_FACTORIES: dict[str, ProviderFactory] = {
    "placeholder": lambda config: PlaceholderProvider(),
    "football_data_org": lambda config: FootballDataOrgProvider.from_config(config),
}
_INSTANCE_CACHE: dict[str, FootballDataProvider] = {}


def register_provider_factory(name: str, factory: ProviderFactory) -> None:
    _FACTORIES[name] = factory
    _INSTANCE_CACHE.pop(name, None)


def clear_provider_cache() -> None:
    """Drop cached provider instances (test isolation / re-reading env)."""
    _INSTANCE_CACHE.clear()


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def _resolve_name(config: dict, category: str) -> str:
    return (
        config.get("tool_vendors", {}).get(category)
        or config.get("data_vendors", {}).get(category)
        or "placeholder"
    )


def get_provider(config: dict, category: str = "results") -> FootballDataProvider:
    name = _resolve_name(config, category)
    if name not in _FACTORIES:
        raise KeyError(f"No provider '{name}' for category '{category}'. Known: {available_providers()}")
    if name not in _INSTANCE_CACHE:
        try:
            _INSTANCE_CACHE[name] = _FACTORIES[name](config)
        except Exception as e:  # noqa: BLE001 — degrade gracefully, don't crash predict
            if name == "placeholder":
                raise
            logger.warning("Provider '%s' unavailable (%s); falling back to placeholder.", name, e)
            _INSTANCE_CACHE[name] = PlaceholderProvider()
    return _INSTANCE_CACHE[name]
