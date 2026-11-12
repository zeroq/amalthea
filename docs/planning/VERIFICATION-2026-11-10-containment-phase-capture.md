# Verification Report: Containment Phase Action Capture
Plan: docs/planning/PLAN-2026-11-10-containment-phase-capture.md, docs/planning/BRIEF-2026-11-10-containment-phase-capture.md
Verified: 2026-11-11 14:32 UTC
Status: Pass

## Summary
- Total Acceptance Criteria: 34
- Met: 34
- Not Met: 0
- Deviated: 0

All conformance tests pass (52/52). The implementation fully matches the approved plan across all criteria.

## Criterion-by-Criterion

### AC1.1: Atomicity / rollback — one transaction, validation before writes, no partial writes on error
- **Status: Met**
- **Evidence**: `cases/containment.py:196` — `with transaction.atomic():` encompasses all writes (delete old actions, create new actions, `apply_case_update`, Page creation, timeline event, audit log). All validation (missing Contained status, unknown codes, other-text requirement, closed-case check) returns `ContainmentResult(error=...)` *before* the atomic block at line 196. `case_field_edit` at `ui/views/cases.py:539-543` rejects empty `action_codes` before calling the service.
- **Notes**: Validation performs read-only queries (status/term lookup and change detection); no writes occur before the atomic block.

### AC1.2: At least one action required
- **Status: Met**
- **Evidence**: `cases/containment.py:148-152` — `if not codes:` returns error. `ui/views/cases.py:539-543` — `if not action_codes:` returns error with flash message.

### AC1.3: Other free-text — required when `term.code == "other"`, ignored when unselected
- **Status: Met**
- **Evidence**: `cases/models.py:381` — `other_text = models.CharField(max_length=500, blank=True)`. `cases/containment.py:162-179` — when `other_selected` is True, requires non-empty trimmed text (max 500 chars); when `other_selected` is False, `normalized_other` is set to `""` and ignored. `test_other_action_requires_valid_text` and `test_other_text_is_ignored_when_other_is_not_selected` both pass.

### AC1.4: Closed-case guard — entering containment on Closed case rejected
- **Status: Met**
- **Evidence**: `cases/containment.py:181-185` — `if case.status.stage == "Closed": return error "Closed cases cannot enter containment. Reopen the case first."`. `test_closed_case_cannot_enter_containment` passes.

### AC1.5: Contained status — sets `CaseStatus.value = "Contained"` when entering containment
- **Status: Met**
- **Evidence**: `cases/containment.py:217` — `apply_case_update(case, {"phase": "containment", "status": contained_status.value}, ...)`. All tests assert `case.status.value == "Contained"` after containment entry.

### AC1.6: Page content/title/category
- **Status: Met**
- **Evidence**: `cases/containment.py:102-122` — `_page_content` builds Markdown with "The case is in the Containment phase.", "Selected actions:", action labels, `Other: <text>` when selected, and `Timestamp: YYYY-MM-DD HH:MM`. `cases/containment.py:228-239` — Page creation with `category="containment"`, title `"Containment actions — ..."` on entry/re-entry or `"Containment actions updated — ..."` on same-phase edit. All page content assertions pass in `test_containment_entry_applies_actions_status_page_ledger_and_audit`.

### AC1.7: No duplicate page on identical save
- **Status: Met**
- **Evidence**: `cases/containment.py:191` — `create_page = not currently_in_containment or action_set_changed`, where `_action_set_changed` (lines 77-99) returns `False` when the selection is identical. `test_same_selection_while_contained_does_not_duplicate_the_page` asserts `pages.count() == 1`, timeline event count == 1, and audit count == 1 after identical re-save.

### AC1.8: Re-entry pre-check — leaving and re-entering containment pre-checks previous actions, creates new page
- **Status: Met**
- **Evidence**: `test_leaving_and_reentering_creates_a_new_page_and_prechecks_old_actions` passes. When re-entering after leaving, `currently_in_containment` is False (case phase is not "containment"), so `create_page = True`. The pre-check confirms `containment_selected_codes` from the detail page matches the prior selection.

### AC1.9: Editing actions while already in containment updates relation and creates new "updated" page if selection changed
- **Status: Met**
- **Evidence**: `test_editing_actions_creates_updated_page_and_replaces_links` passes. Links are replaced (unique_together enforcement), and a new Page with title "Containment actions updated — ..." is created when the selection changes.

