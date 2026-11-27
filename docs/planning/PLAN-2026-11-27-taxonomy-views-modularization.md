# PLAN-2026-11-27 — Modularize `ui/taxonomy_views.py` (TODO §13.2)

**Status:** Closed 2026-11-27 — implementation complete; user full pytest green
**Tracker item:** `TODO.md` §13.2 (P1 — next wave)
**Scope:** §13.2 only. §13.3–§13.20 remain backlog items for later waves.

## Summary

`ui/taxonomy_views.py` is a 787-line UI mutation module for the three database-backed taxonomy
vocabularies (case statuses, alert statuses, observable types) plus compiled taxonomy terms. It has
**20 top-level functions** — 12 decorated POST handlers and 8 helpers — and **3 module constants**.
The handlers are imported directly by `ui/urls.py` under 12 stable route names.

This plan converts the module into a `ui/taxonomy_views/` package with one module per vocabulary,
following the approved precedents from `PLAN-2026-11-04-ui-views-modularization.md` and
`PLAN-2026-11-26-cases-views-modularization.md`. The refactor is **move-only**: no endpoint, status
code, form field, template, HTMX fragment, audit payload, auth check, or UI behaviour changes.
`ui/urls.py` and `ui/views/taxonomy.py` stay byte-identical. `ui/taxonomy_views/__init__.py` becomes
a compatibility re-export layer.

## Goals

- Split `ui/taxonomy_views.py` into focused modules of roughly 100–300 lines.
- Keep all 12 route names, patterns, handler attributes, and order unchanged.
- Keep every current `from ui.taxonomy_views import ...` path working, including private helpers.
- Leave the `ui/views/taxonomy.py` context helper and `ui/taxonomy_forms.py` untouched.
- Make domain ownership obvious for the next taxonomy feature wave.
- Keep the diff reviewable as code movement with no expected test edits.
- Preserve strict mypy and ruff cleanliness across the new package.

## Non-goals

- No new feature, bug fix, or behaviour change.
- No URL renaming, route reordering, decorator reordering, or template changes.
- No schema, migration, model, service, form, or serializer changes.
- No changes to `_taxonomy_context()` or the taxonomy page markup.
- No cleanup of historical docs that reference `ui/taxonomy_views.py:NNNN`; those are snapshots.
- No refactoring of internal function bodies beyond what is required to move code between modules.
- No work on §13.3–§13.20 in this plan.

## Current state

Measured baseline:

- `ui/taxonomy_views.py`: **787 lines**, 20 top-level functions, 3 constants, 12 decorated handlers.
- `ui/urls.py` imports `from . import taxonomy_views, views` and uses
  `taxonomy_views.<handler>` for 12 taxonomy mutation routes.
- No direct imports of `ui.taxonomy_views` exist in `tests/`; tests exercise the routes through
  `reverse(...)` and the Django test client.
- `ui/views/taxonomy.py:36` owns `_taxonomy_context()`, used by the page and by mutation error
  re-renders. It is re-exported from `ui/views/__init__.py` and must not move in this wave.
- `ui/taxonomy_forms.py` owns `FormResult`, `status_form`, `observable_type_form`,
  `taxonomy_term_create_form`, `taxonomy_term_edit_form`, and `service_error`.
- The three status/type handler groups share `_audit()`, `_success()`, `_status_error()`,
  `_type_error()`, and `_delete_error()`.
- Taxonomy-term handlers deliberately use a manual superAdmin check that renders `ui/404.html`
  with status 404, not `_require_superadmin_ui()`. This difference is intentional and preserved.
- No TheHive wire field-name literals are present in `ui/taxonomy_views.py`; no wire-boundary
  allowlist change is expected.

Handlers and routes to preserve:

