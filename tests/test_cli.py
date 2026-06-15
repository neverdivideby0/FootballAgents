"""CLI config-building tests (non-TTY -> no interactive prompt, hermetic)."""

from __future__ import annotations

from worldcupagents.cli import _build_config


def test_explicit_provider_sets_catalog_models():
    cfg = _build_config("openai", None, None, None, None)
    assert cfg["use_llm"] is True
    assert cfg["llm_provider"] == "openai"
    assert cfg["deep_think_llm"] == "gpt-5.4-mini"   # updated default (2026-05)
    assert cfg["quick_think_llm"] == "gpt-5-nano"


def test_no_provider_no_llm_by_default():
    cfg = _build_config(None, None, None, None, None)
    assert cfg["use_llm"] is False  # no provider, no --llm, config default off


def test_model_and_rounds_overrides():
    cfg = _build_config("deepseek", "my-deep", "my-quick", None, 3)
    assert cfg["llm_provider"] == "deepseek"
    assert cfg["deep_think_llm"] == "my-deep"
    assert cfg["quick_think_llm"] == "my-quick"
    assert cfg["max_debate_rounds"] == 3


def test_interactive_in_non_tty_does_not_hang():
    # -i forces use_llm, but no TTY -> picker is skipped, falls back to config provider.
    cfg = _build_config(None, None, None, None, None, interactive=True)
    assert cfg["use_llm"] is True
    assert cfg["llm_provider"] in ("anthropic", "openai", "google", "deepseek")


def test_hoard_data_cli_invokes_pipeline(monkeypatch):
    from typer.testing import CliRunner
    from worldcupagents.cli import app
    from worldcupagents.pipelines.hoard_data import HoardResult

    called = {}

    def fake_hoard(config, source, refresh, populate_summary, limit_source):
        called.update({
            "source": source,
            "refresh": refresh,
            "populate_summary": populate_summary,
            "limit_source": limit_source,
        })
        return HoardResult(source=source, snapshot="20260611", raw_dir="/tmp/raw", counts={"wh_matches": 2})

    monkeypatch.setattr("worldcupagents.pipelines.hoard_data.hoard_data", fake_hoard)
    res = CliRunner().invoke(app, [
        "hoard-data", "--source", "international-results", "--refresh",
        "--no-populate-summary", "--limit-source", "2",
    ])

    assert res.exit_code == 0
    assert called == {
        "source": "international_results",
        "refresh": True,
        "populate_summary": False,
        "limit_source": 2,
    }
    assert "wh_matches: 2" in res.output
