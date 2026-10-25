# BRIEF-2026-10-22 — Editable Taxonomy Lists (Phase 1)

**Plan:** `docs/planning/PLAN-2026-10-22-taxonomy-editing.md`
**Status:** Approved for implementation.
**Scope:** UI and API write support for Case Statuses, Alert Statuses, and Observable Types.
**Out of scope:** the five compiled enum cards (Case Scope, Phase, Type, Data Classification,
Verdict); they remain Phase 2.

---

## Non-negotiable decisions

1. **Only `superAdmin` may mutate taxonomy**, through the UI **and** the REST API. `admin`,
   `analyst`, and `viewer` keep read access but every write returns a uniform 404.
2. **Renames are allowed** for `CaseStatus.value`, `AlertStatus.value`, and
   `ObservableType.name`. `stage` stays immutable.
3. **In-use rows cannot be deleted.** Unused rows may be hard-deleted.
4. **Taxonomy is global**, not per-organisation.
5. **Hidden statuses are shown on `/taxonomy`** with a badge and can be edited/unhidden. They stay
   out of case/alert pickers.
6. No extra guard for seeded or last-remaining statuses.
7. Every mutation is audited by both UI and API.

Read the full plan before coding. Record any deviation in the plan and, if it changes a wire or data
contract, in `docs/spec/deviations.md`.

---

## Task split

### Backend — `django-backend`

#### B1. Shared service layer

**New file:** `core/taxonomy.py`

Implement small, explicit functions used by both UI and API:

```python
create_case_status(*, value, stage, order, description, hidden)
update_case_status(status, *, value=None, order=None, description=None, hidden=None)
delete_case_status(status)

create_alert_status(*, value, stage, order, description, hidden)
update_alert_status(status, *, value=None, order=None, description=None, hidden=None)
delete_alert_status(status)

create_observable_type(*, name, is_attachment, is_case_sensitive)
update_observable_type(otype, *, name=None, is_attachment=None, is_case_sensitive=None)
delete_observable_type(otype)
```

Requirements:

- Validate required fields, non-empty strings, and max lengths (`64` for status `value`, `100` for
  observable-type `name`). Return/raise field errors, not truncation.
- Validate `stage` on create only, against `CASE_STAGES` / `ALERT_STAGES`.
- Check uniqueness excluding the current row on create and rename. Catch `IntegrityError` and
  convert to a field error.
- Reject in-use deletion:
  - `status.cases.exists()` for case statuses;
  - `status.alerts.exists()` for alert statuses;
  - `otype.observables.exists()` for observable types.
- Return enough information for audit: target type, target id, action, changed field names, old
  identifier, new identifier. A small dataclass is preferred over loose dicts.
- Do not import app models at module import time if it creates a cycle; local imports are fine.

**AC**
- [ ] Create/update/delete work for all three entity types.
- [ ] Rename validates uniqueness and persists.
- [ ] Over-length and blank values return field errors.
- [ ] Invalid stage returns a field error.
- [ ] In-use delete is refused; unused delete succeeds.
- [ ] No API response shape changes beyond the intentional rename and authorization changes.

#### B2. API authorization and rename support

**Files:** `cases/views.py`, `alerts/views.py`, `observables/views.py`.

- Add a small helper for unsafe-method superAdmin checks. The guard must run before resolving the
  target row and must return the existing uniform `404 NotFoundError` envelope for non-superAdmin
  callers. `GET` stays open.
- Apply to:
  - `case_status_collection` and `case_status_detail`
  - `alert_status_collection` and `alert_status_detail`
  - `observable_type_collection` and `observable_type_detail`
- `POST` requires superAdmin.
- `PATCH` requires superAdmin and now accepts:
  - `value` on case/alert statuses;
  - `name` on observable types;
  - the existing metadata fields.
