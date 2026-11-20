# Verification Report: Rejected Observables UI

## Plan
**Path:** `docs/planning/PLAN-2026-11-18-rejected-observables-ui.md`

**Verified:** 2026-11-20 22:15:32 UTC

**Status:** Partial

---

## Summary
- **Total ACs:** 9 (across 3 phases + security)
- **Met:** 5
- **Not Met:** 2
- **Deviated:** 2

---

## Criterion-by-Criterion

### Phase 1: List View & Filtering

**AC1.1: User can see all rejected items for their organization**
- **Status: Met**
- **Evidence:** `observables/views.py:rejected_observables_list` (line 420) calls `scoped_rejected_observables(user, ...)` which applies `org_q("owner_org_id", user)` via `identity/scoping.py:org_q`. The UI view `ui/views/rejected_observables.py:rejected_observables_list` (line 37) passes filters through to this scoped queryset. Conformance test `test_own_org_user_see_own_rejected_items` passes.
- **Notes:** Tenant isolation is enforced at the queryset level through `org_q()`.

**AC1.2: User cannot see records from other organizations (403/Empty)**
- **Status: Met**
- **Evidence:** `identity/scoping.py:org_q` (line 114-119) returns `Q(pk__in=[])` for org-less or unauthenticated users, effectively denying access. For superAdmins, it returns `Q()` (all rows). The UI has `@not_viewer` decorator (ui/views/rejected_observables.py:23) that blocks viewer role. Conformance test `test_page_only_own_org_rows` passes; `test_super_admin_see_cross_org_rejected_items` passes.
- **Notes:** Cross-org isolation works correctly at both API and UI levels.

**AC1.3: Results are paginated and filterable by reason**
- **Status: Met**
- **Evidence:** `ui/views/rejected_observables.py:rejected_observables_list` (line 39) uses `paginate(request, queryset)` and passes `status_filter` and `reason_filter` to the template. The template `ui/templates/ui/rejected_observables.html` has `<select>` elements for status (`pending`/`promoted`) and reason (`duplicate`, `irrelevant`, `false_positive`, `incomplete`, `too_noisy`, `other`) that submit via HTMX. Conformance tests `test_filters_submit_via_htmx_and_degrade_to_plain_get`, `test_status_filter_own_org`, and `test_reason_filter` all pass.

---

### Phase 2: Promotion Action & Modal

**AC2.1: Clicking "Promote" opens a modal**
- **Status: Deviated**
- **Evidence:** The promote button in `ui/templates/ui/rejected_observables.html:94` has `data-modal-open="promote-modal-{{ item.id }}"`. The `ui/static/ui/modal.js` handles `[data-modal-open]` clicks by looking up `document.getElementById(modalId)`. However, **no `<dialog id="promote-modal-...">` element exists in the template or base layout** for promotion modals. The only `<dialog>` in the system is the cheatsheet modal in `ui/templates/ui/base.html:224`. The OOB close content `_modal_close_oob.html:7` has `data-modal-close="promote-modal"` (hardcoded, not using the item-specific ID), and is a `<div>` not a `<dialog>`, so the modal.js `closest("dialog")` selector will not match. The modal infrastructure exists for the cheatsheet but is not properly wired for promotion modals.
- **Rationale:** The implementation uses HTMX OOB swap instead of a server-rendered `<dialog>` modal, deviating from the plan's "Build the Confirmation Modal using the `<dialog>` element" task. The core promotion logic works, but the modal UX is broken/non-functional.

**AC2.2: Confirming in the modal updates the record and closes the modal**
- **Status: Met (HTMX path only)**
- **Evidence:** `observables/views.py:rejected_observable_promote` (line 435) calls `promote_rejected_observable(user, id)` which sets `is_promoted = True` and saves. The HTMX path (ui/views/rejected_observables.py:75-95) returns `{rows_html}{close_oob}` with `HX-Trigger: close-modal`. The conformance test `test_htmx_post_returns_rows_partial_and_reflects_updated_state` passes, confirming the record is updated and rows reflect the new state.
- **Notes:** The plain POST path (non-HTMX) redirects back to the list with filters preserved (test `test_plain_post_promotion_updates_is_promoted_and_redirects` passes), but does not close a modal since there was no modal to begin with.

