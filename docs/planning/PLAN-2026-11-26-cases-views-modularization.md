# PLAN-2026-11-26 — Modularize `cases/views.py` (TODO §13.1)

**Status:** Approved 2026-11-26 — user accepted all defaults; implementation and verification complete, pending user acceptance for tracker closeout
**Tracker item:** `TODO.md` §13.1 (P0 — next structural refactor)
**Scope:** §13.1 only. §13.2–§13.20 remain backlog items for later waves.

## Summary

`cases/views.py` has grown to **2,990 lines** with **101 top-level definitions**: 42 `@api_view`
handlers, 59 helpers, 71 named routes in `cases/urls.py`, and a 44-name `__all__`. It mixes case
CRUD, tasks, observables, tags, statuses, comments, pages, shares, attachments, bulk operations,
templates, procedures/TTPs, export, timeline/custom events, and case flow. This is the last large
API-view monolith and the P0 item in the §13 maintainability backlog.

This plan converts it into a `cases/views/` package with one module per domain, following the
approved precedent from `PLAN-2026-11-04-ui-views-modularization.md`. The refactor is **move-only**:
no endpoint, status code, payload, auth check, transaction boundary, model, migration, template, or
UI behaviour changes. `cases/urls.py` stays byte-identical. `cases/views/__init__.py` becomes a
compatibility re-export layer.

## Goals

- Split `cases/views.py` into focused domain modules of roughly 100–750 lines.
- Keep all 71 route names, patterns, handlers, and order unchanged.
- Keep every current `from cases.views import ...` path working, including private helpers used by
  UI views and tests.
- Make domain ownership obvious for the next feature wave.
- Keep the diff reviewable as code movement plus two test-target/boundary edits.
- Preserve strict mypy and ruff cleanliness across the new package.

## Non-goals

- No new feature, bug fix, or behaviour change.
- No schema, migration, model, serializer, or API contract change.
- No URL renaming, route reordering, or decorator reordering.
- No template, CSS, HTMX, WebSocket payload, audit, or ledger change.
- No cleanup of historical docs that reference `cases/views.py:NNNN`; those are snapshots.
- No refactoring of the internal bodies beyond what is required to move code between modules.
- No work on §13.2–§13.20 in this plan.

## Current state

Measured baseline:

- `cases/views.py`: **2,990 lines**, 101 top-level functions, 0 classes, 44 exported names.
- `cases/urls.py`: **71 `path()` entries**, 42 unique handler attributes.
- Module-level constants/state: `_TASK_STATUSES`, `_TIMELINE_KIND_MAP`,
  `_ATTACHMENT_ALLOWED_TYPES`, `_ATTACHMENT_SIGNATURES`, `__all__`.
- No template rendering or HTMX code lives in this API-view module.
- Known external consumers:
  - `cases/urls.py` imports `from . import views` and uses `views.<name>` for all routes.
  - `cases/update.py` lazily imports `_resolve_login` from `cases.views`.
  - `ui/views/cases.py` imports `_as_uuid`, `_resolve_login`, `_case_export_document`.
  - `ui/views/case_artifacts.py` imports `_resolve_ttp_value` and three attachment views.
  - `ui/views/case_templates.py` imports `_apply_template`, `_resolve_case_template`.
  - `ui/views/observables.py` imports `_observable_affected_cases`,
    `_observable_cross_case_guard`, `_set_observable_fields`.
  - `tests/conformance/test_tenant_scoping.py` imports `_case_org_scope`.
  - `tests/conformance/test_case_export_import.py` imports `_case_export_document`.
  - `tests/conformance/test_errors.py` monkeypatches `cases.views.parse_timestamp`.
  - `tests/conformance/test_wire_boundary.py` allowlists `*/views.py`, which must be extended for
    `cases/views/*.py`.
- TODO line-count drift: TODO §13.1 says 2,821 lines; the real current count is 2,990. Record as a
  stale audit snapshot, not a product change.