- `DELETE` requires superAdmin and keeps the in-use `400` shape.
- `stage` remains immutable; a PATCH carrying only `stage` is still `400`/no updatable field.
- Refactor the existing create/update/delete logic to call `core.taxonomy`.
- Add audit calls for API mutations. Audit must be in the same transaction as the write. Capture
  ids and old identifiers before deletion.

**AC**
- [ ] `superAdmin` can create/update/rename/delete through the API.
- [ ] `admin`, `analyst`, and `viewer` writes return `404`; their GETs still return `200`.
- [ ] Rename by UUID URL works; the old value/name path then returns `404`.
- [ ] Rename to an existing value/name returns `400` with a field error.
- [ ] In-use delete returns the existing `400`.
- [ ] Successful mutations write `AuditLog` rows with `target_org=None`, actor, action, target id,
      changed fields, and old/new identifiers.
- [ ] Existing non-vocabulary API behavior is unchanged.

#### B3. Audit helper

**File:** `identity/audit.py`

Add:

```python
record_taxonomy_change(request, *, actor, action, target_type, target_id, details)
```

- `target_org=None` (global taxonomy).
- `details` must contain only non-secret field names and identifiers.
- Reuse the existing IP extraction.

**AC**
- [ ] UI and API both produce the same audit shape.
- [ ] No raw payloads or secrets are written.

#### B4. API tests

Update `tests/conformance/test_t2_p1_surface.py`:

- Use a `superAdmin` API client for vocabulary writes.
- Keep `analyst` for non-vocabulary tests.
- Add denial tests for `admin`, `analyst`, and `viewer` on all three vocabularies.
- Add rename tests for all three vocabularies.
- Add audit assertions.
- Keep the existing in-use delete tests.

**AC**
- [ ] The updated file passes.
- [ ] Denial tests assert no row was written.
- [ ] Rename tests assert the row and FK references remain intact.

---

### Frontend — `django-frontend`

#### F1. Taxonomy view context

**Files:** `ui/views.py::taxonomy`, `ui/templates/ui/taxonomy.html`.

- Query **all** case statuses and alert statuses (remove `hidden=False`) ordered by `order`,
  `value`.
- Add `can_manage_taxonomy = request.user.role == "superAdmin"` to the context.
- Keep the five compiled enum cards unchanged.
- Keep `GET /taxonomy` readable by all authenticated roles.

**AC**
- [ ] Hidden statuses render with a `Hidden` badge.
- [ ] Add/edit/delete controls render only for superAdmin.
- [ ] Non-superAdmin sees the page without mutation controls.

#### F2. Forms

**New file:** `ui/taxonomy_forms.py`

- `CaseStatusCreateForm`: `value`, `stage`, `order`, `description`, `hidden`.
- `CaseStatusEditForm`: `value`, `order`, `description`, `hidden`; `stage` displayed read-only.
- `AlertStatusCreateForm` / `AlertStatusEditForm`: same, with `ALERT_STAGES`.
- `ObservableTypeCreateForm`: `name`, `is_case_sensitive`, `is_attachment`.
- `ObservableTypeEditForm`: `name`, flags.
- Use model max lengths and server-side validation messages.
- No inline JS.

**AC**
- [ ] Blank, over-length, duplicate, and invalid-stage inputs produce field errors.
- [ ] Edit forms preserve `stage` unchanged.
- [ ] Rename help text warns that the old name stops resolving and all referencing records update.

#### F3. Mutation views and routes

**New file:** `ui/taxonomy_views.py`; **edit** `ui/urls.py`.

Add the 9 POST routes listed in plan §6.4. Every view:

- is decorated `@not_viewer`, `@login_required`, `@require_POST`;
- calls `_require_superadmin_ui(request)` and returns 404 for everyone else;
- uses `core.taxonomy` services;
- writes an audit row in the same transaction;
- returns `204` + `HX-Redirect` to `/taxonomy` on HTMX success;
- returns a body fragment with `422` on HTMX validation errors;
- re-renders the page with the correct modal auto-opened (`open_modal_id`) and bound values on
  plain-POST errors;
