# Plan — §13.3 UI case views modularization (2026-11-27)

**Status:** Closed 2026-11-27 — implementation complete; user full pytest green
**Owner:** project planner (`opencode/big-pickle`)
**Implementation target:** `django-backend`
**Related:** `TODO.md` §13.3; `AGENTS.md`; ADR-006 (uniform 404s); ADR-008 (webhook routing, not touched)

## Summary

Split the oversized `ui/views/cases.py` into focused sibling modules while keeping
`ui/views/cases.py` as a compatibility shim. This is the third P1 item in the §13 modularization
program. It is a move-only refactor: no route, template, permission, audit, or behaviour change.

Assumption carried forward: §13.2 (`ui/taxonomy_views/` package split) is implemented but still
uncommitted and awaiting the user-owned full pytest gate. §13.3 must preserve those uncommitted
changes and must not fold them into its own eventual commit.

## Goals

- Reduce `ui/views/cases.py` from a 1,274-line catch-all to a small re-export shim.
- Group case UI handlers by user-facing responsibility: list/bulk, create/edit, mutations, shares.
- Preserve every import path consumed by `ui/views/__init__.py` and `ui/urls.py`.
- Preserve all decorators, scoping checks, audit calls, HTMX response branches, templates, redirects,
  and local imports byte-for-byte inside function bodies.
- Keep `ui/urls.py` and `ui/views/__init__.py` byte-identical.
- Record any deviation in this plan before merge.

## Non-goals

- No new API endpoints, route names, URL paths, templates, or template context keys.
- No database schema change and no migration.
- No change to `cases/views/`, `cases/services`, `identity`, or `core` modules.
- No change to `ui/taxonomy_views/` from §13.2.
- No test edits; no new inline tests; no full pytest run by the implementation agent.
- No commit or push until explicitly requested after user acceptance.
- No subagents.

## Architecture

Stack remains Django 5.x + DRF, server-rendered Django templates, HTMX, Tailwind, Channels,
Celery/Redis, and PostgreSQL as specified in `AGENTS.md`. This change is a Python module-layout
refactor only.

Option chosen: **sibling modules plus compatibility shim**.

- Create `ui/views/case_list.py`, `ui/views/case_edit.py`, `ui/views/case_actions.py`, and
  `ui/views/case_shares.py`.
- Keep `ui/views/cases.py` as a shim that only imports/re-exports names.
- Do **not** create `ui/views/cases/` as a package: `ui/views/__init__.py` already imports from
  `.cases`, and `ui/urls.py` resolves `views.case_*` through that package export. A sibling layout
  keeps both files byte-identical and avoids another compatibility layer.

### Baseline measured 2026-11-27

- `ui/views/cases.py`: 1,274 lines (the TODO text says 1,002; the file is larger now).
- 24 top-level functions: 18 decorated handlers and 6 helpers.
- 18 route names resolve to these handlers through `ui.views` re-exports.
- 1 module-level `logger`; no `__all__`.
- Only external importer is `ui/views/__init__.py`; no test imports or monkeypatches
  `ui.views.cases.*`.
- `logger` is used once, inside `case_field_edit`.
- No direct TheHive wire literals in this module.

## DB schema

No change. Core models and migrations stay untouched. No migration is required.

## API

No API contract change. `ui/urls.py` must remain byte-identical. These 18 route names must keep
resolving to callables with the same signatures and decorators:

`ui-case-list`, `ui-case-bulk`, `ui-case-create`, `ui-case-import`, `ui-case-delete`,
`ui-case-alert-link`, `ui-case-status`, `ui-case-close`, `ui-case-field-edit`,
`ui-case-custom-field-edit`, `ui-case-comment`, `ui-case-export`, `ui-case-edit`,
`ui-case-tag-add`, `ui-case-tag-remove`, `ui-case-merge`, `ui-case-share-list`,
`ui-case-share-revoke`.

Compatibility surface: the shim must expose all 24 original functions plus `logger`, with no
`__all__` added. `ui/views/__init__.py` imports 23 of those names and must continue to work unchanged.

## UI

No template or markup change. All `render()`, `redirect()`, `_base_context()`, HTMX partial branches,
messages, and fragment names must remain exactly as authored. Keyboard-driven dark-theme UI and
Font Awesome conventions stay unchanged.

## Module ownership