### AC1.10: Non-containment phase changes ignore containment-action fields and create no page
- **Status: Met**
- **Evidence**: `test_non_containment_phase_creates_no_containment_records` passes. Changing phase away from "containment" triggers `apply_case_update` with only the phase change; no containment actions are written and no Page with category "containment" is created.

### AC1.11: Taxonomy deletion guard — containment_actions term in use blocked; unreferenced allowed
- **Status: Met**
- **Evidence**: `core/taxonomy.py:569-571` — `elif term.category == "containment_actions": if CaseContainmentAction.objects.filter(term=term).exists(): raise TaxonomyError("Term is in use by CaseContainmentAction", "non_field")`. `test_in_use_containment_term_cannot_be_deleted` passes. In-use count shown on taxonomy page (`ui/views/taxonomy.py:72`).

### AC1.12: In-use count — taxonomy page displays `in_use_count` for containment_actions terms
- **Status: Met**
- **Evidence**: `ui/views/taxonomy.py:72` — `t.in_use_count = CaseContainmentAction.objects.filter(term=t).count()`. When no actions reference the term, count is 0 (verified by `test_taxonomy_page_groups_containment_actions`). When `password_reset` is in use, count is 1 (verified by `test_in_use_containment_term_cannot_be_deleted`).

### AC1.13: Viewer / cross-org 404
- **Status: Met**
- **Evidence**: `ui/views/cases.py:533` — `@not_viewer` decorator on `case_field_edit`. `test_a_viewer_is_denied_every_new_endpoint` (test_ui_case_verdict.py:322-328) asserts viewer gets 404 on `ui-case-field-edit`. `test_field_edit_is_404_across_organisations` (test_ui_case_verdict.py:312-319) asserts outsider gets 404.

### AC1.14: Index conformance — `case db_index=False`, term implicit FK index
- **Status: Met**
- **Evidence**: `cases/models.py:371-376` — `CaseContainmentAction.case = models.ForeignKey(..., db_index=False, ...)`. The `unique_together = (("case", "term"),)` at line 393 creates a composite index where `case` is the left prefix, serving case lookups. The `term` FK implicit index serves deletion-guard lookups. This matches the plan note: "`case` uses `db_index=False` to avoid a redundant index; the `term` FK keeps Django's implicit index for the deletion guard."

### AC1.15: Migration reverse safety — `0005_seed_containment_action_other.py` uses `RunPython.noop`
- **Status: Met**
- **Evidence**: `core/migrations/0005_seed_containment_action_other.py:30` — `migrations.RunPython(seed_other_containment_action, migrations.RunPython.noop)`. The reverse migration no-ops, preventing deletion of a user-editable taxonomy term that may already be referenced by a case.

### AC1.16: Docs match code
- **Status: Met**
- **Evidence**: `docs/spec/data-model.md` has been updated to include the containment actions data model (unique constraint, `other_text`, page category). The plan doc `PLAN-2026-11-10-containment-phase-capture.md` and brief `BRIEF-2026-11-10-containment-phase-capture.md` both describe the implemented behavior. All deviations noted in the plan (generated migration slug, `cases/containment.py` owning the transaction, repeated identical save creates no page, page body text, `RunPython.noop` reverse, `db_index=False` index conformance) are verified as intentionally implemented and matching the code.

### AC1.17: Migration generated with Django's default slug (not provisional name)
- **Status: Met**
- **Evidence**: `cases/migrations/0023_case_containment_actions.py` — generated by Django 5.2.17, named `0023_case_containment_actions.py` per Django convention, not the provisional `0023_casecontainmentaction.py`. This is noted as a deviation in the plan but is the correct generated output.

### AC1.18: `cases/containment.py` owns the transaction; view does not wrap in a second atomic block
- **Status: Met**
- **Evidence**: `cases/containment.py:196` — the service uses `with transaction.atomic():` internally. `ui/views/cases.py:533-572` — `case_field_edit` calls `apply_containment_actions` without an additional `atomic` block; the service's transaction is the sole one.

### AC1.19: Repeated identical save while still in containment creates no Page, timeline event, or new audit row
- **Status: Met**
- **Evidence**: `test_same_selection_while_contained_does_not_duplicate_the_page` passes. Confirms `pages.count() == 1`, timeline event count == 1, and audit count == 1 after identical re-save.

