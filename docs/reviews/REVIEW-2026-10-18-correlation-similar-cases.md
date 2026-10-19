# Security Review: Correlation & Similar Cases (PLAN-2026-10-18)

**Date:** 2026-10-19  
**Reviewer:** Security Auditor  
**Scope:** `cases/correlation.py`, `ui/views.py` (case_detail, alert_detail, alert_escalate, alert_merge), `ui/templates/ui/_tab_similar.html`, `ui/templates/ui/alert_detail.html`, `ui/static/ui/app.css`, `tests/conformance/test_correlation_similar_cases.py`  
**Plan:** `docs/planning/PLAN-2026-10-18-correlation-similar-cases.md` (approved)  
**Architecture:** Django 5.x + DRF + Channels, PostgreSQL, multi-tenant (ADR-004), roles viewer < analyst < admin (ADR-006)

---

## Revision Summary

This wave introduces two new capabilities sharing one primitive (`cases/correlation.py`):

1. **Module C (P1):** "Similar cases" tab — ranks visible cases in the same org by shared-observable overlap, renders the shared artifacts as explanation chips, with honest truncation (`similar_total` vs displayed count) and two distinct empty states.

2. **Module B (P2):** Correlation-ranked, audited alert→case attach — upgrades the existing "Merge into existing case" form and the escalate path to rank candidates by correlation, and writes exactly one `TimelineEvent(kind="correlation-bundle")` + one `AuditLog(action="case.alert_bundled")` per attach, with typed refusals for closed cases, cross-org, and viewer actors.

No new models, no migrations, no new API routes. The plan's security requirements S1–S5 are the evaluation criteria.

---

## Findings

### M — Material (Must Fix)

#### M1: `alert_escalate` lacks transaction atomicity across link + bundle recording

**Location:** `ui/views.py:494-514` (`alert_escalate`)

**Code:**
```python
case = import_alert_to_case(...)  # @transaction.atomic — commits here
record_alert_bundled(...)  # runs OUTSIDE any transaction
```

**Attack path / failure mode:**  
`import_alert_to_case` is decorated with `@transaction.atomic` and commits its own transaction (creates case, links alert, extracts observables, writes `alert-imported` ledger entry). `record_alert_bundled` is then called in the same `try` block but **without** a wrapping `transaction.atomic()`. If `record_alert_bundled` raises (e.g., DB deadlock, constraint violation, PermissionDenied from idempotence check on a race), the alert is already linked to the new case but **no `correlation-bundle` ledger entry and no `case.alert_bundled` audit row exist**. The system is left in an inconsistent state: the link exists but the audit trail says it doesn't. A retry is blocked because `import_alert_to_case` refuses an already-linked alert (`ValueError: alert ... is already attached`).

**Contrast:** `alert_merge` (lines 529-540) correctly wraps both calls in `with transaction.atomic():` — the outer block joins the inner `@transaction.atomic` of `merge_alert_into_case` via savepoint, so a failure in `record_alert_bundled` rolls back the link, the observables, and the `alert-merged` ledger entry together.

**Fix:** Wrap the `import_alert_to_case` + `record_alert_bundled` sequence in `with transaction.atomic():` in `alert_escalate`, mirroring `alert_merge`.

---

#### M2: No bound on intermediate query results in correlation joins

**Location:** `cases/correlation.py:104` (`obs_ids = list(matching_observables)`), `:117-123` (`case_obs` query), `:250` (`matching_obs_ids`), `:264-270` (`case_obs` query)

**Issue:** Both `cases_sharing_findings` and `candidate_cases_for_alert` accept a `limit` parameter (default `RAIL_CAP`/`PICKER_PAGE_SIZE` = 200) but apply it **only to the final Python list slice** (`similar_cases[:limit]`). The preceding database queries fetch **all** matching observable IDs and **all** `CaseObservable` rows for those IDs in visible cases.

**Attack path:** An attacker with a valid ingestion token sends a webhook payload containing many distinct observable values (e.g., 10,000 unique IPs, domains, hashes) that all match existing observables in the org. The alert is stored. When an analyst opens the alert detail page, `candidate_cases_for_alert` runs:
1. `find_observables` extracts all 10,000 findings
2. `matching_obs_ids` fetches 10,000 observable PKs
3. `case_obs` query joins `CaseObservable` for all 10,000 observables across all visible cases
4. Python groups 10,000+ rows, sorts, then slices to 200

