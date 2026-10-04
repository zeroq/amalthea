#!/usr/bin/env bash
# Full Definition of Done before anything leaves the machine.
#
# Deliberately heavier than pre-commit: this is the last chance to catch a type error or an
# unsynchronised migration before it becomes someone else's problem.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "hooks: running full check (ruff, mypy, django check, migrations, pytest)…"
if ! make --no-print-directory check; then
    echo ""
    echo "hooks: PUSH BLOCKED — check failed."
    exit 1
fi

# The schema gate is the project's main quality control, so it also requires a green suite.
if ! .venv/bin/pytest tests/conformance/test_schema_mutation_guards.py -q; then
    echo "hooks: PUSH BLOCKED — mutation guards failed; the conformance suite may be vacuous again."
    exit 1
fi
echo "hooks: full check passed — pushing"