## Architecture

### Stack and ADR reference

This is a structural refactor in the existing Django 5.x + DRF API layer. It does not change the
architecture or introduce a new ADR. ADR-001 (Django backend) and ADR-006 (role semantics) remain
in force. The split follows the repository precedent from the `ui/views.py` → `ui/views/` package
refactor.

### Package shape

```text
cases/views/
  __init__.py        # compatibility re-exports only
  _common.py         # shared guards, constants, resolvers
  cases.py           # case CRUD, alert removal, timeline, custom events/fields, flow
  tasks.py
  observables.py
  tags.py
  statuses.py        # case statuses + aggregate taxonomy view
  comments.py
  pages.py
  shares.py
  attachments.py
  bulk.py            # bulk updates + case merge
  templates.py       # case templates + apply-template
  procedures.py      # procedures + TTP vocabulary
  export.py
```

`__init__.py` re-exports all names currently in `__all__` plus the private helpers consumed outside
the package. Domain modules import shared helpers from `_common.py` or directly from the owning
domain module; they must not import from `cases.views` itself, to avoid partial-initialization
cycles.

### Module ownership

Line ranges are current-source guidance, not acceptance criteria. Every top-level definition must
appear exactly once after the split.

| Module | Owns | Current source |
|---|---|---|
| `_common.py` | `_TASK_STATUSES`, `_actor`, `_not_found`, `_bad`, `_require_superadmin_for_write`, `_as_uuid`, `_as_int`, `_case_org_scope`, `_case_access`, `_resolve_task`, `_resolve_observable_scoped`, `_resolve_login` | 116–237, 599–623 |
| `cases.py` | `case_collection`, `case_detail`, `_create_case`, `_update_case`, `case_alert_remove`, `_lookup_alert_for_case`, `case_custom_event_create`, `_TIMELINE_KIND_MAP`, `_timeline_event_wire`, `case_timeline`, `custom_event_detail`, `_lookup_custom_event`, `_update_custom_event`, `custom_field_list`, `case_flow` | 252–411, 696–928, 1617–1654 |
| `tasks.py` | `case_task_create`, `case_task_list`, `task_detail`, `_lookup_task`, `_set_task_fields`, `_update_task` | 414–596 |
| `observables.py` | `case_observable_add`, `case_observable_list`, `_observable_affected_cases`, `_cross_case_error`, `_observable_cross_case_guard`, `observable_detail`, `_set_observable_fields`, `_update_observable`, `_observable_detail_payload` | 626–693, 931–1111 |
| `tags.py` | `_resolve_tag`, `tag_collection`, `_create_tag`, `tag_detail`, `_tag_in_use`, `_update_tag`, `_requested_tags`, `case_tag_link`, `observable_tag_link` | 1117–1211, 1372–1429 |
| `statuses.py` | `_resolve_case_status`, `case_status_collection`, `_create_case_status`, `case_status_detail`, `_update_case_status`, `taxonomy` | 1214–1369, 2497–2520 |
| `comments.py` | `_comment_message`, `case_comment_list`, `comment_detail` | 1435–1513 |
| `pages.py` | `case_page_list`, `case_page_detail` | 1516–1614 |
| `shares.py` | `_resolve_org`, `case_share_list`, `share_detail` | 240–249, 1657–1749 |
| `attachments.py` | `_ATTACHMENT_ALLOWED_TYPES`, `_ATTACHMENT_SIGNATURES`, `_unsupported`, `_too_large`, `_safe_attachment_name`, `_attachment_storage_path`, `_sniff_attachment`, `_find_attachment`, `case_attachment_list`, `case_attachment_download`, `case_attachment_detail` | 1752–1969 |
| `bulk.py` | `case_bulk_update`, `_resolve_task_for_bulk`, `task_bulk_update`, `_resolve_observable_for_bulk`, `_bulk_cross_case_guard`, `observable_bulk_update`, `case_merge` | 1977–2141 |
| `templates.py` | `_resolve_case_template`, `_apply_template`, `case_apply_template`, `case_template_collection`, `_create_case_template`, `_template_tags`, `case_template_detail`, `_update_case_template` | 2144–2494 |
| `procedures.py` | `_resolve_ttp_value`, `_parse_procedure_payload`, `case_procedure_create`, `case_procedures_create`, `_create_procedures`, `procedure_detail`, `_resolve_procedure`, `_update_procedure`, `procedure_bulk_delete`, `ttp_collection`, `ttp_detail`, `_update_ttp` | 2526–2755 |
| `export.py` | `_case_export_document`, `case_export` | 2758–2942 |
| `__init__.py` | Existing 44-name `__all__` plus compatibility aliases for private helpers listed in the compatibility section | 2945–2990 |

