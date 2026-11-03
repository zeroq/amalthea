# Plan: Case detail — conditional "Custom" tab

**Date:** 2026-11-02

**Status:** Implemented — 2026-11-03; targeted conformance tests and the full quality gate passed.

**Scope:** UI tab with editable values, plus `customFields` in the case-detail API response. No schema change.

## Summary

Add a new **Custom** tab to the case detail page. The tab appears only when the case has at least one `CaseCustomFieldValue`. It displays the case's custom fields and values and allows authorised users to edit existing values. It is not part of the General tab.

Today, applying a template copies matching custom-field defaults into `CaseCustomFieldValue` (`cases/views.py:2139` `_apply_template`, loop at `cases/views.py:2208`), but no case-detail surface renders them and `GET /api/v1/case/{id}` omits them. This plan closes both gaps without changing template application or the data model.

## Confirmed decisions

1. **Editable** — edit existing custom-field values in the tab. Adding/removing field bindings remains out of scope.
2. **API parity** — `GET /api/v1/case/{id}` returns `customFields` as an ordered array of `OutputCustomFieldValue`-shaped rows.
3. **Placement/count** — tab directly after **General**, with a count badge.
4. **Grouping** — one list; show `CustomField.group` as a dim label when present.

## Goals

1. Add a **Custom** tab to case detail, directly after **General**.
2. Render the tab and panel only when `case.custom_field_values` is non-empty.
3. Display each value with its field name, type, optional group, and formatted value.
4. Sort deterministically: `CaseCustomFieldValue.order`, then `CustomField.name`.
5. Let authorised users edit an existing value through a dark-theme modal and a plain POST fallback.
6. Validate/coerce edited values against `CustomField.type`; reject bad input without writing.
7. Emit a case timeline event for each successful custom-field value update.
8. Fall back safely when `?tab=custom` is requested for a case with no custom fields.
9. Include ordered `customFields` in `GET /api/v1/case/{id}` (detail response only).
10. Preserve CSP and accessibility rules: server-rendered, escaped output, no inline JS/CSS, no colour-only signals.

## Non-goals

- No add/remove custom-field bindings from the case UI.
- No changes to `_apply_template` or `CaseTemplate` storage.
- No `PATCH /api/v1/case/{id}` `customFields` write support in this iteration; the UI uses its own session-auth endpoint. A later plan can route writes through the API service.
- No database schema change or migration.
- No new global keyboard shortcut. Existing tab number shortcuts apply automatically.
- No new CSS framework work; reuse existing case-detail classes unless a scoped overflow fix is proven necessary.

## Architecture

### API output

**`core/serializers.py`**

- Import `CaseCustomFieldValue`.
- Add `custom_field_value_json(value)` returning:
  ```json
  {"_id": "<value UUID>", "name": "<field name>", "type": "<field type>", "value": <JSON value>, "order": 0}
  ```
  `_id` is the `CaseCustomFieldValue` UUID, matching TheHive's value-id semantics.
- In `case_json(..., detail=True)`, add:
  ```python
  payload["customFields"] = [
      custom_field_value_json(v)
      for v in case.custom_field_values.select_related("custom_field").order_by(
          "order", "custom_field__name"
      )
  ]
  ```
- Do **not** add `customFields` to the list/query `case_json` output in this iteration; avoids N+1 in list paths and matches the confirmed scope (`GET /api/v1/case/{id}`).

### UI read path

**New:** `ui/case_custom_fields.py`

- `case_custom_field_rows(case) -> list[dict]`
  - Queries `case.custom_field_values.select_related("custom_field").order_by("order", "custom_field__name")`.
  - Returns rows shaped for the template:
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
  - Formatting:
    - `None` or `""` → `display="—"`, `edit_value=""`
    - `bool` → `display="Yes"` / `"No"`, `edit_value="true"` / `"false"`
    - dict/list → `display=None`, `json_text=json.dumps(..., indent=2, ensure_ascii=False)`, `edit_value` the same JSON, `edit_is_json=True`
    - other scalar → `display=str(value)`, `edit_value=str(value)`

### UI edit path

**New:** `ui/case_custom_fields.py` also owns small update helpers.

