"""Per-provider default models, API-key env vars, and pricing data.

Defaults are sensible, broadly-available picks — override per run with
``--deep-model`` / ``--quick-model`` or the WCA_DEEP_THINK_LLM / WCA_QUICK_THINK_LLM
env vars. (deep = judge/reasoning; quick = advocates.)

Pricing is approximate (per 1M tokens, USD) and cached as of PRICING_AS_OF.
Run ``worldcupagents check --pricing`` to see the full table.
"""

from __future__ import annotations

PROVIDERS = ("anthropic", "openai", "google", "deepseek")

# ── Model IDs (2026-05) ────────────────────────────────────────────────────
# provider -> (deep_model, quick_model)
DEFAULT_MODELS: dict[str, tuple[str, str]] = {
    "anthropic": ("claude-opus-4-7",      "claude-haiku-4-5"),
    "openai":    ("gpt-5.4-mini",         "gpt-5-nano"),        # updated 2026-05
    "google":    ("gemini-3.5-flash",     "gemini-3.1-flash-lite"),  # updated 2026-05
    "deepseek":  ("deepseek-reasoner",    "deepseek-chat"),
}

# provider -> selectable models for the arrow-key picker ("Custom…" appended at UI layer)
MODEL_CHOICES: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-8",          # flagship (2026-05-28)
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "openai": [
        # GPT-5.4 series — current production generation
        "gpt-5.4-mini",             # $0.75/$4.50  — capable & efficient
        "gpt-5.4-nano",             # $0.20/$1.25  — fast & cheap
        # GPT-5 series — previous generation, still widely available
        "gpt-5-mini",               # $0.25/$2.00
        "gpt-5-nano",               # $0.05/$0.40  — cheapest GPT
        # Reasoning / legacy
        "o3",                       # $2.00/$8.00  — reasoning model
        "o4-mini",                  # $1.10/$4.40
        "gpt-4o",                   # $2.50/$10.00
        "gpt-4o-mini",              # $0.15/$0.60
    ],
    "google": [
        # Gemini 3.x series — newest generation
        "gemini-3.5-flash",         # $1.50/$9.00  — latest flagship (2026-05-19)
        "gemini-3.1-flash-lite",    # $0.25/$1.50  — cost-effective 3.x
        "gemini-3-flash",           # $0.50/$3.00  — preview
        # Gemini 2.5 series — stable & well-supported
        "gemini-2.5-pro",           # $1.25/$10.00
        "gemini-2.5-flash",         # $0.30/$2.50
        "gemini-2.5-flash-lite",    # $0.10/$0.40  — cheapest Gemini
        # Legacy
        "gemini-2.0-flash",         # $0.10/$0.40
    ],
    "deepseek": [
        "deepseek-v4-pro",          # $1.74/$3.48
        "deepseek-v4-flash",        # $0.14/$0.28
        "deepseek-reasoner",        # $0.55/$2.19  — R1
        "deepseek-chat",            # $0.14/$0.28  — V3
    ],
}

# ── Pricing table (USD per 1 M tokens, input / output) ────────────────────
# Sources: official provider pricing pages, last verified 2026-05.
# Update PRICING_AS_OF when you refresh these numbers.
PRICING_AS_OF = "2026-05"

MODEL_PRICING: dict[str, tuple[float, float]] = {
    # ── Anthropic ──────────────────────────────────────────────────────────
    "claude-opus-4-8":           ( 5.00,  25.00),  # flagship  (2026-05-28)
    "claude-opus-4-7":           ( 5.00,  25.00),
    "claude-opus-4-6":           ( 5.00,  25.00),
    "claude-opus-4-20250514":    (15.00,  75.00),  # legacy snapshot ID
    "claude-sonnet-4-6":         ( 3.00,  15.00),
    "claude-haiku-4-5":          ( 1.00,   5.00),
    "claude-haiku-4-5-20251001": ( 1.00,   5.00),
    "claude-3-5-haiku-20241022": ( 0.80,   4.00),
    # ── OpenAI — GPT-5.4 series ────────────────────────────────────────────
    "gpt-5.4-mini":   ( 0.75,   4.50),  # current production workhorse
    "gpt-5.4-nano":   ( 0.20,   1.25),
    # ── OpenAI — GPT-5 series ──────────────────────────────────────────────
    "gpt-5-mini":     ( 0.25,   2.00),
    "gpt-5-nano":     ( 0.05,   0.40),  # cheapest GPT option
    # ── OpenAI — reasoning / legacy ────────────────────────────────────────
    "o3":             ( 2.00,   8.00),
    "o4-mini":        ( 1.10,   4.40),
    "gpt-4o":         ( 2.50,  10.00),
    "gpt-4o-mini":    ( 0.15,   0.60),
    # ── Google — Gemini 3.x series ─────────────────────────────────────────
    "gemini-3.5-flash":       ( 1.50,   9.00),  # latest flagship (2026-05-19)
    "gemini-3.1-flash-lite":  ( 0.25,   1.50),  # cost-effective 3.x
    "gemini-3-flash":         ( 0.50,   3.00),  # preview
    # ── Google — Gemini 2.5 series ─────────────────────────────────────────
    "gemini-2.5-pro":         ( 1.25,  10.00),  # ≤200 K context
    "gemini-2.5-flash":       ( 0.30,   2.50),
    "gemini-2.5-flash-lite":  ( 0.10,   0.40),
    # ── Google — legacy ────────────────────────────────────────────────────
    "gemini-2.0-flash":       ( 0.10,   0.40),
    "gemini-1.5-pro":         ( 1.25,   5.00),
    "gemini-1.5-flash":       ( 0.075,  0.30),
    # ── DeepSeek ───────────────────────────────────────────────────────────
    "deepseek-v4-pro":   ( 1.74,   3.48),
    "deepseek-v4-flash": ( 0.14,   0.28),
    "deepseek-reasoner": ( 0.55,   2.19),  # R1
    "deepseek-chat":     ( 0.14,   0.28),  # V3
}


# ── Public helpers ─────────────────────────────────────────────────────────

def model_choices(provider: str) -> list[str]:
    return MODEL_CHOICES.get((provider or "").lower(), [])


def default_models(provider: str) -> tuple[str, str]:
    return DEFAULT_MODELS.get((provider or "").lower(), DEFAULT_MODELS["anthropic"])


# provider -> the env var holding its API key (for clear error messages)
API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "google":    "GOOGLE_API_KEY",
    "deepseek":  "DEEPSEEK_API_KEY",
}


def cost_label(model_id: str) -> str:
    """Short pricing string shown next to a model in the arrow-key picker."""
    p = MODEL_PRICING.get(model_id)
    if p is None:
        return "pricing unknown"
    return f"${p[0]:.2f} in / ${p[1]:.2f} out per 1M"


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return estimated USD cost for the given token counts, or None if unlisted."""
    p = MODEL_PRICING.get(model_id)
    if p is None:
        return None
    return (input_tokens * p[0] + output_tokens * p[1]) / 1_000_000
