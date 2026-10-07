#!/usr/bin/env bash
# Regenerate requirements/{base,dev}.txt with all-platform hashes.
#
# Mechanism (deviation P11-1, plan PLAN-2026-10-07-ci-hashed-requirements.md):
#
# The old lock used freeze-subtraction: build a throwaway venv of the runtime top-levels,
# freeze it, and treat everything else in the full dev install as the dev closure. The
# resulting files were *pinned but unhashed*.
#
# New mechanism: `pip-compile --generate-hashes` over the EXISTING exact pins. Because the
# input is fully pinned (`name==version`), the resolver has no version choice to make, so
# the pass can only add hashes/comments/ordering — it cannot drift versions. Hashes come
# from the PyPI JSON API and cover ALL platform wheels, not the local one: a mac-generated
# hash set must not break a Linux CI install (`pip hash` would have baked mac-only hashes).
#
# The closure invariant is enforced mechanically: the (name==version) multiset is parsed
# before and after the pass and compared; the only allowed diff is the pip-tools bootstrap
# (dev-only), which is declared in the ALLOW_DIFF list below.
#
#   ./scripts/lock.sh           # regenerate (deterministic; run twice == identical)
#   ./scripts/lock.sh --check   # exit non-zero if committed files are stale (CI gate)

set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
PIP_COMPILE="$PWD/.venv/bin/pip-compile"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BASE=requirements/base.txt
DEV=requirements/dev.txt

# --- parse a requirements file into sorted (name==version) lines: hash/comment/continuation
#     aware. `-r` includes are resolved via the referenced file; `-c` constraints pin versions
#     but are not part of the closure.
parse_closure() {
    "$PY" - "$@" <<'PYEOF'
import re, sys
from pathlib import Path

def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()

def read(req: Path, out: list[str], seen: set[Path]) -> None:
    req = req.resolve()
    if req in seen:
        return
    seen.add(req)
    if not req.exists():
        return
    # join physical lines that continue on the next line (pip-compile wraps with a
    # trailing backslash), then evaluate logical lines.
    physical: list[str] = []
    for raw in req.read_text().splitlines():
        if physical and physical[-1].endswith("\\"):
            physical[-1] = physical[-1].rstrip("\\") + raw.strip()
        else:
            physical.append(raw)
    for line in physical:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            read(req.parent / line.split(" ", 1)[1], out, seen)
            continue
        if line.startswith("-c ") or line.startswith("--constraint "):
            continue
        # strip inline hash annotations left by a previous run:  pkg==ver --hash=sha256:...
        line = re.split(r"\s+--hash=", line, maxsplit=1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)(?:\[[A-Za-z0-9_,.-]+\])?\s*==\s*([^;\s]+)(.*)$", line)
        if not m:
            continue
        # env markers (; python_version >= ...) are metadata, not identity
        out.append(f"{norm(m.group(1))}=={m.group(2).strip()}")

out: list[str] = []
for path in sys.argv[1:]:
    read(Path(path), out, set())
print("\n".join(sorted(set(out))))
PYEOF
}

# --- closure invariant: old == new, except the declared dev bootstrap additions.
#     $4 = newline-separated allowlist of "name==version" lines that MAY differ.
check_closure() {
    local old="$1" new="$2" label="$3" allow="$4" ok=0
    local diff extra
    diff="$(diff <(printf '%s\n' "$old") <(printf '%s\n' "$new") || true)"
    # keep only actual changed lines ('<'/'>'), drop diff metadata (11a12 …), then strip the
    # prefix so allowlist entries match bare "name==version" lines
    extra="$(printf '%s\n' "$diff" | grep '^[<>] ' | sed 's/^[<>] //' \
             | grep -vFxf <(printf '%s\n' "$allow") || true)"
    if [ -n "$extra" ]; then
        echo "ERROR: $label closure drifted beyond the bootstrap allowlist"
        printf '%s\n' "$extra" | sed 's/^/  /'
        ok=1
    fi
    return $ok
}

# ---------------------------------------------------------------- bootstrap additions
# pip-compile needs to run before pip-tools' own hashes exist; these enter the dev closure
# on the FIRST hashing pass and are allowed to differ from the pre-pass snapshot:
#   pip-tools, build, pyproject-hooks, wheel  (pip-tools' toolchain)
#   pip, setuptools                          (pip-tools declares them; --allow-unsafe pins them)
BOOTSTRAP_ALLOW="$(printf '%s\n' \
    'build==1.6.1' 'pyproject-hooks==1.3.3' 'pip-tools==7.6.2' 'wheel==0.48.0' \
    'pip==26.2.1' 'setuptools==84.0.0')"

if [ "${1:-}" = "--check" ]; then
    echo "lock: verifying committed requirements are current…"
    "$0" >/dev/null  # regenerate into place (deterministic)
    git diff --quiet -- "$BASE" "$DEV" \
        || { echo "lock: STALE — '$BASE' or '$DEV' differ from what the lock produces"; exit 1; }
    echo "lock: current ✓"
    exit 0
fi