- `coerce_custom_field_value(field_type, raw, *, is_json=False)`
  - Empty/whitespace → `""` (clears the value; display becomes `—`).
  - `is_json=True` → `json.loads`; invalid JSON raises `CaseUpdateError("BadRequest", ...)`.
  - `integer` → `int`
  - `float` → `float`
  - `boolean` → accepts `true/false/1/0/yes/no/on/off`; maps to `bool`
  - `date` → `date.fromisoformat`, stored as ISO `YYYY-MM-DD`
  - `string` / unknown → raw string
  - Enforce a 64 KiB cap on the raw value to avoid unbounded JSON payloads.
- `update_case_custom_field_value(case, value, raw, *, actor)`
  - Coerces using the existing row's `custom_field.type` and `is_json=isinstance(value.value, (dict, list))`.
  - Saves only `value` and `updated_at`.
  - Appends a timeline event:
    - `kind="custom-field-updated"`
    - `title=f"Custom field updated: {field.name}"`
    - `metadata={"field": field.name}`
  - Returns the updated row.

### UI endpoint

**`ui/views.py`**

- Add `("custom", "Custom")` to `CASE_TABS` immediately after `general`; update the stale "known panels" docstring.
- Keep `custom` in `CASE_TAB_SLUGS` so the tab is a valid redirect target.
- Fetch `custom_field_rows = case_custom_field_rows(case)` before resolving rendered tabs.
- Build tabs, then filter out `custom` when `not custom_field_rows`.
- Resolve `active_tab` against the **visible** tab slugs; `?tab=custom` with no rows falls back to `general`.
- Add `"custom": len(custom_field_rows)` to the counts map.
- Pass `custom_field_rows` and `case_custom_field_edit_url` in context.

**New view:** `case_custom_field_edit(request, case_id)`

- `@not_viewer`, `@login_required`, `@require_POST`.
- `case = resolve_case(request.user, case_id, write=True)`; `None` → 404.
- Parse `value_id` with the existing `_as_uuid` helper; missing/invalid → 404.
- Look up `case.custom_field_values.select_related("custom_field").filter(pk=value_uuid).first()`; missing/other case → 404.
- Call `update_case_custom_field_value(...)` inside `transaction.atomic()`.
- `CaseUpdateError` → `messages.error(request, exc.message)`; success → `messages.success(...)`.
- Always return `_case_detail_redirect(request, case.number)`; the form posts `tab=custom`, and `CASE_TAB_SLUGS` accepts it.

**`ui/urls.py`**

- Add `cases/<str:case_id>/custom-field` → `views.case_custom_field_edit`, name `ui-case-custom-field-edit`.

### Template

**New:** `ui/templates/ui/_tab_custom.html`

- A single `<section class="card">` with heading `Custom fields` and count.
- Reuse existing metadata classes (`meta-grid`, `meta-row`, `meta-label`, `meta-value`).
- Each row shows field name, optional dim group label, type as plain text, and value.
- Complex values render in `<pre class="body mono">{{ row.json_text }}</pre>`.
- When `can_mutate`, each row has an icon-only edit button with an accessible name,
  `data-modal-open="case-cf-{{ row.id }}"`.
- Render one modal per row when `can_mutate`, using the existing `_modal.html` pattern:
  - `body_template="ui/_case_custom_field_form.html"`
  - `form_action=case_custom_field_edit_url`
  - `edit_value_id=row.id`, `edit_label=row.name`, `edit_type=row.type`,
    `edit_value=row.edit_value`, `edit_is_json=row.edit_is_json`
  - `modal_id="case-cf-<id>"`, `form_id="case-cf-<id>-form"`

**New:** `ui/templates/ui/_case_custom_field_form.html`

- Hidden `value_id` and `tab=custom` targeting the modal footer form via `form="{{ form_id }}"`.
- Label tied to the input.
- Input control by type:
  - `boolean` → `<select>` Yes/No (`true`/`false`)
  - `integer` → `<input type="number" step="1">`
  - `float` → `<input type="number" step="any">`
  - `date` → `<input type="date">`
  - `edit_is_json` → `<textarea rows="6">` with JSON text
  - otherwise → `<input type="text">`
- No inline JS/CSS; no `safe`.

**`ui/templates/ui/case_detail.html`**

- Add the `custom` icon branch (`fa-solid fa-table-list`) to the tab loop.
- Add a `panel-custom` section rendered only when `custom_field_rows` is non-empty.
- No changes to `tabs.js`; it discovers tabs and panels from the DOM.

