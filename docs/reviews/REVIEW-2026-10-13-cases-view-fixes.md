# Security Review: Cases View Fixes (Trailing-Slash Middleware + New-Case Modal)

**Date:** 2026-10-13  
**Scope:** `core/middleware.py::StripTrailingSlashMiddleware`, `amalthea/settings/base.py` (middleware registration), `ui/views.py` (case create + list render helpers), `ui/templates/ui/case_list.html`, `ui/templates/ui/_case_create_fields.html`, `ui/templates/ui/case_create.html`, `ui/templates/ui/_modal.html`, `ui/static/ui/modal.js`, `ui/static/ui/keys.js`  
**Plan:** `docs/planning/PLAN-2026-10-13-cases-view-fixes.md`  
**Django check --deploy:** 6 warnings (all pre-existing production hardening flags: HSTS, SSL redirect, SECRET_KEY, secure cookies, DEBUG) — **no new issues introduced by this change**.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 0 |
| Low      | 2 |
| Info     | 3 |

**No critical or high-severity findings.** The implementation is sound, with defence-in-depth controls (resolve() guard, excluded prefixes, CSP, CSRF, viewer gate) correctly applied. Two low-severity hardening opportunities and three informational notes are documented below.

---

## Findings

### ID: LOW-01 | Severity: Low | Location: `core/middleware.py:75-76`
**Description:** Query string preserved verbatim in redirect `Location` header without validation for CRLF or header-injection sequences.  
**Impact:** Theoretical HTTP response splitting / header injection if `QUERY_STRING` contains `%0D%0A` (CRLF) or similar. Django's `HttpResponsePermanentRedirect` ultimately uses `HttpResponseRedirectBase` which delegates to `iri_to_uri`/`escape_leading_slashes` — these do **not** strip CRLF from the query component. A crafted request like `/cases/?foo=%0D%0ASet-Cookie:%20hacked` could inject a header into the 301/308 response.  
**Recommendation:** Sanitize the query string before concatenation, e.g. `query = request.META.get("QUERY_STRING", "").replace("\r", "").replace("\n", "")` or use `urlencode(parse_qsl(query))` to re-encode safely. The risk is low because (a) the middleware only redirects when `resolve(stripped)` succeeds, limiting the attack surface to existing UI routes, and (b) modern browsers and proxies reject responses with bare LF in headers. Still, defence-in-depth warrants sanitisation.

