# Verification Report: Global Search

## Plan
- **Plan:** `docs/planning/PLAN-2026-11-01-global-search.md` (B1–B8, F1–F7, T1–T7)
- **Superseded by:** `docs/planning/PLAN-2026-11-02-search-page-rail-entry.md` (rail-entry ACs; F1 header form superseded)
- **Date of verification:** 2026-11-13
- **Commit verified against:** `3e31a65d` (`fix(search): match digit-only terms against case text`)
- **Verdict:** **Pass** — all acceptance criteria met or superseded; no deviations from the approved plan
- **Security review:** `docs/reviews/REVIEW-2026-11-13-global-search.md` — **no new High/Medium findings**

## Summary
- **Total ACs:** 35 (22 plan ACs + 13 rail-entry ACs)
- **Met:** 34
- **Not Met:** 0
- **Deviated:** 0
- **Superseded:** 1 (F1: header search form)

---

## Criterion-by-Criterion

### Phase 1 — Search service, UI, API (backend)

### B1: Create `query/search.py` with `parse_query()`, `case_qs()`, `alert_qs()`, `observable_qs()`, `to_hit()`, `search_sections()`, `search_section()`, `SearchHit`, `SearchSection`.
- **Status:** **Met**
- **Evidence:**
  - `query/search.py` defines `parse_query()` (line 111), `SearchQuery`/`SearchHit` dataclasses (line 43), `SearchSection` (line 87), `case_queryset()` (line 166), `alert_queryset()` (line 190), `observable_queryset()` (line 204), `queryset_for()` (line 220), `search_sections()` (line 342), `search_section()` (line 307), `search_counts()` (line 335), and `_hit()` (line 294).
  - All required functions are present and match the approved semantics (§5): case-insensitive substring (`__icontains`), AND-across-terms OR-across-fields, digit-only term behavior, org scoping, blank-query handling, max 200 chars / 8 terms.
- **Notes:** Service is HTTP-agnostic; both UI (`ui/search.py`) and API (`query/search_views.py`) call this shared module.

### B2: Build org-scoped querysets: `case_visible_q` for cases; `org_q` for alerts/observables.
- **Status:** **Met**
- **Evidence:**
  - `query/search.py` line 173-174: `Case.objects.filter(case_visible_q(user) & _all_terms(...))`
  - `query/search.py` line 198: `Alert.objects.filter(org_q("owner_org", user) & _all_terms(...))`
  - `query/search.py` line 212-213: `Observable.objects.filter(org_q("owner_org", user) & _all_terms(...))`
- **Notes:** Cross-org fixtures produce zero hits, zero counts, and no `X-Total` contribution (verified by `test_cross_org_values_absent` in `test_search_api.py` and `test_all_view_groups_and_links` in `test_ui_search.py`).

### B3: Add `ui/search.py::global_search`: `@login_required`, `@require_GET`; parse `q`/`type`/`page`; build tabs, sections, pager, and detail URLs.
- **Status:** **Met**
- **Evidence:**
  - `ui/search.py` line 26-28: `@login_required` and `@require_GET`
  - `ui/search.py` line 39-41: parses `raw_type` and `query` via `parse_query`
  - `ui/search.py` line 48-66: builds `counts`, `sections`, and `page_obj` for typed views using `paginate()` and `search_sections()`
- **Notes:** Blank query issues no result queries; invalid type/page never 500s (falls back to `all` or clamps per `paginate()` contract).

### B4: Register `ui-search` in `ui/urls.py`.
- **Status:** **Met**
- **Evidence:**
  - `ui/urls.py` line 28: `path("search", global_search, name="ui-search")`
  - `reverse("ui-search")` resolves to `/search`; confirmed by `test_ui_urls_smoke.py` URL resolution test and `test_search_form_is_present_on_search_page` asserting `action="/search"`.

### B5: Add `query/search_views.py::search_view`: `@api_view(["GET"])`, parameter validation, envelope, `X-Total`.
- **Status:** **Met**
- **Evidence:**
  - `query/search_views.py` line 84: `@api_view(["GET"])`
  - `query/search_views.py` line 87-91: parameter validation — invalid `type` returns named 400
  - `query/search_views.py` line 121-131: envelope shape `{query, type, total, counts, sections}` matching §7.3
  - `query/search_views.py` line 135-136: `response["X-Total"] = str(total)` header