### CSS

- Reuse existing classes; expected CSS change: none.
- If JSON blocks overflow, add only a scoped rule in `ui/static/ui/app.src.css` and regenerate
  `ui/static/ui/app.css` with `make css`.

## Data flow

```text
CaseTemplate.custom_fields [{"name", "value"}]
        │
        │ _apply_template (existing)
        ▼
CaseCustomFieldValue rows
        │
        ├── UI: case_custom_field_rows → custom_field_rows → _tab_custom.html
        │        └── edit modal → POST ui-case-custom-field-edit → update helper
        │
        └── API: case_json(detail=True) → customFields[]
```

## Phases and tasks

### Phase 1 — API output

**Files:** `core/serializers.py`, `tests/conformance/test_case_custom_fields_api.py`

- [x] Add `custom_field_value_json()`.
- [x] Add ordered `customFields` to `case_json(detail=True)`.
- [x] Cover exact keys, ordering, empty list, and detail-only behaviour.

**Acceptance criteria**

- [x] `GET /api/v1/case/{id}` returns `customFields` as an array with `_id`, `name`, `type`, `value`, `order`.
- [x] `_id` is the `CaseCustomFieldValue` UUID.
- [x] Ordering is `order`, then field name.
- [x] A case with no values returns `customFields: []`.
- [x] List/query responses keep their existing shape.

### Phase 2 — UI read path

**Files:** `ui/case_custom_fields.py`, `ui/views.py`, `tests/conformance/test_ui_custom_fields_tab.py`

- [x] Add `case_custom_field_rows()` and formatting.
- [x] Add `custom` to `CASE_TABS` and visible-tab filtering.
- [x] Resolve active tab against visible tabs.
- [x] Add the count and context values.

**Acceptance criteria**

- [x] A case with ≥1 value has a `custom` tab immediately after `general`.
- [x] A case with no values has no `custom` tab or panel.
- [x] `?tab=custom` with no values renders General as active.
- [x] Rows are ordered by `order`, then `custom_field.name`.
- [x] Boolean, empty, scalar, and JSON formatting match the contract.

### Phase 3 — Edit path

**Files:** `ui/case_custom_fields.py`, `ui/views.py`, `ui/urls.py`,
`ui/templates/ui/_tab_custom.html`, `ui/templates/ui/_case_custom_field_form.html`,
`tests/conformance/test_ui_custom_fields_tab.py`

- [x] Add coercion and update helper with timeline event.
- [x] Add POST route/view with `resolve_case(..., write=True)` and `_as_uuid`.
- [x] Add modal edit control per row and type-specific form body.
- [x] Preserve no-JS plain POST behaviour.

**Acceptance criteria**

- [x] An owner/admin can edit an existing value; the DB value and timeline event update.
- [x] Viewers/non-owners cannot edit; existing permission behaviour is preserved.
- [x] A value from another case cannot be edited through the endpoint (404).
- [x] Invalid integer/float/boolean/date/JSON input shows an error and writes nothing.
- [x] The form returns to the Custom tab after success or validation failure.
- [x] No inline JS/CSS and no `safe` interpolation.

### Phase 4 — Template

**Files:** `ui/templates/ui/case_detail.html`, `ui/templates/ui/_tab_custom.html`,
`ui/templates/ui/_case_custom_field_form.html`

- [x] Add the tab icon and conditional panel.
- [x] Add the count badge.
- [x] Render names, groups, types, and values with existing classes.
- [x] Add accessible edit controls and modals.

**Acceptance criteria**

- [x] Custom tab is keyboard reachable like every other tab.
- [x] Panel has `id="panel-custom"`, `role="tabpanel"`, `aria-labelledby="tab-custom"`, and `tabindex="0"`.
- [x] HTML in field names/values is rendered as text, not markup.
- [x] Complex values are readable and do not overflow the panel.

### Phase 5 — Docs and checks

- [x] Update `docs/spec/api.md` for case-detail `customFields`.
- [x] Update `docs/spec/ui-style.md` with the conditional Custom tab convention.
- [x] Run targeted tests, `make check-fast`, and `make css-check` (if CSS changed).
- [x] Ensure no schema/migration or unrelated template changes.

## Security

