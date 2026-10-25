# PLAN-2026-10-22 — Editable Taxonomy Lists

**Status:** APPROVED — decisions recorded 2026-10-22; implementation may begin.
**Owner:** planner
**Related:** `docs/decisions/ADR-002-thehive-api-compatibility.md` §D4, `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §6.2, `docs/spec/ui-style.md`

---

## 1. Summary

Turn `/taxonomy` from a read-only reference page into an editor for Amalthea's first-class
vocabularies:

- **Case Statuses** (`cases.CaseStatus`)
- **Alert Statuses** (`alerts.AlertStatus`)
- **Observable Types** (`observables.ObservableType`)

Each editable card gets:

- an **Add** button in its header;
- an **Actions** column with edit and delete icon buttons;
- server-rendered modal forms using the existing `_modal.html` pattern;
- inline validation and HTMX success/error handling, with a plain-POST fallback.

These three lists are already DB-backed, already have REST CRUD, and are already queried live by
the UI. Adding or renaming values therefore makes them immediately available in case, alert, and
observable pickers without any caching or WebSocket work.

All taxonomy mutations — UI **and** API — are restricted to the `superAdmin` role. Reads remain
available to every authenticated user (viewers included).

The other five taxonomy cards — **Case Scope, Case Phase, Case Type, Data Classification,
Verdict** — are compiled enums in `core/enums.py`, enforced by `choices` and DB `CheckConstraint`s.
They cannot be made editable by wiring CRUD. They are planned as a separate Phase 2 in §11.

---

## 2. Goals

| ID | Goal |
|---|---|
| G1 | Add, edit, rename, and delete Case Statuses, Alert Statuses, and Observable Types from `/taxonomy`. |
| G2 | Keep `stage` immutable; allow `value`/`name` renames with uniqueness checks and audit. In-use rows cannot be deleted. |
| G3 | Newly added or renamed values appear immediately in the corresponding selectors across the platform. |
| G4 | Every taxonomy mutation, UI and API, is restricted to `superAdmin`, POST-only where applicable, CSRF-protected, and audited. |
| G5 | The UI is keyboard-accessible, dark-theme-consistent, and degrades to plain POSTs without HTMX. |
| G6 | Hidden statuses remain manageable from the taxonomy page so they can be unhidden. |
| G7 | No regression outside the intentionally changed API contract; all other tests stay green. |

## 3. Non-Goals

| ID | Non-goal |
|---|---|
| NG1 | Converting the five `core/enums.py` vocabularies to database-backed values. |
| NG2 | Organisation-specific taxonomy overrides. Current vocabularies are global. |
| NG3 | Editing Tags or Case Templates from this page. |
| NG4 | WebSocket push of taxonomy changes; the UI already reads live DB values. |
| NG5 | Redesigning the taxonomy page layout or navigation. |

---

## 4. Current State (verified)

- `/taxonomy` is served by `ui/views.py::taxonomy` with `@login_required` and `@require_GET`.
- The view currently filters statuses to `hidden=False`, so hidden rows cannot be managed.
- The template renders statuses, observable types, and the five compiled enum lists.
- Existing REST CRUD:
  - `GET|POST /api/v1/caseStatus`, `GET|PATCH|DELETE /api/v1/caseStatus/{idOrValue}`
  - `GET|POST /api/v1/alertStatus`, `GET|PATCH|DELETE /api/v1/alertStatus/{idOrValue}`
  - `GET|POST /api/v1/observable/type`, `GET|PATCH|DELETE /api/v1/observable/type/{typeId}`
- Existing contracts (to be changed by this plan):
  - `CaseStatus` / `AlertStatus`: `value` and `stage` immutable after create; PATCH only
    `order`, `description`, `hidden`. Delete is `400` while in use.
  - `ObservableType`: `name` immutable after create; PATCH only `isAttachment` /
    `isCaseSensitive`. Delete is `400` while in use.
  - Write access is currently any authenticated non-viewer; this plan tightens it to `superAdmin`.
- `Case.status`, `Alert.status`, and `Observable.data_type` are FKs with `PROTECT`, so renames are
  structurally safe; deletion is guarded.
- Existing UI modal pattern:
  - `ui/templates/ui/_modal.html`
  - `ui/static/ui/modal.js`
  - `ui/templates/ui/tag_list.html` demonstrates action-column icon links and per-row delete modals.

---

## 5. Decisions (recorded)

| ID | Decision |
|---|---|
| D1 | Phase 1 covers the three DB-backed lists only. The five compiled enums are planned as Phase 2 (§11). |
| D2 | All taxonomy mutations, UI and API, are restricted to `superAdmin`. `admin`, `analyst`, and `viewer` may read but not write. |
| D3 | `CaseStatus.value`, `AlertStatus.value`, and `ObservableType.name` may be renamed. `stage` remains immutable. Renames are unique-checked and audited. |
| D4 | In-use rows cannot be deleted. Delete is allowed only when unused. Hiding a status remains the retirement path. |
| D5 | Taxonomy stays global across organisations. No per-organisation overrides. |
| D6 | Hidden statuses are shown on `/taxonomy` with a `Hidden` badge so they can be edited/unhidden; they stay out of pickers. |
| D7 | Every mutation is audited with actor, action, target type/id, and changed field names/old-new identifiers. |
| D8 | No extra guard for seeded or last-remaining statuses. Existing `PROTECT` FKs and in-use checks are the only deletion guards. |
| D9 | Phase 2, if approved, uses a new `TaxonomyTerm` model; implementation choice is recorded in §11. |

---

## 6. Architecture (Phase 1: three DB-backed lists)

### 6.1 No schema change

Phase 1 adds no tables or columns. It uses the existing models and constraints. Renaming is an
`UPDATE` on a unique column and does not affect the FK references.

### 6.2 Service layer

Create `core/taxonomy.py` with small functions shared by the UI and the existing API views:

- `create_case_status(*, value, stage, order, description, hidden)`
- `update_case_status(status, *, value=None, order=None, description=None, hidden=None)`
- `delete_case_status(status)` → raises/returns a structured in-use error
- equivalent alert-status functions
- `create_observable_type(*, name, is_attachment, is_case_sensitive)`
- `update_observable_type(otype, *, name=None, is_attachment=None, is_case_sensitive=None)`
- `delete_observable_type(otype)`

The service validates lengths, stage allowlists, uniqueness (excluding the current row), and
in-use deletion. `stage` is not accepted by the update functions. API views call the same service,
so UI and API cannot drift.

### 6.3 SuperAdmin authorization

**UI:** `can_manage_taxonomy = request.user.role == "superAdmin"`. Mutation views call
`_require_superadmin_ui(request)` after the decorators and return the existing `ui/404.html` with
status 404 for everyone else. `@not_viewer` remains outermost because the viewer-readonly
conformance test enumerates every mutating route.

**API:** add a view-level guard for unsafe methods. `GET` remains available to every authenticated
user; `POST`, `PATCH`, `PUT`, and `DELETE` return the uniform `NotFoundError` 404 unless
`request.user.role == "superAdmin"`. Apply it to:

- `cases.views.case_status_collection` / `case_status_detail`
- `alerts.views.alert_status_collection` / `alert_status_detail`
- `observables.views.observable_type_collection` / `observable_type_detail`

The guard runs before resolving the target row, matching `identity.views` management endpoints.
A read-scoped API key remains read-only; a superAdmin's write-scoped key may mutate.
`IsSuperAdmin` (403) is not reused for the collection views because they also serve GET.

### 6.4 UI views and routes

Put the new mutation views in `ui/taxonomy_views.py` to avoid growing the 6,000-line `ui/views.py`.
They may import `_base_context`, `not_viewer`, `_require_superadmin_ui`, and `_htmx_redirect` from
`ui.views`; `ui.views` must not import the new module.

Routes in `ui/urls.py`:

| Method | Path | Name |
|---|---|---|
| POST | `/taxonomy/case-statuses/new` | `ui-taxonomy-case-status-create` |
| POST | `/taxonomy/case-statuses/<uuid:status_id>/edit` | `ui-taxonomy-case-status-edit` |
| POST | `/taxonomy/case-statuses/<uuid:status_id>/delete` | `ui-taxonomy-case-status-delete` |
| POST | `/taxonomy/alert-statuses/new` | `ui-taxonomy-alert-status-create` |
| POST | `/taxonomy/alert-statuses/<uuid:status_id>/edit` | `ui-taxonomy-alert-status-edit` |
| POST | `/taxonomy/alert-statuses/<uuid:status_id>/delete` | `ui-taxonomy-alert-status-delete` |
| POST | `/taxonomy/observable-types/new` | `ui-taxonomy-observable-type-create` |
| POST | `/taxonomy/observable-types/<uuid:type_id>/edit` | `ui-taxonomy-observable-type-edit` |
| POST | `/taxonomy/observable-types/<uuid:type_id>/delete` | `ui-taxonomy-observable-type-delete` |

All mutation views are decorated `@not_viewer`, `@login_required`, `@require_POST`, then call
`_require_superadmin_ui(request)` at the top. `admin`, `analyst`, and `viewer` receive 404.
The existing `GET /taxonomy` remains readable by all authenticated roles.

### 6.5 Forms

Add `ui/taxonomy_forms.py`:

- `CaseStatusCreateForm`: `value` (max 64, required), `stage` (allowlist), `order` (int),
  `description`, `hidden`.
- `CaseStatusEditForm`: `value` (max 64, required), `order`, `description`, `hidden`.
  `stage` is rendered as static text because it remains immutable.
- `AlertStatusCreateForm` / `AlertStatusEditForm`: same shape with `ALERT_STAGES`.
- `ObservableTypeCreateForm`: `name` (max 100), `is_case_sensitive`, `is_attachment`.
- `ObservableTypeEditForm`: `name`, `is_case_sensitive`, `is_attachment`.

Both edit forms explain that renaming updates every case, alert, or observable that references the
row, and that the URL-by-value lookup stops resolving the old name. Forms provide bound values and
field errors for the HTMX error path. Server-side service validation remains authoritative.

### 6.6 Audit

Add `identity.audit.record_taxonomy_change(request, *, actor, action, target_type, target_id,
details)` that writes an `AuditLog` with:

- `actor`, `actor_org`;
- `target_org=None` (taxonomy is global);
- `action` such as `taxonomy.case_status.created`, `taxonomy.case_status.updated`,
  `taxonomy.case_status.deleted`;
- `target_type` one of `CaseStatus`, `AlertStatus`, `ObservableType`;
- `target_id`;
- `details={"changed": ["value", "order"], "old": {"value": "Containment"},
  "new": {"value": "Contained", "order": 3}}` (non-secret field names and identifiers only);
- client IP.

Record creation, update, and deletion in the same transaction as the mutation. For delete, capture
the id and old identifier before `delete()`. Both UI and API mutations call the same audit helper.

---

## 7. UI / UX

### 7.1 Cards

Each of the three editable cards gets a header row:

```html
<div class="page-row">
  <h3>Case Statuses</h3>
  {% if can_manage_taxonomy %}
  <button type="button" class="btn btn-sm btn-primary" data-modal-open="create-case-status">
    <i class="fa-solid fa-plus" aria-hidden="true"></i> Add status
  </button>
  {% endif %}