| Route name | Handler |
|---|---|
| `ui-taxonomy-case-status-create` | `case_status_create` |
| `ui-taxonomy-case-status-edit` | `case_status_edit` |
| `ui-taxonomy-case-status-delete` | `case_status_delete` |
| `ui-taxonomy-alert-status-create` | `alert_status_create` |
| `ui-taxonomy-alert-status-edit` | `alert_status_edit` |
| `ui-taxonomy-alert-status-delete` | `alert_status_delete` |
| `ui-taxonomy-observable-type-create` | `observable_type_create` |
| `ui-taxonomy-observable-type-edit` | `observable_type_edit` |
| `ui-taxonomy-observable-type-delete` | `observable_type_delete` |
| `ui-taxonomy-term-create` | `taxonomy_term_create` |
| `ui-taxonomy-term-edit` | `taxonomy_term_edit` |
| `ui-taxonomy-term-delete` | `taxonomy_term_delete` |

## Architecture

### Stack and ADR reference

This is a structural refactor in the existing Django 5.x server-rendered UI layer. It introduces no
new architecture and no ADR. ADR-001 (Django backend) and ADR-006 (role semantics) remain in force.
The split follows the repository precedent from the `ui/views.py` → `ui/views/` package refactor.

### Package shape

```text
ui/taxonomy_views/
  __init__.py        # compatibility re-exports only
  _common.py         # shared constants, audit/error/success helpers
  case_statuses.py   # case-status create/edit/delete
  alert_statuses.py  # alert-status create/edit/delete
  observable_types.py
  terms.py           # compiled taxonomy-term create/edit/delete
```

The package is placed at `ui/taxonomy_views/` rather than `ui/views/taxonomy/` so that the existing
`ui/views/taxonomy.py` context module stays where it is. `ui/urls.py` keeps `from . import
taxonomy_views, views`; Python resolves that import to the package without any URL edit.

`__init__.py` re-exports all 20 functions and 3 constants that are currently top-level in
`ui/taxonomy_views.py`. No new `__all__` is added, because the current module has none and adding
one would change `from ui.taxonomy_views import *` semantics.

Domain modules import shared helpers from `_common.py` or directly from the owning domain module;
they must not import from `ui.taxonomy_views` itself, to avoid partial-initialization cycles.

### Module ownership

Line ranges are current-source guidance, not acceptance criteria. Every top-level definition must
appear exactly once after the split.

| Module | Owns | Current source |
|---|---|---|
| `_common.py` | `_STATUS_PARTIALS`, `_TYPE_PARTIAL`, `_DELETE_PARTIAL`, `_audit_details`, `_audit`, `_success`, `_status_error`, `_type_error`, `_delete_error` | 60–198 |
| `case_statuses.py` | `case_status_create`, `case_status_edit`, `case_status_delete` | 201–317 |
| `alert_statuses.py` | `alert_status_create`, `alert_status_edit`, `alert_status_delete` | 319–435 |
| `observable_types.py` | `observable_type_create`, `observable_type_edit`, `observable_type_delete` | 437–536 |
| `terms.py` | `_term_create_context`, `_term_edit_context`, `taxonomy_term_create`, `taxonomy_term_edit`, `taxonomy_term_delete` | 538–787 |
| `__init__.py` | Compatibility re-exports for all 20 functions and 3 constants | new |

### Compatibility strategy

1. `git mv ui/taxonomy_views.py ui/taxonomy_views/__init__.py`.
2. Extract `_common.py` first; re-export every moved name from `__init__.py`.
3. Extract one domain module at a time; after each extraction:
   - add explicit re-exports to `__init__.py`;
   - run import smoke checks for `ui.taxonomy_views` and `ui.urls`;
   - run the taxonomy-writing targeted tests;
   - run `ruff check` and `ruff format --check` on touched files.
4. Leave `ui/urls.py` unchanged. It continues to resolve handler attributes through the package.
5. Leave `ui/views/taxonomy.py` and `ui/views/__init__.py` unchanged. `_taxonomy_context()` remains
   imported from `ui.views.taxonomy` by `_common.py` and `terms.py`.
6. Keep `@not_viewer`, `@login_required`, `@require_POST` decorator order identical on all 12
   handlers.
