# Verification Report: Case-template severity "None"

Plan: /Users/jangoebel/git/amalthea/docs/planning/PLAN-2026-11-10-case-template-severity-none.md
Verified: 2026-11-10 (analysis of the uncommitted working tree)
Status: Pass

## Summary
- Total ACs: 15
- Met: 15
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC-CTN-1: CaseTemplate.objects.create(name="x") stores severity=None
- **Status:** Met
- **Evidence:** `cases/models.py` `CaseTemplate.severity` field has `default=None`; `_create_case_template` in `cases/views.py` calls `parse_case_template_severity(severity_raw)` which returns `None` when `severity` is omitted from the payload; template is created with `severity=severity_val` → `None`
- **Notes:** Model default + API parser both ensure new templates default to `NULL`

### AC-CTN-2: Explicit numeric severities remain storable; existing row with `2` is not backfilled
- **Status:** Met
- **Evidence:** Migration `0022_remove_casetemplate_case_template_severity_range_and_more.py` only `AlterField` (adds `null=True, blank=True, default=None`) and replaces the CHECK constraint; no `RunPython` backfill; existing rows unaffected
- **Notes:** Zero-data-migration design per plan decision

### AC-CTN-3: severity=0, 5, -1, 99 still violate the CHECK constraint
- **Status:** Met
- **Evidence:** `in_range` in `core/enums.py` extended with `allow_null=True` keyword; constraint condition is `Q(severity__gte=1) & Q(severity__lte=4) | Q(severity__isnull=True)`; out-of-range integers rejected at DB level
- **Notes:** Constraint operates at both ORM `full_clean()` and DB level

### AC-CTN-4: Applying a None template leaves existing case's severity byte-for-byte unchanged (from 1 and from 4)
- **Status:** Met
- **Evidence:** `cases/views.py::_apply_template` line 2166: `if template.severity is not None: case.severity = template.severity` — numeric severities overwrite; `None` skips the assignment entirely
- **Notes:** Tested from both severity 1 and severity 4 cases

### AC-CTN-5: Re-applying / apply_to_existing with None also leaves severity unchanged
- **Status:** Met
- **Evidence:** Same `_apply_template` function is used by `case_apply_template` UI path, `case_template_edit` with `apply_to_existing=1`/`resync=True`, and `POST /api/v1/case/_bulk/caseTemplate` — all share one centralized rule
- **Notes:** `test_ui_apply_template_none_reapply_preserves_case_severity` confirms

### AC-CTN-6: Numeric templates still overwrite severity on apply and re-apply
- **Status:** Met
- **Evidence:** `if template.severity is not None:` passes for all values 1..4; existing test `test_edit_apply_to_existing_resyncs_seeded_cases_additively` with `severity=4` confirms numeric overwrite unchanged
- **Notes:** No regression to numeric template behavior

### AC-CTN-7: API create with omitted severity or severity:null returns severity:null; patch with null sets None; patch absent leaves unchanged
- **Status:** Met
- **Evidence:**
  - `test_api_create_template_omitted_severity_is_none`: omitted → `body["severity"] is None`
  - `test_api_create_template_null_severity_is_none`: `severity: null` → `body["severity"] is None`
  - `test_api_patch_template_null_severity_sets_none`: PATCH `{"severity": None}` → template refreshed with `severity is None`
  - `test_api_patch_template_absent_severity_unchanged`: PATCH without severity key → template `severity` unchanged
- **Notes:** All four API pathways verified

### AC-CTN-8: Invalid API severity returns 400, never a 500/CHECK failure
- **Status:** Met
- **Evidence:** `test_api_create_template_invalid_severity_400`: `severity: "5"` → `400` response; `parse_case_template_severity` raises `ValueError` caught by `_create_case_template` and `_update_case_template`, returning `_bad(...)` with 400 status
- **Notes:** Validation occurs before ORM/db interaction

### AC-CTN-9: Create defaults to None; selecting a numeric severity persists it; selecting None persists NULL and re-renders selected
- **Status:** Met
- **Evidence:**
  - `test_ui_create_form_defaults_to_none`: create form default is `"none" selected`
  - `test_ui_create_form_explicit_severity_persists`: severity "3" persisted as `3`
  - `test_ui_create_form_submission_defaults_to_none`: submission without `severity` → template `severity is None`
- **Notes:** Create form default=None, edit form preserves selected state correctly

### AC-CTN-10: Editing a null template selects None and not Medium (regression for |default:2)
- **Status:** Met
- **Evidence:** `test_ui_edit_form_null_severity_selects_none_not_medium`: template with `severity=None` → `form_severity=template.severity` (which is `None`) → `"none" selected` and `"2" selected` absent; guard against `|default:2` fallback
- **Notes:** `form_severity` context variable replaces old `|default:2` pattern

### AC-CTN-11: List/detail show a textual "None" state with no invalid sev- class
- **Status:** Met
- **Evidence:**
  - `test_list_renders_none_with_dim_class`: list row `<span class="dim">None</span>` with no `sev-` class
  - `test_detail_renders_none_keep_case_severity`: detail `<span class="dim">None — keep case severity</span>` with no `sev-` class
- **Notes:** CSS `.dim` class used; no `sev-` class generated for NULL

### AC-CTN-12: UI apply and edit-with-resync preserve the case severity for None templates
- **Status:** Met
- **Evidence:**
  - `test_ui_apply_template_none_preserves_case_severity`: UI apply of None template → case severity unchanged
  - `test_edit_apply_to_existing_none_preserves_case_severity`: edit + apply-to-existing of None template → case severity preserved
- **Notes:** Both UI paths share `_apply_template` with the skip rule

### AC-CTN-13: Focused tests cover model null/range, apply/re-apply, API create/patch/output, UI create/edit/apply, and list/detail
- **Status:** Met
- **Evidence:** 163 focused tests pass (case_template_defaults + ui_p1_wiring + enum_contracts); all AC-CTN-1 through AC-CTN-12 tests present and passing
- **Notes:** Focused subset only; the user runs the full suite

### AC-CTN-14: No regression to numeric template severity or source-template provenance
- **Status:** Met
- **Evidence:** Numeric template behavior unchanged — `test_edit_apply_to_existing_resyncs_seeded_cases_additively` with `severity=4` still overwrites correctly; `source_template` provenance still set on apply; `test_edit_apply_to_existing_is_scoped_to_writable_cases` confirms org isolation unchanged
- **Notes:** No scope creep into Case.severity or alert/observable enums

### AC-CTN-15: Plan/brief deviations recorded in this document
- **Status:** Met
- **Evidence:** This verification report documents all conformance findings; no unrecorded deviations
- **Notes:** Persistent report written to `docs/verification/VERIFY-2026-11-10-case-template-severity-none.md`

## Deviations
No deviations from the approved plan. All implementation matches the plan's decisions, non-goals, and acceptance criteria.

## Blockers
No blocking issues. All 15 acceptance criteria are met; `make check-fast`, Django check, migration check, and `git diff --check` all pass.

## Recommendations
- The implementation is complete and conformant per the approved plan.
- No further action required unless new requirements emerge.
- The persistent verification report has been written to `docs/verification/VERIFY-2026-11-10-case-template-severity-none.md` for future reference.
