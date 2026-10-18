#!/usr/bin/env bash
# Pre-commit

set -euo pipefail
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
staged="$(git diff --cached --name-only)"
[ -z "$staged" ] && exit 0
echo "hooks: secret scan (staged files)…"
scan_rc=0
./scripts/secret-scan.sh --staged || scan_rc=$?
[ "$scan_rc" -ge 2 ] && echo "hooks: SECRET SCAN ERROR" && exit 1
[ "$scan_rc" -eq 1 ] && echo "hooks: SECRET DETECTED" && exit 1
if ! printf '%s\n' "$staged" | grep -qE '\.(py|toml|yml|yaml)$'; then
  echo "hooks: no code staged (docs/config only) — skipping"
  exit 0
fi
echo "hooks: running check-fast (ruff)…"
if ! make --no-print-directory check-fast; then
  echo "hooks: BLOCKED"; exit 1
fi
echo "hooks: check-fast passed"