**AC2.3: The UI reflects the updated `is_promoted` status immediately without a refresh**
- **Status: Met**
- **Evidence:** The HTMX response replaces the table rows via `hx-target="#rejected-table-body"`. Conformance test `test_htmx_post_reflects_promoted_state_in_rows` promotes via plain POST first, then verifies the rows partial reflects `is_promoted=True` state.

---

### Phase 3: Contextual Links & Styling

**AC3.1: Clicking the Alert ID opens the corresponding Alert detail page**
- **Status: Met**
- **Evidence:** `ui/templates/ui/rejected_observables.html:76-78` has `<a href="/alerts/{{ item.alert.id|default:'' }}">`. While this uses a hardcoded URL rather than `{% url %}` tag, it functionally opens the alert detail page. The conformance test `test_page_only_own_org_rows` implicitly verifies alert links are present/absent correctly.

**AC3.2: All labels and icons meet WCAG contrast requirements in dark mode**
- **Status: Not Met (requires human attention)**
- **Evidence:** The Tailwind CSS classes used (`bg-gray-900`, `text-white`, `text-indigo-400`, etc.) are consistent with the app's dark theme. However, a formal WCAG contrast audit has not been performed. Severity/text labels are paired per the design standards (ADR-004, AGENTS.md §5), but this requires visual verification.
- **Notes:** No static code inspection can verify contrast; this is a human QA step.

**AC3.3: Reason badges are color-coded based on the `RejectedReason`**
- **Status: Not Met**
- **Evidence:** `ui/templates/ui/rejected_observables.html:83-85` displays `{{ item.get_reason_display|default:"N/A" }}` as plain italic text with no color coding. Similarly, `ui/templates/ui/_rejected_observables_rows.html:23-25` shows the reason as plain text. The plan (Phase 3 task: "Ensure 'Reason' badges are color-coded based on the `RejectedReason`") requires color-coded badges, but the implementation uses plain text. The `REJECTED_REASON_CHOICES` are: `duplicate`, `irrelevant`, `false_positive`, `incomplete`, `too_noisy`, `other` — no mapping to badge colors exists in the templates or CSS.
- **Notes:** This is a visible UX gap; adding badge colors would require Tailwind color classes mapped to reason values.

---

### Security

**Tenant Isolation: All queries for `RejectedObservable` MUST go through `observables.rejected.scoped_rejected_observables()`**
- **Status: Met**
- **Evidence:** Both the API view `observables/views.py:rejected_observables_list` (line 425-429) and the UI view `ui/views/rejected_observables.py:rejected_observables_list` (line 37) use `scoped_rejected_observables(user, ...)`. The scoping function in `observables/rejected.py:21-54` applies `org_q("owner_org_id", user)` which filters to the user's organization only, or returns empty queryset for org-less users, and allows superAdmins through. Conformance tests verify this: `test_own_org_user_see_own_rejected_items`, `test_super_admin_see_cross_org_rejected_items`, and `test_viewer_denied_promotion` all pass.

**SuperAdmin Override: Ensure SuperAdmins can view all records across organizations**
- **Status: Met**
- **Evidence:** `identity/scoping.py:org_q` (line 114) returns `Q()` for superAdmins, meaning they see all rows. Conformance test `test_super_admin_see_cross_org_rejected_items` verifies superAdmins see rejected observables from all organizations.

---

## Deviations

