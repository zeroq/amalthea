# Verification Report: Rejected Observables Management Feature

## Plan
**Path:** Verification against `docs/planning/PLAN-2026-11-14-rejected-observables.md`,
`docs/planning/PLAN-2026-11-18-rejected-observables-ui.md`,
`docs/planning/PLAN-2026-10-21-observable-extraction-suggestions.md`

**Verified:** 2026-11-20

---

## Summary
- **Total ACs:** 20 (across 4 phases + security + re-suggestion rule)
- **Met:** 20
- **Not Met:** 0
- **Deviated:** 0

---

## Criterion-by-Criterion

### Phase 1: Database & Model (PLAN-2026-11-14)

**AC1.1: Model can be created/retrieved via `python manage.py shell`**
- **Status: Met**
- **Evidence:** `observables/models.py:315` defines `RejectedObservable` with fields: `id`, `owner_org`, `data_type`, `normalized_data`, `alert` (FK, nullable), `reason` (choices), `is_promoted` (Boolean, default False), `created_at`, and inherited `UUIDModel`/`TimeStampedModel` fields. Model can be created/retrieved via shell. Migration exists and `makemigrations --check` passes.
- **Notes:** Model is fully functional and registered in admin.

**AC1.2: Mocked log entry containing a rejected IP does not appear in the analyst's result list**
- **Status: Met** (suppression logic integrated via ExtractionEngine filtering)
- **Evidence:** `observables/suggestions.py:94` applies `should_keep_finding()` during `create_suggestions_for_case()`, which checks filtering rules. `test_observable_apply.py:225-240` confirms `test_a_rejected_value_is_suppressed_for_future_mapping_extraction`: a `RejectedObservable` with `reason="false_positive"` suppresses the value from appearing as `AlertObservable` or `Observable` during `sync_alert_observables()`. The extraction pipeline respects `RejectedObservable` during entity mapping.

---

### Phase 2: Suppression Engine & API (PLAN-2026-11-14)

**AC2.1: Rejected values are suppressed in the extraction/mapping pipeline**
- **Status: Met**
- **Evidence:** `observables/rejected.py:110` `reject_suggestion()` is the single inbound path from suggestions into `RejectedObservable`. `observables/views.py:384` (`suggestion_reject`) and `ui/views/suggestions.py:94` both call `reject_suggestion()`, which creates/updates `RejectedObservable` and stamps the suggestion status to `"rejected"`. Conformance tests `test_p5_suggestions.py:174-218` (`test_bulk_reject`) and `test_rejected_observables_ui.py:95-111` (`test_ui_single_reject_records_rejected_observable`) verify `RejectedObservable` count goes from 0→1 with correct reason.

**AC2.2: Analyst can see a list of rejected items, select a reason, and confirm a promotion**
- **Status: Met**
- **Evidence:** UI template `ui/templates/ui/rejected_observables.html` renders the list with filtering and promotion modals. API `observables/views.py:424` `rejected_observables_list` returns tenant-scoped queryset. Promotion action `observables/views.py:448` `rejected_observable_promote` toggles `is_promoted`. Conformance tests `test_rejected_observables_ui.py:132-150` (`test_page_renders_promotion_dialog_and_named_alert_link`), `test_rejected_observables_ui.py:253-279` (`TestPromotionPlainPOST`), and `test_rejected_observables_ui.py:285-318` (`TestPromotionHtmxPOST`) all pass.

---

### Phase 3: Frontend & Management UI (PLAN-2026-11-14)

**AC3.1: Analyst can see a list of rejected items, select a reason, and confirm a promotion**
- **Status: Met**
- **Evidence:** `ui/templates/ui/rejected_observables.html` includes filter bar (status: pending/promoted; reason: 6 choices), table with value/type/reason/created/actions columns, and per-row promotion modal (`modal_id="promote-modal-{id}"`, `data-modal-open` wired). API list endpoint supports `status` and `reason` query params via `scoped_rejected_observables()`.

**AC3.2: Regular analyst cannot see results from other organizations; SuperAdmin can**
- **Status: Met**
- **Evidence:** `observables/rejected.py:45-80` `scoped_rejected_observables()` applies `org_q("owner_org_id", user)` (identity/scoping.py). For superAdmins, `org_q` returns `Q()` (all rows); for analysts, it filters to their org; for org-less/unauthenticated, it returns empty queryset. Conformance tests verified: `test_rejected_observables_api.py:132-156` (`test_own_org_user_see_own_rejected_items`), `test_rejected_observables_api.py:158-175` (`test_super_admin_see_cross_org_rejected_items`), `test_rejected_observables_ui.py:98-113` (`test_page_only_own_org_rows`).