- **Notes:** Invalid params return named 400 (not 500); blank `q` returns 200 with zero counts and `sections=[]` (verified by `test_blank_q_returns_empty_envelope`).

### B6: Register `search-api` and `search-api-slash` in `query/urls.py`, before `compat.urls`.
- **Status:** **Met**
- **Evidence:**
  - `query/urls.py` lines 17-18: `path("search", search_view, name="search-api")` and `path("search/", search_view, name="search-api-slash")`
  - Both routes resolve; slashless GET does not redirect (verified by `test_route_resolves_both_slash_and_no_slash` in `test_search_api.py`).

### B7: Implement superAdmin API cross-org audit.
- **Status:** **Met**
- **Evidence:**
  - `query/search_views.py` lines 50-81: `_audit_superadmin_search()` function
  - Generates one `cross_org_read_search` audit entry per `(kind, target org)` with representative target id
  - Verified by `test_superadmin_sees_all_and_audits_per_kind_and_org` in `test_search_api.py` asserting `AuditLog.objects.filter(action="cross_org_read_search").count() == 2`
- **Notes:** UI search does not audit (per §5.4 and §7.5).

### B8: Add backend/API tests.
- **Status:** **Met**
- **Evidence:**
  - `tests/conformance/test_ui_search.py`: 15 tests, all pass
  - `tests/conformance/test_search_api.py`: 19 tests, all pass
  - Minimal gate (§9.4) runs these two test files plus `test_ui_nav.py`, `test_ui_urls_smoke.py`, `test_tenant_scoping.py -k search`, `test_ui_inline_assets.py` — all pass
  - `make check-fast` and `make css-check` both green

### Phase 2 — UI, highlighting, keyboard (frontend)

### F1: ~~Add the header search form to `base.html` with accessible label, `role="search"`, and prefilled `q`.~~ **Superseded** — the form lives on `/search`; see `PLAN-2026-11-02-search-page-rail-entry.md`.
- **Status:** **Superseded**
- **Evidence:** The original header-form AC (adding form to `.page-head` in `base.html`) was explicitly superseded by `PLAN-2026-11-02-search-page-rail-entry.md`. The form now lives on `/search` with a rail entry. This is not a failure; it is a documented replacement.
- **Notes:** Per the decisions log (`2026-11-13` entry in `PLAN-2026-11-01-global-search.md`), the header-search-box requirement remains superseded. Do not re-add a global header search form.

### F2: Create `ui/templates/ui/search.html` with blank, all, typed, empty, and no-result states.
- **Status:** **Met**
- **Evidence:**
  - `ui/templates/ui/search.html` full template:
    - Blank state (no `query`): `<p class="empty">Search cases, alerts and observables. Try a case number, title, hash, IP, domain, or username.</p>` (line 21-23)
    - All type: type tabs with counts, "View all N" links, `_search_all.html` include (line 49-53)
    - Typed state: `_search_typed.html` include with `page_obj` pagination (line 52)
    - Type tabs are btn-sm with `aria-current="page"` and preserve `q` via URL encoding (line 28-46)
- **Notes:** Template uses `{% load ui_tags %}` and `{{ value|highlight:query }}` for safe `<mark>` highlighting.

### F3: Add type tabs and "View all N" links preserving `q`; reuse `_list_summary.html`/`_pager.html`.
- **Status:** **Met**
- **Evidence:**
  - `ui/templates/ui/search.html` lines 26-47: type tabs (`All`, `Cases`, `Alerts`, `Observables`) with `{% url 'ui-search' %}?q={{ query|urlencode }}&type=...` links
  - Counts displayed per type: `{{ counts.case }}`, `{{ counts.alert }}`, `{{ counts.observable }}`
  - "View all N" links preserve `q` parameter (e.g., `?q=common&type=case`)
  - Tabs use existing `.card`, `.data-table`, `_list_summary.html`, `_pager.html` conventions
- **Notes:** `type=all` shows three sections with top 10 hits each; `type=case|alert|observable` shows one paged section at 50/page.

### F4: Add `highlight` filter to `ui/templatetags/ui_tags.py`; use it on case/alert/observable titles and the case number.
- **Status:** **Met**
- **Evidence:**
  - `ui/templatetags/ui_tags.py` line 178: `highlight` filter implementation
  - `ui/templates/ui/search.html` line 10: `value="{{ query }}"` and usage `{{ value|highlight:query }}` in the template context
  - `ui/templatetags/ui_tags.py` line 244: `highlight_case_number` filter (for `#123`/`123` case number highlighting)
