# Security Audit: Case Template Defaults / Source-Alert Payload Feature

**Date:** 2026-10-22  
**Scope:** `cases/template_defaults.py`, `cases/views.py::_apply_template`, `ui/views.py` (`task_detail`, `task_source_payload`, `case_apply_template`, template create/edit), `ui/templates/ui/task_detail.html`, `_task_source_payload.html`, `_task_source_payload_loading.html`, `case_template_form.html`  
**Auditor:** Security Auditor (AppSec/IR focus)  
**Verdict:** **No blocking issues.** The feature is well-designed with defense-in-depth; all identified findings are Medium/Low/Informational.

---

## Executive Summary

The case-template defaults feature introduces a `{{ alert.raw_payload }}` token that template authors can place in task descriptions. When a template is applied to a case, the token is replaced with the earliest linked alert's payload (pretty-printed JSON, capped at 64 KiB in the task description). A Markdown sentinel is appended, enabling an opt-in "View full alert payload" modal (lazy-loaded via HTMX, capped at 8 MiB display). The implementation correctly enforces tenant isolation, prevents XSS, caps resource consumption, and maintains existing permission semantics.

---

## Findings by Severity

### Critical
**None.**

### High
**None.**

### Medium

#### M1: Missing Cross-Org Read Audit on UI Endpoints
**Files:** `ui/views.py:1913` (`task_detail`), `ui/views.py:1967` (`task_source_payload`)  
**Evidence:**  
- `task_detail` calls `resolve_task`/`resolve_alert` which allow superAdmin cross-org access (scoping.py:294-298, 238-242)  
- `task_source_payload` similarly allows superAdmin via `resolve_task`/`resolve_alert`  
- API endpoints (`cases/views.py:284-292`, `cases/views.py:477-485`) call `maybe_record_cross_org_read` for superAdmin reads  
- UI endpoints do **not** call `maybe_record_cross_org_read`  

**Impact:** SuperAdmin cross-org reads of task details and full alert payloads via the UI are not audit-logged, creating a blind spot in the audit trail.  
**Remediation:** Add `maybe_record_cross_org_read` calls in `task_detail` and `task_source_payload` when `case.owner_org_id != request.user.org_id` (mirror the pattern in `case_detail` at line 1080).

#### M2: Pretty-Print Memory Allocation Before Size Check
**File:** `cases/template_defaults.py:220-227` (`format_full_payload`)  
**Evidence:**  
```python
pretty_json = json.dumps(
    alert.raw_payload, ensure_ascii=False, indent=2, sort_keys=True, default=str
)
if len(pretty_json.encode("utf-8")) > MAX_FULL_PAYLOAD_BYTES:
    return compact_json  # fallback after allocation
```  
**Impact:** For a payload whose compact size is just under 8 MiB, pretty-printing with `indent=2` can temporarily allocate ~16-24 MiB in memory before the cap check triggers. In a constrained container or under concurrent load, this could contribute to OOM.  
**Remediation:** Estimate pretty size before allocation (e.g., `compact_len * 1.5` heuristic) and skip pretty-print if estimate exceeds cap, or stream the JSON serialization. Given typical alert payloads (< 1 MiB), this is a hardening measure, not an urgent fix.

### Low

#### L1: No Rate Limiting on `task_source_payload` Endpoint
**File:** `ui/views.py:1967-1994`  
**Evidence:** Endpoint has `@never_cache`, `@login_required`, `@require_GET` but no per-user/IP rate limit.  
**Impact:** An authenticated user could hammer the endpoint (HTMX or direct GET), causing repeated JSON serialization and template rendering.  
**Remediation:** Apply a lightweight rate limit (e.g., `django-ratelimit` or the existing `identity.ratelimit` pattern) — 30 req/min per user is sufficient for interactive use.

