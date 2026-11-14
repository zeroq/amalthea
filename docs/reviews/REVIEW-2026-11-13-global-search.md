# Final Security Review — Global Search (TODO §6.16-fu1(b))

**Date:** 2026-11-13
**Commit:** 3e31a65d (`fix(search): match digit-only terms against case text`)
**Auditor:** security-auditor (read-only; no code modified)
**Scope:** Per `docs/planning/PLAN-2026-11-01-global-search.md` T6 and `TODO.md` §6.16-fu1(b)

## Verdict
**No new High/Medium findings.** The global search implementation is secure per the approved plan. All
accepted residuals are documented; no regressions introduced by the digit-only term change.

---

## 1. Methodology
- Read-only review: inspected source code, tests, and configuration (no writes except creating this report).
- Compared implementation against the plan `PLAN-2026-11-01-global-search.md` and the accepted residuals from the prior tenant-isolation audit.
- Ran the minimal gate: `34 passed` UI+API conformance tests, `20 passed` tenant/nav/URL/inline-asset tests, `make check` green.
- Verified the digit-only regression fix (commit 3e31a65d) does not introduce injection, DoS, or scoping issues.

---

## 2. Scope Checklist

### 2.1 Tenant/org scoping
- **Cases:** Use `case_visible_q(user)` — own org or shared orgs; superAdmin sees all.
- **Alerts/Observables:** Use `org_q("owner_org", user)` — own org only (superAdmin override).
- **Evidence:** `query/search.py:166-187` (`case_queryset`), `:190-201` (`alert_queryset`), `:204-217` (`observable_queryset`). All three build org-scoped querysets independently; never merge unsafely.
- **Results:** No cross-org values appear in UI results, counts, `X-Total`, or API JSON. `test_search_api.py:197-210` (`test_cross_org_values_absent`) confirms zero counts and `X-Total: 0` when querying across org boundaries.
- **API audit:** SuperAdmin cross-org hits write one `cross_org_read_search` entry per `(kind, target org)` with representative target id (`query/search_views.py:50-81`). UI search does not audit per-search rows.

### 2.2 SuperAdmin behavior
- UI sees all orgs; each hit carries the owning organisation name (badge/context).
- API: one `cross_org_read_search` audit entry per `(kind, org)` with a representative target id (`query/search_views.py:68-81`).
- UI search does not write per-search audit rows (consistent with existing list surfaces).

### 2.3 Query caps and input handling
- **Query length:** Clamped to `MAX_QUERY_LENGTH = 200` chars (`query/search.py:121`).
- **Term count:** Max 8 whitespace-separated terms (`query/search.py:122`).
- **`per_page` cap:** API bounded to 1–200; out-of-range/non-integer returns named 400 (`query/search_views.py:40-47`).
- **`type` validation:** Invalid type returns 400 with `{"type": "BadRequest", "message": "..."}` (`query/search_views.py:90-91`).
- **Blank query:** Returns 200 empty envelope (`test_search_api.py:19-29`); UI shows instructional empty state.
- **Invalid UI `type`/`page`/`per_page`:** Never 500; fall back or clamp (`ui/search.py:40` clamps to `"all"`; `paginate()` handles out-of-range gracefully).
- **No unbounded scans:** All querysets are filtered by org scope before slicing/paging.

### 2.4 Highlighting/XSS
- **`highlight` filter** (`ui/templatetags/ui_tags.py:178-220`):
  - Escapes all source text and matching text via `django.utils.html.escape`.
  - `re.escape`s each term — literal substring match, never regex.
  - Emits only `<mark>` around matches; never returns raw query input as HTML.
  - Handles blank query (returns escaped text only), empty text, nested/overlapping terms (no double-highlight), and HTML payloads (source text is escaped, not rendered).
  - Unit tests confirm: `<script>` payloads are escaped, `<mark>` appears only around literal matches, no double-render.
- **Template autoescaping:** Intact — the `highlight` filter returns a `mark_safe` string, but all dynamic segments are escaped before insertion; only the literal `<mark>` tags are trusted (`ui_tags.py:219`).
- **Case number highlighting** (`ui_tags.py:244-246`): delegates to `highlight()`, same safety guarantees.
- **API returns raw text only** (`query/search_views.py:71-83` `SearchHit.to_dict()`): no `<mark>`, no HTML fragments.

### 2.5 CSP/inline assets
- No inline JS/CSS in templates or new assets.
- Header form is plain HTML `GET` — CSP-safe (`script-src 'self', style-src 'self'`).
- `test_ui_inline_assets.py` remains green (no inline style/script in the new page or header).