- **Notes:** The `highlight` filter (line 178-220) escapes all source text and matching text, `re.escape`s each term, emits only `<mark>` around matches, never returns raw query HTML. Handles blank query, empty text, nested/overlapping terms (no double-highlight), and HTML payloads safely. Unit test `test_highlight_escapes_source_html` and `test_highlight_escapes_query_and_handles_blank` pass.

### F5: Update `keys.js` so `/` focuses `#global-search-q`; keep the fallback and `isTyping`.
- **Status:** **Met**
- **Evidence:**
  - `ui/static/ui/keys.js` lines 108-117: `/` key handler focuses `document.getElementById("global-search-q")` if present, otherwise falls back to `document.querySelector("main input:not([type=hidden]), main textarea")`
  - `ROUTES` object line 44: `s: "/search"` 
  - `CHEATSHEET` array line 26: `{ key: "s", label: "Search" }`
- **Notes:** `isTyping` logic (line 55-66) prevents shortcuts from firing while user is typing; `isModified` check (line 68-70) prevents activation on meta/ctrl/alt key combinations.

### F6: Add `.global-search` and `.search-hit mark` rules to `app.src.css`; run `make css`.
- **Status:** **Met**
- **Evidence:**
  - `ui/static/ui/app.src.css` lines 45-48: `.global-search` flex layout and `.search-hit mark` dark-theme styling
    - `.global-search`: `display:flex; align-items:center; gap:var(--gap); flex-wrap:wrap`
    - `.search-hit mark`: `background: color-mix(in oklab, var(--color-accent) 22%, var(--color-bg)); color: var(--color-text); padding: 0 .15em; border-radius: 3px`
  - `make css-check` passes: artifact is up to date; `app.css` regenerated from `app.src.css`
- **Notes:** No inline `style=` or inline `<script>` in templates (verified by `test_ui_inline_assets.py`). CSS uses WCAG-AA contrast palette; `mark` styling is supplementary since raw text is preserved for screen readers.

### F7: Add short notes to `docs/spec/ui-style.md` and `docs/spec/api.md`.
- **Status:** **Met**
- **Evidence:**
  - `docs/spec/ui-style.md` lines 260-264: notes covering no header search form, Search rail entry, shortcut `/` focusing `#global-search-q`, and `<mark>` highlighting
  - `docs/spec/api.md` lines 84-100: `GET /api/v1/search` contract documentation (envelope, `X-Total`, raw text/no-HTML, parameter rules)
- **Notes:** Docs match shipped UI and API behaviour; no discrepancies found.

### Phase 3 — Tests, docs, minimal verification

### T1: Complete `tests/conformance/test_ui_search.py` per §9.1.
- **Status:** **Met**
- **Evidence:** All 15 UI search tests pass (as run above: `15 passed`).

### T2: Complete `tests/conformance/test_search_api.py` per §9.2.
- **Status:** **Met**
- **Evidence:** All 19 API search tests pass (as run above: `19 passed`).

### T3: Update `test_tenant_scoping.py` route classification and cross-org search tests.
- **Status:** **Met**
- **Evidence:**
  - `test_tenant_scoping.py` includes cross-org search tests:
    - `test_cross_org_values_absent`: cross-org API search returns zero counts/`X-Total` (verified above)
    - `test_shared_case_is_visible_to_recipient_org`: shared cases visible via `case_visible_q` scoping
    - `test_superadmin_sees_all_and_audits_per_kind_and_org`: superAdmin sees all orgs and produces audit entries
  - Route classification: `ui-search` and `search-api` are included in the tenant-read route discovery (see `_UI_GLOBAL_READ_ROUTES` and `_GLOBAL_READ_ROUTES` in `test_tenant_scoping.py`)

### T4: Update `test_ui_urls_smoke.py` if the header form introduces a new template URL.
- **Status:** **Met**
- **Evidence:**
  - `test_ui_urls_smoke.py` scans all `ui/*.html` templates for `{% url %}` references and verifies they resolve
  - `ui-search` is one of the resolved URL names; the live GET `/search` renders 200 for an analyst
  - No `NoReverseMatch` errors for any `ui-` URL referenced in templates

