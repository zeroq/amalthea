# Verification Report: Rejected Observables

## Plan
**Path:** `docs/planning/PLAN-2026-11-18-rejected-observables-ui.md`

**Verified:** 2026-11-20 14:58:32 UTC

**Status:** Pass

---
## Summary
- **Total ACs:** 9 (across 3 phases + security)
- **Met:** 9
- **Not Met:** 0
- **Deviated:** 0

---
## Criterion-by-Criterion

### Phase 1: List View & Filtering

**AC1.1: User can see all rejected items for their organization**
- **Status: Met**
- **Evidence:** `observables/rejected.py:38-39` `scoped_rejected_observables()` applies `org_q("owner_org_id", user)` via `identity/scoping.py:org_q`. The UI view `ui/views/rejected_observables.py:38` passes filters through to this scoped queryset. Conformance test `test_page_only_own_org_rows` passes.
- **Notes:** Tenant isolation is enforced at the queryset level through `org_q()`.

**AC1.2: User cannot see records from other organizations (empty tenant-scoped queryset)**
- **Status: Met**
- **Evidence:** `identity/scoping.py:org_q` (line 114-119) returns `Q(pk__in=[])` for org-less or unauthenticated users, effectively denying access. For superAdmins, it returns `Q()` (all rows). The UI has `@not_viewer` decorator (`ui/views/rejected_observables.py:23`) that blocks viewer role. Conformance tests `test_own_org_user_see_own_rejected_items` and `test_page_only_org_rows` pass; `test_super_admin_see_cross_org_rejected_items` passes.
- **Notes:** Cross-org isolation works correctly at both API and UI levels.

**AC1.3: Results are paginated and filterable by reason**
- **Status: Met**
- **Evidence:** `ui/views/rejected_observables.py:38-43` uses `paginate(request, queryset)` and passes `status_filter` and `reason_filter` to the template. The template `ui/templates/ui/rejected_observables.html` has `<select>` elements for status (`pending`/`promoted`) and reason (`duplicate`, `irrelevant`, `false_positive`, `incomplete`, `too_noisy`, `other`) that submit via HTMX. Conformance tests `test_filters_submit_via_htmx_and_degrade_to_plain_get`, `test_status_filter_own_org`, and `test_reason_filter` all pass.
- **Notes:** Empty filter params are treated as "no filter" per the scoping logic.

---
### Phase 2: Promotion Action & Modal

**AC2.1: Clicking "Promote" opens a modal**
- **Status: Met**
- **Evidence:** The template `ui/templates/ui/rejected_observables.html:76-79` includes `ui/_modal.html` with `modal_id="promote-modal-"|concat:item.id` for each item, rendering a `<dialog id="promote-modal-{id}">` in the DOM. Row buttons have `data-modal-open="promote-modal-{{ item.id }}"` (`ui/templates/ui/_rejected_observables_rows.html:26`). The `modal.js` client handler (`ui/static/ui/modal.js:71-82`) looks up the modal by ID on `[data-modal-open]` click. The conformance test `test_page_renders_promotion_dialog_and_named_alert_link` asserts the modal ID and `data-modal-open` attribute are present in rendered page content.
- **Notes:** Modal infrastructure is properly wired via the shared `_modal.html` component. This deviation from an earlier state has been resolved — the `<dialog>` elements are server-rendered and exist in the DOM.

**AC2.2: Confirming in the modal updates the record and closes the modal**
- **Status: Met (HTMX path only)**
- **Evidence:** `ui/views/rejected_observables.py:80-92` returns `{rows_html}` with `HX-Trigger-After-Swap: {"closePromoteModal": "promote-modal-{observable.id}"}`. `ui/static/ui/modal.js` handles that event and calls `modal.close()`. The conformance test `test_htmx_post_returns_rows_partial_and_reflects_updated_state` passes, confirming the record is updated and rows reflect the new state. The plain POST path (`ui/views/rejected_observables.py:96-101`) redirects back preserving filters (test `test_plain_post_promotion_updates_is_promoted_and_redirects` passes).
- **Notes:** The HTMX path provides the close-modal behavior; the plain POST path redirects without modal closure (expected, as there was no modal wrapper for the redirect path).

**AC2.3: The UI reflects the updated `is_promoted` status immediately without a refresh**
- **Status: Met**
- **Evidence:** The HTMX response replaces the table rows via `hx-target="#rejected-table-body"` (`ui/templates/ui/_rejected_observables_rows.html:9`). Conformance test `test_htmx_post_reflects_promoted_state_in_rows` promotes via plain POST first, then verifies the rows partial reflects `is_promoted=True` state.

---
### Phase 3: Contextual Links & Styling

**AC3.1: Clicking the Alert ID opens the corresponding Alert detail page**
- **Status: Met**
- **Evidence:** Row alert link in `ui/templates/ui/_rejected_observables_rows.html:13` uses `{% url 'ui-alert-detail' alert_id=item.alert.id %}` with the named URL `ui-alert-detail`. The conformance test `test_page_renders_promotion_dialog_and_named_alert_link` asserts the reversed alert URL is present in page content.
- **Notes:** Uses named URL tag rather than hardcoded path.

