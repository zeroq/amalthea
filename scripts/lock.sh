#!/usr/bin/env bash
# Regenerate requirements/{base,dev}.txt from a verified install.
#
# The split is a real dependency-closure computation, not a name filter: we build a
# throwaway venv containing only the runtime top-level packages, freeze it, and treat
# everything else in the full dev install as the dev closure. Name-based splitting
# silently misclassifies transitive packages (e.g. CacheControl, thehive4py).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

RUNTIME=(
  "Django>=5.2.13,<5.3" "djangorestframework>=3.16,<4" "channels[daphne]>=4.2,<5"
  "celery[redis]>=5.4,<6" "jsonpath-ng>=1.7,<2" "psycopg[binary]>=3.2,<4"
  "python-dotenv>=1.0,<2" "argon2-cffi>=23.1"
)

"$PY" -m venv "$TMP/rt"
"$TMP/rt/bin/pip" install -q --upgrade pip
"$TMP/rt/bin/pip" install -q "${RUNTIME[@]}"
"$TMP/rt/bin/pip" freeze | sort > "$TMP/runtime.txt"

.venv/bin/pip freeze | sort > "$TMP/all.txt"

"$PY" - "$TMP" <<'PYEOF'
import sys
tmp = sys.argv[1]
norm = lambda n: n.split('==')[0].split('[')[0].strip().lower().replace('_','-').replace('.','-')
runtime = {norm(l): l.strip() for l in open(f"{tmp}/runtime.txt") if l.strip()}
alldeps = {norm(l): l.strip() for l in open(f"{tmp}/all.txt") if l.strip()}
dev = {k: v for k, v in alldeps.items() if k not in runtime}
assert not (set(runtime) & set(dev)), "runtime/dev overlap"
open("requirements/base.txt","w").write(
    "# Amalthea runtime dependencies — PINNED.\n"
    "# Python 3.14.6 | Django 5.2 LTS\n"
    "# Regenerate: scripts/lock.sh\n\n"
    + "\n".join(runtime[k] for k in sorted(runtime)) + "\n")
open("requirements/dev.txt","w").write(
    "-r base.txt\n\n# dev / test / lint / interop tooling — PINNED\n"
    "# TheHive4py backs acceptance criterion AC8.4.\n\n"
    + "\n".join(dev[k] for k in sorted(dev)) + "\n")
print(f"runtime={len(runtime)} dev={len(dev)}")
PYEOF
echo "requirements regenerated"