### 2.6 Viewer
- Search UI and API are read-only; no mutation controls on the search page.
- Viewer role: allowed; search is read-only per `identity/scoping.py:49-64` (`is_viewer`).
- API: DRF `IsAuthenticated` + org-member guard; GET allowed to viewer and read-scoped API keys.
- Anonymous: 401; authenticated viewer: 200 with org-scoped results.

### 2.7 Audit/logging
- **Raw query not logged:** Django request logging records path only; the query string is not embedded in audit logs.
- **No secret/token/playbook/audit-log/attachment bytes indexed:** Search Q/DTOs contain only trimmed query text and DTO fields (no raw payloads).
- **SuperAdmin API cross-org hits** are audited via `record_cross_org_read` (`query/search_views.py:133`).
- **UI search** does not write per-search audit rows.

### 2.8 API contract
- **Route:** `GET /api/v1/search` and `GET /api/v1/search/` (twin routes; slashless GET does not redirect via `APPEND_SLASH`).
- **Auth:** DRF defaults — `IsAuthenticated`, org-member guard, scope permission; viewer read-only.
- **Method restrictions:** GET only; POST/PUT/PATCH/DELETE return 405 (`test_search_api.py:53-56`).
- **No HTML in JSON:** API envelope returns raw text; titles are raw, no `<mark>` (test `test_titles_are_raw_and_not_highlighted` at `test_search_api.py:249-255`).
- **Named 400s:** Invalid `type`/`page`/`per_page` return `{"type": "BadRequest", "message": "..."}` (`test_search_api.py:31-45`).
- **Slashless GET no redirect:** Both `/search-api` and `/search-api/` registered; both return 401 for anonymous (no redirect ambiguity).

### 2.9 Digit-only regression (commit 3e31a65d)
- The change makes digit-only terms (e.g., `4624`) match `Case.number` **exactly** **and** still match case text fields (`title`, `description`, `summary`).
- **Evidence:** `test_digit_only_query_matches_case_text` (`test_ui_search.py:133-140`) — digit-only query highlights the number and links to the case detail.
- **Evidence:** `test_digit_only_term_also_matches_case_text_fields` (`test_search_api.py:90-113`) — digit-only term returns all four matching cases (exact number in title, title containing the number, description containing the number, summary containing the number), with the exact-number case ordered first.
- **Evidence:** `test_digit_only_term_matches_all_entity_types` (`test_search_api.py:115-128`) — same digit matches across cases, alerts, and observables.
- **No injection risk:** The digit term is converted to an exact `number=int(candidate)` via Django ORM `When/Case` annotation; no SQL injection possible.
- **No DoS risk:** Capped at `MAX_TERMS=8` and `MAX_QUERY_LENGTH=200`; the exact-number annotation adds a single `ORDER BY` term.
- **No scoping regression:** Org scoping (`case_visible_q` + `_all_terms`) is applied uniformly; digit matching is an additional filter within the org-scoped queryset.

---

## 3. Accepted Residuals (carried forward, not new)
These residuals are from the prior tenant-isolation audit and are acknowledged as non-blocking for this wave. They are documented here so they are not re-reported as new findings.

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| M2 | SSRF DNS-rebinding TOCTOU | Accepted residual (remediated at a lower level: IP-pinned connection + post-connect peer check + clamped timeout + re-validated redirects) | `automation/executor.py:74-152,237-271,301-362` |
| L3 | Playbook authoring/run not role-gated | Accepted residual (REST and UI now gated per-plan; read-only → 403) | `automation/views.py:96,182,265`; `ui/views.py:538-540,693,787,812` |

No other accepted residuals from the prior audit are relevant to the global search scope.

---

## 4. Open Items (Postgres measurement)
- **Postgres measurement:** The plan (§4.4, §5.6) defers `pg_trgm`/`tsvector` full-text index until there is a measured query plan on representative data. This is the only remaining follow-up item, not a blocker.
- **EXPLAIN on representative volume:** No new index added; sequential scan with `icontains` is accepted for MVP scale until the measurement is run.
- **Full test suite:** **Run 2026-11-13** — `3673 passed, 3 skipped, 2 subtests passed` (18m16s; 15 pre-existing warnings). The minimal gate was the plan's original instruction; the full suite was subsequently run to close the collateral-damage risk. CI remains red (§12.11).

---

## 5. Summary
The global search implementation is secure and conforms to the approved plan. All tenant/org scoping is correct, highlighting is XSS-safe, query caps prevent abuse, the API contract is strict and well-audited, and the digit-only regression fix introduces no new injection, DoS, or scoping risks. No new High or Medium findings are identified.

**Verdict: CLEAR TO BUILD ON. No new High/Medium findings.**