</div>
```

`.page-row` already exists and zeroes the standalone `.btn` top margin, so no new CSS is needed.

Table gains a final `<th scope="col"><span class="sr-only">Actions</span></th>` and, per row,
icon buttons:

- edit: `fa-regular fa-pen-to-square`, `aria-label="Edit case status {{ s.value }}"`;
- delete: `fa-regular fa-trash-can`, `aria-label="Delete case status {{ s.value }}"`.

Icon-only controls carry accessible names. Primary actions keep text labels.

### 7.2 Add / edit modals

- One create modal per card, rendered once at the bottom of the template.
- One edit modal per row, rendered with a unique id (`edit-case-status-{{ s.id }}`).
- One delete confirmation modal per row (`delete-case-status-{{ s.id }}`).
- Use `_modal.html` with `form_id`, `body_template`, `form_action`, `form_hx_post`, and
  `form_hx_target`. No inline scripts/styles.

Edit modal body makes the identifier editable (`value` or `name`) and shows `stage` as read-only
text for statuses. Include the warning: "Renaming updates every case, alert, or observable that
references this value. The old name will stop resolving in API paths." Rename is unique-checked on
submit; a duplicate returns a field error.

### 7.3 Delete modal

- Shows the value/name and usage count.
- If in use: explain that it cannot be deleted and, for statuses, suggest hiding instead;
  render confirm disabled via `confirm_disabled`.
- If unused: destructive confirm button (`btn-err`).
- Server re-checks usage at POST time; the modal is advisory only.

### 7.4 Hidden statuses

- Change the taxonomy view to query all statuses, ordered by `order`, `value`.
- Render a `Hidden` badge for `s.hidden`.
- Hidden statuses remain excluded from case/alert pickers (existing behaviour).
- Edit form exposes the `hidden` checkbox.

### 7.5 HTMX and no-JS behaviour

- HTMX POST success: `204` + `HX-Redirect` to `/taxonomy`; flash a success message.
- HTMX validation error: return the modal body partial with `errors` and bound values, status
  `422`; no full-page reload.
- Plain POST success: flash + redirect.
- Plain POST error: re-render `/taxonomy` with the relevant modal auto-opened
  (`open_modal_id`) and bound values, status `422`. Do not discard user input.
- Keyboard: native `<dialog>` handles Esc/focus; modal.js handles trap/open/close.

### 7.6 Empty states

- Keep "No … defined." text.
- If `can_manage_taxonomy`, include an inline "Add one" link that opens the create modal.
- If not, text only.

---

## 8. Data / API Contracts

### 8.1 REST API changes in Phase 1

- Mutating verbs on all three vocabularies become **`superAdmin` only**. Non-superAdmin callers
  receive a uniform `404`; GET remains open to authenticated users.
- `PATCH /api/v1/caseStatus/{idOrValue}` accepts `value` in addition to `order`, `description`,
  and `hidden`.
- `PATCH /api/v1/alertStatus/{idOrValue}` accepts `value` in addition to `order`, `description`,
  and `hidden`.
- `PATCH /api/v1/observable/type/{typeId}` accepts `name` in addition to `isAttachment` and
  `isCaseSensitive`.
- `stage` remains immutable. A PATCH carrying `stage` is ignored unless another updatable field is
  present; with no updatable field it remains `400`.
- Successful mutations write an audit row, including API calls.
- `GET /api/v1/taxonomy` is unchanged; it already contains the three lists.
- Update `docs/spec/api.md` and add a deviation entry in `docs/spec/deviations.md` for the rename
  and superAdmin-only policy.

### 8.2 Invariants

| Entity | Create | Update | Rename | Delete |
|---|---|---|---|---|
| CaseStatus | `value` unique, 1–64; `stage` ∈ `CASE_STAGES`; `order` int | `order`, `description`, `hidden` | `value` unique, 1–64 | blocked if `status.cases.exists()` |
| AlertStatus | `value` unique, 1–64; `stage` ∈ `ALERT_STAGES`; `order` int | `order`, `description`, `hidden` | `value` unique, 1–64 | blocked if `status.alerts.exists()` |
| ObservableType | `name` unique, 1–100 | `is_attachment`, `is_case_sensitive` | `name` unique, 1–100 | blocked if `type.observables.exists()` |

A rename by UUID path succeeds; the old value/name path then returns `404`. FK references are
unchanged because the row's primary key is stable.

---

## 9. Implementation Phases and Acceptance Criteria

### P1 — Service layer, API authorization, and audit

**Files:** `core/taxonomy.py`, `identity/audit.py`, refactors in `cases/views.py`,
`alerts/views.py`, `observables/views.py`.

**Tasks**
- Add service functions for create/update/delete for the three entity types, including rename.
- Refactor API views to call them without changing response shapes or existing error envelopes.
- Add the superAdmin write guard to all six API views; leave GET open.
- Add `record_taxonomy_change`.
- Add tests for validation, duplicate handling, rename, in-use delete, authorization, and audit.

**AC**
- [ ] Missing/blank `value` or `name` returns a field error.
- [ ] Invalid stage returns a field error on create.
- [ ] Duplicate value/name returns a field error, not a 500.
- [ ] Rename succeeds by UUID path, persists, and the old value/name path is `404`.
- [ ] Rename to an existing value/name returns a field error.
- [ ] `stage` remains unchanged when a PATCH includes it.
- [ ] In-use delete is refused with the existing 400 shape.
- [ ] Unused delete succeeds and writes an audit row with the id captured before deletion.
- [ ] `admin`, `analyst`, and `viewer` API writes return `404`; GET still returns `200`.
- [ ] `superAdmin` API writes succeed and are audited.
- [ ] Existing API tests are updated for the new role policy and otherwise retain their assertions.

### P2 — UI routes and forms

**Files:** `ui/taxonomy_views.py`, `ui/taxonomy_forms.py`, `ui/urls.py`.

**Tasks**
- Add 9 POST routes.
- Decorate with `@not_viewer`, `@login_required`, `@require_POST`.
- Call `_require_superadmin_ui(request)` at the top of every mutation view.
- Implement HTMX and plain-POST success/error paths.
- Add forms with max lengths, rename fields, and field-level errors.

**AC**
- [ ] `viewer`, `analyst`, and `admin` POSTs return 404 and perform no write.
- [ ] `superAdmin` POSTs succeed.
- [ ] CSRF token is required.
- [ ] Successful create/edit/delete redirects to `/taxonomy` and audits.
- [ ] Invalid HTMX POST returns `422` with a body fragment containing the error and bound values.
- [ ] Invalid plain POST returns `422` with the page re-rendered and the correct modal auto-opened.
- [ ] Rename form renames the row and preserves all FK references.
- [ ] Duplicate rename shows a field error and does not write.

### P3 — Template and UX

**Files:** `ui/templates/ui/taxonomy.html`, new `ui/templates/ui/_taxonomy_*.html` partials,
`ui/views.py::taxonomy`.

**Tasks**
- Add card header Add buttons.
- Add Actions column with edit/delete icon controls.
- Add create/edit/delete modals.
- Show hidden statuses with a badge.
- Add `can_manage_taxonomy` to context.
- Preserve empty states and add inline "Add one" links.

**AC**
- [ ] Each editable card has an Add button and an Actions column.
- [ ] Add/edit forms use the existing modal component; no native `confirm()`/`alert()`.
- [ ] Icon-only buttons have accessible names; decorative icons are `aria-hidden`.
- [ ] Every action is keyboard reachable; Esc closes dialogs.
- [ ] Hidden statuses are visible on the page with a `Hidden` badge.
- [ ] Delete confirm is disabled for in-use rows and explains why.
- [ ] No inline `<script>` or `<style>` is introduced.

### P4 — Live picker verification

**Files:** tests only.

**Tasks**
- Add integration tests that create/rename a status or type through the UI, then assert the new
  value appears in the relevant case/alert/observable selector on the next request.

**AC**
- [ ] A newly created case status is selectable in the case edit form.
- [ ] A newly created alert status is selectable in alert triage.
- [ ] A newly created observable type is selectable when adding an observable.
- [ ] A renamed case/alert status shows the new value in pickers and on existing records.
- [ ] A renamed observable type shows the new name in pickers.
- [ ] A hidden status is not selectable.
- [ ] No cache invalidation step is required.

### P5 — Regression, docs, verification

**Tasks**
- Add `tests/conformance/test_ui_taxonomy_editing.py`.
- Add the new mutating routes to the viewer-readonly route matrix expectations if required.
- Update `tests/conformance/test_t2_p1_surface.py` to use `superAdmin` for vocabulary writes and
  add non-superAdmin denial tests.
- Update `docs/spec/api.md` for superAdmin-only writes and rename support.
- Add a `docs/spec/deviations.md` entry recording the change from immutable identifiers.
- Run targeted and full suites.
- Update the plan with deviations.
- Hand to `verifier`, `qa-tester`, and `security-auditor`.

**Commands**
```bash
python manage.py test tests.conformance.test_ui_taxonomy_editing
python manage.py test tests.conformance.test_t2_p1_surface \
  tests.conformance.test_tenant_scoping \
  tests.conformance.test_viewer_readonly_ui \
  tests.conformance.test_ui_taxonomy_editing
