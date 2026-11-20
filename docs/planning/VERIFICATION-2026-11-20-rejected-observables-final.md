# Verification Report: Rejected Observables Inline Reject Fix

## Plan

- **Paths:**
  - `docs/planning/PLAN-2026-11-14-rejected-observables.md`
  - `docs/planning/PLAN-2026-11-18-rejected-observables-ui.md`
  - `docs/planning/PLAN-2026-10-21-observable-extraction-suggestions.md` (re-extraction compatibility constraint only)
- **Verified:** 2026-11-20
- **Status:** READY — PASS

---

## Summary

- The approved plans are sufficient; no re-explanation was needed.
- The runtime defect was reproduced: rejecting a suggested observable changed `ObservableSuggestion.status` to `rejected` but did not create a `RejectedObservable`, so the value did not appear on the management page.
- The defect is fixed by routing single and bulk reject actions through `observables.rejected.reject_suggestion()`.
- Rejection reasons are now explicitly collected in the UI, allow-listed in API/UI paths, and displayed on the management page.
- Mapping-rule extraction suppression remains distinct from case re-extraction: rejected values are suppressed during mapping extraction, while case re-extraction may re-suggest rejected values as required by the earlier plan.
- External verifier verdict: **READY** (20/20 ACs Met).
- Security review verdict: **PASS** (no critical/high findings; nav visibility discrepancy is UX-only).
- QA review priority gaps were addressed with added tests for bulk-reject badge visibility, invalid UI reason, and idempotent double reject.

---

## Criterion-by-Criterion

### AC1 — Rejecting a suggestion creates a `RejectedObservable`

- **Status:** Met
- **Evidence:**
  - `observables/rejected.py` adds `reject_suggestion()` as the single write path from suggestion rejection into the `RejectedObservable` ledger.
  - `observables/views.py` calls it from `suggestion_reject` and bulk reject.
  - `ui/views/suggestions.py` calls it from `suggestion_reject` and `_bulk_suggestion_action`.
  - Conformance tests in `tests/conformance/test_p5_suggestions.py` and `tests/conformance/test_p6_suggestions_ui.py` assert rows are created with expected org, type, normalized value, reason, `is_promoted=False`, and `alert=None` where applicable.

### AC2 — Rejected value appears on `ui-rejected-observables`

- **Status:** Met
- **Evidence:**
  - Runtime transaction-rolled-back verification against seeded data showed:
    - reject response `200`
    - suggestion status `rejected`
    - `RejectedObservable` count `0 -> 1`
    - matching rejected rows `1`
    - management page status `200`
    - rejected value present on management page
    - reason label present on management page
  - New test `test_rejected_suggestion_appears_on_rejected_observables_page` locks this behavior in.
  - Existing management-list tests in `tests/conformance/test_rejected_observables_ui.py` and `tests/conformance/test_rejected_observables_api.py` continue to pass.

### AC3 — Reject reason is explicit in UI and allow-listed everywhere

- **Status:** Met
- **Evidence:**
  - New `ui/templates/ui/_suggestion_reject_modal_body.html` renders a reason `<select>` built from `RejectedObservable.REJECTED_REASON_CHOICES`.
  - `ui/templates/ui/_suggestions_bulk_action_modal_body.html` renders the same reason control for bulk reject.
  - `ui/views/case_detail.py` supplies `rejection_reason_choices` to the templates.
  - `normalize_rejection_reason()` in `observables/rejected.py` maps empty input to `other` and raises `ValueError` for unknown tokens.
  - API returns HTTP 400 for unknown reason; UI shows an error message and does not mutate the suggestion.
  - Tests cover valid reason persistence, invalid API reason, invalid UI reason, and reason badge visibility after bulk reject.

### AC4 — Bulk reject creates suppression records

- **Status:** Met
- **Evidence:**
  - `observables.suggestions.bulk_review_suggestions()` accepts `reason` and calls `reject_suggestion()` for rejected targets, including backfill for already-rejected suggestions.
  - API and UI bulk paths pass the validated reason.
  - Tests verify two API rows with `reason="irrelevant"` and two UI rows with `reason="duplicate"`; the UI test also verifies two `data-reason="duplicate"` badges on the management page.