#### L2: Template Author Can Inject Arbitrary Markdown in Task Descriptions
**File:** `ui/templates/ui/case_template_form.html:85` (tasks textarea accepts raw JSON with Markdown)  
**Evidence:** Task descriptions are stored as Markdown and rendered via `|markdown` filter in templates (e.g., `task_detail.html:93`). A template author (analyst+) can embed arbitrary Markdown, including links, images, or HTML (if the Markdown renderer allows raw HTML).  
**Impact:** Stored XSS if the Markdown processor permits raw HTML or dangerous constructs (e.g., `javascript:` links). The current renderer (likely `markdown2` or similar) should be verified to run in safe mode.  
**Remediation:** Confirm the Markdown renderer is configured with `safe_mode`/`escape_html` enabled. If not, sanitize on render or restrict to a safe subset. Document that template authors are trusted (analyst+ role).

#### L3: Sentinel Spoofing Could Show "View Full Payload" Button Without Actual Payload
**File:** `cases/template_defaults.py:72-85` (`has_source_payload_sentinel`), `ui/views.py:1934-1938`  
**Evidence:** `has_source_payload_sentinel` checks for exact line match of `[//]: # (amalthea:source-alert-payload)`. A user with task write access could inject this line into a task description.  
**Impact:** The button appears, but `task_source_payload` independently verifies alert access via `resolve_alert` (line 1984). The modal would show "No alert is linked…" — no data leak, only a confusing UI element.  
**Remediation:** Acceptable as-is (defense-in-depth works). Optionally, verify the alert actually exists and has payload before showing the button (already done via `earliest_alert_for_case` check).

#### L4: Alert Title/Link Exposed in Modal to Users with Alert Access
**File:** `ui/templates/ui/_task_source_payload.html:9-13`  
**Evidence:** Modal renders `<a href="{{ payload_url }}">{{ alert.title }}</a>` with alert title and link.  
**Impact:** Only users who pass `resolve_alert` (same org or superAdmin) reach this modal, so they already have access to the alert detail page. No cross-org leakage.  
**Remediation:** None needed — confirmed correct.

#### L5: `case_apply_template` GET Does Not Enforce Write Access for Template List
**File:** `ui/views.py:2834-2840`  
**Evidence:** GET renders `CaseTemplate.objects.all().order_by("name")` — global list, not org-scoped.  
**Impact:** Templates are global (not org-owned), so listing all is by design. A viewer is blocked by `@not_viewer`. No data leak.  
**Remediation:** None — confirmed by design (templates are global vocabulary).

---

## Informational / Hardening

#### I1: Dynamic Fence Construction Is Correct but Complex
**File:** `cases/template_defaults.py:101-115` (`_build_dynamic_fence`)  
**Assessment:** The fence-finding regex `r"\`+"` correctly identifies the longest backtick run and adds one. Minimum fence length 3 ensures valid Markdown. No fence-breaking possible. Well done.

#### I2: UTF-8 Safe Truncation
**File:** `cases/template_defaults.py:88-98` (`_truncate_utf8_bytes`)  
**Assessment:** Truncates at byte boundary, then decodes with `errors="ignore"` to drop partial multi-byte characters. Correct and safe.

#### I3: Sentinel as Markdown Link-Reference Definition
**File:** `cases/template_defaults.py:22`  
**Assessment:** `[//]: # (amalthea:source-alert-payload)` is a valid Markdown link-reference that renders invisibly. The blank-line requirement (line 186-187) prevents it from being absorbed into a paragraph. Robust design.

#### I4: `earliest_alert_for_case` Correctly Scopes to Case's Org
**File:** `cases/template_defaults.py:66`  
**Assessment:** `.filter(owner_org_id=case.owner_org_id)` ensures alerts from other orgs (even if mistakenly linked) are ignored. Correct.

#### I5: Task Write Access Required for Template Application
**File:** `ui/views.py:2831` (`resolve_case(..., write=True)`), `cases/views.py:2158` (`_case_access(..., write=True)`)  
**Assessment:** Both UI and API require case write access to apply a template. Viewers blocked by `@not_viewer`. Correct.