### ID: LOW-02 | Severity: Low | Location: `core/middleware.py:65`
**Description:** `path.rstrip("/")` strips **all** trailing slashes, not just one. A path like `/cases////` becomes `/cases`.  
**Impact:** Minimal. The subsequent `resolve(stripped)` will still succeed (Django's resolver normalises multiple slashes), and the redirect target is canonical. No open redirect or bypass is introduced. The behaviour is arguably a minor UX improvement (collapsing multiple slashes).  
**Recommendation:** Document the behaviour or change to `path[:-1]` if single-slash stripping is the strict intent. Not a security issue.

### ID: INFO-01 | Severity: Info | Location: `core/middleware.py:29`, `amalthea/settings/base.py:44-56`
**Description:** Middleware ordering places `StripTrailingSlashMiddleware` **after** `CommonMiddleware` but **before** `CsrfViewMiddleware`, `AuthenticationMiddleware`, and `SecurityMiddleware` (response phase).  
**Impact:** The redirect response (301/308) still passes through `CommonMiddleware` (response), `CSPMiddleware`, and `SecurityMiddleware` on the way out, so CSP and security headers (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, HSTS if enabled) are applied to the redirect. `CsrfViewMiddleware` and `AuthenticationMiddleware` do **not** run on the request (short-circuited by the middleware returning a response), which is correct — a redirect must not depend on auth/CSRF state.  
**Note:** This ordering is correct and intentional. No action needed.

### ID: INFO-02 | Severity: Info | Location: `ui/views.py:612-621`, `ui/templates/ui/_case_create_fields.html:18-20`
**Description:** The `origin=cases` hidden field is a server-chosen literal, used only to select the error re-render path (`_render_case_list` vs `_render_case_create_form`). It is never reflected into HTML outside the hidden input, never logged, and the `@not_viewer` gate (line 555) runs **before** any parsing.  
**Impact:** No request-forgery or state-confusion vector. A viewer cannot reach the POST handler (404 at decorator). An analyst cannot be tricked into a different error page because `origin` is not user-controllable beyond the binary "cases" / absent.  
**Note:** Clean implementation. No action needed.

### ID: INFO-03 | Severity: Info | Location: `ui/static/ui/modal.js:49-59`, `ui/templates/ui/case_list.html:14`
**Description:** Progressive enhancement: the "New case" button is a real `<a href="/cases/new" data-modal-open="new-case-modal">`. `modal.js` intercepts the click, calls `preventDefault()`, and opens the dialog **only if the modal element exists**. If not found (JS error, CSP block, template omission), execution falls through to `return` (line 59) and the browser follows the `href` — the standalone page loads.  
**Impact:** No click-hijack, no fail-closed UX. The fallback is the intended no-JS path.  
**Note:** Correct pattern. No action needed.

---

## Independently Cleared (Checked and Sound)

| Area | Check | Result |
|------|-------|--------|
| **Open redirect / SSRF via middleware** | `resolve(stripped)` guard ensures target is a registered Django view; `_EXCLUDED_PREFIXES` blocks `/api/`, `/admin/`, `/static/`, `/media/`, `/ws/`; `HttpResponsePermanentRedirect` uses Django's safe redirect logic (scheme allow-list, no `javascript:` or `//evil.com`). | **Clear** — an attacker cannot redirect to an arbitrary external domain. The worst case is a redirect to another internal UI route (which must resolve). |
| **Header injection via path** | `request.path_info` is already decoded and normalised by Django (CommonMiddleware runs before this middleware). Encoded slashes (`%2F`) are decoded to `/` before `path_info` reaches the middleware. `resolve()` receives the decoded path. | **Clear** — no bypass via double-encoding. |
| **308 re-POST bypassing CSRF** | The 308 redirect preserves method + body. The browser re-sends the request to `/cases/new` **with the same cookies and headers** (including `X-CSRFToken` header or `csrfmiddlewaretoken` in body). `CsrfViewMiddleware` runs on the **redirected request** (middleware order: it's after the strip middleware, so the second request hits it normally). | **Clear** — CSRF protection is intact. |
| **Cache poisoning via 301** | 301 is only used for safe methods (GET/HEAD). Unsafe methods get 308 (non-cacheable by default, method-preserving). No risk of a cached 301 turning a POST into a GET. | **Clear** — RFC 7231 / RFC 7538 compliant. |
| **`APPEND_SLASH` interaction** | `APPEND_SLASH` (in `CommonMiddleware`) only **appends** a slash when the slash-less path 404s. This middleware **strips** a slash when the slash-less path **resolves**. They are complementary mirrors; no conflict. The middleware runs after `CommonMiddleware`, so `APPEND_SLASH` has already done its work (or not) on the request path. | **Clear** — no redirect loops or double-redirects. |
| **`/ws/` Channels fallback** | `_EXCLUDED_PREFIXES` includes `/ws/`. Channels HTTP consumers (fallback) are mounted under `/ws/` via `realtime/routing.py`. The middleware will not rewrite them. | **Clear** — WebSocket HTTP fallback unaffected. |
| **XSS in new templates/JS** | `_case_create_fields.html` uses `{{ form_values.* }}` — Django auto-escapes. `form_id` is a server-chosen literal (`"new-case-form"` from the include context), never user input. `modal.js` and `keys.js` use zero `innerHTML`, `eval`, or unsafe sinks; all DOM manipulation is via `textContent`, `setAttribute`, `classList`, `focus()`. CSP `script-src 'self'` allows the external script files. | **Clear** — no XSS surface introduced. |
| **Viewer gate on modal + error path** | `@not_viewer` on `case_create` (line 555) returns 404 before any parsing. The case list template gates the button and modal on `can_mutate` (which is `not is_viewer(request.user)`). The `origin=cases` error re-render calls `_render_case_list`, which also checks `is_viewer` before including form context (line 530). A viewer never sees the modal, never sees the form fields, and POST gets 404. | **Clear** — ADR-006 enforced at all layers. |
| **Log hygiene (CWE-117)** | `not_viewer` logs `match.url_name` (server-controlled) not `request.path` (line 129-132). The new middleware logs nothing. `case_create` uses `messages.error` (user-facing flash), not log injection. | **Clear** — no log forging vector. |

---

## Out of Scope / Not Reviewed

- The broader viewer-role enforcement wave (uncommitted changes in `git diff` outside the listed files).
- API endpoints (`/api/v1/...`) — explicitly excluded from the middleware and unchanged.
- `case_edit`, `case_merge`, `case_share_*`, `case_attachment_*`, `case_procedure_*`, `case_template_*`, `tag_*`, `observable_*`, `playbook_*`, `settings_*` views — not modified by this change.
- Deployment/infrastructure settings (the 6 `manage.py check --deploy` warnings are pre-existing and unrelated).
- Automated playbook execution (`automation/executor.py`, `automation/dispatcher.py`) — not touched.

---

## Concise Summary for Leadership

**No critical or high-severity issues.** The trailing-slash middleware correctly implements a safe, scoped redirect with a `resolve()` guard and explicit exclusions for API/admin/static/media/ws. The new-case modal is a well-scoped progressive enhancement: the button is a real link, the modal is server-rendered with CSP-compliant external JS, CSRF and viewer gates are enforced at the view layer, and the `origin=cases` error re-render stays on the cases list without leaking data.

**Two low-severity hardening items:**
1. **LOW-01**: Sanitize the preserved query string for CRLF before building the `Location` header (defence-in-depth against theoretical header injection).
2. **LOW-02**: Document or tighten `rstrip("/")` to single-slash stripping (behavioural clarity only).

**Three informational confirmations:** Middleware ordering is correct (security/CSP headers apply to redirects), `origin=cases` is a safe server-controlled flag, and the no-JS fallback works as designed.

**Recommendation:** Merge with LOW-01 addressed (one-line query-string sanitisation). LOW-02 is optional.
