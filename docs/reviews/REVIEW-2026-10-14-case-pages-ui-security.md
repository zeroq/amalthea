# Security Review: Case Pages UI (CRUD)

**Date:** 2026-10-14  
**Reviewer:** Security Auditor  
**Scope:** `ui/urls.py` routes `ui-case-page-create`, `ui-case-page-edit`, `ui-case-page-delete`; `ui/views.py` handlers `case_page_create`, `case_page_update`, `case_page_delete`; templates `_tab_general.html`, `_page_entry.html`, `_page_form.html`, `_page_delete_form.html`; Markdown rendering via `ui.markdown.render_markdown`; live updates via `live.js::refreshPages()`.

---

## Executive Summary

The Case Pages UI implementation demonstrates **strong security posture** with defense-in-depth across authorization, XSS prevention, CSRF protection, and tenant isolation. All critical and high-severity checks pass. Three **Medium** findings and several **Low/Info** items are documented for remediation or tracking.

---

## Findings

### Critical
*None.*

---

### High
*None.*

---

### Medium

#### M1: Race Condition in Page Order Assignment (`case_page_create`)
**Location:** `ui/views.py:929-935`  
**Code:**
```python
last = case.pages.aggregate(highest=Max("order"))["highest"]
page = Page.objects.create(
    case=case,
    title=title[:500],
    content=(request.POST.get("content") or "").strip(),
    category=(request.POST.get("category") or "").strip()[:100],
    order=0 if last is None else last + 1,
    created_by=request.user,
)
```
**Issue:** Concurrent `POST /cases/{id}/page` requests can read the same `Max("order")` and assign identical `order` values. The model has an index on `(case, order, created_at)` but **no unique constraint**. Two analysts creating pages simultaneously (or a scripted double-submit) will produce duplicate `order` values, causing non-deterministic list ordering.
**Impact:** UI list order becomes unstable; analysts may see pages "jump" on reload. No data integrity loss (pages remain distinct by PK), but UX degradation.
**Recommendation:** Add a `UniqueConstraint(fields=["case", "order"], name="uniq_page_case_order")` to the `Page` model (with a migration). In the view, catch `IntegrityError` and retry with a fresh `Max("order")` read, or use `SELECT FOR UPDATE` on the case's pages within a transaction. For MVP, the current behavior is *acceptable but fragile*; document as known limitation and fix in next iteration.

#### M2: No Request Body Size Limit on Markdown Content (DoS Risk)
**Location:** `ui/views.py:933` (`content=(request.POST.get("content") or "").strip()`), `ui/views.py:970` (`page.content = (request.POST.get("content") or "").strip()`)  
**Issue:** `Page.content` is a `TextField` with no `max_length`. An authenticated analyst can POST arbitrarily large Markdown bodies (megabytes). The `render_markdown` pipeline parses with `markdown-it-py` then sanitizes with `nh3` — both operate on the full string in memory. No per-request size limit is enforced at the view or middleware level (`DATA_UPLOAD_MAX_MEMORY_SIZE` defaults to 2.5 MB but can be exceeded by chunked uploads; the view reads the full POST body).
**Impact:** Authenticated DoS via memory/CPU exhaustion during parsing/sanitization. An attacker with analyst credentials could degrade the worker process.
**Recommendation:** Enforce a reasonable limit at the view layer (e.g., `MAX_PAGE_CONTENT = 100_000` chars ≈ 100 KB) and return a 400/error message before parsing. Add a `maxlength` attribute to the `<textarea>` in `_page_form.html` (already has client-side limit but server must enforce). Consider adding `max_length=100000` to the model field for defense-in-depth (requires migration).

