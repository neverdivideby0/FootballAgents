"""Central config with WCA_* env-var overrides (mirrors TradingAgents' pattern).

Loads .env on import. Auto-detects the data vendor: if FOOTBALL_DATA_ORG_TOKEN is
present, the default vendor becomes football_data_org (live data); otherwise it
stays on the offline placeholder. Override explicitly with WCA_DATA_VENDOR.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Anchor everything to the project root so the CLI works from ANY directory
# (previously relative paths broke `predict -i` when run from ~). Override the
# home with WCA_HOME to relocate data/memory/runs/exports wholesale.
PROJECT_ROOT = Path(os.environ.get("WCA_HOME") or Path(__file__).resolve().parents[1])

load_dotenv(PROJECT_ROOT / ".env")  # the project .env regardless of cwd
load_dotenv()                       # plus a cwd .env if present (cwd wins nothing already set)

# env var -> config key. Add a row to expose a new override; no other changes.
_ENV_OVERRIDES = {
    "WCA_LLM_PROVIDER": "llm_provider",
    "WCA_DEEP_THINK_LLM": "deep_think_llm",
    "WCA_QUICK_THINK_LLM": "quick_think_llm",
    "WCA_MAX_DEBATE_ROUNDS": "max_debate_rounds",
    "WCA_MAX_SCENARIO_ROUNDS": "max_scenario_rounds",
    "WCA_ENABLE_ANALYST_REPORTS": "enable_analyst_reports",
    "WCA_ANALYST_REPORTS_LLM": "analyst_reports_llm",
    "WCA_ENABLE_SCENARIO_DEBATE": "enable_scenario_debate",
    "WCA_ENSEMBLE_JUDGE_WEIGHT": "ensemble_judge_weight",
    "WCA_USE_LLM": "use_llm",
    "WCA_USE_STATS_LAMBDA": "use_stats_lambda",
    "WCA_VERDICT_MODE": "verdict_mode",
    "WCA_LLM_TEMPERATURE": "llm_temperature",
    "WCA_LEAGUE": "league",
    "WCA_SEASON": "season",
    "WCA_CACHE_DIR": "cache_dir",
    "WCA_DATA_DIR": "data_dir",
    "WCA_FD_COMPETITION": "fd_competition",
}

_DATA_CATEGORIES = ("fixtures", "results", "squads", "stats_xg", "news")


def _coerce(value: str, reference):
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


def _default_vendor() -> str:
    explicit = os.environ.get("WCA_DATA_VENDOR")
    if explicit:
        return explicit
    return "football_data_org" if os.environ.get("FOOTBALL_DATA_ORG_TOKEN") else "placeholder"


def _default_commentary_vendor() -> str:
    explicit = os.environ.get("WCA_COMMENTARY_VENDOR")
    if explicit:
        return explicit
    return "guardian" if os.environ.get("GUARDIAN_API_KEY") else "placeholder"


def _build_data_vendors() -> dict:
    vendors = {cat: _default_vendor() for cat in _DATA_CATEGORIES}
    vendors["commentary"] = _default_commentary_vendor()  # own registry (dataflows.commentary)
    return vendors


DEFAULT_CONFIG = _apply_env_overrides({
    # Paths — anchored to PROJECT_ROOT so any cwd works (WCA_HOME to relocate).
    "results_dir": str(PROJECT_ROOT / "runs"),
    "exports_dir": str(PROJECT_ROOT / "exports"),
    "memory_dir": str(PROJECT_ROOT / "memory"),
    "prediction_log_path": str(PROJECT_ROOT / "memory" / "prediction_log.md"),
    "wc2026_state_path": str(PROJECT_ROOT / "memory" / "wc2026_state.json"),
    "cache_dir": str(PROJECT_ROOT / ".cache"),
    "data_dir": str(PROJECT_ROOT / "data"),  # SQLite match store (DATA_PLAN M1.1)
    # LLM settings — default to Anthropic Claude (2026-05 model IDs).
    "llm_provider": "anthropic",
    "deep_think_llm": "claude-opus-4-7",
    "quick_think_llm": "claude-haiku-4-5",
    "use_llm": False,  # M2 flips this on (needs a key)
    # M1.2: fitted strengths for λ. Validated 2026-06 on PL (backtest --from-store):
    # stats-poisson LOOCV Brier 0.579 vs rank-Elo 0.654 — and the rank baseline has
    # NO home advantage (WC neutral-venue design), which flattened league anchors
    # (first LLM-lift eval: baseline hit-rate 27%). Unseen teams (e.g. WC nationals
    # pre-tournament) still fall back to rank-Elo inside team_lambdas.
    "use_stats_lambda": True,
    # Strength-model guards. A team with fewer than this many fitted games falls back
    # to rank-Elo (stops 1-game WC samples flooring elite sides to λ≈0.18).
    "strength_min_games": 2,
    # National-team strengths are fitted on weighted INTERNATIONAL history (wh_matches):
    # exponential recency (half-life yrs), a HARD cutoff (nothing older counts),
    # tournament>qualifier>friendly weighting, and shrinkage toward the mean.
    "intl_strength_half_life_years": 2.0,
    "intl_strength_max_age_years": 4.0,
    "intl_strength_type_weights": {"tournament": 1.0, "qualifier": 0.7, "friendly": 0.4},
    "intl_strength_shrinkage_k": 4.0,
    # Opponent-adjusted (Dixon–Coles) iteration — a goal vs a strong defence counts more.
    "intl_strength_iters": 50,
    "intl_strength_tol": 1e-4,
    # Debate (TA topology: analysts → advocate debate → judge → scenario debate → final pundit).
    "max_debate_rounds": 2,
    "enable_analyst_reports": True,   # deterministic digests; zero LLM cost
    "analyst_reports_llm": False,     # quick-LLM polish of the three reports (3 extra calls)
    "enable_scenario_debate": True,   # risk-team debate + Final Pundit (TA-like out of the box)
    "max_scenario_rounds": 1,         # cap = 3 * rounds pundit turns
    # How the verdict's SCORELINE + probabilities are formed:
    #   "agents" (default) — the advocates name 3 likely scorelines + 1 black swan,
    #            the judge picks the final score and STATES the W/D/L probabilities;
    #            no Poisson/blend (the resolve/Brier/calibration loop still scores them).
    #   "stats"  — the statistical path (fitted-strength λ → Poisson grid → blend with
    #            the judge read). Kept as a clickable choice and the AUTOMATIC fallback
    #            whenever there is no usable LLM judge read (offline / missing key / error),
    #            so predict never crashes and simulate/backtest/evaluate stay on the math.
    "verdict_mode": "agents",
    # Sampling temperature for the DEBATE LLMs (advocates + judge + scenario pundits).
    # Higher = bolder, less hedged scorelines (the agents stop defaulting to 1-goal
    # margins). Applied in Predictor only — extraction commands (analyze-match/watch)
    # keep their own deterministic clients. Note: some OpenAI reasoning models only
    # accept temperature=1; set this to 1.0 (or via WCA_LLM_TEMPERATURE) for those.
    "llm_temperature": 0.9,
    # Ensemble: weight on the judge's qualitative read vs the statistical baseline.
    "ensemble_judge_weight": 0.6,
    # Calibration guardrails. Draw uplift: max P(draw) added for a close, cagey GROUP
    # game (the Poisson base under-forecasts draws). Contextual clamp: the blended LLM
    # read may move each probability at most this far from the Tier-1 base.
    "draw_calibration_max": 0.08,
    "max_contextual_delta": 0.15,
    # Let the blend weight adapt from the eval log (recency-weighted fit shrunk
    # toward the 0.6 prior; written to data/fitted_weights.json by resolve/refresh).
    # Set False to pin the prior above (calibration.effective_judge_weight).
    "use_fitted_judge_weight": True,
    # Show the live market (The Odds API consensus + Polymarket crowd) to the judge
    # so it can argue where its read should differ. On for predictions; the eval
    # harness forces it OFF so the LLM-lift test stays an honest independent measure.
    "enable_market_context": True,
    # Data vendors: category -> provider name (registered in dataflows.interface).
    # Auto-detected from FOOTBALL_DATA_ORG_TOKEN; override with WCA_DATA_VENDOR.
    "league": "WC2026",      # active competition (MULTILEAGUE_PLAN.md); WC by default
    "season": None,          # e.g. "2025-26"; None = no season scoping (tournaments)
    "fd_competition": "WC",  # football-data.org competition code (set from the league)
    "data_vendors": _build_data_vendors(),
    "tool_vendors": {},  # per-tool override of the category default
})


# Research-depth presets (TA's shallow/medium/deep). Individual flags still override.
RESEARCH_DEPTH_PRESETS: dict[str, dict] = {
    "shallow": {"max_debate_rounds": 1, "enable_scenario_debate": False, "analyst_reports_llm": False},
    "medium":  {"max_debate_rounds": 2, "enable_scenario_debate": True, "max_scenario_rounds": 1,
                "analyst_reports_llm": False},
    "deep":    {"max_debate_rounds": 3, "enable_scenario_debate": True, "max_scenario_rounds": 2,
                "analyst_reports_llm": True},
}


def apply_research_depth(config: dict, depth: str) -> dict:
    """Fold a depth preset into a run config (mutates and returns it)."""
    preset = RESEARCH_DEPTH_PRESETS.get((depth or "").lower())
    if preset is None:
        raise ValueError(f"Unknown research depth {depth!r}. Known: {', '.join(RESEARCH_DEPTH_PRESETS)}")
    config.update(preset)
    return config
