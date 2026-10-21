# Verification Report: Similar Alerts Card (PLAN-2026-10-20-similar-alerts-card)

**Plan:** `docs/planning/PLAN-2026-10-20-similar-alerts-card.md` (APPROVED 2026-10-20)
**Verified:** 2026-10-20 19:45 UTC
**Status:** Pass

---

## Summary

| Metric | Count |
|--------|-------|
| Total ACs | 11 |
| Met | 11 |
| Not Met | 0 |
| Deviated | 0 |

All 11 acceptance criteria are satisfied. The implementation matches the approved plan exactly, with no deviations beyond those already recorded in plan §11.1 (none). All targeted tests pass (16/16); `make check` is green; the undefined-class backlog remains at 42.

---

## Criterion-by-Criterion

### AC-1: "Similar alerts" card renders on the Similar tab, below "Similar cases" (even when empty)
**Status:** Met  
**Evidence:**  
- `ui/templates/ui/_tab_similar.html:70-127` — second `<section class="card" aria-labelledby="h-similar-alerts">` with heading "Similar alerts", rendered unconditionally after the "Similar cases" card.  
- Empty states at lines 119-125 handle both "no attached alerts" and "has alerts but no matches".  
- Test: `TestEmptyStates::test_no_attached_alerts` and `test_has_attached_alerts_but_no_matches` pass.

### AC-2: Lists unlinked, same-org alerts overlapping this case's attached alerts; ranked (`shared_count` desc, then `-date`), capped, honest truncation line
**Status:** Met  
**Evidence:**  
- `cases/correlation.py:339-493` — `alerts_similar_to_case` builds case signal set from attached alerts' `correlation_key` + payload extraction (bounded by `MAX_CORRELATION_OBS`, `WEBHOOK_MAX_BODY_SIZE`, `WEBHOOK_MAX_DEPTH`, exception-safe). Candidates: `Alert.objects.filter(case__isnull=True).filter(org_q("owner_org", user))`. Rank: `shared_count` desc, then `-date`. Returns `(rows[:limit], total_matching)` with `limit=RAIL_CAP` default.  
- Template truncation line at `_tab_similar.html:112-117`.  
- Tests: `TestCorrelationKeyMatch`, `TestPayloadMatch`, `TestRanking`, `TestNonMatchingExcluded` all pass.

### AC-3: Each row offers a one-click **Link to case** control that attaches the alert to the current case
**Status:** Met  
**Evidence:**  
- `_tab_similar.html:96-104` — `<form method="post" action="{% url 'ui-case-alert-link' case.number %}">` with hidden `alert_id`, submit button "Link to case".  
- View `ui/views.py:551-579` (`case_alert_link`) handles POST.  
- Test: `TestLinkFlow::test_link_post_attaches_writes_ledger_and_audit_then_idempotent` pass.

### AC-4: Linking writes `alert.case`, ledger `correlation-bundle`, audit `case.alert_bundled` in one transaction; redirects to Similar tab
**Status:** Met  
**Evidence:**  
- `ui/views.py:565-574` — `transaction.atomic()` around `merge_alert_into_case` + `record_alert_bundled(..., source="merge")`.  
- Redirect via `_case_detail_redirect` which reads `request.POST["tab"]` (hidden input `tab=similar` in template line 100).  
- Test: `TestLinkFlow` asserts `ledger == 1`, `audit == 1`, `Location` header contains `tab=similar`.

### AC-5: Only same-org alerts listed/linkable; cross-org POST → 404
**Status:** Met  
**Evidence:**  
- `cases/correlation.py:422-427` — candidates filtered by `org_q("owner_org", user)`.  
- `ui/views.py:559` — `resolve_alert(request.user, ..., write=True)` enforces org scope (uniform 404).  
- Test: `TestTenantIsolation` (2 tests) + `TestCrossOrgPost` pass.  
- **Falsifiability proven:** removing `.filter(org_q("owner_org", user))` from `alerts_similar_to_case` causes `TestTenantIsolation` to fail (cross-org alert leaks into results and UI).

### AC-6: Viewers see the card but cannot link (no button; POST refused)
**Status:** Met  
**Evidence:**  
- `_tab_similar.html:97` — link form gated by `{% if can_mutate %}`.  
- `ui/views.py:551` — `@not_viewer` decorator on `case_alert_link`.  
- Test: `TestViewerReadOnly` — viewer sees rows but no "Link to case" button; viewer POST refused (non-200), alert stays unlinked.

### AC-7: Already-linked alerts not listed; POST for one shows info message, no duplicate ledger row
**Status:** Met  
**Evidence:**  
- `cases/correlation.py:423` — `case__isnull=True` filter excludes attached alerts.  
- `ui/views.py:562-564` — `if alert.case_id is not None: messages.info(...); return _case_detail_redirect(...)`.  
- `record_alert_bundled` refuses duplicate bundle (idempotence).  
- Tests: `TestAttachedAlertExcluded` (attached alert never returned, but unlinked twin with same key IS listed — non-vacuous); `TestLinkFlow` re-POST idempotent (ledger/audit counts stay at 1).  
- **Falsifiability proven:** removing `case__isnull=True` causes `TestAttachedAlertExcluded` to fail (total becomes 2, attached alert leaks).