This consumes unbounded memory and CPU in a **web request** (GET `alert_detail`). The same applies to `cases_sharing_findings` when the Similar tab is rendered for a case with many observables.

**Mitigating factors:** Ingest path caps payload at 5 MiB and depth 30 (`WEBHOOK_MAX_BODY_SIZE`, `WEBHOOK_MAX_DEPTH`), limiting the number of extractable findings. But 5 MiB can still hold thousands of small observables. The `find_observables` deduplication by `(type_name, normalized)` helps but doesn't bound distinct values.

**Fix:** Apply a hard cap (e.g., `MAX_CORRELATION_OBS = 1000`) on `obs_ids` / `matching_obs_ids` before the `CaseObservable` join, or push the limit into the query via a subquery/window function. At minimum, `list(matching_observables[:MAX_CORRELATION_OBS])` and document the cap.

---

#### M3: `candidate_cases_for_alert` re-processes `alert.raw_payload` without re-applying ingest bounds

**Location:** `cases/correlation.py:193-200` (`json_leaves(alert.raw_payload)`), `observables/extractor.py:226-253` (`_walk` recursive), `:256-295` (`find_observables` quadratic `consumed` check)

**Issue:** The ingest path (`ingest/views.py`) enforces `WEBHOOK_MAX_BODY_SIZE` (5 MiB) and `WEBHOOK_MAX_DEPTH` (30) **before** storing the payload. The correlation path (`candidate_cases_for_alert`) re-reads `alert.raw_payload` and passes it to `json_leaves` → `find_observables` **without re-checking size/depth**. While the stored payload should already be within bounds, three gaps exist:

1. **Recursion depth:** `json_leaves` uses a **recursive** `_walk` function. Python's recursion limit (~1000) is far above the ingest depth limit (30), but an admin could raise `WEBHOOK_MAX_DEPTH` post-ingest, or a superAdmin could manually create an alert with a deeper payload via Django admin/shell. A depth > 1000 causes `RecursionError` (500) on the alert detail page.

2. **Quadratic match checking:** `find_observables` tracks `consumed` spans and for each new match checks `any(not (end <= s or start >= e) for s, e in consumed)` — O(matches²). A crafted payload with many non-overlapping matches (e.g., 5000 distinct IPs) causes ~25M span comparisons in the alert detail GET request.

3. **No size guard on re-read:** If `alert.raw_payload` were somehow inflated (direct DB write, migration bug), `json_leaves` materialises the entire flattened string in memory.

**Plan requirement S5:** "the dry-run path must keep the existing per-payload bounds (size/depth limits from the ingest path) and must not raise on malformed input."

**Fix:** In `candidate_cases_for_alert`, before calling `json_leaves`:
- Check `len(json.dumps(alert.raw_payload))` against `settings.WEBHOOK_MAX_BODY_SIZE` (or a correlation-specific cap)
- Check depth with the existing iterative `_depth_exceeds` (copy from `ingest/views.py`)
- Wrap `find_observables` in a try/except that catches `RecursionError` and `MemoryError` (already has bare `except Exception` but `RecursionError` derives from `BaseException`, not `Exception` — **this is a bug**: `RecursionError` would escape and 500 the page)

---

#### M4: `cases_sharing_findings` excludes shared cases due to contradictory scoping

**Location:** `cases/correlation.py:110` (`base_qs = Case.objects.filter(case_visible_q(user)).filter(owner_org_id=user_org_id)`)

**Issue:** `case_visible_q(user)` returns `Q(owner_org_id=org_id) | Q(shares__organisation_id=org_id)` — i.e., own-org cases **plus** cases shared with the user's org. The subsequent `.filter(owner_org_id=user_org_id)` then **strips out the shared cases**, because their `owner_org_id` is the *other* org.

**Result:** An analyst in Org A who has a case from Org B shared with them will **not** see that shared case in the Similar tab, and `similar_total` will not count it. This contradicts ADR-004 §30: "`Share` remains the only sanctioned cross-tenant read" and the plan's own S1: "Both the rows and the `(N)` are computed AFTER `case_visible_q(user)` + same-org scoping" — the code applies `case_visible_q` **then** narrows to same-org, which is not what the plan describes.

