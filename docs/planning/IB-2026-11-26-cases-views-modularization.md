# Implementation Brief — 2026-11-26 `cases/views.py` modularization (§13.1)

**For:** `django-backend`
**Plan:** [`PLAN-2026-11-26-cases-views-modularization.md`](./PLAN-2026-11-26-cases-views-modularization.md) — **Approved**
**Tracker:** `TODO.md` §13.1
**Status:** Verification complete — user-supplied full pytest green; pending tracker closeout
**Full pytest:** user-owned; green log supplied at `/tmp/amalthea-full-pytest.log`

## Scope

Implement only §13.1: convert `cases/views.py` into a `cases/views/` package with the approved
14-module map. This is a move-only refactor. No feature work, no bug fixes, no schema changes, no
API/UI behaviour changes, and no commits.

## Global constraints

- Preserve all 101 top-level definitions exactly once.
- Preserve all 42 `@api_view` handlers, 71 named routes in `cases/urls.py`, and the 44-name
  `__all__` surface.
- Do **not** edit `cases/urls.py`, `amalthea/urls.py`, models, migrations, serializers, templates,
  CSS, or docs outside this brief/plan unless a deviation is discovered.
- Domain modules must import from `_common.py` or the owning domain module, never from
  `cases.views` itself.
- No module-level side effects or import-time DB/settings/URL access.
- No secrets in code, tests, docs, logs, or responses.
- `make check` must pass. Full pytest remains user-owned.

## Package map

Create:

```text
cases/views/
  __init__.py
  _common.py
  cases.py
  tasks.py
  observables.py
  tags.py
  statuses.py
  comments.py
  pages.py
  shares.py
  attachments.py
  bulk.py
  templates.py
  procedures.py
  export.py
```

Module ownership is defined in the approved plan’s “Module ownership” table. Follow it exactly:

- `_common.py`: shared constants/helpers/resolvers; no sibling imports.
- `cases.py`: case CRUD, alert removal, timeline, custom events/fields, flow.
- `tasks.py`: task create/list/detail/helpers.
- `observables.py`: observable add/list/detail/helpers/cross-case guard.
- `tags.py`: tag CRUD/link handlers.
- `statuses.py`: case statuses + aggregate `taxonomy`.
- `comments.py`, `pages.py`, `shares.py`, `attachments.py`: as named.
- `bulk.py`: bulk updates + `case_merge`.
- `templates.py`: template CRUD + `case_apply_template`.
- `procedures.py`: procedures + TTP vocabulary.
- `export.py`: export helpers and `case_export`.
- `__init__.py`: compatibility re-exports and `__all__` only.

## Tasks and acceptance criteria

### T0 — Baseline and package skeleton

1. Confirm clean worktree and run `make check-fast`.
2. `git mv cases/views.py cases/views/__init__.py`.
3. Smoke import `cases.urls`.

**AC-T0**

- [ ] `git status --short` was clean before implementation.
- [ ] Baseline `make check-fast` passed.
- [ ] `cases.views` and `cases.urls` import after the move.
- [ ] No handler body changed in this step.

### T1 — Extract `_common.py`

Move: `_TASK_STATUSES`, `_actor`, `_not_found`, `_bad`,
`_require_superadmin_for_write`, `_as_uuid`, `_as_int`, `_case_org_scope`, `_case_access`,
`_resolve_task`, `_resolve_observable_scoped`, `_resolve_login`.

**AC-T1**

- [ ] `_common.py` imports no sibling view modules.
- [ ] All moved names remain importable from `cases.views`.
- [ ] `cases/update.py` lazy import still works.
- [ ] Tenant-scoping and UI import smoke tests pass.

### T2 — Extract core case surface

Move `cases.py` and `tasks.py`, then `observables.py` and `tags.py`.

**AC-T2**

- [ ] `case_task_create` and `case_observable_add` remain undecorated helpers called by their list
      dispatchers.
- [ ] All case/task/observable/tag routes resolve with original names/order.
- [ ] `parse_timestamp` patch target in `tests/conformance/test_errors.py` becomes
      `cases.views.cases.parse_timestamp`.