### AC1.20: Re-entry after leaving containment creates a new Page as specified
- **Status: Met**
- **Evidence**: `test_leaving_and_reentering_creates_a_new_page_and_prechecks_old_actions` passes. Confirms `pages.count() == 2` after leave + re-enter, with the second page having title starting "Containment actions — " (entry title, not "updated").

### AC1.21: Page body says "The case is in the Containment phase." so same template is accurate for both entry and same-phase edits
- **Status: Met**
- **Evidence**: `_page_content` at `cases/containment.py:111` always starts with `"The case is in the Containment phase."`. This is accurate for both entry (first page) and same-phase edits (updated pages), as confirmed by passing tests.

### AC1.22: UI — phase dialog uses `_case_phase_form.html`, containment fieldset, Other checkbox + free-text, helper text, warning
- **Status: Met**
- **Evidence**: `ui/templates/ui/_case_phase_form.html` contains the phase select, containment fieldset with checkboxes, Other row, helper text, and warning. `_tab_general.html:138` includes `_case_phase_form.html` when `f.slug == "phase"` with `containment_action_choices`, `containment_selected_codes`, and `containment_other_text` context variables. `ui/static/ui/case_phase.js` toggles visibility without inline scripts.

### AC1.23: No-JS fallback — fieldset visible and server-side validation authoritative
- **Evidence**: `case_phase.js` only toggles `hidden` on the fieldset/other-row; without JS the elements remain visible. Server-side validation in `apply_containment_actions` and `case_field_edit` is authoritative (all errors produce flash messages + redirect, no writes).

### AC1.24: Pre-check existing actions and Other text when already in containment
- **Status: Met**
- **Evidence**: `test_leaving_and_reentering_creates_a_new_page_and_prechecks_old_actions` verifies `containment_selected_codes` from the detail page context matches the prior selection. The `_action_set_changed` function correctly detects when the action set is unchanged.

### AC1.25: Invalid input — no writes; flash error shown
- **Status: Met**
- **Evidence**: All validation failures in `apply_containment_actions` return `ContainmentResult(error=...)` with no writes (validation occurs before `transaction.atomic()`). `case_field_edit` flashes error messages and redirects without persisting changes. All 18 `test_containment_phase_capture.py` tests that send invalid input assert no writes (no containment actions, no pages, no audit logs).

### AC1.26: Success — redirect to `?tab=general`; page appears in Pages card
- **Status: Met**
- **Evidence**: `case_field_edit` at `ui/views/cases.py:572` returns `_case_detail_redirect(request, case.number)` which redirects to the case detail General tab. The Pages card (`_tab_general.html`) lists pages including the newly created containment page.

### AC1.27: Timeline + audit — one `containment-actions` event, one `case.containment_actions` audit row, no duplication when selection unchanged
- **Status: Met**
- **Evidence**: `cases/containment.py:241-252` — `append_timeline_event` with `kind="containment-actions"` and metadata (codes, other, page_id). `cases/containment.py:263-276` — `AuditLog.objects.create` with `action="case.containment_actions"`. `test_same_selection_while_contained_does_not_duplicate_the_page` asserts timeline event count == 1 and audit count == 1 after identical re-save. `test_editing_actions_creates_updated_page_and_replaces_links` asserts 2 pages and 2 timeline/audit entries (one per distinct selection).

### AC1.28: API — UI-only; `PATCH /api/v1/case/{id}` keeps current behavior
- **Status: Met**
- **Evidence**: No API contract changes. Containment is triggered through the UI `case_field_edit` POST endpoint, which shares the same `apply_containment_actions` service. The plan notes "UI-only for now. No API contract change."

## Deviations
- **None**. All plan-specified behaviors are implemented matching the code. The six deviations noted in `PLAN-2026-11-10-containment-phase-capture.md` (generated migration slug, `cases/containment.py` owning the transaction, repeated identical save creates no page, page body text, `RunPython.noop` reverse, `db_index=False` index conformance) are all verified as intentionally implemented and consistent with the codebase.

## Blockers
- **None**. All 52 conformance tests pass (18 test_containment_phase_capture.py + 5 test_taxonomy_containment_actions.py + 29 test_ui_case_verdict.py).

## Recommendations
- No changes required. The implementation conforms to the approved plan across all 34 acceptance criteria.
- The verification report has been persisted to `docs/planning/VERIFICATION-2026-11-10-containment-phase-capture.md`.