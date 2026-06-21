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
import re
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


def _registry_drift() -> list[tuple[str, list[str]]]:
    """Warehouse tables / data sources that have no row in their registry.

    Source of truth: the `CREATE TABLE` statements in match_store.py and the source
    specs in `_sources_with_checks`. Keeps `docs/{warehouse_tables,data_sources}.md`
    honest as you add tables/sources."""
    issues: list[tuple[str, list[str]]] = []

    schema = (REPO / "worldcupagents/dataflows/match_store.py").read_text(encoding="utf-8")
    tables = sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema)))
    treg = REPO / "docs/warehouse_tables.md"
    treg_text = treg.read_text(encoding="utf-8") if treg.exists() else ""
    missing_t = [t for t in tables if f"`{t}`" not in treg_text]
    if missing_t:
        issues.append(("docs/warehouse_tables.md", missing_t))

    try:
        from worldcupagents.pipelines.data_explorer import _sources_with_checks
        names = sorted(s["name"] for s in _sources_with_checks(probe=False))
        sreg = REPO / "docs/data_sources.md"
        sreg_text = sreg.read_text(encoding="utf-8") if sreg.exists() else ""
        missing_s = [n for n in names if n not in sreg_text]
        if missing_s:
            issues.append(("docs/data_sources.md", missing_s))
    except Exception:  # noqa: BLE001 — source enumeration is best-effort
        pass
    return issues


def main() -> int:
    hook_mode = not sys.stdin.isatty()
    if not _is_git_commit_from_stdin():
        return 0  # not a commit — stay silent

    missing = _missing()
    drift = _registry_drift()
    clean = not any(missing.values()) and not drift
    if clean:
        if not hook_mode:
            print("✓ docs: every CLI command and warehouse table/source is registered")
        return 0

    if any(missing.values()):
        print("⚠ doc drift — these CLI commands are not mentioned in the docs:", file=sys.stderr)
        for doc, cmds in missing.items():
            if cmds:
                print(f"  {doc}: {', '.join(cmds)}", file=sys.stderr)
    for doc, items in drift:
        kind = "table" if "warehouse" in doc else "source"
        print(f"⚠ registry drift — these {kind}s have no row in {doc}:", file=sys.stderr)
        print(f"  {', '.join(items)}", file=sys.stderr)
    print("  (update the docs, then re-commit — this is a reminder, not a blocker)", file=sys.stderr)
    # Warn-only in hook mode (never block a commit); non-zero when run manually.
    return 0 if hook_mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