### Compatibility strategy

1. `git mv cases/views.py cases/views/__init__.py`.
2. Extract `_common.py` first; re-export every moved name from `__init__.py`.
3. Extract one domain module at a time; after each extraction:
   - add explicit re-exports to `__init__.py`;
   - run import smoke checks;
   - run the domain's targeted tests;
   - run `ruff check` and `ruff format --check` on touched files.
4. Leave `cases/urls.py` unchanged. It continues to use `from . import views` and `views.<name>`.
   This is the strongest compatibility gate: a missing re-export fails URLconf import.
5. Keep `cases/update.py`'s call-time `from cases.views import _resolve_login` working through the
   re-export. Do not change it unless an import-cycle test proves it necessary.
6. Keep UI imports working through re-exports:
   `_as_uuid`, `_resolve_login`, `_case_export_document`, `_resolve_ttp_value`, three
   `case_attachment_*` views, `_apply_template`, `_resolve_case_template`,
   `_observable_affected_cases`, `_observable_cross_case_guard`, `_set_observable_fields`.
7. Update only two tests:
   - `tests/conformance/test_errors.py`: patch target becomes
     `cases.views.cases.parse_timestamp`.
   - `tests/conformance/test_wire_boundary.py`: add `cases/views/` to `WIRE_BOUNDARY` and add a
     matcher assertion for a module such as `cases/views/cases.py`, mirroring `ui/views/`.
8. At the end, `cases/views/__init__.py` contains only a package docstring, imports, and `__all__`.

### Route-order constraints

`cases/urls.py` is order-sensitive and must not be edited:

- `case/<id>/<subresource>` routes (including `caseStatus`, `tag`, `template`, `procedure`, `_bulk`,
  `export`, `flow`, `shares`) must stay above bare `case/<str:case_id>`.
- `case/_bulk/caseTemplate` follows `case/_bulk` but stays above case detail.
- `observable/<id>/tag` and `observable/_bulk` precede `observable/<str:observable_id>`.
- `procedure/delete/_bulk` precedes `procedure/<str:procedure_id>`.
- Slash/slashless route pairs dispatch to the same callable; do not split GET/POST handlers.
- `cases.urls` remains mounted before `compat.urls` in `amalthea/urls.py`.

## Data model

No changes. No migrations, model fields, constraints, indexes, or relationships.

## API

No changes. All 42 `@api_view` handlers keep their verb lists, renderer decorators,
`@transaction.atomic` placement, signatures, status codes, response envelopes, field names, query
parameters, pagination rules, and export caps. No route name, pattern, or handler attribute changes.

## UI

No changes. Templates, context keys, HTMX fragments, modal behaviour, keyboard shortcuts, and flash
messages are untouched. UI modules that import API helpers must continue to work via compatibility
re-exports.

## Phases and tasks

### Phase 0 — Baseline and package skeleton

**Task 0.1 — Capture baseline**
- Confirm a clean worktree.
- Run targeted pre-split suites and `make check-fast`.
- Acceptance criteria:
  - [ ] Worktree is clean before implementation.
  - [ ] Baseline targeted suites pass.
  - [ ] No production code changed in this step.