7. Keep the manual `request.user.role == "superAdmin"` checks and `ui/404.html` rendering in
   `terms.py` unchanged.
8. At the end, `ui/taxonomy_views/__init__.py` contains only a package docstring, imports, and
   re-export aliases — no view bodies or helper implementations.

## Data model

No changes. No migrations, model fields, constraints, indexes, or relationships.

## API

No changes. This module is a server-rendered UI mutation surface, not a DRF API surface. Status
codes, redirect targets, `HX-Redirect` behaviour, 404/422 responses, form field names, and error
messages remain identical.

## UI

No changes. Templates, context keys, HTMX fragment names, modal IDs, form IDs, keyboard behaviour,
and flash messages are untouched. `_taxonomy_context()` continues to supply the full taxonomy page
for non-HTMX error re-renders.

## Phases and tasks

### Phase 0 — Baseline and package skeleton

**Task 0.1 — Capture baseline**
- Confirm a clean worktree.
- Run the baseline targeted suites and `make check-fast`.
- Acceptance criteria:
  - [ ] Worktree is clean before implementation.
  - [ ] Baseline targeted suites pass.
  - [ ] No production code changed in this step.

**Task 0.2 — Create the package**
- `git mv ui/taxonomy_views.py ui/taxonomy_views/__init__.py`.
- Import `ui.urls` and run URL smoke tests.
- Acceptance criteria:
  - [ ] `ui.taxonomy_views` still imports.
  - [ ] `ui.urls` imports and all 12 taxonomy mutation routes resolve.
  - [ ] No handler body changed in this step.

### Phase 1 — Shared foundation

**Task 1.1 — Extract `_common.py`**
- Move the 3 constants and 7 shared helpers listed in the ownership table.
- Re-export all moved names.
- Acceptance criteria:
  - [ ] `_common.py` imports no sibling taxonomy-view modules.
  - [ ] `_STATUS_PARTIALS`, `_TYPE_PARTIAL`, `_DELETE_PARTIAL`, `_audit_details`, `_audit`,
    `_success`, `_status_error`, `_type_error`, and `_delete_error` remain importable from
    `ui.taxonomy_views`.
  - [ ] `_taxonomy_context` continues to come from `ui.views.taxonomy`.
  - [ ] Taxonomy editing tests pass unchanged.

### Phase 2 — Status vocabularies

**Task 2.1 — Extract `case_statuses.py`**
- Move `case_status_create`, `case_status_edit`, and `case_status_delete`.
- Acceptance criteria:
  - [ ] Decorator order and `transaction.atomic()` placement are unchanged.
  - [ ] `CASE_STAGES` is still used for the status form and error partial.
  - [ ] `ui-taxonomy-case-status-*` routes resolve with unchanged status codes and flash messages.
  - [ ] Audit namespace remains `case_status` and identifier key remains `value`.

**Task 2.2 — Extract `alert_statuses.py`**
- Move `alert_status_create`, `alert_status_edit`, and `alert_status_delete`.
- Acceptance criteria:
  - [ ] Decorator order and `transaction.atomic()` placement are unchanged.
  - [ ] `ALERT_STAGES` is still used for the status form and error partial.
  - [ ] `ui-taxonomy-alert-status-*` routes resolve with unchanged status codes and flash messages.
  - [ ] Audit namespace remains `alert_status` and identifier key remains `value`.

### Phase 3 — Types and compiled terms

**Task 3.1 — Extract `observable_types.py`**
- Move `observable_type_create`, `observable_type_edit`, and `observable_type_delete`.
- Acceptance criteria:
  - [ ] Decorator order and `transaction.atomic()` placement are unchanged.
  - [ ] In-use delete protection still reports `obj.observables.count()`.
  - [ ] `ui-taxonomy-observable-type-*` routes resolve with unchanged 422/404/redirect behaviour.
  - [ ] Audit namespace remains `observable_type` and identifier key remains `name`.

