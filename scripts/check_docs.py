#!/usr/bin/env python3
"""Doc-drift check: every CLI command should be mentioned in README.md + CLAUDE.md.

Hooks run fixed shell commands — they can't WRITE judgment-based prose. So this is a
*reminder*, not an auto-writer: it warns when a `footballagents` command isn't
documented (the usual drift after adding a command). Updating the prose stays a human
/ model job, done in the same change.

Two modes:
  * Manual:  `python scripts/check_docs.py`  → prints findings, exits non-zero on drift.
  * Hook:    wired as a Claude Code PreToolUse(Bash) hook. It reads the tool-call JSON
             on stdin and only runs when the command is a `git commit`; it always exits
             0 (warn-only — it never blocks your commit).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "CLAUDE.md")


def _command_names() -> list[str]:
    from worldcupagents.cli import app
    try:
        from typer.main import get_command_name
    except Exception:  # noqa: BLE001
        def get_command_name(s: str) -> str:
            return s.replace("_", "-")
    return sorted({c.name or get_command_name(c.callback.__name__) for c in app.registered_commands})


def _missing() -> dict[str, list[str]]:
    """For each doc, the commands it does not mention."""
    out: dict[str, list[str]] = {}
    for doc in DOCS:
        text = (REPO / doc).read_text(encoding="utf-8")
        out[doc] = [c for c in _command_names() if c not in text]
    return out


def _is_git_commit_from_stdin() -> bool:
    """Hook mode: only act on a `git commit`. A tty (manual run) always proceeds."""
    if sys.stdin.isatty():
        return True
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — not JSON / empty → behave like a manual run
        return True
    command = (payload.get("tool_input") or {}).get("command", "")
    return "git commit" in command


def main() -> int:
    hook_mode = not sys.stdin.isatty()
    if not _is_git_commit_from_stdin():
        return 0  # not a commit — stay silent

    missing = _missing()
    if not any(missing.values()):
        if not hook_mode:
            print("✓ docs: every CLI command is mentioned in README.md and CLAUDE.md")
        return 0

    print("⚠ doc drift — these CLI commands are not mentioned in the docs:", file=sys.stderr)
    for doc, cmds in missing.items():
        if cmds:
            print(f"  {doc}: {', '.join(cmds)}", file=sys.stderr)
    print("  (update the prose, then re-commit — this is a reminder, not a blocker)", file=sys.stderr)
    # Warn-only in hook mode (never block a commit); non-zero when run manually.
    return 0 if hook_mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