#### M3: UI Write Operations Not Audit-Logged (R3 Gap)
**Location:** `ui/views.py:907-1001` (`case_page_create`, `case_page_update`, `case_page_delete`)  
**Issue:** The three mutating views create/update/delete `Page` records but **do not emit `AuditLog` entries**. The API equivalents (`POST|PATCH|DELETE /api/v1/case/{id}/page/{pageId}`) also lack audit logging (per codebase audit). Other UI mutations (`case_create`, `case_set_status`, `case_comment`, `case_task_create`) do audit via `AuditLog.objects.create(...)`.
**Impact:** Forensic gap — page creation, edits, and deletions are invisible in the Audit tab and `AuditLog` table. An insider could manipulate case documentation without trace.
**Recommendation:** Add `AuditLog` entries in each view, inside the same `transaction.atomic()` block (or `transaction.on_commit` for create/update to include the new PK). Use actions `page.created`, `page.updated`, `page.deleted` with `target_type="Page"`, `target_id=str(page.id)`. Include `metadata` with changed fields (for update) or deleted title (for delete). Align with existing pattern in `case_create` (lines 646-654).

---

### Low

#### L1: `case_page_update` Allows Clearing Content Without Confirmation
**Location:** `ui/views.py:970`  
**Code:** `page.content = (request.POST.get("content") or "").strip()`  
**Issue:** An empty submitted textarea is treated as intentional clearing of the page body. No "are you sure" guard exists in the UI (the modal shows current content; analyst can select-all → delete → save). This is consistent with the API's `PATCH` semantics but differs from `case_comment` which rejects empty body (line 843-850).
**Impact:** Accidental data loss if an analyst inadvertently clears the textarea and submits.
**Recommendation:** Add a client-side confirmation in `modal.js` when the content textarea is emptied, or a server-side check that warns if `content` went from non-empty to empty (soft warning via `messages.warning`, still allow). Low priority — analyst intent is presumed.

#### L2: `case_page_delete` Idempotency Returns 404 on Double-POST (Acceptable)
**Location:** `ui/views.py:994-1001`  
**Code:**
```python
page = get_object_or_404(Page, pk=page_id, case=case)
title = page.title
page.delete()
messages.success(request, f"Deleted page '{title}'.")
```
**Issue:** A second POST for the same `page_id` returns 404 (page no longer exists). The success message from the first POST is lost on redirect (messages framework consumes on display). Not a vulnerability — standard RESTful delete semantics.
**Recommendation:** Document as expected behavior. If UX requires idempotent "delete" (return 200 with "already deleted"), change to `Page.objects.filter(pk=page_id, case=case).delete()` and check row count. Not required for MVP.

#### L3: No Rate Limiting on Page Mutations
**Location:** `ui/views.py:907-1001`  
**Issue:** No per-user or per-case rate limit on `case_page_create`, `case_page_update`, `case_page_delete`. An analyst (or compromised session) could spam page creates/updates, bloating the `page` table and triggering many WebSocket `page` frames (each create/update publishes via `publish_case_event`).
**Impact:** Resource exhaustion (DB rows, WebSocket traffic). Low likelihood — requires authenticated analyst.
**Recommendation:** Apply the existing `identity.ratelimit` pattern (used on login) to mutating UI views, or add a lightweight decorator `@ratelimit(key="user", rate="30/m")` on the three views. Defer to post-MVP hardening sprint.

---

### Info

#### I1: Markdown Sanitizer Is the Single Trusted XSS Boundary (ADR-005) — Verified
**Location:** `ui/markdown.py:57-84`, `ui/templatetags/ui_tags.py:79-94`  
**Observation:** `render_markdown` uses `markdown-it-py` with `html=False` (raw HTML escaped) + `nh3.clean()` with strict tag/attribute/URL-scheme allow-lists. On any exception, fails closed to `escape(text)`. The template filter marks the sanitized output `SafeString` — correct and necessary. No `|safe` filters on user content anywhere in the page templates. **Pass.**

#### I2: Live Updates Use DOMParser + replaceWith, No `innerHTML` — Verified
**Location:** `ui/static/ui/live.js:141-163` (`refreshPages()`)  
**Observation:** `fetch()` → `response.text()` → `DOMParser().parseFromString(html, "text/html")` → `getElementById("case-pages")` → `current.replaceWith(fresh)`. No script execution, no event handler attachment, same-origin fetch with credentials. **Pass.**