**Note:** The plan §4 line 87-88 says "Scope: candidates restricted to `case_visible_q(user)` AND same `owner_org` before counting". This is ambiguous: it could mean (a) the intersection of visible cases AND same-org cases (current code, excludes shares), or (b) the visible cases (which already includes same-org + shares). Given ADR-004's sharing model, (b) is the intended reading. The test `test_cross_org_no_visibility` only tests cross-org **without** shares; no test covers shared-case visibility in correlation.

**Severity:** Material if shares are meant to extend to correlation (per ADR-004). If the plan explicitly intends correlation to be same-org-only, this is a **plan deviation** that should be documented (not a code bug). Either way, the contradiction between `case_visible_q` and the extra filter must be resolved.

---

### L — Low (Accepted with Reason)

#### L1: `PermissionDenied` messages in `record_alert_bundled` are caught and shown as flash messages, not 404

**Location:** `ui/views.py:507-512` (`alert_escalate`), `:535-539` (`alert_merge`)

**Assessment:** The views catch `PermissionDenied` from `record_alert_bundled` and show `messages.error(request, str(exc))` + redirect. However, the **only** `PermissionDenied` cases that reach this code are:
- Closed case (user has read access to the case, so "Cannot bundle alert into a closed case" is a legitimate business-rule message, not an existence leak)
- Duplicate bundle (alert already linked to this case; user has access to both)
- Cross-org and viewer are **already blocked earlier** by `resolve_alert`/`resolve_case(..., write=True)` returning `None` → `Http404()`.

No existence oracle is introduced. Accepted.

---

#### L2: `correlation_key` namespace→type mapping falls back to raw namespace

**Location:** `cases/correlation.py:204-230`

**Assessment:** If `alert.correlation_key` has a namespace not in `type_map` (e.g., `custom_type:value`), the code uses `ns` as `obs_type_name`. This synthetic finding will then fail to match any observables because the `Observable` query filters by `data_type__name=obs_type_name`, and `ObservableType` likely has no such type. Result: graceful degradation to plain open-case fallback. No security impact. Accepted.

---

### O — Observations / Open

#### O1: Idempotence check uses ledger entry, not `Alert.case` (Deviation D7 — Open)

**Location:** `cases/correlation.py:350-356`

**Plan §4.1:** "Alert.case is the marker; an already-attached alert is refused, never re-linked"

**Implementation:** Checks `TimelineEvent.objects.filter(case=case, kind="correlation-bundle", metadata__alert_id=str(alert.id)).exists()`

**Gap:** The `import_alert_to_case` and `merge_alert_into_case` functions create `TimelineEvent` with `kind="alert-imported"` and `"alert-merged"` respectively — **not** `"correlation-bundle"`. The first call to `record_alert_bundled` after either will **not** find an existing `correlation-bundle` event and will create one. A second call (race, double-submit) will find it and refuse. This works for the attach path, but:
- If `record_alert_bundled` is called directly on an alert that was linked via `import_alert_to_case` but never had `record_alert_bundled` called (e.g., legacy data, or a bug where the view skipped it), the first call succeeds (no double-write).
- If `Alert.case` is set but the `correlation-bundle` ledger entry is missing (M1 scenario), a subsequent `record_alert_bundled` call would succeed, creating a ledger entry for a link that already exists — not a double-link, but a delayed audit record.

**Verification required:** Confirm this matches the intended idempotence semantics. The plan's "Alert.case is the marker" would be simpler and more robust.

---

#### O2: No test for shared-case visibility in correlation

**Location:** `tests/conformance/test_correlation_similar_cases.py::TestCrossOrgVisibility`

**Issue:** The test `test_cross_org_no_visibility` creates two orgs with the same observable value but **no share**. It verifies neither org sees the other's case. It does **not** test the case where Org B's case is explicitly shared with Org A. Given M4's ambiguity, a test is needed to lock in the expected behaviour (include shared cases or exclude them).

---

#### O3: Inconsistent transaction structure between `alert_escalate` and `alert_merge`

**Location:** `ui/views.py:494-514` vs `:529-540`

**Issue:** `alert_merge` wraps both `merge_alert_into_case` + `record_alert_bundled` in `with transaction.atomic():`. `alert_escalate` does not (see M1). Both should follow the same pattern for consistency and audit integrity.

---

#### O4: `find_observables` bare `except Exception` misses `RecursionError`

**Location:** `cases/correlation.py:198-200`