#### I6: SuperAdmin Path Audited at API Layer Only
**Files:** `cases/views.py:284-292`, `cases/views.py:477-485` call `maybe_record_cross_org_read`  
**Assessment:** API endpoints audit superAdmin cross-org reads; UI endpoints do not (see M1). Consistent with historical pattern but a gap for UI-initiated superAdmin actions.

---

## Permission Matrix Verification

| Action | Owner Org | Share Org (read) | Share Org (write) | Viewer | SuperAdmin |
|--------|-----------|------------------|-------------------|--------|------------|
| View task detail (64 KiB excerpt) | ✓ | ✓ | ✓ | ✓ | ✓ |
| View full payload modal | ✓ | ✗ (alert no shares) | ✗ | ✗ | ✓ (audited*) |
| Apply template | ✓ | ✗ | ✓ | ✗ | ✓ |
| Create/edit template | analyst+ | N/A (global) | N/A | ✗ | ✓ |

*SuperAdmin cross-org reads via UI not audit-logged (M1).

---

## Compliance with Security Requirements (AGENTS.md §5)

- **No hardcoded secrets:** Verified — no secrets in reviewed files.
- **Environment variables for config:** Verified — constants are code, not config.
- **Secret scan enforced:** Pre-commit hook and CI scan cover new files.
- **Prod settings fail closed:** Unrelated to this feature.

---

## Recommendations Priority

1. **Fix M1** (add audit logging for superAdmin cross-org UI reads) — low effort, closes audit gap.
2. **Address M2** (pretty-print memory) — hardening, can be batched with other JSON safety work.
3. **Add rate limit L1** — standard hygiene for lazy-load endpoints.
4. **Verify Markdown renderer safety (L2)** — confirm `markdown2`/`mistune` safe mode; document trust boundary.

---

## Follow-up Remediation (2026-10-22)

- **M1 — remediated.** `ui/views.py::task_detail` and `ui/views.py::task_source_payload` now call
  `maybe_record_cross_org_read` with `target_type="Task"`, matching the API task-detail view.
  Regression coverage: `test_superadmin_task_reads_write_cross_org_audit_log` (detail + fragment,
  and no audit rows for same-org reads).
- **M2 — remediated.** `format_full_payload` skips pretty-printing when a conservative
  `compact_bytes * 2` estimate exceeds the display cap, returning the complete compact JSON instead
  of allocating a pretty string that would be discarded. Regression coverage:
  `test_format_full_payload_skips_pretty_when_estimate_exceeds_cap`.
- **L1 — accepted for MVP.** The fragment remains authenticated, GET-only, `never_cache`, and
  8 MiB-capped; per-user rate limiting is left as deployment/edge-control work.
- **L2 — verified.** `ui/markdown.py` parses with `MarkdownIt("commonmark", {"html": False})` and
  sanitizes with `nh3` against a strict allow-list (`p`, `pre`, `code`, `a`, headings, lists, etc.)
  with only `href`/`title`, `http`/`https`/`mailto` schemes, and `rel="noopener noreferrer"`.
  No `safe_mode` change is needed; template authors are trusted analyst+ users and output is still
  sanitized.
- **L3/L4/L5 — accepted.** No data leak; the permission matrix is unchanged.

---

## Conclusion

The case-template defaults / source-alert payload feature is **secure by design**:

- Tenant isolation is correctly enforced at both the case level (shares) and alert level (no shares, direct org ownership).
- The sentinel mechanism enables opt-in full-payload access without leaking data to case sharees.
- XSS is prevented by treating payloads as plain strings, using dynamic fences, and relying on Django template auto-escaping.
- DoS is mitigated by 64 KiB / 8 MiB caps, UTF-8-safe truncation, and no-cache headers.
- Auth/CSRF/HTTP methods are correctly handled.
- No regression to existing case/task permissions.

**Approval:** This feature is ready for merge. M1 and M2 were remediated in the follow-up above;
L1 remains a deployment/edge-control recommendation.

---

**Audit artifacts:** This document (`docs/security/AUDIT-2026-10-22-case-template-defaults.md`).
