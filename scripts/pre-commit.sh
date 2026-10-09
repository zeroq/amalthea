#!/usr/bin/env bash
# Enforce the Amalthea Definition of Done *before* a commit lands.
#
# Installed by `make hooks`. The full rationale lives in the Makefile header; the short version:
# two consecutive gate rounds passed green tests while real defects shipped, because "mypy clean"
# and "the suite is green" named whatever command the reporter happened to type, and because
# mutation guards were never required to prove their own mutation landed.
#
# `check-fast` is ruff + pytest: roughly four seconds, so it costs nothing on every commit. The
# heavier `make check` (adds mypy, django check, migration sync) is what CI and pre-push run.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

# Only guard commits that touch code or tests; docs-only commits skip the hook.
staged="$(git diff --cached --name-only)"
if [ -z "$staged" ]; then
    exit 0
fi

# The secret scan runs BEFORE the docs-only early-exit below, so every commit —
# including docs/config-only ones — passes through it. A secret must not be able
# to land just because no code changed.
echo "hooks: secret scan (staged files)…"
scan_rc=0
./scripts/secret-scan.sh --staged || scan_rc=$?
if [ "$scan_rc" -ge 2 ]; then
    cat <<'EOF'

hooks: SECRET SCAN ERROR — commit blocked (fail closed).

  The scanner could not verify the staged content (exit >= 2). A scan that
  cannot run must not be treated as a scan that passed: fix the error above,
  or inspect `git diff --cached` by hand before retrying.
EOF
    exit 1
elif [ "$scan_rc" -eq 1 ]; then
    cat <<'EOF'

hooks: SECRET DETECTED — commit blocked.

  Remove the secret and ROTATE it at the provider (see the findings above and
  SECURITY.md). Keep the replacement in an environment variable or a local,
  gitignored .env file. Do NOT use --no-verify to get around this.
EOF
    exit 1
fi

if ! printf '%s\n' "$staged" | grep -qE '\.(py|toml|yml|yaml)$'; then
    echo "hooks: no code staged (docs/config only) — skipping"
    exit 0
fi

echo "hooks: running check-fast (ruff + pytest)…"
if ! make --no-print-directory check-fast; then
    cat <<'EOF'

hooks: BLOCKED — the commit was not created.

  Fix the failures above, or stage fewer files and commit with --no-verify if you are certain.

  If a mutation guard fails, read the failure before you touch it. A guard that reports
  "MUTATION DID NOT LAND" is telling you the test could not demonstrate its own failure mode —
  that is the R11 defect, not a broken test.
EOF
    exit 1
fi

echo "hooks: check-fast passed"