```python
try:
    findings = find_observables(*texts, types=vocabulary)
except Exception:
    findings = []
```

**Issue:** `RecursionError` derives from `BaseException`, not `Exception`. A payload that triggers recursion depth exceeded in `json_leaves._walk` would crash the alert detail page with 500 instead of degrading to plain open cases. This is a pre-existing `find_observables` issue but exposed on a new read path.

---

#### O5: `similar_total` count computed only when `active_tab == "similar"`

**Location:** `ui/views.py:1004-1022` (`case_detail`)

**Assessment:** Correct per AC-P1.2 ("no extra query when the tab is not rendered"). Not a security issue. The count is not exposed elsewhere, so no oracle risk.

---

## Verified Safe (S1–S5)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| **S1:** Rows + `(N)` after `case_visible_q` + same-org scoping | ✅ (with M4 caveat) | `cases_sharing_findings:110`, `candidate_cases_for_alert:188-190` both filter by `owner_org_id=user_org_id` after `case_visible_q`. Observables queried with `owner_org_id=user_org_id` (lines 90, 251). |
| **S2:** Cross-org test proves no visibility, not even count | ✅ | `test_cross_org_no_visibility` asserts `total1 == 0` and `total2 == 0` for cross-org same-value observables. No test for shared cases (O2). |
| **S3:** Refusals typed; uniform 404 for out-of-scope; transaction atomicity | ⚠️ Partial | Typed refusals ✅ (`PermissionDenied` with distinct messages). Uniform 404 for out-of-scope ids ✅ (`resolve_case`/`resolve_alert` return `None` → `Http404`). Transaction atomicity ❌ for `alert_escalate` (M1), ✅ for `alert_merge`. |
| **S4:** Audit `details` carries no secret, no raw payload | ✅ | `record_alert_bundled:359-367` — only `alert_id`, `alert_source`, `alert_type`, `alert_source_ref`, `correlation_key`, `reason`, `source`. No `raw_payload`, no secrets. `actor_org`/`target_org` correct. |
| **S5:** Dry-run keeps ingest bounds, no raise on malformed input | ❌ | Bounds not re-applied (M3). `RecursionError` not caught (O4). Quadratic `consumed` check unbounded (M3). |

---

## Template / Rendering Safety

| Check | Result |
|-------|--------|
| Shared observable values escaped in `_tab_similar.html` | ✅ `{{ data_type }}: {{ value }}` — Django auto-escape in text and `title` attribute |
| Shared values escaped in `alert_detail.html` merge dropdown | ✅ Inside `<option>` — Django auto-escape |
| No `|safe` filter on attacker-controlled strings | ✅ None found in new/changed templates |
| No inline script/style in new CSS | ✅ `app.css` additions use only theme tokens (`--color-bg-raised`, `--color-line`, `--radius-sm`, `--font-mono`) |

---

## Verdict

**CONDITIONAL PASS — M1, M2, M3, M4 must be fixed before merge.**

- **M1** breaks audit integrity on the escalate path (link without audit trail, unrecoverable).
- **M2** allows unbounded intermediate query results in web requests (DoS).
- **M3** violates plan S5: ingest bounds not re-applied; `RecursionError` escapes.
- **M4** either violates ADR-004 sharing model or the plan's scoping description — must be resolved (code or plan).

L1, L2 accepted. O1–O5 are open items for verification/follow-up.

---

## Items Requiring Runtime Verification (Cannot Assess Statically)

1. **Query plan / index usage** — Plan T-DB1 requires `db` agent to confirm `cases_sharing_findings` and `candidate_cases_for_alert` use `caseobs_obs_case_idx` and `alert_corrkey_date_idx`. No `EXPLAIN ANALYZE` evidence in tree.

2. **Actual payload size/depth at ingest** — The 5 MiB / 30 depth defaults are in `settings/base.py` but could be overridden in deployment. The correlation path should defensively re-apply bounds regardless.

3. **WebSocket / real-time propagation** — The Similar tab data is server-rendered; no WebSocket event for "similar cases changed". Not in scope for this wave but worth noting for future.

4. **Rate limiting on `alert_detail`** — The correlation computation runs on every GET to `alert_detail`. No per-user/IP rate limit on this view. An attacker with a valid session could hammer the endpoint with alerts containing large payloads.

---

*End of Report*