make check
python manage.py test
```

**AC**
- [ ] New tests cover each category's create/edit/rename/delete paths.
- [ ] Vocabulary API tests cover superAdmin success and admin/analyst/viewer denial.
- [ ] Viewer-readonly conformance passes with the new routes.
- [ ] Full suite passes (baseline: 3099 passed, 3 skipped before this feature).
- [ ] `make check` passes.
- [ ] No secrets or credentials are introduced.

---

## 10. Security and Operational Notes

- **Global blast radius:** taxonomy rows are not org-scoped. Any edit changes every tenant's
  pickers. Entry is restricted to `superAdmin`.
- **CSRF:** all UI mutations are POST with Django CSRF protection; HTMX uses the same form token.
- **Authorization:** `@not_viewer` is mandatory for the route-inventory test; the superAdmin
  gate is the real control and returns 404, not 403, to avoid object-existence leakage. API
  writes use the same uniform 404.
- **API reads stay open:** all authenticated users may list vocabularies and render pickers.
- **Input validation:** server-side allowlists and max lengths; never trust `maxlength` alone.
- **Rename:** because rows are FKs, renames are safe for stored cases/alerts/observables. The old
  value/name path stops resolving; clients using UUIDs are unaffected. Audit old and new
  identifiers.
- **Concurrency:** unique constraints plus service pre-checks; catch `IntegrityError` and convert
  to a field error.
- **Audit:** never store secrets or raw payloads. Field names and taxonomy identifiers only.
- **Deletion:** use `PROTECT` as the final guard; the explicit in-use check gives a usable 400/UI
  error. No additional seeded/last-value guard (D8).
- **No inline JS:** CSP-safe `modal.js` only.

---

## 11. Phase 2 (planned) — All Eight Cards

The five compiled lists are closed enums:

- `CASE_SCOPE_CHOICES`
- `CASE_PHASE_CHOICES`
- `CASE_TYPE_CHOICES`
- `DATA_CLASSIFICATION_CHOICES`
- `CASE_VERDICT_CHOICES`

They are used in model fields, DB check constraints, serializers, forms, templates, and case
update/close logic (~100 references). Making them editable requires a migration, not just CRUD.

### Proposed model

```python
class TaxonomyTerm(UUIDModel, TimeStampedModel):
    category = CharField(choices=[...])  # case_scope, case_phase, case_type,
    # data_classification, case_verdict
    code = CharField(max_length=64)
    label = CharField(max_length=128)
    order = IntegerField(default=0)
    hidden = BooleanField(default=False)
    protected = BooleanField(default=False)  # unset/undefined and other built-ins
    is_closing = BooleanField(default=False)  # verdict only

    class Meta:
        constraints = [UniqueConstraint(fields=["category", "code"], name="...")]