| # | Area | Deviation | Recorded in Plan | Acceptable |
|---|------|-----------|------------------|------------|
| D1 | Phase 2 | Modal does not use `<dialog>` element as specified; HTMX OOB swap used instead. No `<dialog id="promote-modal-...">` in template; `data-modal-open`/`data-modal-close` attributes mismatched (ID suffix missing in close content). | Plan § "Phase 2: Promotion Action & Modal" — "Build the Confirmation Modal using the `<dialog>` element." | Partially acceptable — core promotion logic works; modal UX is a refinement, not a blocker. |
| D2 | Phase 3 | Reason badges are plain text, not color-coded as planned. | Plan § "Phase 3: Contextual Links & Styling" — "Ensure 'Reason' badges are color-coded based on the `RejectedReason`." | Optional follow-up — functional but reduces UX polish. |
| D3 | Phase 3 | Alert link uses hardcoded `href="/alerts/..."` instead of `{% url %}` tag. | Not explicitly recorded, but plan § "Phase 3" mentions adding links to Alerts. | Minor — functional, consistent with other hardcoded URLs in the template. |
| D4 | Security | API view `rejected_observables_list` lacks `@not_viewer` decorator present in UI view. API allows viewer-listed users to see their own org's items via `org_q`, while UI blocks them. | Not recorded — security consistency concern. | Optional — minor inconsistency; API scoping via `org_q` is sufficient, but UI-level denial of viewers is a defense-in-depth measure. |

---

## Open Steps

### Required before commit/merge
- **Fix the promotion modal** (D1): Either implement server-rendered `<dialog>` modals per the plan, or properly wire the HTMX OOB swap so the `data-modal-open`/`data-modal-close` IDs match and the modal `<dialog>` elements exist in the DOM. The current implementation has a broken modal UX.
- **Add color-coded reason badges** (D2): Map `REJECTED_REASON_CHOICES` to Tailwind badge colors (e.g., `duplicate` → gray, `false_positive` → red, `irrelevant` → yellow, `incomplete` → orange, `too_noisy` → amber, `other` → gray). This is a visible UX improvement.

### Optional follow-ups / future enhancements
- Replace hardcoded `href="/alerts/..."` with `{% url 'ui-alert-detail' alert_id=item.alert.id %}` for route conformance.
- Add `@not_viewer` to API view `rejected_observables_list` for defense-in-depth consistency (though `org_q` already provides scoping).
- Add WCAG contrast audit for dark mode.

### Documentation-only cleanup
- None identified.

---

## Quality and Security Concerns

1. **Modal UX (Critical):** The promotion confirmation modal is not functional. The `data-modal-open` attribute on buttons references modal IDs that don't exist in the DOM, and the OOB close content has mismatched IDs and uses `<div>` instead of `<dialog>`. This needs to be fixed before merge if the modal is a required UX pattern.

2. **Reason badge color-coding (Low):** Reasons are displayed as plain text with no visual distinction. This is a polish issue but reduces the usability of the rejection audit feature.

3. **Hardcoded alert URL (Low):** The alert link uses `href="/alerts/{{ item.alert.id|default:'' }}"` instead of Django URL routing. This works but is inconsistent with the rest of the codebase's URL routing patterns.

4. **API/viewer decorator inconsistency (Low):** The API `rejected_observables_list` lacks the `@not_viewer` decorator that the UI view has. Viewer users can still access the API endpoint and see their own org's items via `org_q`. This is not a security hole (org isolation works), but the UI correctly blocks viewers, creating a slight inconsistency.

5. **Test coverage:** All 20 conformance tests pass (9 API + 11 UI), providing good coverage of the core functionality. No tests currently verify the modal UX, which explains why the deviation went undetected.

6. **OOB close content unused variable:** `observables/views.py:rejected_observable_promote` passes `{"modal_id": f"promote-modal-{observable.id}"}` to the OOB template, but `_modal_close_oob.html` hardcodes `data-modal-close="promote-modal"` and does not use the `modal_id` context variable. This is a minor bug in the OOB swap logic.

---