**Task 3.2 — Extract `terms.py`**
- Move `_term_create_context`, `_term_edit_context`, and the three term handlers.
- Acceptance criteria:
  - [ ] Local `from core.models import TaxonomyTerm` imports stay inside `taxonomy_term_edit` and
    `taxonomy_term_delete` exactly as today.
  - [ ] Manual superAdmin check and `ui/404.html` response stay unchanged.
  - [ ] `ui-taxonomy-term-*` routes resolve with unchanged 422/204/200/redirect behaviour.
  - [ ] Audit action remains `taxonomy.term.<result.action>` and details keys remain
    `changed` / `old` / `new`.

### Phase 4 — Cleanup and verification

**Task 4.1 — Shrink `__init__.py`**
- Leave only a package docstring, imports, and re-export aliases.
- Acceptance criteria:
  - [ ] `ui/taxonomy_views/__init__.py` contains no view bodies or helper implementations.
  - [ ] All 20 original functions and 3 constants remain importable from `ui.taxonomy_views`.
  - [ ] `ui/urls.py`, `ui/views/taxonomy.py`, and `ui/views/__init__.py` are byte-identical.
  - [ ] No test edit is required; if one is discovered, record it as a deviation before merge.
  - [ ] Ruff reports no unused imports.

**Task 4.2 — Full verification**
- Run targeted suites, `make check`, secret scan, and the user-owned full pytest gate.
- Acceptance criteria:
  - [ ] Targeted suites pass.
  - [ ] `make check` passes, including strict mypy over the new package.
  - [ ] No migration is generated.
  - [ ] Full `pytest` is green; per standing instruction this is user-run unless explicitly
    authorized.
  - [ ] `git diff --stat` shows code movement with no unintended edits.
  - [ ] No behaviour change.

## Security

- Preserve every decorator block and its order: `@not_viewer`, `@login_required`, `@require_POST`.
- Preserve `_require_superadmin_ui()` on status/type handlers and the manual superAdmin check on
  term handlers.
- Preserve `transaction.atomic()` scope around service calls plus audit writes.
- Preserve `record_taxonomy_change()` call order, actor, action, target type/id, and details shape.
- Preserve HTMX error responses at status 422, non-HTMX full-page re-renders at status 200, and
  success redirects / `204 + HX-Redirect`.
- Preserve in-use delete counts and error messages; do not leak exception internals beyond the
  existing `TaxonomyError.message` display.
- Do not add import-time database access, settings reads, URL resolution, or side effects.
- Do not expose raw exception text, secrets, or raw payloads in any new response or log line.

## Testing

Targeted suites to run after relevant phases:

```bash
.venv/bin/pytest tests/conformance/test_ui_taxonomy_editing.py -q
.venv/bin/pytest tests/conformance/test_taxonomy_containment_actions.py -q
.venv/bin/pytest tests/conformance/test_ui_containment_actions_taxonomy.py -q
.venv/bin/pytest tests/conformance/test_ui_case_verdict.py -q
.venv/bin/pytest tests/conformance/test_ui_p2_wiring.py -q
.venv/bin/pytest tests/conformance/test_viewer_reads.py -q
.venv/bin/pytest tests/conformance/test_tenant_scoping.py -q
.venv/bin/pytest tests/conformance/test_ui_urls_smoke.py -q
.venv/bin/pytest tests/conformance/test_ui_trailing_slash.py -q
.venv/bin/pytest tests/conformance/test_wire_boundary.py -q
```

Final gates:

```bash
make check
scripts/secret-scan.sh --tracked
git diff --check
.venv/bin/pytest   # user-owned; do not run/claim unless requested
```

Structural checks:

- AST comparison against `HEAD:ui/taxonomy_views.py`: 20 functions and 3 constants, no missing,
  extra, or changed definitions, and decorator lists match exactly.
- Import smoke: `from ui import taxonomy_views` and `import ui.urls`.
- Route smoke: all 12 taxonomy mutation route names resolve to the re-exported handlers.