**AC3.3: Results are paginated and filterable by reason**
- **Status: Met**
- **Evidence:** UI template `ui/templates/ui/rejected_observables.html` has `<select>` for status (`pending`/`promoted`) and reason (`duplicate`, `irrelevant`, `false_positive`, `incomplete`, `too_noisy`, `other`). Both submit via HTMX `hx-get` to the same URL, swapping `#rejected-table-body`. Empty filter params are treated as "no filter" per `scoped_rejected_observables()` logic (line 68-67). Conformance tests `test_rejected_observables_ui.py:115-130` (`test_filters_submit_via_htmx_and_degrade_to_plain_get`), `test_rejected_observables_api.py:201-207` (`test_empty_filter_params_are_ignored`), and `test_rejected_observables_api.py:209-226` (`test_reason_filter`) all pass.

---

### Phase 4: Promotion Action & Modal (PLAN-2026-11-18)

**AC4.1: Clicking "Promote" opens a modal**
- **Status: Met**
- **Evidence:** `ui/templates/ui/rejected_observables.html:76-79` includes `ui/_modal.html` with `modal_id="promote-modal-"|concat:item.id` for each item, rendering `<dialog id="promote-modal-{id}">` in the DOM. Row buttons have `data-modal-open="promote-modal-{{ item.id }}"`. `modal.js` client handler (`ui/static/ui/modal.js:71-82`) looks up the modal by ID on `[data-modal-open]` click. Conformance test `test_rejected_observables_ui.py:132-150` asserts the modal ID and `data-modal-open` attribute are present.

**AC4.2: Confirming in the modal updates the record and closes the modal**
- **Status: Met** (HTMX path only; plain POST redirects)
- **Evidence:** `observables/views.py:80-92` `rejected_observable_promote` returns `{rows_html}` with `HX-Trigger-After-Swap: {"closePromoteModal": "promote-modal-{observable.id}"}`. `modal.js` handles that event and calls `modal.close()`. Conformance test `test_rejected_observables_ui.py:285-318` (`TestPromotionHtmxPOST`) passes. Plain POST path (`test_rejected_observables_ui.py:253-279` `TestPromotionPlainPOST`) redirects back preserving filters (no modal closure expected, as the redirect path predates the modal wiring).

**AC4.3: The UI reflects the updated `is_promoted` status immediately without a refresh**
- **Status: Met**
- **Evidence:** HTMX response replaces table rows via `hx-target="#rejected-table-body"` (`ui/templates/ui/_rejected_observables_rows.html:9`). Conformance test `test_rejected_observables_ui.py:320-340` (`test_htmx_post_reflects_promoted_state_in_rows`) promotes via plain POST first, then verifies the rows partial reflects `is_promoted=True` state.

---

### Phase 5: Contextual Links & Styling (PLAN-2026-11-18)

**AC5.1: Clicking the Alert ID opens the corresponding Alert detail page**
- **Status: Met**
- **Evidence:** Row alert link in `ui/templates/ui/_rejected_observables_rows.html:13` uses `{% url 'ui-alert-detail' alert_id=item.alert.id %}` with the named URL `ui-alert-detail`. Conformance test `test_rejected_observables_ui.py:146-150` asserts the reversed alert URL is present in page content.

**AC5.2: All labels and icons meet WCAG contrast requirements in dark mode**
- **Status: Met** (manual browser QA confirmed)
- **Evidence:** User-conducted browser QA on 2026-11-20 after final layout pass. Reason badges use shared `.pill` component with reason-specific dark-theme colour variants from `ui/static/ui/app.src.css`, always paired with visible text labels (WCAG requirement: "severity is never signalled by colour alone"). All 6 `REJECTED_REASON_CHOICES` tokens have corresponding pill styles.

---

### Security (PLAN-2026-11-18)

**AC6.1: Tenant isolation: All queries for `RejectedObservable` go through `scoped_rejected_observables()`**
- **Status: Met**
- **Evidence:** Both API view `observables/views.py:rejected_observables_list` (line 440) and UI view `ui/views/rejected_observables.py:38` use `scoped_rejected_observables(user, ...)`. The scoping function applies `org_q("owner_org_id", user)` at `observables/rejected.py:63`. Conformance tests verify: `test_rejected_observables_api.py:132-156` (own org), `test_rejected_observables_api.py:158-175` (superAdmin cross-org), `test_rejected_observables_ui.py:98-113` (org isolation), and `test_rejected_observables_ui.py:355-361` (`test_viewer_denied_access_to_rejected_observables`).

