# Implementation Brief — Case ledger modularization (approved as §13.4)

**Plan:** `docs/planning/PLAN-2026-11-27-ledger-modularization.md`
**Status:** Complete — user full pytest green; see numbering note in plan
**Numbering note:** Actual `TODO.md` §13.4 is the `alerts/views.py` split, not this item.
**Agent:** `django-backend` (executed directly by planner; no subagents)
**Deviations:** D-13.4-1 through D-13.4-6 in the plan

## Scope

Split `cases/ledger.py` into `cases/ledger/` with a write module, a read module, and a compatibility
`__init__.py`. Preserve the uncommitted §13.2 and §13.3 work; do not stage or commit it as part of
§13.4.

## Task 1 — Plan/brief bookkeeping

- Set the §13.4 plan status to approved.
- Keep this brief as the implementation source of truth.
- AC: no commit or push.

## Task 2 — Create package and move functions

- Create `cases/ledger/`.
- Move `cases/ledger.py` to `cases/ledger/__init__.py` via `git mv`.
- Create `cases/ledger/event_log.py` containing `append_timeline_event` exactly as authored.
- Create `cases/ledger/read.py` containing `events_after` exactly as authored.
- Replace `cases/ledger/__init__.py` contents with the original contract docstring plus re-exports:
  `from .event_log import append_timeline_event`
  `from .read import events_after`
- Add `__all__ = ["append_timeline_event", "events_after"]` so mypy recognizes explicit exports.
- AC: both function bodies and signatures are AST-identical to `HEAD:cases/ledger.py`.
- AC: `from cases.ledger import append_timeline_event, events_after` works with Django initialized.
- AC: `TimelineEvent.objects.create(` occurs only in `cases/ledger/event_log.py` among scanned apps.

## Task 3 — Update required guard tests

### `tests/conformance/test_realtime.py`

- Change `LEDGER` to `REPO_ROOT / "cases" / "ledger" / "event_log.py"`.
- Update comments/docstrings from `cases/ledger.py` to `cases/ledger/event_log.py`.
- In the synthetic scan self-test, create `tmp_path / "cases" / "ledger"` with `parents=True`.
- AC: positive and negative choke-point assertions remain equally strong.

### `tests/conformance/test_realtime_org.py`

- Change `monkeypatch.setattr("cases.ledger.publish_org_event", publisher)` to
  `monkeypatch.setattr("cases.ledger.event_log.publish_org_event", publisher)`.
- AC: org refresh payload assertions remain unchanged.

## Task 4 — Structural and static verification

Run:

```bash
.venv/bin/python - <<'PY'
import ast, subprocess
from pathlib import Path

old = ast.parse(subprocess.check_output(["git", "show", "HEAD:cases/ledger.py"], text=True))
def funcs(tree):
    return {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
old_funcs = funcs(old)
new_funcs = {}
for p in (Path("cases/ledger/event_log.py"), Path("cases/ledger/read.py")):
    new_funcs.update(funcs(ast.parse(p.read_text())))
print("old", len(old_funcs), "new", len(new_funcs))
print("missing", sorted(set(old_funcs) - set(new_funcs)))
print("changed", sorted(k for k in old_funcs if old_funcs[k] != new_funcs.get(k)))
PY

DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/python -c \
  'import django; django.setup(); from cases.ledger import append_timeline_event, events_after; print("imports ok")'

rg -n 'TimelineEvent\.objects\.create\(' cases ui alerts automation --glob '*.py'
```

AC: no missing, extra, or changed definitions.
AC: only the new writer file contains the direct-create literal.
AC: docs/tests changed are limited to the two files in Task 3 plus plan/brief.

## Task 5 — Targeted behavioural verification

Run the 10-file targeted suite list in the plan. Do not run full pytest.

Then run:

```bash
make check
scripts/secret-scan.sh --tracked
git diff --check
git status --short --untracked-files=all
git diff --stat
```

AC: targeted suites green.
AC: `make check` and secret scan clean.
AC: no additional test edits needed; if required, stop and record under D-13.4-6.
AC: changes remain uncommitted pending user-run full pytest and acceptance.

## Constraints

- `AGENTS.md` rules remain in force; no secrets, no subagents.
- Do not weaken the realtime choke-point guard beyond the single allowed writer file.
- Preserve ADR-006, tenant scoping, `transaction.on_commit`, event vocabulary, and keyset ordering.
- No schema change or migration.
- Record any deviation in the plan before merge.
