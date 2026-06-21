#!/usr/bin/env bash
# List the warehouse tables (and, with --sources, the data sources) to fan out over.
#
# "Audit all" is a main-agent fan-out: ask Claude "audit all tables", and it spawns one
# data-auditor (or source-auditor) per name below — in parallel, each single-
# responsibility — then merges the verdicts. This script just emits the names.
#
#   scripts/audit_tables.sh            # warehouse table names (from match_store.py)
#   scripts/audit_tables.sh --sources  # data-source names (from _sources_with_checks)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--sources" ]]; then
    .venv/bin/python -c "from worldcupagents.pipelines.data_explorer import _sources_with_checks; [print(s['name']) for s in _sources_with_checks(probe=False)]"
else
    grep -oE 'CREATE TABLE IF NOT EXISTS [a-z_]+' worldcupagents/dataflows/match_store.py | awk '{print $NF}'
fi
