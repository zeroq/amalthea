# Implementation Brief: Case custom fields tab

**Date:** 2026-11-02

**Plan:** [`PLAN-2026-11-02-case-custom-fields-tab.md`](./PLAN-2026-11-02-case-custom-fields-tab.md)

**Status:** Implemented — see the plan’s implementation record for deviations and verification.

## Objective

Add a conditional **Custom** tab to case detail that lists `CaseCustomFieldValue` rows, allows
authorised users to edit existing values, and returns ordered `customFields` from
`GET /api/v1/case/{id}`.

Confirmed decisions: editable values; API read parity; tab after General with a count; one list
with optional group labels.

## Shared contracts

### Template context

`ui/views.py::case_detail` must provide:

```python
custom_field_rows: list[dict]
case_custom_field_edit_url: (
    str  # reverse("ui-case-custom-field-edit", kwargs={"case_id": case.number})
)
```

Each row:

```python
{
    "id": str(value.id),
    "name": field.name,
    "group": field.group,
    "type": field.type,
    "display": str | None,
    "json_text": str | None,
    "edit_value": str,
    "edit_is_json": bool,
}
```

### API row

`GET /api/v1/case/{id}` detail response:

```json
{
  "customFields": [
    {"_id": "<CaseCustomFieldValue UUID>", "name": "Field", "type": "string", "value": "x", "order": 0}
  ]
}
```

Ordering: `CaseCustomFieldValue.order`, then `CustomField.name`. Empty → `[]`. Detail response only.

### UI endpoint

`POST /cases/<case_id>/custom-field`, name `ui-case-custom-field-edit`.

Posted fields:

- `value_id` — `CaseCustomFieldValue` UUID
- `value` — raw form value
- `tab` — `custom`

Behaviour:

- `resolve_case(..., write=True)`; missing/foreign value → 404.
- `@not_viewer`, `@login_required`, `@require_POST`.
- Coerce by `CustomField.type`; invalid input flashes an error and writes nothing.
- On success, save value + append timeline event `kind="custom-field-updated"`.
- Redirect via `_case_detail_redirect` (preserve `tab=custom`).

## Tasks

### Task 1 — API serializer

**Owner:** django-backend
**Files:** `core/serializers.py`, `tests/conformance/test_case_custom_fields_api.py`

- Add `CaseCustomFieldValue` import.
- Add `custom_field_value_json(value)`.
- In `case_json(..., detail=True)`, add ordered `customFields`.
- Do not add it to list/query output.

**ACs**

- [x] Detail `GET /api/v1/case/{id}` returns exact row keys and values.
- [x] `_id` is the value row UUID.
- [x] Ordering is deterministic.
- [x] No values → `[]`.
- [x] List/query responses unchanged.

### Task 2 — Read/format/coerce helpers

**Owner:** django-backend
**Files:** `ui/case_custom_fields.py`

- `case_custom_field_rows(case)` — query with `select_related("custom_field")`, order, format.
- `coerce_custom_field_value(field_type, raw, *, is_json=False)` — empty, JSON, integer, float, boolean, date, string; 64 KiB cap; raise `CaseUpdateError` on bad input.
- `update_case_custom_field_value(case, value, raw, *, actor)` — coerce, save `value` + `updated_at`, append timeline event with field name only.

**ACs**

- [x] Row shape matches the shared contract.
- [x] Booleans display `Yes`/`No`; empty displays `—`; dict/list renders pretty JSON.
- [x] Coercion covers all `CUSTOM_FIELD_TYPES` and rejects invalid typed input.
- [x] Update emits exactly one `custom-field-updated` timeline event.

### Task 3 — Case-detail context and edit endpoint

**Owner:** django-backend
**Files:** `ui/views.py`, `ui/urls.py`

- Add `("custom", "Custom")` to `CASE_TABS` after `general`; update docstring.
- Fetch rows; build tabs; remove `custom` when empty; resolve active tab against visible tabs.
- Add `custom` count.
- Pass `custom_field_rows` and `case_custom_field_edit_url`.
- Add `case_custom_field_edit` view and route.

**ACs**

- [x] Custom tab present only when values exist.
- [x] `?tab=custom` with no values falls back to General.
- [x] Edit endpoint requires write access and returns 404 for foreign/missing values.
- [x] Invalid input flashes an error and writes nothing.
- [x] Successful edit returns to `?tab=custom`.
- [x] Viewer/non-owner cannot edit.

### Task 4 — Templates

**Owner:** django-frontend
**Files:** `ui/templates/ui/_tab_custom.html` (new), `ui/templates/ui/_case_custom_field_form.html` (new), `ui/templates/ui/case_detail.html`

- Add `panel-custom` and tab icon `fa-solid fa-table-list`.
- Render rows with existing `meta-grid`/`meta-row`/`meta-label`/`meta-value` classes.
- Add count badge.
- Add per-row edit button only when `can_mutate`, with accessible name.
- Add one modal per row using `_modal.html` and `_case_custom_field_form.html`.
- Form body uses type-specific controls; JSON values use a textarea.
- No inline JS/CSS; no `safe`.

**ACs**

- [x] Panel has `id="panel-custom"`, `role="tabpanel"`, `aria-labelledby="tab-custom"`, `tabindex="0"`.
- [x] Tab and panel are absent with no values.
- [x] Edit controls are keyboard reachable and labelled.
- [x] No-JS form submission works.
- [x] HTML in field names/values is escaped.
- [x] Complex values do not overflow the panel.

### Task 5 — Tests

**Owner:** django-backend (API + UI view tests). Frontend may add template smoke assertions but must not edit backend tests.

**Files:** `tests/conformance/test_case_custom_fields_api.py`, `tests/conformance/test_ui_custom_fields_tab.py`

Cover:

- Tab presence/absence, order, count.
- Active-tab fallback.
- Formatting: boolean, empty, scalar, JSON.
- Escaping.
- Successful edit for each scalar type; DB + timeline.
- Invalid input per type; no write.
- Foreign/missing value id → 404.
- Viewer/non-owner denied.
- API detail shape/order; list unchanged.

## Constraints

- No schema or migration changes.
- No `PATCH /api/v1/case/{id}` custom-field write support in this iteration.
- No adding/removing case custom-field bindings.
- Keep CSP: no inline JS/CSS.
- Use `.venv/bin/python` / `.venv/bin/pytest`.
- Regenerate CSS with `make css` only if `ui/static/ui/app.src.css` changes.
- Do not commit or push unless explicitly requested.
- Record any deviation in the plan before completion.

## Validation

```bash
.venv/bin/pytest tests/conformance/test_case_custom_fields_api.py -q
.venv/bin/pytest tests/conformance/test_ui_custom_fields_tab.py -q
.venv/bin/pytest tests/conformance/test_case_template_defaults.py -q
make check-fast
make css-check
```

## Dependencies

- Task 2 depends on Task 1 only for shared conventions; no code dependency.
- Task 3 depends on Task 2.
- Task 4 depends on the shared context contract from Task 3; can start against the contract in parallel.
- Task 5 depends on Tasks 1–4.