**Task 0.2 — Create the package**
- `git mv cases/views.py cases/views/__init__.py`.
- Import `cases.urls` and run URL smoke tests.
- Acceptance criteria:
  - [ ] `cases.views` still imports.
  - [ ] `cases.urls` imports and resolves all 71 routes.
  - [ ] No view body changed in this step.

### Phase 1 — Shared foundation

**Task 1.1 — Extract `_common.py`**
- Move shared constants and resolvers listed in the ownership table.
- Re-export all moved names.
- Acceptance criteria:
  - [ ] `_common.py` has no imports from sibling view modules.
  - [ ] `_as_uuid`, `_case_org_scope`, `_resolve_login`, `_resolve_task`,
    `_resolve_observable_scoped`, `_actor`, `_not_found`, `_bad`, `_case_access`,
    `_require_superadmin_for_write`, `_TASK_STATUSES`, `_as_int` remain importable from
    `cases.views`.
  - [ ] Tenant-scoping and UI import smoke tests pass unchanged.

### Phase 2 — Core case surface

**Task 2.1 — Extract `cases.py`**
- Move case CRUD, alert removal, timeline, custom events/fields, and flow.
- Acceptance criteria:
  - [ ] All `case-*`, `case-timeline`, `customEvent/*`, `customField`, and `case-flow` routes resolve.
  - [ ] `parse_timestamp` patch target is updated in `test_errors.py`.
  - [ ] Timeline kinds, custom-event validation, and flow payloads are unchanged.
  - [ ] `cases/update.py` lazy import still works.

**Task 2.2 — Extract `tasks.py`**
- Move task creation, list dispatch, detail, and field-update helpers.
- Acceptance criteria:
  - [ ] `case_task_create` remains an undecorated helper called by `case_task_list`.
  - [ ] `case-task-list`, `case-task-list-slash`, and `task-detail` routes pass unchanged.
  - [ ] `resolve_case(..., write=...)` flags and uniform 404s are unchanged.
  - [ ] `_set_task_fields` remains importable for bulk usage.

**Task 2.3 — Extract `observables.py` and `tags.py`**
- Move observable add/list/detail/helpers and tag CRUD/link handlers.
- Acceptance criteria:
  - [ ] `case_observable_add` remains an undecorated helper called by `case_observable_list`.
  - [ ] Cross-case observable guard and tenant scoping are unchanged.
  - [ ] `observable-tag-link` and `case-tag-link` response shapes are unchanged.
  - [ ] UI imports of observable helpers still work.

### Phase 3 — Case sub-resources

**Task 3.1 — Extract `statuses.py`, `comments.py`, `pages.py`, `shares.py`**
- Move status/taxonomy, comment, page, and share handlers.
- Acceptance criteria:
  - [ ] SuperAdmin-before-resolution guard on status writes is unchanged.
  - [ ] Comment case-vs-alert branching and `maybe_record_cross_org_read` order are unchanged.
  - [ ] `transaction.on_commit(partial(publish_case_event, ...))` in `case_page_list` is unchanged.
  - [ ] Share owner-org-only checks are unchanged.
  - [ ] Taxonomy route names resolve from `statuses.py`.

**Task 3.2 — Extract `attachments.py`**
- Move attachment constants, sniffing helpers, path helpers, and handlers.
- Acceptance criteria:
  - [ ] Manual blob cleanup on upload failure is unchanged.
  - [ ] `_safe_attachment_name`, `_attachment_storage_path`, `_sniff_attachment` remain importable
    from `cases.views` if any external caller relies on them.
  - [ ] UI views calling `case_attachment_list/download/detail` as plain callables still work.
  - [ ] Download `FileResponse` headers and status codes are unchanged.

