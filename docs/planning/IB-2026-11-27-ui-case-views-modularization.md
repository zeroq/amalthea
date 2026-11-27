# Implementation Brief — §13.3 UI case views modularization

**Plan:** `docs/planning/PLAN-2026-11-27-ui-case-views-modularization.md`
**Status:** Complete — user full pytest green
**Agent:** `django-backend` (executed directly by planner; no subagents)
**Deviations:** D-13.3-1 through D-13.3-6 in the plan

## Scope

Move-only split of `ui/views/cases.py` into four sibling modules plus a compatibility shim.
Preserve the uncommitted §13.2 `ui/taxonomy_views/` work; do not stage or commit it as part of §13.3.

## Task 1 — Plan/brief bookkeeping

- Set the §13.3 plan status to approved.
- Keep this brief as the implementation source of truth.
- AC: no commit or push.

## Task 2 — Create sibling modules

Create from `HEAD:ui/views/cases.py` (current worktree file is unchanged):

| File | Names moved |
| --- | --- |
| `ui/views/case_list.py` | `_case_bulk_redirect`, `case_bulk`, `_case_create_form_context`, `_render_case_list`, `case_list` |
| `ui/views/case_edit.py` | `_render_case_create_form`, `case_create`, `case_edit`, `_render_case_edit_form` |
| `ui/views/case_actions.py` | `logger`, `case_alert_link`, `case_delete_ui`, `_case_status_for_stage`, `case_set_status`, `case_close`, `case_field_edit`, `case_tag_add`, `case_tag_remove`, `case_comment`, `case_custom_field_edit`, `case_export`, `case_import_ui`, `case_merge` |
| `ui/views/case_shares.py` | `case_share_list`, `case_share_revoke` |

- Use the original import block in each module; let `ruff check --fix` remove unused imports.
- `case_edit.py` must import `_case_create_form_context` and `_render_case_list` from `.case_list`;
  no other sibling imports.
- `logger = logging.getLogger(__name__)` belongs only in `case_actions.py`.
- AC: all moved function bodies and decorator lists are AST-identical to `HEAD:ui/views/cases.py`.
- AC: no logic, templates, messages, redirects, scope checks, audit calls, or local imports change.

## Task 3 — Replace `ui/views/cases.py` with shim

- Keep `ui/views/cases.py` as a compatibility re-export module.
- Re-export all 24 functions and `logger`.
- Do not add `__all__`.
- AC: `from ui.views.cases import <name>` works for every original name.
- AC: `ui/urls.py` and `ui/views/__init__.py` remain byte-identical.

## Task 4 — Structural and static verification

Run:

```bash
.venv/bin/python - <<'PY'
import ast, subprocess
from pathlib import Path

old = ast.parse(subprocess.check_output(["git", "show", "HEAD:ui/views/cases.py"], text=True))
def funcs(tree):
    return {n.name: (ast.unparse(n), [ast.unparse(d) for d in n.decorator_list])
            for n in tree.body if isinstance(n, ast.FunctionDef)}
old_funcs = funcs(old)
new_funcs = {}
for path in Path("ui/views").glob("case_*.py"):
    new_funcs.update(funcs(ast.parse(path.read_text())))
print("old", len(old_funcs), "new", len(new_funcs))
print("missing", sorted(set(old_funcs) - set(new_funcs)))
print("extra", sorted(set(new_funcs) - set(old_funcs)))
print("changed", sorted(k for k in old_funcs if old_funcs[k] != new_funcs.get(k)))
PY

DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/python -c \
  'import django; django.setup(); from ui.views import cases; import ui.urls; print("imports ok")'

git diff --quiet -- ui/urls.py && echo "ui/urls.py unchanged"
git diff --quiet -- ui/views/__init__.py && echo "ui/views/__init__.py unchanged"
git diff --check
```

Also verify all 18 `ui-case-*` route names resolve to the expected handler `__name__`.
AC: no missing, extra, changed, or decorator-drifted functions.
AC: static checks clean; no trailing whitespace.

## Task 5 — Targeted behavioural verification

Run exactly the targeted suite list in the plan (19 conformance files). Do not run full pytest.

Then run:

```bash
make check
scripts/secret-scan.sh --tracked
git status --short --untracked-files=all
git diff --stat
```

AC: targeted suites green.
AC: `make check` and secret scan clean.
AC: no test edits needed; if any are required, stop and record under D-13.3-4.
AC: changes remain uncommitted pending user-run full pytest and acceptance.

## Constraints

- Stack and design rules from `AGENTS.md`: dark-theme UI, keyboard-driven UX, semantic HTML, no secrets.
- ADR-006 uniform 404 behaviour for forbidden case access/admin-only import.
- No schema change or migration.
- No subagents.
- No `--no-verify`, no secret commits.
- Record any deviation in the plan before merge.