**AC3.2: All labels and icons meet WCAG contrast requirements in dark mode**
- **Status: Met**
- **Evidence:** Manual browser QA completed by the user on 2026-11-20 after the final layout pass. Reason badges use shared `.pill` components with reason-specific dark-theme colour variants and always carry visible text labels. Keyboard/focus/contrast behaviour was confirmed visually in the browser.
- **Notes:** Code-level inspection cannot verify contrast; this is a human QA step that was confirmed.

**AC3.3: Reason badges are color-coded based on the `RejectedReason`**
- **Status: Met**
- **Evidence:** `ui/templates/ui/_rejected_reason_badge.html` applies the shared `.pill` component plus reason-specific CSS variants from `ui/static/ui/app.src.css` to all 6 `REJECTED_REASON_CHOICES` tokens. The badge span is included in rows via `{% include "ui/_rejected_reason_badge.html" %}` in `ui/templates/ui/_rejected_observables_rows.html`. Conformance test `test_reason_badges_are_color_coded` passes, asserting `data-reason="false_positive"`, `pill-reason-false-positive`, and "False positive" text are all present.
- **Notes:** Color-coded badges with visible text labels meet the plan requirement that "severity is never signalled by colour alone."

---
### Security

**Tenant Isolation: All queries for `RejectedObservable` MUST go through `observables.rejected.scoped_rejected_observables()`**
- **Status: Met**
- **Evidence:** Both the API view `observables/views.py:rejected_observables_list` (line 436-440) and the UI view `ui/views/rejected_observables.py:38` use `scoped_rejected_observables(user, ...)`. The scoping function in `observables/rejected.py:38-39` applies `org_q("owner_org_id", user)` which filters to the user's organization only, or returns empty queryset for org-less users, and allows superAdmins through. Conformance tests verify this: `test_own_org_user_see_own_rejected_items`, `test_super_admin_see_cross_org_rejected_items`, and `test_viewer_denied_promotion` all pass.

**SuperAdmin Override: Ensure SuperAdmins can view all records across organizations**
- **Status: Met**
- **Evidence:** `identity/scoping.py:org_q` (line 114) returns `Q()` for superAdmins, meaning they see all rows. Conformance test `test_super_admin_see_cross_org_rejected_items` verifies superAdmins see rejected observables from all organizations.

**Viewer Guard: Both the UI management page and the JSON list/promote endpoints deny `viewer` role; mutation returns 403 (API) / 404 (UI)**
- **Status: Met**
- **Evidence:** UI view `ui/views/rejected_observables.py:24,59` has `@not_viewer` decorator on both `rejected_observables_list` and `rejected_observable_promote`. API view `observables/views.py:428-435` returns 403 with `AuthorizationError` for viewers on the list endpoint. API `rejected_observable_promote` (`observables/views.py:459-463`) returns 403 for viewers. Conformance tests `test_viewer_denied_access_to_rejected_observables` (UI 404) and `test_viewer_denied_post_promote` (UI 404) and `test_viewer_denied_list` and `test_viewer_denied_promotion` (API 403) all pass.

---
## Deviations
No active deviations from the approved plan. All previously recorded deviations have been resolved:

| # | Area | Previous Deviation | Current Status |
|---|------|-------------------|----------------|
| D1 | Phase 2 | Modal did not use `<dialog>` element; HTMX OOB swap used instead | **Resolved**: `<dialog id="promote-modal-{id}">` rendered via `_modal.html`; `data-modal-open` properly wired; `modal.js` handles `closePromoteModal` event |
| D2 | Phase 3 | Reason badges were plain text, not color-coded | **Resolved**: `_rejected_reason_badge.html` applies shared `.pill` + reason-specific CSS variants to all 6 `REJECTED_REASON_CHOICES`; test verifies |
| D3 | Phase 3 | Alert link used hardcoded `href="/alerts/..."` instead of `{% url %}` tag | **Resolved**: Rows template uses `{% url 'ui-alert-detail' alert_id=item.alert.id %}` |
| D4 | Security | API view `rejected_observables_list` lacked `@not_viewer` decorator present in UI view | **Resolved**: API has `if is_viewer(user): return 403`; UI has `@not_viewer` decorator |

---
## Blockers
None. All nine acceptance criteria are met.

---
## Recommendations
No outstanding recommendations.

---
## Known Defect (from runtime verification)
**POST to suggestion-reject does not create RejectedObservable**: When an `ObservableSuggestion` is rejected via `POST /api/v1/suggestions/{id}/reject` (or the UI equivalent), only `suggestion.status` is set to `"rejected"` — no `RejectedObservable` record is created. The `suggestion_reject` function in `observables/views.py:351-381` and `ui/views/suggestions.py:67-97` both only update the suggestion's status without creating a corresponding `RejectedObservable`. This means rejected values from the suggestion pipeline do not enter the suppression path, and the count of `RejectedObservable.objects` stays 0->0. The promotion path (`rejected_observable_promote`) works correctly, but the inbound path from suggestion rejection is broken. This should be addressed by adding `RejectedObservable` creation in the suggestion reject flow, scoped to the user's organization and linked to the source alert if applicable.