- flashes success and redirects on plain-POST success.

**AC**
- [ ] All 9 routes resolve and are POST-only.
- [ ] `viewer`, `analyst`, and `admin` POSTs return 404 and write nothing.
- [ ] `superAdmin` create/edit/delete succeeds.
- [ ] HTMX errors keep the modal open with inline errors.
- [ ] Plain POST errors do not discard input.
- [ ] CSRF is enforced.

#### F4. Template and modals

**Files:** `ui/templates/ui/taxonomy.html`, new partials under `ui/templates/ui/`:

- `_taxonomy_case_status_form.html`
- `_taxonomy_alert_status_form.html`
- `_taxonomy_observable_type_form.html`
- `_taxonomy_delete_body.html`

Use `ui/_modal.html` and the existing `.page-row` header utility. Do not add CSS unless strictly
needed.

Each editable card:

- header `.page-row` with `<h3>` and an Add button;
- table action column with edit/delete icon buttons;
- `aria-label` on every icon-only control;
- `fa-regular fa-pen-to-square` and `fa-regular fa-trash-can`, decorative icons `aria-hidden`;
- per-row edit and delete modals with unique ids;
- delete modal shows usage count, disables confirm when in use, and explains why;
- empty state includes an inline "Add one" affordance for superAdmin only.

**AC**
- [ ] Add buttons and action columns appear only on the three editable cards.
- [ ] Add/edit/delete use modal dialogs, never native `confirm()`/`alert()`.
- [ ] Keyboard-only flow works; Esc closes modals.
- [ ] Hidden statuses are visible and editable.
- [ ] In-use delete is visually blocked and server-enforced.
- [ ] No inline `<script>` or `<style>`.

#### F5. UI tests

**New file:** `tests/conformance/test_ui_taxonomy_editing.py`.

Cover:

- page render for anonymous/viewer/analyst/admin/superAdmin;
- hidden status badge and visibility;
- Add button/action column visibility;
- UI create/rename/delete for each entity;
- non-superAdmin POST 404s and no-write assertions;
- duplicate rename error;
- in-use delete disabled + server 400/error;
- HTMX error body;
- no-JS fallback;
- live picker appearance after create/rename;
- audit rows.

**AC**
- [ ] New tests fail on a missing control or missing authorization check.
- [ ] Tests assert database state, not only status codes.

---

## Regression gate

Run, in this order:

```bash
python manage.py test tests.conformance.test_t2_p1_surface
python manage.py test tests.conformance.test_ui_taxonomy_editing
python manage.py test tests.conformance.test_tenant_scoping \
  tests.conformance.test_viewer_readonly_ui \
  tests.conformance.test_ui_taxonomy_editing
make check
python manage.py test
```

Expected baseline before new tests: `3099 passed, 3 skipped`. New tests add to that count.

## Docs to update

- `docs/spec/api.md`: superAdmin-only writes, rename support, uniform 404.
- `docs/spec/deviations.md`: rename deviation from the previously recorded immutable identifiers.
- `docs/planning/PLAN-2026-10-22-taxonomy-editing.md`: record implementation deviations.
- No change to `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md`; it is historical.

## Dependencies / constraints

- Every mutation is superAdmin-only. Do not implement an `admin` fallback.
- Do not add per-organisation taxonomy.
- Do not touch the five compiled enum cards except to leave them read-only.
- Do not introduce cache invalidation; queries already read the DB.
- Use `identity.AuditLog`; do not create a parallel audit table.
- `Case.status`, `Alert.status`, and `Observable.data_type` are `PROTECT` FKs; renames are safe,
  deletes are not when in use.

## Handoff order

1. `django-backend` — B1–B4.
2. `django-frontend` — F1–F5, depending on B1/B2/B3.
3. `qa-tester` — full regression and edge cases.
4. `security-auditor` — authorization, audit, CSRF, rename safety.
5. `verifier` — check every AC in plan §9 and this brief against the diff.
