#!/usr/bin/env bash
# Full Definition of Done before anything leaves the machine.
#
# Deliberately heavier than pre-commit: this is the last chance to catch a type error or an
# unsynchronised migration before it becomes someone else's problem.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Secrets never leave the machine: scan the whole committed tree first, so a push
# cannot publish a key even if the commit hooks were bypassed with --no-verify.
echo "hooks: secret scan (tracked tree)…"
scan_rc=0
./scripts/secret-scan.sh --tracked || scan_rc=$?
if [ "$scan_rc" -ge 2 ]; then
    echo ""
    echo "hooks: SECRET SCAN ERROR — PUSH BLOCKED (fail closed)."
    echo "        The scanner could not verify the tracked tree (exit >= 2); fix the"
    echo "        error above or inspect HEAD by hand before retrying."
    exit 1
elif [ "$scan_rc" -eq 1 ]; then
    echo ""
    echo "hooks: PUSH BLOCKED — secret detected."
    echo "        Remove and rotate the secret(s) reported above; see SECURITY.md."
    exit 1
fi

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