### AC5 — Idempotent reject/backfill

- **Status:** Met
- **Evidence:**
  - `reject_suggestion()` uses `get_or_create` scoped by org, type, normalized value, and alert.
  - Existing rows have their reason reset and `is_promoted` cleared when rejected again.
  - Test `test_reject_twice_updates_one_rejected_observable` verifies one row remains and the reason updates from `false_positive` to `irrelevant`.
  - Test `test_reject_idempotent` verifies rejecting an already-rejected suggestion succeeds and backfills a row.

### AC6 — Suppression and re-extraction semantics

- **Status:** Met
- **Evidence:**
  - `ingest/observable_rules.py` consults `RejectedObservable` for future mapping-rule extraction.
  - New test `test_a_rejected_value_is_suppressed_for_future_mapping_extraction` verifies a rejected email is not linked when `sync_alert_observables()` runs.
  - Existing re-extraction tests verify rejected suggestions can be re-proposed by case re-extraction, preserving the earlier plan constraint.

### AC7 — Tenant scoping and authorization

- **Status:** Met
- **Evidence:**
  - `reject_suggestion()` uses `suggestion.owner_org` for the created ledger row.
  - API/UI reject paths resolve suggestions through their existing scoped helpers before mutation.
  - List/promote paths continue to use `scoped_rejected_observables()`.
  - Security review confirmed tenant scoping, non-viewer authorization, reason allow-listing, CSRF via POST forms, and no cross-org leakage path.
  - Existing tenant scoping tests continue to pass.

### AC8 — UI interaction/modal behavior

- **Status:** Met
- **Evidence:**
  - `ui/templates/ui/_tab_observables.html` replaces the inline Reject form with a modal-opening button per proposed suggestion.
  - Per-suggestion reject modals include the hidden `tab=observables` field so plain POSTs preserve the active tab.
  - Modal IDs and form IDs include `suggestion.id`, avoiding the loop-variable pitfall used by the existing observable edit/remove modals.
  - Bulk reject modal receives `rejection_reason_choices`.
  - New template compile test covers `ui/_suggestion_reject_modal_body.html`.

### AC9 — Broken Refresh button removed

- **Status:** Met
- **Evidence:**
  - `ui/templates/ui/rejected_observables.html` no longer renders the non-functional Refresh button.
  - `tests/conformance/test_rejected_observables_ui.py` passes.

---

## Validation

- `.venv/bin/pytest -q tests/conformance/test_p5_suggestions.py tests/conformance/test_p6_suggestions_ui.py tests/conformance/test_rejected_observables_ui.py tests/conformance/test_rejected_observables_api.py tests/conformance/test_observable_apply.py`
  - `83 passed in 56.81s`
- `.venv/bin/python manage.py check`
  - `System check identified no issues (0 silenced).`
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - `No changes detected`
- `scripts/secret-scan.sh --tracked`
  - `clean — no secrets in 847 tracked file(s) at HEAD (10 patterns, mode --tracked)`
- Runtime transaction-rolled-back verification:
  - no demo data persisted
  - management page visible after reject with correct value and reason

---

## Deviations / Notes

- API and service defaults preserve backwards compatibility by mapping an omitted/empty reason to `other`; the UI requires an explicit selection but defaults to `other`.
- Settings/nav links for rejected observables remain admin/superAdmin-only as documented in `PLAN-2026-11-18-rejected-observables-ui.md`, while the view itself allows analysts via `@not_viewer`. Security review classified this as UX inconsistency, not a vulnerability.
- No unrelated MCP Phase 3 files were modified during this fix; the existing worktree changes for MCP Phase 3 remain intact.
- No commits were created.

---

## Overall Verdict

- **READY — PASS**
- The original reject-to-management-page defect is fixed, covered by regression tests, verified at runtime, and reviewed for security and acceptance criteria.