#### I3: CSRF Tokens Present on All Modal Forms — Verified
**Location:** `ui/templates/ui/_modal.html:60` (`{% csrf_token %}`)  
**Observation:** The modal footer renders a real `<form>` with `{% csrf_token %}` when `form_action` is provided (which it is for all three page modals). The form is submitted via native browser POST (HTMX not used for these modals). **Pass.**

#### I4: Authorization Stack Correct — Verified
**Location:** `ui/views.py:904-906`, `945-947`, `981-983`  
**Observation:** All three views decorated with `@not_viewer` (outermost), `@login_required`, `@require_POST`. `resolve_case(..., write=True)` enforces org ownership + share write permission + viewer denial. `get_object_or_404(Page, pk=page_id, case=case)` ensures page belongs to the resolved case. Uniform 404 on all denial paths (viewer, cross-tenant, non-existent). **Pass.**

#### I5: CSP Unchanged — Verified
**Observation:** No inline scripts/styles added. Modal behavior in external `modal.js`. Live updates use same-origin `fetch`. `script-src 'self'` remains satisfied. **Pass.**

#### I6: Enumeration Resistance — Verified
**Observation:** `resolve_case` returns `None` for viewer, org-less, cross-tenant without write share, and non-existent — all become 404 via `if case is None: return render(request, "ui/404.html", status=404)`. No timing or shape difference. **Pass.**

#### I7: Focus Ring Uses CSS Variable (`--color-accent`) — Informational
**Observation:** Modal focus styles reference `--color-accent` (defined in Tailwind config). No hardcoded colors. **Pass.**

---

## Summary Table

| ID | Severity | Component | Status |
|----|----------|-----------|--------|
| M1 | Medium | Order race condition | Open — add unique constraint + retry logic |
| M2 | Medium | DoS via large Markdown | Open — enforce server-side size limit |
| M3 | Medium | Missing audit logs | Open — add AuditLog entries |
| L1 | Low | Accidental content clear | Open — consider confirmation |
| L2 | Low | Delete idempotency | Acceptable — document |
| L3 | Low | No rate limiting | Deferred — post-MVP |
| I1–I7 | Info | Various verified controls | Pass |

---

## Recommended Remediation Order

1. **M3 (Audit logging)** — Forensic completeness; aligns with existing patterns; low effort.
2. **M2 (Content size limit)** — DoS mitigation; add view-layer check + model `max_length`.
3. **M1 (Order race)** — Add unique constraint + migration; handle `IntegrityError` in view.
4. **L1 (Clear confirmation)** — Client-side guard in `modal.js`.
5. **L3 (Rate limiting)** — Apply `@ratelimit` decorator post-MVP.

---

## Compliance with Security Checklist

| Check | Requirement | Result |
|-------|-------------|--------|
| S1 | Authorization: `@not_viewer` + `resolve_case(write=True)` + `get_object_or_404(Page, case=case)` | ✅ Pass |
| S2 | XSS: Markdown sanitized; no `|safe`; live updates parse DOM; form prefill escaped | ✅ Pass |
| S3 | CSRF: Modal footer forms include `{% csrf_token %}` | ✅ Pass |
| S4 | CSP: Unchanged (no inline scripts, same-origin fetch) | ✅ Pass |
| S5 | Enumeration: Uniform 404s (viewer POST → 404, foreign/cross-tenant → 404) | ✅ Pass |
| S6 | Audit: UI writes not AuditLogged | ❌ **Fail** (M3) |

---

## Additional Notes

- **Mass-assignment:** Views use explicit field assignment (`title`, `content`, `category`, `order` on create; `title`, `content`, `category`, `updated_by` on update with `update_fields`). No mass-assignment risk.
- **Input validation:** Title required (non-empty), truncated to 500 chars; category truncated to 100 chars; content unbounded (M2).
- **Tenant isolation:** `resolve_case` enforces org boundary; `Page` FK to `Case` cascades; no cross-tenant page access possible.
- **Secrets:** No secrets in templates, views, or static assets.
- **Dependencies:** `markdown-it-py`, `nh3` are pinned in `requirements.txt` (verify via `pip-audit` in CI).

---

*End of Report*