**Task 3.3 — Extract `bulk.py`, `templates.py`, `procedures.py`, `export.py`**
- Move bulk updates, case merge, template CRUD/apply, procedure/TTP handlers, and export.
- Acceptance criteria:
  - [ ] `bulk_patch` per-item savepoints are unchanged.
  - [ ] `case_apply_template` stays with `_apply_template` in `templates.py`.
  - [ ] Procedure payload parsing and case-or-alert procedure resolution are unchanged.
  - [ ] TTP routes remain in `procedures.py`.
  - [ ] Export caps and `_case_export_document` output are unchanged.
  - [ ] UI imports of `_apply_template`, `_resolve_case_template`, `_resolve_ttp_value`, and
    `_case_export_document` still work.

### Phase 4 — Cleanup and verification

**Task 4.1 — Shrink `__init__.py`**
- Leave only docstring, explicit re-exports, and `__all__`.
- Update the wire-boundary allowlist.
- Acceptance criteria:
  - [ ] `cases/views/__init__.py` contains no view bodies or helper implementations.
  - [ ] All 44 original `__all__` names remain exported.
  - [ ] All private helpers used by app code/tests remain exported.
  - [ ] `test_wire_boundary.py` treats `cases/views/` as a presentation boundary.
  - [ ] Ruff reports no unused imports.

**Task 4.2 — Full verification**
- Run targeted suites, `make check`, and the user-owned full pytest gate.
- Acceptance criteria:
  - [x] Targeted suites pass.
  - [x] `make check` passes, including strict mypy over the new package.
  - [x] No migration is generated.
  - [x] Full `pytest` is green; per standing instruction this was user-run and the log was supplied.
  - [x] `git diff --stat` shows code movement plus only the two documented test edits.
  - [x] No behaviour change outside the two test edits.

## Security

- Preserve every decorator block, verb list, and `@transaction.atomic` placement exactly.
- Preserve `resolve_case(..., write=...)`, `resolve_observable`, `resolve_alert`, `resolve_org`,
  superAdmin guards, owner-org checks, share-aware `_case_access`, and uniform 404s.
- Preserve audit writes and ledger/realtime call order:
  `append_timeline_event`, `publish_case_event` on commit, `maybe_record_cross_org_read`,
  `record_taxonomy_change`.
- Preserve attachment content-type/signature checks, storage path construction, and upload-failure
  cleanup.
- Do not add import-time database access, settings reads, URL resolution, or side effects.
- Do not expose raw exception text, secrets, or raw payloads in any new response or log line.

## Testing