```

### Integration choices

| Option | Approach | Trade-off |
|---|---|---|
| B1 | Keep `Case`/`CaseTemplate` fields as code strings; drop fixed choices/checks; validate against `TaxonomyTerm`. | Smaller migration, weaker DB integrity, every call site must use the lookup. |
| B2 | Convert fields to FKs to `TaxonomyTerm`; serialize `.code` on the wire. | Strong integrity, larger migration and serializer/form churn. |

Recommended if Phase 2 is approved: **B1 for the first pass**, with a DB trigger or service-level
guard only if integrity concerns dominate. `verdict` needs `is_closing` to replace the hardcoded
`CASE_CLOSING_VERDICTS`; `undefined` and each `unset` are `protected`. New verdicts default to
non-closing unless the form exposes and the product approves a closing flag.

This phase needs its own plan, migration/rollback test, and compatibility review. It should not be
bundled into the Phase 1 handoff.

---

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Global taxonomy edits affect all tenants | High | `superAdmin`-only mutation, audit, clear UI copy. |
| Renaming identifiers breaks clients that address rows by value/name | Medium | UUID paths still work; show warning; audit old/new; document in `deviations.md`. |
| API role tightening breaks existing analyst/admin clients | Medium | Explicit API contract update, 404 denial tests, release note. |
| UI and API validation drift | Medium | Shared service layer + contract tests. |
| Concurrent duplicate create/rename | Low | Unique constraint mapped to field error. |
| Modal count/perf on large status lists | Low | Lists are small; render per-row dialogs; paginate only if needed. |
| Code-enum conversion scope creep | High | Separate Phase 2 plan. |

---

## 13. Verification Plan

- `verifier`: check every AC in §9 against the implementation and diff.
- `qa-tester`: execute the targeted command block in §9 P5; add regression cases for hidden
  statuses and in-use delete.
- `security-auditor`: review authorization, CSRF, audit content, input validation, and the
  global-scope decision.

---

## 14. Handoff

Decisions recorded; implementation may begin. The implementation brief is
`docs/planning/BRIEF-2026-10-22-taxonomy-editing.md`.

Deviations from this plan must be recorded here and in `docs/spec/deviations.md` when they change a
wire or data contract. Acceptance is defined by the ACs in §9; `verifier` checks them against the
diff, not against the implementation summary.
