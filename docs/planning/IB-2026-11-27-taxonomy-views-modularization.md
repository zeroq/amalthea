# Implementation Brief — 2026-11-27 `ui/taxonomy_views.py` modularization (§13.2)

**For:** `django-backend`
**Plan:** [`PLAN-2026-11-27-taxonomy-views-modularization.md`](./PLAN-2026-11-27-taxonomy-views-modularization.md) — **Approved**
**Tracker:** `TODO.md` §13.2
**Status:** Complete — user full pytest green
**Full pytest:** user-provided `/tmp/amalthea-full-pytest.log` — 4444 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.92s

## Scope

Implement only §13.2: convert `ui/taxonomy_views.py` into the approved `ui/taxonomy_views/` package.
This is a move-only refactor. No feature work, no bug fixes, no schema changes, no URL/template/form
behaviour changes, and no commits.

## Global constraints

- Preserve all 20 top-level functions and 3 module constants exactly once.
- Preserve all 12 decorated POST handlers and their `@not_viewer` / `@login_required` /
  `@require_POST` order.
- Preserve all 12 route names in `ui/urls.py`; leave that file byte-identical.
- Leave `ui/views/taxonomy.py` and `ui/views/__init__.py` byte-identical; `_taxonomy_context` stays
  where it is.
- Domain modules must import shared helpers from `_common.py` or the owning domain module, never
  from `ui.taxonomy_views` itself.
- Re-export all 20 functions and 3 constants from `__init__.py`; do not add `__all__`.
- No module-level side effects or import-time DB/settings/URL access.
- No secrets in code, tests, docs, logs, or responses.
- `make check` must pass. Full pytest remains user-owned.

## Package map

Create:

```text
ui/taxonomy_views/
  __init__.py
  _common.py
  case_statuses.py
  alert_statuses.py
  observable_types.py
  terms.py
```

Module ownership follows the approved plan’s table exactly:

- `_common.py`: `_STATUS_PARTIALS`, `_TYPE_PARTIAL`, `_DELETE_PARTIAL`, `_audit_details`, `_audit`,
  `_success`, `_status_error`, `_type_error`, `_delete_error`.
- `case_statuses.py`: three `case_status_*` handlers.
- `alert_statuses.py`: three `alert_status_*` handlers.
- `observable_types.py`: three `observable_type_*` handlers.
- `terms.py`: `_term_create_context`, `_term_edit_context`, three `taxonomy_term_*` handlers.
- `__init__.py`: compatibility re-exports only; no implementations and no `__all__`.

## Tasks and acceptance criteria

### T0 — Baseline and package skeleton

1. Confirm clean worktree and run `make check-fast`.
2. `git mv ui/taxonomy_views.py ui/taxonomy_views/__init__.py`.
3. Smoke import `ui.urls`.

**AC-T0**

- [ ] `git status --short` was clean before implementation.
- [ ] Baseline `make check-fast` passed.
- [ ] `ui.taxonomy_views` and `ui.urls` import after the move.
- [ ] No handler body changed in this step.

### T1 — Extract `_common.py`

Move the 3 constants and 7 shared helpers listed above. Re-export all moved names.

**AC-T1**

- [ ] `_common.py` imports no sibling taxonomy-view modules.
- [ ] All moved names remain importable from `ui.taxonomy_views`.
- [ ] `_taxonomy_context` is still imported from `ui.views.taxonomy`.
- [ ] Taxonomy editing tests pass unchanged.

### T2 — Extract status modules

Move `case_statuses.py`, then `alert_statuses.py`.

**AC-T2**

- [ ] Decorator order and `transaction.atomic()` placement are unchanged.
- [ ] Correct stage vocabulary remains in each status module: `CASE_STAGES` / `ALERT_STAGES`.
- [ ] Audit namespaces and identifier keys remain `case_status` / `value` and
      `alert_status` / `value`.
- [ ] All six status routes resolve with unchanged status codes and flash messages.

### T3 — Extract types and terms

Move `observable_types.py`, then `terms.py`.

**AC-T3**

- [ ] In-use delete protection still uses `obj.observables.count()`.
- [ ] Observable-type audit namespace/key remains `observable_type` / `name`.
- [ ] Term handlers keep local `from core.models import TaxonomyTerm` imports.
- [ ] Term handlers keep the manual superAdmin check and `ui/404.html` response.
- [ ] Audit action remains `taxonomy.term.<result.action>` with `changed` / `old` / `new` details.
- [ ] All six type/term routes resolve with unchanged 200/204/404/422/redirect behaviour.

### T4 — Final compatibility and verification

1. Reduce `__init__.py` to docstring, imports, and re-export aliases.
2. Run structural checks, targeted suites, `make check`, and secret scan.

**AC-T4**

- [ ] All 20 original functions and 3 constants are importable from `ui.taxonomy_views`.
- [ ] `ui/taxonomy_views/__init__.py` contains no implementations and no `__all__`.
- [ ] `ui/urls.py`, `ui/views/taxonomy.py`, and `ui/views/__init__.py` are byte-identical.
- [ ] No test edits were needed; if one is required, record it in the plan before proceeding.
- [ ] AST comparison against `HEAD:ui/taxonomy_views.py` reports no missing, extra, or changed
      definitions and no decorator drift.
- [ ] `make check` passes, including strict mypy over the new package.
- [ ] `scripts/secret-scan.sh --staged` is clean if staged; otherwise `--tracked` is clean.
- [ ] Full pytest is not run or claimed by the implementation agent.

## Targeted verification commands

Run after the relevant phase and at final gate:

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
make check
```

## Dependencies and sequencing

- Follow T0 → T1 → T2 → T3 → T4.
- After each extraction, add re-exports before running tests.
- Do not combine multiple domain moves into one unreviewable step unless trivial.

## Deviations already approved

Recorded in the plan as D-13.2-1 through D-13.2-4:

- `ui/taxonomy_views/` package location.
- Shared status/type helpers in `_common.py`; term context helpers in `terms.py`.
- No new `__all__`.
- No test edits expected.

Any new deviation must be added to the plan before merge.

## Definition of done

- All ACs above satisfied.
- No application behaviour changed.
- Targeted suites and `make check` green.
- No full pytest run by the implementation agent.
- No commit or push.
