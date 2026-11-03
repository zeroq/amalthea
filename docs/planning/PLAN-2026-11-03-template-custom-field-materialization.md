# PLAN-2026-11-03 — Template custom-field materialization

## Goal

Enable `CaseTemplate` specs containing `custom_fields` to create missing global
`CustomField` definitions at apply time, so that templates no longer silently drop
their default fields. When a template references a `CustomField` name not yet in the
vocabulary, `_apply_template` creates the row using the spec's `type` (validated against
`CUSTOM_FIELD_TYPES`, defaulting to `"string"` for missing/invalid types). The existing
global definition's type is never overridden — the template's declared type only applies
when the field is brand new. A `CaseCustomFieldValue` is then created via
`get_or_create` for every case the template is applied to.

This is a deliberate product decision reversal: templates already materialize tags at
apply time (Plan §6.2); a template that silently drops its own default field is a
broken blueprint. The Custom tab should appear on case detail when a template provides
default fields, not only when someone manually created the matching `CustomField`
earlier.

## Non-goals

- No schema or migration changes.
- No changes to the `CaseTemplate` storage model (`custom_fields` remains a JSONB list).
- No changes to the UI tab logic or the `case_custom_field_rows` contract.
- No changes to the API detail response shape (`customFields` already exists).
- No changes to the edit/coercion logic for existing values.

## Decisions

- **Exact-name matching stays case-sensitive.** `CustomField.name` is globally unique and exact;
  changing that would need a migration and a collision policy. Template authors must use the exact
  vocabulary name.
- **Existing definitions win.** A template's declared `type` is only used when creating a new
  `CustomField`; it never mutates an existing global definition.
- **Definitions are global.** This mirrors the model's existing org-neutral design and the
  apply-time materialization already used for `Tag`.
- **Already-applied cases are not backfilled automatically.** Re-applying the template creates the
  missing value; a bulk backfill would need a separate, explicit decision.

Confirmed by the product owner on 2026-11-03: re-apply for existing cases; exact-name matching
stays case-sensitive; existing definitions keep their type; invalid types fall back to `"string"`.
The implementation already matches all four decisions.

## Acceptance Criteria

1. **Missing field creates vocabulary:** Applying a template with a `custom_fields` spec
   whose name has no matching `CustomField` row creates the global `CustomField` and the
   associated `CaseCustomFieldValue` on the case. The case-detail **Custom** tab becomes
   visible (it appears when `case_custom_field_rows(case)` is non-empty).

2. **Existing field reused, type unchanged:** If a `CustomField` with that name already
   exists, `_apply_template` re-uses it without modifying its `type`. A template that
   declares a different type does NOT overwrite the existing definition.

3. **Missing/invalid type defaults to `"string"`:** If the template spec's `type` is
   absent, empty, or not in `CUSTOM_FIELD_TYPES` (`"string"`, `"integer"`, `"float"`,
   `"boolean"`, `"date"`, `"url"`), the newly created `CustomField` gets `type="string"`.

4. **Resync does not overwrite:** Re-applying a template (via the UI edit form or the
   `POST /api/v1/case/_bulk/caseTemplate` endpoint with `resync` semantics) must not
   overwrite an existing `CaseCustomFieldValue`. The `get_or_create` pattern preserves
   the original value.

## Changes

### `cases/views.py::_apply_template` (modified)

The `custom_fields` loop (previously lines 2208-2222) now:

1. Trims the spec's `name` to 100 chars; skips empty names as before.
2. Resolves `CustomField` by exact name (case-sensitive; `CustomField.name` is unique).
3. Resolves the definition with `get_or_create(name=..., defaults={"type": field_type})`;
   `field_type` is the template spec's `type` validated against `CUSTOM_FIELD_TYPES`,
   defaulting to `"string"` for missing/invalid types.
4. If existing: `get_or_create` reuses it and never changes the `type` — the existing global
   definition wins.
5. Then creates the `CaseCustomFieldValue` via `get_or_create` exactly as today.

### Docs updated

- `docs/spec/data-model.md` — updated `CustomField` entry to document that templates
  now materialize missing definitions.
- `docs/planning/PLAN-2026-10-22-case-template-defaults.md` — added "Update 2026-11-03"
  note explaining the deliberate reversal and the rationale.
- `docs/planning/PLAN-2026-11-03-template-custom-field-materialization.md` — this file.

## Test evidence

The following regression tests were added to `tests/conformance/test_case_template_defaults.py`:

- `test_template_missing_field_creates_custom_field_and_value` — verifies that applying a
  template with a previously unknown `CustomField` name creates the global row and the
  case value, and that the case-detail Custom tab becomes visible.
- `test_template_existing_field_reused_type_not_changed` — verifies that an existing
  `CustomField` is reused (not duplicated) and its `type` is not changed by a template
  declaring a different type.
- `test_template_missing_invalid_type_defaults_to_string` — verifies that a missing or
  invalid `type` in the template spec defaults to `"string"`.
- `test_template_resync_does_not_overwrite_existing_value` — verifies that resync
  (via the API bulk endpoint) does not overwrite an existing
  `CaseCustomFieldValue`.

## Verification

Run:

```bash
.venv/bin/pytest \
  tests/conformance/test_case_template_defaults.py \
  tests/conformance/test_t2_p4_surface.py \
  tests/conformance/test_ui_custom_fields_tab.py \
  tests/conformance/test_case_custom_fields_api.py -q
```

and

```bash
make check
```

All existing tests must continue to pass, and the four new tests must pass as well.

**Executed 2026-11-03:** 79 targeted tests passed in 54.47s; 452 related UI,
classification, tenant-scoping and authz tests passed in 217.20s; `make check` green. The full
suite was not rerun for this fix.