# Snapshot the pre-pass closure (the verified truth) from git HEAD, so a dirty working tree
# from a partial earlier run can never become the baseline. The snapshots keep the real
# file names so "-r base.txt" includes inside dev.txt resolve the same way on both sides of
# the closure comparison.
git show HEAD:"$BASE" > "$TMP/base.txt" 2>/dev/null || cp "$BASE" "$TMP/base.txt"
git show HEAD:"$DEV"  > "$TMP/dev.txt"  2>/dev/null || cp "$DEV" "$TMP/dev.txt"
OLD_BASE="$(parse_closure "$TMP/base.txt")"
OLD_DEV="$(parse_closure "$TMP/dev.txt")"

echo "lock: hashing runtime closure ($(printf '%s\n' "$OLD_BASE" | wc -l | tr -d ' ') pkgs)…"
cp "$BASE" "$TMP/base.in"
"$PIP_COMPILE" --generate-hashes --quiet --no-strip-extras \
    --output-file="$TMP/base.raw.txt" "$TMP/base.in"
# pip-compile annotates the command and "via" provenance with the absolute temp input path;
# normalize it away so `make lock` output is byte-identical across runs (determinism AC2).
sed -E "s#$TMP/##g" "$TMP/base.raw.txt" > "$BASE"

# dev is compiled against base as a CONSTRAINT (-c): base pins hold, but base's packages are
# not re-emitted into dev.txt — the file keeps its documented "-r base.txt" contract line.
# Feed pip-compile a copy of dev.txt minus its "-r base.txt" line so the include is not
# flattened into the output, and --allow-unsafe so pip/setuptools (declared by pip-tools)
# are pinned with hashes instead of producing a WARNING that breaks hash-mode installs.
grep -v '^-r ' "$DEV" > "$TMP/dev.in"
"$PIP_COMPILE" --generate-hashes --quiet --allow-unsafe --no-strip-extras -c "$BASE" \
    --output-file="$TMP/dev.raw.txt" "$TMP/dev.in"
sed -E "s#$TMP/##g" "$TMP/dev.raw.txt" > "$TMP/dev.raw.norm.txt"

# Dev tools depend on some runtime packages (asgiref, django, click, …). base.txt already
# supplies those with hashes, so re-emitting them here would duplicate pins and break the
# runtime/dev disjointness invariant. Filter base-overlapping entries out of the dev body.
"$PY" - "$TMP" "$BASE" <<'PYEOF'
import re, sys
from pathlib import Path

def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()

def closure(req: Path, seen: set[Path] | None = None) -> set[str]:
    seen = seen or set()
    req = req.resolve()
    if req in seen:
        return set()
    seen.add(req)
    names: set[str] = set()
    physical: list[str] = []
    for raw in req.read_text().splitlines():
        if physical and physical[-1].endswith("\\"):
            physical[-1] = physical[-1].rstrip("\\") + raw.strip()
        else:
            physical.append(raw)
    for line in physical:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            names |= closure(req.parent / line.split(" ", 1)[1], seen)
            continue
        line = re.split(r"\s+--hash=", line, maxsplit=1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)(?:\[[A-Za-z0-9_,.-]+\])?\s*==\s*([^;\s]+)", line)
        if m:
            names.add(norm(m.group(1)))
    return names

base_names = closure(Path(sys.argv[2]))
raw = Path(sys.argv[1]) / "dev.raw.norm.txt"
out: list[str] = []
for raw_line in raw.read_text().splitlines():
    line = raw_line.strip()
    m = re.match(r"^([A-Za-z0-9._-]+)(?:\[[A-Za-z0-9_,.-]+\])?\s*==\s*([^;\s]+)", line)
    if m and norm(m.group(1)) in base_names:
        continue  # already supplied by base.txt
    out.append(raw_line)
Path(sys.argv[1], "dev.out.txt").write_text("\n".join(out) + "\n")
PYEOF

{ printf '%s\n' "# Amalthea dev/test/lint/interop closure — PINNED + hash-verified."
  printf '%s\n' "# Runtime deps come from base.txt (all-platform hashes); this file is dev-only tools."
  printf '%s\n' "# Regenerate: scripts/lock.sh"
  printf '%s\n\n' "-r base.txt"
  cat "$TMP/dev.out.txt"
} > "$DEV"

# Closure invariant after the pass.
NEW_BASE="$(parse_closure "$BASE")"
NEW_DEV="$(parse_closure "$DEV")"
check_closure "$OLD_BASE" "$NEW_BASE" "base.txt" "" \
    || { echo "lock: ABORTED — base.txt closure changed unexpectedly"; exit 1; }
check_closure "$OLD_DEV" "$NEW_DEV" "dev.txt" "$BOOTSTRAP_ALLOW" \
    || { echo "lock: ABORTED — dev.txt closure changed unexpectedly"; exit 1; }

echo "lock: requirements regenerated (all-platform hashes; $(printf '%s\n' "$NEW_BASE" | wc -l | tr -d ' ') runtime + $(printf '%s\n' "$NEW_DEV" | wc -l | tr -d ' ') dev pkgs)"