Targeted suites to run after relevant phases:

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
```

Final gates:

```bash
make check
.venv/bin/pytest   # user-owned; do not run/claim unless requested
```

No new production tests are required for the split itself unless a compatibility gap is found. The
URLconf import, re-exports, targeted suites, `make check`, and full pytest are the regression gate.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Circular import via `cases/update.py` | Import-time failure | Keep lazy call-time import and package re-export; domain modules never import `cases.views`. |
| Missed re-export breaks URLconf | All API case routes fail | Import `cases.urls` after every extraction; URL smoke tests. |
| Monkeypatch silently stops exercising real code | False green | Update `test_errors.py` patch target to the owning module; run it explicitly. |
| Wire-boundary test rejects moved wire literals | Static gate failure | Add `cases/views/` to `WIRE_BOUNDARY` and add matcher assertion. |
| Decorator order changes | Auth/status-code regression | Treat decorator blocks as atomic; run authz/viewer/attachment suites. |
| Transaction or on-commit order changes | Broken ledger/realtime/audit | Move bodies unchanged; review every `transaction` and `on_commit` line in diff. |
| Route order changes | Literal segments swallowed by `case/<id>` | Never edit `cases/urls.py`; run route enumeration tests. |
| Historical docs cite old line numbers | Confusion only | Leave historical docs untouched; note snapshots in verification. |
| TODO says 2,821 lines | Stale tracker fact | Record actual 2,990-line baseline in this plan; do not treat as a product deviation. |

## Planned deviations to record before merge

- **D-13.1-1:** `_common.py` keeps the leading underscore from the TODO target even though the UI
  precedent used `common.py`; this avoids implying a public API module.
- **D-13.1-2:** Timeline/custom-event/custom-field/flow handlers are placed in `cases.py` because
  the TODO target list has no dedicated module for them.
- **D-13.1-3:** Aggregate `taxonomy` is placed in `statuses.py`; TTP vocabulary stays in
  `procedures.py`; `case_merge` stays in `bulk.py`. These close gaps in the suggested module list.
- **D-13.1-4:** `tests/conformance/test_errors.py` and
  `tests/conformance/test_wire_boundary.py` are the only test edits; both are target/allowlist
  changes, not assertion changes.
- **D-13.1-5:** TODO's 2,821-line figure is stale; measured baseline is 2,990 lines.

## Open questions

Defaults were confirmed by the user on 2026-11-26.

1. **Q1 — Module naming:** keep `_common.py` as TODO specifies, or rename to `common.py` to match
   the UI precedent? **Resolved: `_common.py`.**
2. **Q2 — Granularity:** use the 14-module map above, or add `timeline.py`/`taxonomy.py`/`ttp.py`?
   **Resolved: 14 modules; no extra modules in this wave.**
3. **Q3 — Full pytest:** should the implementation agent run the full suite in this task, or remain
   user-owned? **Resolved: user-owned per standing instruction.**
4. **Q4 — Historical docs:** leave all `cases/views.py:NNNN` references untouched? **Resolved: yes.**

## Decisions confirmed 2026-11-26

- Use the 14-module package map with `_common.py` and a compatibility-only `__init__.py`.
- Keep `cases/urls.py` byte-identical and preserve all 71 route names/order.
- Keep timeline/custom-event/custom-field/flow in `cases.py`, taxonomy in `statuses.py`, TTPs in
  `procedures.py`, and `case_merge` in `bulk.py`; record these as planned deviations.
- Update only `tests/conformance/test_errors.py` and `tests/conformance/test_wire_boundary.py`.
- Do not rewrite historical docs or line references.
- Full pytest remains user-owned; implementation is not complete until it is green.

## Verification record

Recorded 2026-11-26 after implementation. The planner did not run the full suite; this gate is met
from user-provided evidence at `/tmp/amalthea-full-pytest.log`.

- Full `pytest`: **4444 passed, 3 skipped, 15 warnings, 2 subtests passed in 681.75s (0:11:21)**.
- The 3 skips are the expected `tests/conformance/test_indexes.py` SQLite planner skip and the two
  Postgres-only `tests/conformance/test_case_numbering.py` skips.
- The 15 warnings are the pre-existing naive-datetime warnings in
  `tests/conformance/test_raw_click_map.py` / `test_raw_click_map_template.py` and the pre-existing
  CSS-token report in `tests/conformance/test_ui_css_tokens.py`.
- Targeted suites: **634 passed** in 511.74s across the 16 listed conformance files.
- `make check`: passed (ruff, ruff format, mypy over 168 files, Django checks, migration sync, CSS
  drift, playbook scripts).
- `scripts/secret-scan.sh --tracked`: clean over 969 tracked files.
- Import smoke: `cases.views` and `cases.urls` import with Django initialized.
- AST comparison against `HEAD:cases/views.py`: 105 top-level definitions, no missing, extra, or
  changed definitions; decorator lists match exactly.
- `git diff --check`: clean.
- Two transient verification findings were fixed before the final gates: the missing
  `@api_view(["PATCH"])` / `@renderer_classes([JSONRenderer])` block on `case_bulk_update`, and
  import placement drift in `_case_export_document`. The final AST comparison is clean.
- No new deviation beyond D-13.1-1 through D-13.1-5.

## Approval gate

Approved 2026-11-26. Implementation brief was produced for `django-backend` using Phases 0–4,
the module ownership table, and the acceptance criteria above. Any deviation from the approved map
must be recorded in this document before merge.