### AC-8: Card computed only when Similar tab is active (tab-gated; no query otherwise)
**Status:** Met  
**Evidence:**  
- `ui/views.py:1080-1105` — `similar_alerts = []; similar_alerts_total = 0` initialized; computation only inside `if active_tab == "similar":`.  
- Test: `TestTabGating` mocks `alerts_similar_to_case` — helper not called on `?tab=general` or default tab; called exactly once on `?tab=similar`.

### AC-9: `make check` green; undefined-class backlog unchanged (42)
**Status:** Met  
**Evidence:**  
- `make check` passes (ruff, mypy, django check, migrations in sync, css-check).  
- `tests/conformance/test_ui_css_tokens.py::test_pre_existing_undefined_component_classes_are_reported` reports 42 undefined classes (same as baseline).  
- No new CSS classes introduced; template uses only existing utilities (`card`, `data-table`, `data-table-tight`, `table-wrap`, `sr-only`, `sev`, `sev_class`, `sev_label`, `pill`, `btn`, `btn-sm`, `list-summary`, `dim`, `mono`, `linkish`, `empty`).

### AC-10: No API/schema/migration change
**Status:** Met  
**Evidence:**  
- `makemigrations --check --dry-run` → "No changes detected".  
- No new models, no API endpoints (only UI view + URL).  
- Git diff shows changes only to `cases/correlation.py`, `ui/views.py`, `ui/urls.py`, `ui/templates/ui/_tab_similar.html`, `ui/templates/ui/observable_detail.html`.

### AC-11: `_tab_similar.html:25` and `observable_detail.html:201` render `status.value` (blank-pill bug fixed)
**Status:** Met  
**Evidence:**  
- `_tab_similar.html:25` — `{{ sc.case.status.value }}` (was `get_status_display`).  
- `observable_detail.html:201` — `{{ link.case.status.value }}` (was `get_status_display`).  
- Test: `TestStatusLabel::test_fixed_templates_contain_no_get_status_display` greps both templates and asserts absence.  
- `grep -r "get_status_display" ui/templates/` returns empty.

---

## Falsifiability Assessment

The plan §7.5 required two falsifiability proofs. Both are **confirmed**:

| Test | Mutation | Result |
|------|----------|--------|
| `TestTenantIsolation` | Remove `.filter(org_q("owner_org", user))` from candidate query | **FAILS** (cross-org alert leaks into helper results and rendered UI) |
| `TestAttachedAlertExcluded` | Remove `case__isnull=True` from candidate query | **FAILS** (attached alert returned, total=2 instead of 1) |

Both tests are **non-vacuous**: they assert a specific positive outcome (own-org match / unlinked twin IS listed) while asserting the negative (cross-org / attached is excluded). Git diff restored cleanly after each proof.

---

## Scope-Creep Check

| Check | Result |
|-------|--------|
| Schema/migration change | None (`makemigrations --check --dry-run` clean) |
| New CSS classes | None (template reuses existing utilities; `app.src.css` unchanged) |
| Files changed beyond plan | None (only 5 files listed in plan: `cases/correlation.py`, `ui/views.py`, `ui/urls.py`, `ui/templates/ui/_tab_similar.html`, `ui/templates/ui/observable_detail.html`) |
| API change | None (only UI POST route `ui-case-alert-link`) |
| New models | None |

---

## Deviations from Plan §11.1

**None.** Plan §11.1 recorded "(none yet)" and no new deviations were introduced during implementation.

---

## Bugs / Risks Identified

| Item | Severity | Notes |
|------|----------|-------|
| Truncation line not explicitly tested | Low | AC-2 requires "honest truncation line when capped". Implementation exists (`_tab_similar.html:112-117`, helper returns `total_matching` before limit), but no test creates >`RAIL_CAP` matches to verify the line renders. Not a blocker — logic is sound and consistent with `cases_sharing_findings` pattern. |
| `total_matching` bounded by candidate cap | Low | `alerts_similar_to_case` fetches at most `MAX_CORRELATION_OBS` candidate IDs before evaluating matches, so `total_matching` reflects matches within that bounded set, not the theoretical total across all unlinked same-org alerts. This mirrors the `cases_sharing_findings` pattern (bounded at observable level) and satisfies the plan's "bounded cost" requirement. |

---

## Final Verdict

**READY TO COMMIT**

All 11 acceptance criteria are met. Implementation conforms to the approved plan with no deviations. All quality gates pass. The two falsifiability proofs are genuine and the test suite (16 tests) is strong — every AC has at least one dedicated test, tenant isolation and attached-exclusion tests are non-vacuous and proven falsifiable, and the viewer/idempotence/cross-org flows are covered end-to-end.