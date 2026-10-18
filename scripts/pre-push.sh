#!/usr/bin/env bash
# Full Definition of Done before anything leaves the machine.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Secrets never leave the machine
echo "hooks: secret scan (tracked tree)…"
scan_rc=0
./scripts/secret-scan.sh --tracked || scan_rc=$?
if [ "$scan_rc" -ge 2 ]; then
    echo "hooks: SECRET SCAN ERROR — PUSH BLOCKED"; exit 1
elif [ "$scan_rc" -eq 1 ]; then
    echo "hooks: PUSH BLOCKED — secret detected"; exit 1
fi

echo "hooks: running full check (ruff, mypy, django check, migrations)…"
if ! make --no-print-directory check; then
    echo "hooks: PUSH BLOCKED — check failed"; exit 1
fi
echo "hooks: check passed — pushing"
