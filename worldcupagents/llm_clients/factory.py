"""Multi-provider LLM factory (lazy imports), mirroring TradingAgents.

Supported: anthropic, openai, google, deepseek. DeepSeek is OpenAI-API-compatible,
so it routes through ChatOpenAI with a base-URL + DEEPSEEK_API_KEY override.
Providers are only imported when used, so an unused provider's SDK need not exist.
"""

from __future__ import annotations

import os
from typing import Any

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def create_llm(provider: str, model: str, **kwargs) -> Any:
    provider = (provider or "").lower()

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:  # pragma: no cover
            raise ImportError("Install: uv pip install -e '.[anthropic]'") from e
        return ChatAnthropic(model=model, **kwargs)

    if provider in ("openai", "deepseek"):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:  # pragma: no cover
            raise ImportError("Install: uv pip install -e '.[openai]'") from e
        if provider == "deepseek":
            kwargs.setdefault("base_url", _DEEPSEEK_BASE_URL)
            kwargs.setdefault("api_key", os.environ.get("DEEPSEEK_API_KEY"))
        return ChatOpenAI(model=model, **kwargs)

    if provider == "google":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as e:  # pragma: no cover
            raise ImportError("Install: uv pip install -e '.[google]'") from e
        return ChatGoogleGenerativeAI(model=model, **kwargs)

    raise ValueError(f"Unknown LLM provider: {provider!r}. Choose from anthropic/openai/google/deepseek.")