- Read/write access is gated by `resolve_case(..., write=True)`; cross-org/unauthorised is 404.
- The edit endpoint accepts only `value_id` and `value`; the value row is re-fetched under the case, so a foreign row id cannot be edited.
- All field names, values, groups, and JSON are autoescaped by Django. No `|safe`, `mark_safe`, or raw HTML interpolation.
- Custom-field values may originate from analyst-authored templates; treat them as untrusted text/JSON.
- Reject invalid typed values and cap raw input at 64 KiB.
- No inline scripts/styles; no new JavaScript.
- Do not log values or secrets. The timeline event records the field name, not the value.

## Testing commands

```bash
.venv/bin/pytest tests/conformance/test_case_custom_fields_api.py -q
.venv/bin/pytest tests/conformance/test_ui_custom_fields_tab.py -q
.venv/bin/pytest tests/conformance/test_case_template_defaults.py -q
make check-fast
make css-check   # only if CSS changes
```

## Risks

- **Dynamic tab list:** `CASE_TAB_SLUGS` stays static while the rendered tab is conditional; `?tab=custom` must be resolved against visible tabs or the page can render no active panel.
- **Keyboard numbering:** inserting Custom after General shifts number shortcuts; test the tab count.
- **Template fields skipped on apply:** `_apply_template` only creates a value when a matching `CustomField` definition exists. The UI must not imply missing template fields were stored.
- **No API write parity:** `PATCH /api/v1/case/{id}` still does not accept `customFields`; editing is UI-only in this iteration.
- **JSON rendering:** pretty-printed values must remain escaped and bounded; very large values should not break layout.
- **Value coercion:** date/boolean/integer/float inputs need explicit server-side validation; never trust the input control type.

## Implementation record (2026-11-03)

Delivered as planned: detail-only `customFields` in the API, conditional and editable Custom tab,
type-aware coercion, timeline event, case-scoped edit endpoint, and docs updates.

### Deviations

- **Tab icon:** the plan named `fa-solid fa-table-list`, but that glyph is not in the vendored Font
  Awesome subset. The tab uses `fa-solid fa-file-lines` instead, which is in the subset and covered
  by `tests/conformance/test_ui_icons.py`.
- **No-op edits:** posting the unchanged value updates nothing and emits no timeline event. The plan
  described the event for a successful update; treating an identical POST as a no-op avoids a
  misleading ledger entry.
- **Boolean input:** in addition to `true`/`false`, the coercer accepts `1`/`0`/`yes`/`no`/`on`/`off`
  (the plan only named the first two). The canonical stored value is still a JSON boolean.
- **No CSS change:** the existing metadata and modal classes were sufficient, so `app.src.css` and
  `app.css` are untouched.

### Verification

- `.venv/bin/pytest tests/conformance/test_case_custom_fields_api.py tests/conformance/test_ui_custom_fields_tab.py tests/conformance/test_case_template_defaults.py -q` — **65 passed**.
- `.venv/bin/pytest tests/conformance/test_viewer_readonly_ui.py tests/conformance/test_ui_urls_smoke.py tests/conformance/test_viewer_affordances.py tests/conformance/test_ui_p1_wiring.py -q` — **56 passed**.
- `.venv/bin/pytest tests/conformance/test_ui_css_tokens.py tests/conformance/test_viewer_readonly_ui.py -q` — **13 passed**.
- `.venv/bin/pytest tests/conformance/test_ui_icons.py -q` — **5 passed**.
- `.venv/bin/pytest tests/conformance/test_query_api.py tests/conformance/test_thehive_fixtures.py tests/conformance/test_wire_boundary.py -q` — **66 passed**.
- `make check` — passed (ruff, format, mypy, Django check, migrations, CSS drift, playbook scripts).
- `make css-check` — passed.
- Full pytest suite was started but aborted by the operator at 8%; it is **not** claimed as passed.

## Handoff

- **django-backend:** API serializer, read/format/coerce/update helpers, `CASE_TABS`/active-tab logic, UI edit route/view, backend tests.
- **django-frontend:** `_tab_custom.html`, `_case_custom_field_form.html`, `case_detail.html` tab/panel, accessible modal wiring.
- Shared contract: row shape and context names above; backend provides `custom_field_rows` and `case_custom_field_edit_url`.
- Implementation brief: `docs/planning/BRIEF-2026-11-02-case-custom-fields-tab.md`.
- Deviations from this plan must be recorded here before completion.