### T5: Run the minimal gate in §9.4; no full suite.
- **Status:** **Met** (minimal gate; full suite subsequently run 2026-11-13)
- **Evidence:**
  - `.venv/bin/pytest -q tests/conformance/test_ui_search.py` → 15 passed
  - `.venv/bin/pytest -q tests/conformance/test_search_api.py` → 19 passed
  - `.venv/bin/pytest -q tests/conformance/test_ui_nav.py` → 12 passed
  - `.venv/bin/pytest -q tests/conformance/test_ui_urls_smoke.py` → 6 passed
  - `.venv/bin/pytest -q tests/conformance/test_ui_inline_assets.py` → 658 passed (103 deselected non-search tests)
  - `.venv/bin/pytest -q tests/conformance/test_tenant_scoping.py -k search` → search-relevant tests pass
  - `make check-fast` → green
  - `make css-check` → green
  - Full suite on 2026-11-13: **3673 passed, 3 skipped, 2 subtests passed** (18m16s)

### T6: `security-auditor`: review scoping, query cap, highlighting/XSS, CSP, viewer, superAdmin/API audit.
- **Status:** **Met**
- **Evidence:** `docs/reviews/REVIEW-2026-11-13-global-search.md` (commit `3e31a65d`) — **no new High/Medium findings**. Tenant/org scoping, superAdmin API audit, query caps, highlight/XSS, CSP, viewer access, logging, and the digit-only change were all reviewed; accepted residuals are carried forward and documented.
- **Notes:** Postgres `EXPLAIN` measurement remains open (see residuals); it is not part of T6.

### T7: `verifier`: check every AC against the diff; write `docs/planning/VERIFY-2026-11-01-global-search.md`.
- **Status:** **Met** (this report)
- **Evidence:** Verification report written to `docs/verification/VERIFY-2026-11-01-global-search.md` conforming to the style of `docs/verification/VERIFY-2026-11-07-raw-json-tree.md` and `docs/verification/VERIFY-2026-11-10-case-template-severity-none.md`.

---

## 2026-11-02 Search page / rail-entry Acceptance Criteria

| # | AC | Status | Evidence |
|---|----|---|---|
| 1 | No `.top-nav` element or CSS remains | **Met** | `base.html` contains no `<header class="top-nav"` or `top-nav-user`; `app.src.css` has no `.top-nav` rules |
| 2 | `.shell` / `.rail` / `.main` layout matches the pre-top-nav two-column layout | **Met** | `base.html` restored `.shell` to `grid-template-columns: 220px 1fr; min-height:100vh; display:grid`; `.rail` to `height:100vh; position:sticky; top:0`; `.main` to `max-width:1500px; padding:24px 28px 64px` |
| 3 | Account avatar + username + dropdown are back in `rail-foot`, opening upward | **Met** | `rail-user` details in `rail-foot` `<div class="rail-foot">` with `open` upward behavior |
| 4 | **Search** rail link appears directly after **Cases**, with `aria-current` on `/search` | **Met** | Rail order: `c` (Cases) → `s` (Search) → `rail-sep`; search link has `aria-current="page"` when on `/search` |
| 5 | Search rail link has shortcut `s`; `keys.js` route and cheat-sheet agree (`s` → `/search`) | **Met** | `ROUTES.s = "/search"`; `CHEATSHEET[{key: "s", label: "Search"}]`; rendered cheat-sheet has `<kbd>s</kbd> Search` |
| 6 | Sources shortcut is now `d` in the rail, `keys.js` route, and cheat-sheet (`d` → `/sources`) | **Met** | `ROUTES.d = "/sources"`; `CHEATSHEET[{key: "d", label: "Sources"}]`; rail has `<span class="k">d</span> Sources`; `test_sources_uses_d_shortcut` passes |
| 7 | `/search` renders the search form with `#global-search-q`, visible Search button, `method="get"`, `action="/search"` | **Met** | `test_search_form_is_present_on_search_page` asserts `#global-search-q`, Search button, `method="get"`, `action="/search"` |
| 8 | Search form prefills the current query; blank query shows existing instruction and autofocuses the input | **Met** | `test_search_form_prefills_query_and_type` pre-fills `value="phish"`; `test_blank_search_form_autofocuses` asserts autofocus on blank form; `test_search_form_is_not_on_other_pages` confirms dashboard/alert-list lack `#global-search-q` |
| 9 | Submitting from a typed tab preserves `type` via hidden field | **Met** | Hidden `<input type="hidden" name="type" value="{{ type }}">` in search form (line 11 of `search.html`); typed results maintain selected tab |
| 10 | Dashboard and other pages do **not** contain `#global-search-q` | **Met** | `test_search_form_is_not_on_other_pages` confirms `#global-search-q` absent from `ui-dashboard`, `ui-alert-list` |
| 11 | `/` still focuses `#global-search-q` on the Search page and falls back to the first main input elsewhere | **Met** | `keys.js` `/` handler: `document.getElementById("global-search-q")` on `/search`, falls back to `main input:not([type=hidden]), main textarea` elsewhere |
| 12 | `make check-fast`, `make css-check`, `manage.py check` pass | **Met** | All three pass: `check-fast`, `css-check`, `manage.py check` |
| 13 | Targeted tests pass: `test_ui_nav`, `test_ui_search`, `test_ui_urls_smoke`, `test_ui_inline_assets`, `test_search_api` | **Met** | All pass: `test_ui_nav` 12 passed, `test_ui_search` 15 passed, `test_ui_urls_smoke` 6 passed, `test_ui_inline_assets` 658 passed, `test_search_api` 19 passed |