- [ ] Timeline, custom-event, custom-field, flow, cross-case guard, and tag-link behaviour are
      unchanged.
- [ ] `resolve_case(..., write=...)` flags and uniform 404s are unchanged.

### T3 — Extract case sub-resources

Move `statuses.py`, `comments.py`, `pages.py`, `shares.py`, `attachments.py`, `bulk.py`,
`templates.py`, `procedures.py`, `export.py`.

**AC-T3**

- [ ] SuperAdmin-before-resolution guard on status writes is unchanged.
- [ ] Comment case-vs-alert branching and `maybe_record_cross_org_read` order are unchanged.
- [ ] `transaction.on_commit(partial(publish_case_event, ...))` in `case_page_list` is unchanged.
- [ ] Share owner-org checks are unchanged.
- [ ] Attachment content/signature checks, storage path, and upload-failure cleanup are unchanged.
- [ ] `bulk_patch` per-item savepoints and export caps are unchanged.
- [ ] `_apply_template`, `_resolve_case_template`, `_resolve_ttp_value`, and
      `_case_export_document` remain importable for UI callers.

### T4 — Final compatibility and verification

1. Reduce `__init__.py` to docstring, imports, and `__all__`.
2. Update `tests/conformance/test_wire_boundary.py` to treat `cases/views/` as a wire boundary and
   assert the matcher.
3. Run targeted suites, `make check`, and secret scan. Do not run full pytest.

**AC-T4**

- [ ] All 44 original `__all__` names and all external private helpers are exported.
- [ ] `cases/views/__init__.py` contains no implementations.
- [ ] Only two test files changed: `test_errors.py` and `test_wire_boundary.py`.
- [ ] `git diff --stat` is code movement plus those two test edits.
- [ ] No migration generated.
- [ ] `make check` passes, including strict mypy over the new package.
- [ ] `scripts/secret-scan.sh --staged` is clean if staged; otherwise `--tracked` is clean.
- [ ] Full pytest is not run or claimed by the implementation agent.

## Targeted verification commands

Run after the relevant phase and at final gate:

```bash
.venv/bin/pytest tests/conformance/test_tenant_scoping.py -q
.venv/bin/pytest tests/conformance/test_case_export_import.py -q
.venv/bin/pytest tests/conformance/test_errors.py -q
.venv/bin/pytest tests/conformance/test_wire_boundary.py -q
.venv/bin/pytest tests/conformance/test_t1_surface.py -q
.venv/bin/pytest tests/conformance/test_t2_p1_surface.py -q
.venv/bin/pytest tests/conformance/test_t2_p2_surface.py -q
.venv/bin/pytest tests/conformance/test_t2_p3_attachments.py -q
.venv/bin/pytest tests/conformance/test_t2_p4_surface.py -q
.venv/bin/pytest tests/conformance/test_t2_p5_surface.py -q
.venv/bin/pytest tests/conformance/test_authz.py -q
.venv/bin/pytest tests/conformance/test_viewer_reads.py -q
.venv/bin/pytest tests/conformance/test_viewer_readonly_api.py -q
.venv/bin/pytest tests/conformance/test_ui_p1_wiring.py -q
.venv/bin/pytest tests/conformance/test_ui_p2_wiring.py -q
.venv/bin/pytest tests/conformance/test_ui_urls_smoke.py -q
make check
```

## Dependencies and sequencing

- Follow T0 → T1 → T2 → T3 → T4.
- After each extraction, add re-exports before running tests.
- Do not combine multiple domain moves into one unreviewable step unless trivial.

## Deviations already approved

Recorded in the plan as D-13.1-1 through D-13.1-5:

- `_common.py` naming.
- Timeline/custom-event/custom-field/flow in `cases.py`.
- Taxonomy in `statuses.py`, TTP in `procedures.py`, merge in `bulk.py`.
- Two test-target/allowlist edits only.
- TODO line count is stale; baseline is 2,990 lines.

Any new deviation must be added to the plan before merge.

## Definition of done

- All ACs above checked.
- No application behaviour changed.
- Targeted suites and `make check` green.
- No full pytest run by the implementation agent; the user-supplied final log is green.
- No commit or push.
