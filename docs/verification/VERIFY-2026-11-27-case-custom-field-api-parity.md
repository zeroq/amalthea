# Verification Report: §10.7-fu1 Custom-field API write parity

Plan: `docs/planning/PLAN-2026-11-27-section-10-open-followups.md`
Verified: 2026-11-27 (analysis of the uncommitted working tree)
Status: Pass

## Summary
- Total ACs: 5
- Met: 5
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC1: API PATCH updates an existing value and emits one `custom-field-updated` event
- **Status:** Met
- **Evidence:** `core/serializers/cases.py::custom_field_updates_from_wire` decodes the external payload at the wire boundary; `cases/update.py::apply_case_update` calls `cases/custom_fields.py::apply_custom_field_updates` with internal `id`/`value` rows. `test_patch_updates_an_existing_value_and_emits_one_event` asserts the stored value changes and exactly one ledger event carries the field name.
- **Notes:** `PUT` uses the same `_update_case` view and applier.

### AC2: Invalid type, oversized value, foreign `_id`, and malformed item return 400 without partial writes
- **Status:** Met
- **Evidence:** `coerce_custom_field_value` retains the 64 KiB and typed-value guards; the boundary decoder validates item shape, UUID and duplicate ids, while `apply_custom_field_updates` enforces case scope. `test_patch_rejects_invalid_types_without_partial_writes`, `test_patch_rejects_an_oversized_value_without_writing`, `test_patch_rejects_a_foreign_value_id`, `test_patch_rejects_malformed_custom_field_items`, and `test_patch_rejects_a_non_list_custom_fields_value` assert 400 responses and unchanged rows/events.
- **Notes:** The oversized-value case is enforced by the shared coercion helper used by both surfaces.

### AC3: Re-submitting the same value emits no new event
- **Status:** Met
- **Evidence:** `update_case_custom_field_value` returns before `save()`/event append when `coerced == value.value`; `test_patch_repeated_value_is_a_no_op` asserts no `custom-field-updated` row.

### AC4: UI write and API write produce the same stored value and event kind
- **Status:** Met
- **Evidence:** `ui/case_custom_fields.py` now re-exports `cases.custom_fields`; `ui/views/case_actions.py` continues to call `update_case_custom_field_value`, and the API calls the same helper through the new applier. `test_api_and_ui_writes_share_value_and_event_contract` compares stored values, titles, and metadata.
- **Notes:** No duplicate coercion or event-emission logic remains in `ui/`.

### AC5: Independent verification record lists AC1–AC4 as PASS with targeted test evidence
- **Status:** Met
- **Evidence:** This report records each criterion with code and test evidence. Focused gate: `.venv/bin/pytest -q tests/conformance/test_case_custom_fields_api.py tests/conformance/test_ui_custom_fields_tab.py` → `44 passed in 68.51s`.
- **Notes:** Final user-run full suite is green: `4511 passed, 3 skipped, 15 warnings, 2 subtests passed in 718.65s`.

## Wire-boundary correction
The first full-suite run found exact TheHive field-name literals in the shared service. The write
decoder now lives in `core/serializers/cases.py` (the API wire boundary), and the service receives
only internal keys. `tests/conformance/test_wire_boundary.py` passes with the correction.

## Deviations
No deviations from the plan.

## Blockers
No blocking issues.

## Recommendations
- API clients can now round-trip `customFields` through the case detail endpoint.
- The next §10 phases (webhook pre-auth limiter and token hardening) should not alter this shared service.