No new production tests are required for the split itself unless a compatibility gap is found. The
URLconf import, re-exports, targeted suites, `make check`, and full pytest are the regression gate.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Circular import through `ui.views.taxonomy` | Import-time failure | Keep `_taxonomy_context` in `ui/views/taxonomy.py`; import it directly, never through the package. |
| Missed re-export breaks URLconf | All 12 taxonomy mutation routes fail | Import `ui.urls` after every extraction; run route smoke and editing tests. |
| Decorator order changes | Auth/method regression | Treat decorator blocks as atomic; AST comparison and viewer/tenant suites. |
| Manual superAdmin branch in terms changes | 404-vs-403 regression | Keep the exact `role` check and `ui/404.html` rendering in `terms.py`. |
| HTMX status split changes | Modal stops swapping or wrong redirect | Preserve `_htmx_redirect`, 422 fragments, and 204 `HX-Redirect` responses verbatim. |
| Audit details drift | Broken review trail | Keep `_audit()` and the term-specific details construction unchanged; compare AST. |
| Wire-boundary test rejects moved file | Static gate failure | No wire literals are present; run `test_wire_boundary.py` and add no allowlist entry unless a real literal appears. |
| Historical docs cite old line numbers | Confusion only | Leave historical docs untouched; note snapshots in verification. |

## Planned deviations to record before merge

- **D-13.2-1:** Use `ui/taxonomy_views/` rather than `ui/views/taxonomy/` so that the existing
  `ui/views/taxonomy.py` context helper and `ui/views/__init__.py` re-export stay untouched.
- **D-13.2-2:** Shared status/type audit and error helpers live in `_common.py`; taxonomy-term
  context helpers stay in `terms.py` because only the term handlers use them.
- **D-13.2-3:** No `__all__` is added to the compatibility package because the original module has
  none; all 20 functions and 3 constants are re-exported explicitly instead.
- **D-13.2-4:** No test edits are expected because tests use URL names and no test imports or
  monkeypatches `ui.taxonomy_views.*`; if that proves false, record the exact edit here before merge.

## Open questions

Defaults were confirmed by the user on 2026-11-27.

1. **Q1 — Package location:** `ui/taxonomy_views/`. **Resolved.**
2. **Q2 — Granularity:** 5 modules plus `__init__.py`; separate `case_statuses.py` and
   `alert_statuses.py`. **Resolved.**
3. **Q3 — Compatibility surface:** Re-export all 20 functions and 3 constants without adding
   `__all__`. **Resolved.**
4. **Q4 — Full pytest:** User-owned. **Resolved.**

## Verification record

Recorded 2026-11-27 after implementation. User-provided full pytest log `/tmp/amalthea-full-pytest.log`
is green: **4444 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.92s**.

- Structural AST comparison against `HEAD:ui/taxonomy_views.py`: 23 top-level definitions (20
  functions + 3 constants), no missing, extra, or changed definitions, and no decorator drift.
- Compatibility smoke: all 23 original functions/constants are importable from `ui.taxonomy_views`;
  `from ui import taxonomy_views` and `import ui.urls` succeed with Django initialized.
- Byte-identical checks passed for `ui/urls.py`, `ui/views/taxonomy.py`, and
  `ui/views/__init__.py`.
- Targeted suites: **241 passed** in 304.26s across the 10 listed conformance files.
- `make check`: passed (ruff, format, mypy over 168 files, Django checks, migration sync, CSS
  drift, playbook scripts).
- `scripts/secret-scan.sh --tracked`: clean over 985 tracked files.
- `git diff --check`: clean; no trailing whitespace in new modules or plan/brief docs.
- No test edits were needed, and no deviation beyond D-13.2-1 through D-13.2-4 was discovered.

## Approval gate

Approved 2026-11-27. Implementation brief produced for `django-backend` using Phases 0–4, the module
ownership table, and the acceptance criteria above. Any deviation from the approved map must be
recorded in this document before merge.