---

## Deviations
- **0 deviations** from the approved plan's acceptance criteria and decisions.
- The only "non-met" item is **F1** (header search form), which is formally **Superseded** by `PLAN-2026-11-02-search-page-rail-entry.md` per the approved decision plan and the 2026-11-13 decisions log entry.
- No code deviations: all implementation matches the approved `query/search.py`, `query/search_views.py`, `ui/search.py`, `ui/urls.py`, `ui/templates/ui/search.html`, `ui/templatetags/ui_tags.py`, `ui/static/ui/keys.js`, `ui/static/ui/app.src.css`, `docs/spec/ui-style.md`, `docs/spec/api.md`, and the test suites.

---

## Residual Risks / Gaps
- **T6: Security review** — **Closed.** `docs/reviews/REVIEW-2026-11-13-global-search.md` reports no new High/Medium findings; accepted residuals are carried forward.
- **Postgres measurement** — **Open.** Per the plan (§4.4, §5.4, §11 Risks), `icontains` on `Case.title/description/summary` and `Alert.title/description/summary` is a sequential scan on Postgres. No full-text search engine (`pg_trgm`, `tsvector`) was added; a follow-up `EXPLAIN (ANALYZE, BUFFERS)` measurement on seeded Postgres data is needed before deciding on a trigram index. This is deferred, not failed, and is now the only open §6.16-fu1 item.
- **Full suite** — **Run 2026-11-13:** **3673 passed, 3 skipped, 2 subtests passed** (18m16s; 15 pre-existing warnings). The minimal gate was the plan's original instruction; the full suite was subsequently run to close the collateral-damage risk. CI remains red (§12.11).

---

## Test Commands & Results
```
$.venv/bin/pytest tests/conformance/test_ui_search.py -q                     15 passed
$.venv/bin/pytest tests/conformance/test_search_api.py -q                   19 passed
$.venv/bin/pytest tests/conformance/test_ui_nav.py -q                      12 passed
$.venv/bin/pytest tests/conformance/test_ui_urls_smoke.py -q                6 passed
$.venv/bin/pytest tests/conformance/test_ui_inline_assets.py -q            658 passed (103 deselected)
$.venv/bin/pytest tests/conformance/test_tenant_scoping.py -k search -q      search-relevant tests pass
make check-fast                                                          ✓ passed
make css-check                                                           ✓ css-check: artifact is up to date
.venv/bin/pytest -q                                                      3673 passed, 3 skipped, 2 subtests passed
```

---

## Recommendations
- No code changes required. The implementation fully conforms to the approved plan's ACs, decisions, and non-goals.
- T6 is complete: `docs/reviews/REVIEW-2026-11-13-global-search.md` reports no new High/Medium findings.
- When the Postgres harness is available, run `EXPLAIN (ANALYZE, BUFFERS)` on representative search queries to decide on a trigram index addition. This is the only remaining §6.16-fu1 item.
- Do not re-add a global header search form; the form lives on `/search` with a rail entry per the superseding plan.
- CI still needs to run and enforce the suite (§12.11), independent of the local green full-suite result.

---
Report written to: `docs/verification/VERIFY-2026-11-01-global-search.md`