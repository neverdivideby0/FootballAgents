"""Guided launcher menu — argv mapping + real-command dispatch (hermetic)."""

from __future__ import annotations

import pytest

from worldcupagents import cli


def _ask(value):
    return type("A", (), {"ask": lambda self: value})()


def test_simple_actions_map_to_argv():
    assert cli._menu_argv("predict") == ["predict", "-i"]
    assert cli._menu_argv("refresh") == ["refresh"]
    assert cli._menu_argv("credit") == ["credit"]
    assert cli._menu_argv("explore") == ["explore"]
    assert cli._menu_argv("help") == ["--help"]


def test_dossier_gathers_two_teams(monkeypatch):
    import questionary
    answers = iter(["Argentina", "France"])
    monkeypatch.setattr(questionary, "text", lambda *a, **k: _ask(next(answers)))
    assert cli._menu_argv("dossier") == ["dossier", "Argentina", "France"]


def test_watch_offline_when_llm_declined(monkeypatch):
    import questionary
    # confirm() #1 = "use an LLM?" -> No; #2 = "keep polling?" -> No
    confirms = iter([False, False])
    monkeypatch.setattr(questionary, "confirm", lambda *a, **k: _ask(next(confirms)))
    assert cli._menu_argv("watch") == ["watch", "--no-llm"]


def test_watch_with_picked_model(monkeypatch):
    import questionary
    confirms = iter([True, True])   # use LLM? yes ; keep polling? yes
    monkeypatch.setattr(questionary, "confirm", lambda *a, **k: _ask(next(confirms)))
    monkeypatch.setattr(cli, "_guided_select", lambda: {"provider": "openai",
                                                        "deep": "gpt-5.4-mini", "quick": "gpt-5.4-mini"})
    assert cli._menu_argv("watch") == [
        "watch", "--provider", "openai", "--model", "gpt-5.4-mini", "--interval", "30"]


def test_run_argv_dispatches_real_command(capsys):
    # _run_argv runs the real Typer app, which sys.exits in standalone mode.
    with pytest.raises(SystemExit):
        cli._run_argv(["leagues"])
    assert "Competitions" in capsys.readouterr().out