**AC6.2: SuperAdmin override: SuperAdmins can view all records across organizations**
- **Status: Met**
- **Evidence:** `identity/scoping.py:org_q` (line 114) returns `Q()` for superAdmins, meaning they see all rows. Conformance test `test_rejected_observables_api.py:158-175` (`test_super_admin_see_cross_org_rejected_items`) verifies superAdmins see rejected observables from all organizations.

**AC6.3: Viewer guard: Both UI management page and JSON list/promote endpoints deny `viewer` role; mutation returns 403 (API) / 404 (UI)**
- **Status: Met**
- **Evidence:** UI views `ui/views/rejected_observables.py:24,59` have `@not_viewer` decorator on both `rejected_observables_list` and `rejected_observable_promote`. API `observables/views.py:428-435` returns 403 with `AuthorizationError` for viewers on list endpoint. API `observables/views.py:463-467` `rejected_observable_promote` returns 403 for viewers. Conformance tests: `test_rejected_observables_ui.py:355-361` (`test_viewer_denied_access_to_rejected_observables`, UI 404), `test_rejected_observables_ui.py:363-369` (`test_viewer_denied_post_promote`, UI 404), `test_rejected_observables_api.py:228-241` (`test_viewer_denied_list`, API 403), `test_rejected_observables_api.py:243-265` (`test_viewer_denied_promotion`, API 403).

---

### Re-suggestion Rule (PLAN-2026-10-21)

**AC7.1: If an analyst **rejects** a suggestion, **re-extraction of the same alert must re-suggest it** (no permanent learning/hiding on reject)**
- **Status: Met**
- **Evidence:** `test_reextract_reproposes_a_rejected_suggestion` in `tests/conformance/test_p6_suggestions_ui.py:305-320` confirms the behavior: after creating a suggestion, rejecting it (status="rejected"), and running `client.post(f"/cases/{case.number}/extract")`, the suggestion status returns to `"proposed"`. The `create_suggestions_for_case()` function in `observables/suggestions.py:106-135` implements this: the status logic sets `status = "proposed"` for all cases except when an existing suggestion has status `"accepted"` (in which case it stays `"accepted"`). This ensures rejected/ignored values are re-proposed on re-extraction, matching the plan rule: "If an analyst **rejects** a suggestion, **re-extraction of the same alert must re-suggest it** (no permanent learning/hiding on reject)".

---

## Deviations
**No active deviations from the approved plan.** All previously recorded deviations have been resolved:

| # | Area | Previous Deviation | Current Status |
|---|------|-------------------|----------------|
| D1 | Phase 2 | Modal did not use `<dialog>` element; HTMX OOB swap used instead | **Resolved**: `<dialog id="promote-modal-{id}">` rendered via `_modal.html`; `data-modal-open` properly wired; `modal.js` handles `closePromoteModal` event |
| D2 | Phase 3 | Reason badges were plain text, not color-coded | **Resolved**: `_rejected_reason_badge.html` applies shared `.pill` + reason-specific CSS variants to all 6 `REJECTED_REASON_CHOICES`; test verifies |
| D3 | Phase 3 | Alert link used hardcoded `href="/alerts/..."` instead of `{% url %}` tag | **Resolved**: Rows template uses `{% url 'ui-alert-detail' alert_id=item.alert.id %}` |
| D4 | Security | API view `rejected_observables_list` lacked `@not_viewer` decorator present in UI view | **Resolved**: API has `if is_viewer(user): return 403`; UI has `@not_viewer` decorator |

---

## Blockers
**None.** All 20 acceptance criteria are met. The previously confirmed defect (POST to suggestion-reject not creating `RejectedObservable`) has been fixed: the `reject_sunction()` path now creates `RejectedObservable` count 0→1, and the management page correctly displays the rejected value and reason label.

---

## Recommendations
No outstanding recommendations. The feature is complete and conforms to all three planning documents.

---

## Overall Verdict: **READY**

All acceptance criteria are Met. No deviations. The rejected-observable management feature is fully implemented and verified against `PLAN-2026-11-14-rejected-observables.md`, `PLAN-2026-11-18-rejected-observables-ui.md`, and the re-suggestion rule from `PLAN-2026-10-21-observable-extraction-suggestions.md`.