| Module | Owned names |
| --- | --- |
| `ui/views/case_list.py` | `_case_bulk_redirect`, `case_bulk`, `_case_create_form_context`, `_render_case_list`, `case_list` |
| `ui/views/case_edit.py` | `_render_case_create_form`, `case_create`, `case_edit`, `_render_case_edit_form` |
| `ui/views/case_actions.py` | `logger`, `case_alert_link`, `case_delete_ui`, `_case_status_for_stage`, `case_set_status`, `case_close`, `case_field_edit`, `case_tag_add`, `case_tag_remove`, `case_comment`, `case_custom_field_edit`, `case_export`, `case_import_ui`, `case_merge` |
| `ui/views/case_shares.py` | `case_share_list`, `case_share_revoke` |
| `ui/views/cases.py` | Compatibility shim only: re-export every name above; no implementations, no `__all__` |

Allowed cross-module dependency: `case_edit.py` imports `_case_create_form_context` and
`_render_case_list` from `case_list.py`. `_render_case_list` embeds the create form when the list is
opened with `?new=1`, and `case_create` re-renders the list after success. Keeping both helpers in
`case_list.py` avoids a circular sibling import. No other sibling-to-sibling imports are expected.
Sibling modules import shared helpers from `.common` and the existing `ui`, `cases`, `core`,
`identity`, and Django modules as in the original file.

## Phases and acceptance criteria

### Phase 0 — Baseline and guardrails

- Confirm clean understanding of uncommitted §13.2 work and leave it untouched.
- Capture AST snapshot of `HEAD:ui/views/cases.py` (24 functions, 18 decorated, 18 routed names).
- AC: plan records baseline and is approved before implementation.

### Phase 1 — Create sibling modules

- Add the four sibling modules with exact function bodies and decorators from `ui/views/cases.py`.
- Move imports conservatively; duplicate imports where needed rather than introduce a new shared
  module.
- AC: each function body is AST-identical to the original; no decorators reordered; no logic edited.
- AC: `case_edit.py` imports `_case_create_form_context` and `_render_case_list` from `case_list.py`;
  no other sibling imports exist.

### Phase 2 — Reduce `cases.py` to compatibility shim

- Replace `ui/views/cases.py` with re-export imports only.
- AC: all 24 functions and `logger` are importable from `ui.views.cases`.
- AC: no `__all__` is added.
- AC: `ui/urls.py` and `ui/views/__init__.py` remain byte-identical.

### Phase 3 — Structural and static verification

- Compare AST against `HEAD:ui/views/cases.py` for all 24 functions and decorator lists.
- Run Django import smoke and route resolution for the 18 route names.
- Run `ruff check --fix`, `ruff format`, `make check`, `git diff --check`, and secret scan.
- AC: no missing, extra, changed, or decorator-drifted functions.
- AC: all 18 route names resolve to the expected callable `__name__` and module.
- AC: static checks clean with no trailing whitespace.

### Phase 4 — Targeted behavioural verification and closeout prep

- Run targeted conformance suites covering case list/create/edit, export/import, mutations, shares,
  scoping, viewer behaviour, HTMX, pagination, URL smoke, trailing slash, and realtime.
- Do not run full pytest; that remains user-owned.
- AC: targeted suites green.
- AC: no test edits needed; if any are required, stop and record the deviation in this plan.
- AC: report results and leave changes uncommitted pending user-run full pytest and acceptance.

## Security

- Preserve `not_viewer`, `login_required`, `require_POST`, `require_GET`, and
  `require_http_methods(["GET", "POST"])` exactly.
- Preserve `resolve_case`, `case_visible_q`, `is_case_owner`, `is_admin`, and `is_viewer` checks.
- Preserve `AuditLog` writes and `append_timeline_event` calls.
- Preserve ADR-006 uniform 404 behaviour for forbidden resources and admin-only import.
- Keep local imports (for example `Share`, `merge_cases`, `_case_export_document`) local.
- No secrets, tokens, or environment values in code, tests, docs, or logs.
- Secret scan must remain clean; no `--no-verify`.

## Testing

Targeted suites proposed for Phase 4:

```bash
DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/pytest -q \
  tests/conformance/test_ui_loop.py \
  tests/conformance/test_ui_case_create.py \
  tests/conformance/test_case_export_import.py \
  tests/conformance/test_case_list_severity_filter.py \
  tests/conformance/test_case_mine_filter.py \
  tests/conformance/test_case_scope_filter.py \
  tests/conformance/test_ui_case_verdict.py \
  tests/conformance/test_ui_p1_wiring.py \
  tests/conformance/test_ui_p2_wiring.py \
  tests/conformance/test_ui_htmx_scope.py \
  tests/conformance/test_ui_pagination.py \
  tests/conformance/test_ui_pagination_edge_cases.py \
  tests/conformance/test_tenant_scoping.py \
  tests/conformance/test_viewer_reads.py \
  tests/conformance/test_viewer_precedence.py \
  tests/conformance/test_ui_urls_smoke.py \
  tests/conformance/test_ui_trailing_slash.py \
  tests/conformance/test_realtime.py \
  tests/conformance/test_containment_phase_capture.py
```

Structural checks:

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
```

## Risks

- **R-13.3-1:** `_render_case_list` embeds `_case_create_form_context`, while `case_create` re-renders
  the list; wrong ownership would introduce a cycle.
  Mitigation: keep both helpers in `case_list.py` and import one-way into `case_edit.py`.
- **R-13.3-2:** `logger` uses `__name__`; moving it changes the logger name.
  Mitigation: accepted move-only deviation; record under D-13.3-3 and confirm no tests assert the
  logger name.
- **R-13.3-3:** `ui/views/__init__.py` has a large import list; a missing shim export breaks all UI
  startup. Mitigation: import smoke before tests; verify 23 imported names.
- **R-13.3-4:** ~~User-owned full pytest remains unrun for §13.2 and §13.3.~~ Resolved: user supplied
  a green full-pytest log covering both items.

## Deviations

- **D-13.3-1:** Sibling modules plus `ui/views/cases.py` shim, not a `ui/views/cases/` package.
  Rationale: keeps `ui/urls.py` and `ui/views/__init__.py` byte-identical.
- **D-13.3-2:** `_case_create_form_context` lives in `case_list.py` with `_render_case_list`, and both
  are imported into `case_edit.py`.
  Rationale: `_render_case_list` embeds the create form for `?new=1`, so this avoids a sibling import
  cycle without changing function bodies or adding a shared module.
- **D-13.3-3:** `logger` moves to `case_actions.py` and is re-exported by the shim; its `__name__`
  therefore changes. Rationale: the only use is inside `case_field_edit`.
- **D-13.3-4:** No test edits expected; any discovered edit must be recorded here before merge.
- **D-13.3-5:** The shim uses comment-only `# noqa: F401` markers on its re-export imports because
  no `__all__` was added. Rationale: keeps the original module surface without lint removals.
- **D-13.3-6:** The unused module-level `case_visible_q` import was dropped from `case_list.py`;
  the local import inside `_render_case_list` is preserved exactly. Rationale: keeps the function
  body AST-identical while satisfying ruff without an `F811` shadow.

## Open questions

None blocking. Defaults above are proposed for approval.

## Verification record

Recorded 2026-11-27 after implementation. User-provided full pytest log `/tmp/amalthea-full-pytest.log`
is green: **4444 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.92s**.

- Structural AST comparison against `HEAD:ui/views/cases.py`: 24 functions, no missing, extra,
  changed, or decorator-drifted definitions.
- Compatibility smoke: all 24 functions plus `logger` are importable from `ui.views.cases`;
  `from ui.views import cases` and `import ui.urls` succeed with Django initialized.
- All 18 `ui-case-*` route names resolve to the expected handler `__name__`.
- Byte-identical checks passed for `ui/urls.py` and `ui/views/__init__.py`.
- Shim contains zero function definitions and no `__all__`.
- Sibling imports: only `case_edit.py` imports from `case_list.py`; no other sibling imports.
- Targeted suites: **553 passed** in 735.74s across the 19 listed conformance files.
- `make check`: passed (ruff, format, mypy over 168 files, Django checks, migration sync, CSS
  drift, playbook scripts).
- `scripts/secret-scan.sh --tracked`: clean over 985 tracked files.
- `git diff --check`: clean; no trailing whitespace in new modules or plan/brief docs.
- No test edits were needed, and no deviation beyond D-13.3-1 through D-13.3-6 was discovered.

## Approval gate

Approved 2026-11-27. Implementation brief written to
`docs/planning/IB-2026-11-27-ui-case-views-modularization.md`. Phases 0–4 completed with D-13.3-1
through D-13.3-6 recorded. Do not commit or push.
