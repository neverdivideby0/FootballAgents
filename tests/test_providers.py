"""Provider factory + catalog tests (hermetic — construction only, no API calls)."""

from __future__ import annotations

import pytest

from worldcupagents.llm_clients.factory import create_llm
from worldcupagents.llm_clients.model_catalog import DEFAULT_MODELS, PROVIDERS, default_models


def test_catalog_has_all_providers():
    assert set(PROVIDERS) == set(DEFAULT_MODELS)
    for p in PROVIDERS:
        deep, quick = default_models(p)
        assert isinstance(deep, str) and isinstance(quick, str)


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        create_llm("bogus", "x")


def test_deepseek_routes_through_openai_client_with_base_url():
    llm = create_llm("deepseek", "deepseek-chat", api_key="dummy")
    # langchain-openai exposes the base URL on the client; just assert it points at deepseek.
    base = str(getattr(llm, "openai_api_base", "") or getattr(getattr(llm, "client", None), "base_url", ""))
    assert "deepseek" in base.lower()


def test_openai_constructs():
    llm = create_llm("openai", "gpt-4o-mini", api_key="dummy")
    assert llm is not None
