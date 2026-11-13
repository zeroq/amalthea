# Plan: Global Search (TODO §6.16 P6 / B3)

**Status:** IMPLEMENTED 2026-11-02 (`6427beeb`) — search service, UI, and API shipped. The header-form
requirement was superseded by `PLAN-2026-11-02-search-page-rail-entry.md`; independent verification,
security review, and Postgres measurement remain in TODO §6.16-fu1.
**Date:** 2026-11-01
**Owner:** planner
**Source:** `TODO.md` §6.16 P6, `docs/planning/PLAN-2026-10-11-ui-completeness.md` B3/Q5
**Target:** Org-scoped global search across cases, alerts, and observables, with a `/` shortcut,
highlighted matches, a server-rendered results page, and `GET /api/v1/search`. The header search box
was superseded by `PLAN-2026-11-02-search-page-rail-entry.md`; the form now lives on `/search`.

---

## 1. Summary

Amalthea has paginated lists but no global search. The most common triage question — *“have we seen this hash / IP / user before?”* — currently requires browsing each list or composing a query API request. This wave adds:

- a **Search** rail entry plus a page-level search field on `/search` (the header box was superseded);
- `/` focuses the search field when present, otherwise the first main input;
- a server-rendered results page at `/search`;
- matches across **cases** (number/title/description/summary), **alerts** (title/description/summary), and **observables** (value);
- safe server-side `<mark>` highlighting of matched terms;
- org-scoped results, including case shares for the caller’s org;
- grouped results with per-type counts and a type-specific paged view;
- `GET /api/v1/search` for integrations, sharing the same search service and scoping rules.

No schema change, background index, search engine, or JavaScript typeahead is required.

---

## 2. Goals

1. Let an analyst find a case number, case title/description, alert title/description, or observable value from anywhere in the UI.
2. Expose the same search to API clients at `GET /api/v1/search`, with the same tenant scoping and deterministic results.
3. Preserve tenant isolation: a cross-org match never appears in results, counts, or API payloads.
4. Keep the UI keyboard-first: `/` focuses the global search; Enter submits; results are plain links and tabbable.
5. Keep the implementation server-rendered and no-JS: a plain `GET` form works with HTMX and JavaScript disabled.
6. Highlight matched terms safely without introducing a second XSS path.
7. Make result volume honest: show per-type counts and paginate the selected type; do not silently truncate.
8. Keep the change reviewable: no schema change, no search-engine dependency, no changes to existing entity serializers.

## 3. Non-goals

- No full-text search engine, Postgres `tsvector`, `pg_trgm`, or search-index migration in this wave. A Postgres index is a follow-up only after `EXPLAIN` on representative volume.
- No search over comments, timeline entries, tasks, attachments, pages, procedures, playbooks, ingestion tokens, or audit logs.
- No fuzzy search, stemming, synonym expansion, saved searches, search history, or typeahead/autocomplete.
- No replacement or extension of the TheHive-compatible `POST /api/v1/query` DSL. The new API endpoint is a simple, documented search surface.
- No changes to existing list pages; the header search box was superseded by the rail-entry plan
  (the form lives on `/search`).
- No cross-org search UI for superAdmin beyond the existing “superAdmin sees all” convention; results label the owning organisation.
- No API HTML/highlight fragments. The API returns raw text; highlighting is a UI concern.

## 4. Architecture

### 4.1 Runtime shape

```
search.html form ──────GET /search?q=…&type=…&page=…──▶ ui.search.global_search ─┐
                                                                                    │
GET /api/v1/search?q=…&type=…&page=…&per_page=… ──▶ query.search_views.search_view ├──▶ query.search service
                                                                                    │      ├─ parse/normalize q
                                                                                    │      ├─ org-scoped querysets
                                                                                    │      ├─ SearchHit / SearchSection
                                                                                    │      └─ counts + slicing/paging
                                                                                    ▼
                                                                       ui/search.html or JSON Response
```

The UI and API must not implement separate matching rules. Both call `query.search`.

### 4.2 Modules and files

| File | Responsibility |
|---|---|
| `query/search.py` (new) | Shared search service: query normalisation, per-entity Q builders, org-scoped querysets, `SearchHit`/`SearchSection`, counts, slicing/paging. |
| `query/search_views.py` (new) | `GET /api/v1/search` DRF view, parameter validation, JSON shape, `X-Total`, superAdmin cross-org audit. |
| `query/urls.py` | Register `search-api` and `search-api-slash` before the compat catch-all. |
| `ui/search.py` (new) | UI `global_search` view: parse request, call service, build detail URLs/tabs/pager context. |
| `ui/urls.py` | Register `path("search", global_search, name="ui-search")`. |
| `ui/templatetags/ui_tags.py` | Add a safe `highlight` filter for `<mark>` output. |
| `ui/templates/ui/search.html` (new) | Results page, grouped sections, type tabs, empty states, pager, highlighted titles. |
| `ui/templates/ui/base.html` | Header search form, accessible label, current query value. |
| `ui/static/ui/keys.js` | `/` focuses `#global-search-q`; fall back to the old first-input behaviour. |
| `ui/static/ui/app.src.css` | `.global-search` layout, responsive wrap, accessible `mark` styling. Run `make css`; do not hand-edit `app.css`. |
| `tests/conformance/test_ui_search.py` (new) | UI route, matching, scoping, pagination, viewer, highlighting/XSS, empty states, header integration. |
| `tests/conformance/test_search_api.py` (new) | API contract, auth, scoping, paging, invalid parameters, audit, raw-text/no-HTML behaviour. |
| `tests/conformance/test_tenant_scoping.py` | Add UI and API search cross-org coverage and route classification. |
| `tests/conformance/test_ui_urls_smoke.py` | Header form introduces `ui-search`; smoke GET must render. |
| `docs/spec/ui-style.md` | Short note for the global search component, highlight styling, and keyboard. |
| `docs/spec/api.md` | Document `GET /api/v1/search` request/response contract. |

### 4.3 Why a shared service, not the query DSL?

`POST /api/v1/query` is a wire-compatible TheHive DSL with its own field whitelist, operators, pagination step, and bare-array response. It is not a good fit for a UI text search, and forcing the UI through it would add serialization overhead and couple the UX to a compatibility surface.

The new `GET /api/v1/search` is intentionally simple: a fixed field set, substring matching, grouped results, and an envelope. It shares `query.search` with the UI but does not alter the existing DSL.

### 4.4 Why not add an index now?

`icontains` on `Case.title/description/summary`, `Alert.title/description/summary`, and `Observable.data` is a sequential scan on Postgres. At current MVP scale that is acceptable. This plan defers `pg_trgm`/`tsvector` until there is a measured query plan on representative data — the same discipline used for `observable_list` in §12.1-fu1. Adding an unused index now would create an inert artifact and a migration to maintain.

---

## 5. Search semantics (approved)

### 5.1 Query normalisation

- Read `q`, strip leading/trailing whitespace, collapse internal whitespace.
- Clamp to `MAX_QUERY_LENGTH = 200` characters.
- Split into at most `MAX_TERMS = 8` whitespace-separated terms.
- Blank query => render the search page with an instructional empty state and issue **zero** result queries. The API returns an empty envelope with zero counts.
- Terms are case-insensitive substring matches (`__icontains`), not regex.
- Multiple terms are **AND** across terms, **OR** across fields within each term. Example: `apt29 hash` requires one field to match `apt29` and one field to match `hash`.
- Leading `#` is stripped for case-number matching; a digit-only term also matches `Case.number`
  exactly **and** still matches the case text fields (title/description/summary).
- No SQL is constructed from the query. Django’s ORM parameterises values; `LIKE` wildcards are escaped by the ORM.

### 5.2 Fields

| Entity | Fields searched | Order |
|---|---|---|
| Case | `number` (numeric exact), `title`, `description`, `summary` | numeric exact first, then `-severity`, `-start_date` |
| Alert | `title`, `description`, `summary` | `-severity`, `-date` |
| Observable | `data` | `-created_at` |

This is the approved Q1 default: cases `number/title/description/summary`, alerts `title/description/summary`, observables `data`.

### 5.3 Scoping

- Cases: `case_visible_q(user)` — own org **or** a share to the caller’s org; superAdmin sees all.
- Alerts: `org_q("owner_org", user)`.
- Observables: `org_q("owner_org", user)`.
- Evaluate each queryset independently; never merge unsafely across orgs.
- Cross-tenant rows never enter counts, sections, UI results, API results, or “view all” links.
- Viewer role: allowed; search is read-only.
- Org-less user: `org_q`/`case_visible_q` deny, yielding no results rather than a 500.
- SuperAdmin: follows existing list behaviour — all orgs, each hit carries the owning organisation name; UI shows it as a column/badge.
- UI search does **not** write a per-search audit row (consistent with existing list surfaces).
- API search **does** audit superAdmin cross-org hits, mirroring `/api/v1/query`: one `cross_org_read_search` entry per `(kind, target org)` with a representative target id. Detail views remain audited as before.

### 5.4 Ranking and pagination

- `type=all` (default): three labelled sections, each with the **top 10** hits and an exact total count; no mixed-result pager. Each section has “View all N” → `?q=…&type=case|alert|observable`.
- `type=case|alert|observable` (UI): one section, paginated with the existing `ui.pagination.paginate()` helper at 50/page, with the existing `_list_summary.html` and `_pager.html` contracts.
- API `type=all`: sections capped at 10 each; `X-Total` is the sum across all sections. `page`/`per_page` are accepted but ignored for `all` (or rejected if non-default; see §7).
- API typed view: `page` defaults to 1; `per_page` defaults to 50 and is bounded to 1–200; out-of-range pages clamp to the last page.
- Invalid UI `type` falls back to `all`. Invalid UI `page`/`per_page` is handled by `paginate()` (never 500).
- Section totals are computed from the same filtered, org-scoped queryset used for the slice.

### 5.5 Result presentation and highlighting

Each hit is a server-side DTO with no raw model assumptions in the template:

```python
@dataclass(frozen=True)
class SearchHit:
    kind: Literal["case", "alert", "observable"]
    id: str
    number: int | None
    title: str
    subtitle: str
    severity: int | None
    status: str | None
    timestamp: datetime | None
    org_name: str | None = None
```

- Case: number `#123`, title = case title; subtitle = status · assignee; link `ui-case-detail` by number.
- Alert: title; subtitle = type/source/status; link `ui-alert-detail`.
- Observable: title = value; subtitle = type; link `ui-observable-detail`.
- Severity uses the existing `sev_class`/`sev_label` filters and always carries a text label.
- UI highlights matching terms in the case title, alert title, and observable value with a safe `highlight` template filter that emits `<mark>`.
- The filter escapes non-matching and matching text; it never returns raw query input as HTML. Terms are `re.escape`d and capped; no overlapping/double-escaped output.
- The case number is highlighted separately from the title so `#123` and `123` both mark the number.
- API results return raw text, never HTML fragments. Clients can highlight with their own renderer; the normalized query is returned in the envelope.
- CSS: `mark` uses the dark theme’s warning/accent palette with WCAG-AA contrast; the match is supplementary, because the raw text is still present for screen readers.

### 5.6 Empty and error states

- Blank query: “Search cases, alerts and observables. Try a case number, title, hash, IP, domain, or username.” No result queries.
- No results: “No matches for ‘q’.” Include a short hint; do not claim “no such observable exists” because search is substring-based.
- One section empty on `type=all`: keep the heading and show `0` so the shape is stable; omit “View all” when count is 0.
- Invalid UI type/page: never 500; fall back or clamp.
- Missing/blank `q` on `type=case`: show the blank-query state, not an empty table.
- API invalid `type`/`page`/`per_page`: named 400, never a silent fallback (see §7).

---

## 6. UI/UX

### 6.1 Header search

Add to the existing `.page-head` in `base.html`:

```html
<form class="global-search inline-form" method="get" action="{% url 'ui-search' %}" role="search">
  <label class="sr-only" for="global-search-q">Search cases, alerts and observables</label>
  <input id="global-search-q" name="q" type="search" value="{{ request.GET.q|default:'' }}"
         placeholder="Search cases, alerts, observables…" autocomplete="off">
  <button class="btn btn-primary" type="submit">Search</button>
</form>
```

- Visible on every authenticated page, including empty states.
- `page-head` becomes a three-part flex row: heading, search, actions; wraps cleanly on narrow screens.
- Search page pre-fills from the query string via `request.GET.q`; no separate JS state.
- The form is a normal GET, so it works without HTMX/JS and is CSP-safe.
- Use `type="search"` and a real `<label>`; the button has a visible text label.

### 6.2 Keyboard

- Update `keys.js`: on `/` (unmodified), focus `#global-search-q` if present, else the previous fallback (`main input`, `main textarea`).
- Keep `?` opening the cheat sheet.
- Keep the current cheat-sheet wording “Focus search / first input”; it remains accurate. If the wording changes, update both `CHEATSHEET` in `keys.js` and the server-rendered table in `base.html` (the existing `test_ui_nav.py` enforces this).
- No global key handler should fire while the user is typing (existing `isTyping` logic).

### 6.3 Results page

- Title: “Search”.
- Query summary: “Results for ‘q’” or “Search”.
- Type tabs: `All (N)`, `Cases (N)`, `Alerts (N)`, `Observables (N)`, implemented as links preserving `q`.
- `type=all`: three `<section class="card">` blocks with headings, count, compact table/list, and “View all N”.
- `type=case|alert|observable`: a single card with the existing summary/pager partials.
- Tables use the existing `data-table` conventions; do not invent a new table style.
- Titles use `{{ value|highlight:query }}`; the case number is wrapped in its own highlighted element.
- Viewer sees the same results; there are no mutation controls on this page.

### 6.4 CSS

Add component rules to `ui/static/ui/app.src.css`:

- `.global-search` — flex, gap, max-width, flexible input, wraps on small screens.
- `.global-search input[type="search"]` — min-width and height aligned with the existing 34px control convention.
- `.search-hit mark` — dark-theme mark background/foreground with WCAG-AA contrast; no animation.
- Optional `.search-count` / `.search-section` only if required by the template; prefer existing `.card`, `.data-table`, `.list-summary`, `.pager`.
- Run `make css`; never hand-edit `ui/static/ui/app.css`.
- No inline `style=` or inline `<script>`.

---

## 7. API contract

### 7.1 Route and method

- `GET /api/v1/search`
- `GET /api/v1/search/` (twin route; clients should not depend on `APPEND_SLASH` redirects)
- Auth: DRF defaults — `IsAuthenticated`, org-member guard, scope permission, viewer read-only. GET is allowed to viewer and read-scoped API keys.
- Throttling: existing DRF user/anonymous rates apply.
- No POST/PUT/PATCH/DELETE.

### 7.2 Parameters

| Param | Default | Rules |
|---|---|---|
| `q` | empty | Trimmed/clipped to 200 chars, max 8 terms. Empty returns a 200 empty envelope. |
| `type` | `all` | `all`, `case`, `alert`, `observable`; anything else => 400. |
| `page` | `1` | Only meaningful for typed requests. Integer >= 1; non-integer/<=0 => 400. Past last page clamps to last. |
| `per_page` | `50` | Only meaningful for typed requests. Integer 1–200; out of range/non-integer => 400. `type=all` ignores it. |

### 7.3 Response envelope

Always a JSON object with `query`, `type`, `total`, `counts`, and `sections`. `sections` is always a list, even for a typed request (one element), so clients can use one parser.

```json
{
  "query": "apt29",
  "type": "all",
  "total": 9,
  "counts": {"case": 3, "alert": 1, "observable": 5},
  "sections": [
    {
      "type": "case",
      "total": 3,
      "page": 1,
      "per_page": 10,
      "has_next": false,
      "results": [
        {
          "type": "case",
          "id": "2e5d…",
          "number": 123,
          "title": "Phishing campaign",
          "subtitle": "Open · alice",
          "severity": 3,
          "status": "Open",
          "timestamp": "2026-11-01T12:00:00+00:00",
          "organisation": "Acme"
        }
      ]
    }
  ]
}
```

- `counts` always has all three keys. `total` is the sum of counts.
- `X-Total: <int>` header mirrors `total` for parity with `/api/v1/query`.
- `type=all`: each section has `per_page=10`, `page=1`, `has_next=false`; only the top 10 are returned.
- Typed request: one section with the requested `page`/`per_page` and real `has_next`.
- `timestamp` is ISO-8601 or `null`.
- `number` is `null` for alerts/observables.
- `organisation` is always present; normal users only ever see their own org / shared cases.
- No HTML, no `<mark>`, no raw query echoed as markup. Highlighting is UI-only.

### 7.4 API errors

- 401/403: standard DRF auth/scope/viewer/org-member responses.
- 400: named JSON envelope, e.g. `{"type": "BadRequest", "message": "Unsupported search type 'foo'"}`.
- 405: non-GET.
- Empty/blank `q` is not an error: 200 with `total=0`, zero counts, `sections=[]`.

### 7.5 Audit

- SuperAdmin API search results spanning orgs write one `cross_org_read_search` audit entry per `(kind, target org)` with a representative target id, mirroring `/api/v1/query`.
- UI search does not write per-search audit rows; clicking a result into a detail page follows the existing detail audit path.

---

## 8. Security

| Threat | Control |
|---|---|
| Cross-tenant data leak | Every queryset derives from `case_visible_q` / `org_q`; no raw `Model.objects.all()`; cross-org results never counted or shown. |
| Existence oracle | Search returns matches, not per-id probes; no 404/403 distinction for a foreign entity. |
| Query injection | ORM-only; no raw SQL; terms are values, not operators. |
| XSS via query echo | Autoescape; query echoed only in text/attribute contexts; no `|safe` on raw query. |
| XSS via highlighting | `highlight` filter escapes all segments, `re.escape`s terms, emits only `<mark>`; unit test with `<script>`/`onerror` payloads. |
| DoS via huge query | `MAX_QUERY_LENGTH=200`, `MAX_TERMS=8`, fixed top-10 sections, max 50/page typed view, API `per_page<=200`; no unbounded `list()`. |
| Expensive scans | Accepted for MVP; measure on Postgres in a follow-up. No new index until `EXPLAIN` justifies it. |
| CSP | No inline JS/CSS; header form is plain HTML; `/` handler is in `keys.js`; `test_ui_inline_assets.py` stays green. |
| Viewer escalation | Search UI and API are read-only; viewer allowed by design. |
| Secret exposure | Search does not index playbooks, ingestion tokens, audit logs, or attachment bytes. |
| Log forging | Do not log the raw query; Django request logging remains path-only. |
| API data export | SuperAdmin cross-org API hits are audited; UI remains list-equivalent. |

---

## 9. Testing

### 9.1 Shared/UI suite — `tests/conformance/test_ui_search.py`

1. `ui-search` resolves and blank `GET /search` renders 200 with instructional empty state.
2. Case number, title, description, and summary each match.
3. Alert title, description, and summary each match.
4. Observable value matches.
5. Multiple terms are ANDed; a query matching only one term returns no hit; a digit-only term still
   matches case title/description/summary and orders the exact case number first.
6. `type=case|alert|observable` filters sections and paginates; invalid type falls back to all.
7. `type=all` shows per-type counts and only top 10 hits; “View all” preserves `q`.
8. Case shares: a case shared to the caller’s org is visible; an unrelated org’s case is not.
9. Cross-org alert/observable values never appear in results or counts.
10. Viewer can search and sees results; no mutation controls.
11. SuperAdmin sees all orgs and the owning organisation label.
12. Malformed/long query never 500s.
13. HTML output escapes `<script>`/`"><img onerror>` in titles and query echo.
14. Highlighting emits `<mark>` around matches, escapes marks in the source text, and does not double-highlight nested terms.
15. Header form exists on a normal page, posts to `ui-search`, has an accessible label, and `/` focus target is in `keys.js`.
16. Inline-asset guard remains green (`test_ui_inline_assets.py`).

### 9.2 API suite — `tests/conformance/test_search_api.py`

1. `GET /api/v1/search` and slash twin resolve; anonymous 401.
2. Authenticated analyst gets the envelope; viewer gets 200; read-scoped API key GET works.
3. Blank `q` returns 200 with zero counts and `sections=[]`.
4. Case/alert/observable matches and field coverage mirror the UI service.
5. Multi-term AND; case-number exact **plus case text fields**; `#123` form; exact number ordered
   first.
6. `type=all` returns three sections top 10 each, `counts` totals, `X-Total` sum.
7. Typed `page`/`per_page` works; `has_next`; out-of-range clamps; `per_page` cap.
8. Invalid `type`/`page`/`per_page` returns named 400.
9. Cross-org values are absent from results, counts, and `X-Total`.
10. SuperAdmin sees all orgs and produces `cross_org_read_search` audit entries.
11. Payload has no HTML/`<mark>`; titles are raw.
12. POST/PUT/PATCH/DELETE return 405.

### 9.3 Existing test updates

- `test_tenant_scoping.py`: classify `ui-search` and `search-api` as tenant routes; add cross-org UI/API assertions.
- `test_ui_urls_smoke.py`: header form makes `ui-search` a template-referenced URL; ensure smoke GET renders.
- `test_ui_nav.py`: unchanged unless the `/` label changes.
- `test_viewer_readonly_ui.py`: confirm search is read-only, not a mutating route.
- `test_ui_inline_assets.py`: no inline style/script in the new page or header.

### 9.4 Minimal final gate (per user instruction)

Do **not** run the full suite for this wave. At the end run only:

```bash
.venv/bin/pytest -q tests/conformance/test_ui_search.py
.venv/bin/pytest -q tests/conformance/test_search_api.py
.venv/bin/pytest -q tests/conformance/test_ui_nav.py
.venv/bin/pytest -q tests/conformance/test_ui_urls_smoke.py
.venv/bin/pytest -q tests/conformance/test_tenant_scoping.py -k search
.venv/bin/pytest -q tests/conformance/test_ui_inline_assets.py
make check
```

If a shared helper changed (e.g. `keys.js` or `base.html`), add the smallest directly affected test file; do not expand to the full suite. Record in the verification report that the full suite was intentionally skipped per user instruction, so a later gate can run it.

---

## 10. Phases and acceptance criteria

### Phase 1 — Search service, UI view, API (backend)

**Owner:** `django-backend`

| # | Task | AC |
|---|---|---|
| B1 | Create `query/search.py` with `parse_query()`, `case_qs()`, `alert_qs()`, `observable_qs()`, `to_hit()`, `search_sections()`, `search_section()`, `SearchHit`, `SearchSection`. | Service checks cover blank, whitespace, long, multi-term, `#123`, non-numeric terms, and case shares. |
| B2 | Build org-scoped querysets: `case_visible_q` for cases; `org_q` for alerts/observables. | Cross-org fixtures produce zero hits, zero counts, and no `X-Total` contribution. |
| B3 | Add `ui/search.py::global_search`: `@login_required`, `@require_GET`; parse `q`/`type`/`page`; build tabs, sections, pager, and detail URLs. | Blank query issues no result queries; invalid type/page never 500s. |
| B4 | Register `ui-search` in `ui/urls.py`. | `reverse("ui-search") == "/search"`; URL smoke finds it. |
| B5 | Add `query/search_views.py::search_view`: `@api_view(["GET"])`, parameter validation, envelope, `X-Total`. | Contract in §7; invalid params return named 400; no HTML in JSON. |
| B6 | Register `search-api` and `search-api-slash` in `query/urls.py`, before `compat.urls`. | Both routes resolve; slashless GET does not redirect. |
| B7 | Implement superAdmin API cross-org audit. | One `cross_org_read_search` entry per `(kind, org)` with representative id; UI search does not audit. |
| B8 | Add backend/API tests. | `test_ui_search.py` service/UI cases and `test_search_api.py` contract cases pass. |

### Phase 2 — UI, highlighting, keyboard (frontend)

**Owner:** `django-frontend`

| # | Task | AC |
|---|---|---|
| F1 | ~~Add the header search form to `base.html` with accessible label, `role="search"`, and prefilled `q`.~~ **Superseded** — the form lives on `/search`; see `PLAN-2026-11-02-search-page-rail-entry.md`. | `/search` renders the form; no other page contains `#global-search-q`. |
| F2 | Create `ui/templates/ui/search.html` with blank, all, typed, empty, and no-result states. | All states render meaningful text; no raw model assumptions leak into the template. |
| F3 | Add type tabs and “View all N” links preserving `q`; reuse `_list_summary.html`/`_pager.html`. | Links retain the query; counts match the view. |
| F4 | Add `highlight` filter to `ui/templatetags/ui_tags.py`; use it on case/alert/observable titles and the case number. | `<mark>` only; source/query payloads stay escaped; nested/overlapping terms do not double-render. |
| F5 | Update `keys.js` so `/` focuses `#global-search-q`; keep the fallback and `isTyping`. | `/` focuses search on `/search`; elsewhere it focuses the first main input; typing a shortcut while focused does nothing. |
| F6 | Add `.global-search` and `.search-hit mark` rules to `app.src.css`; run `make css`. | `make css-check` is clean; no inline styles; accessible contrast; responsive wrap. |
| F7 | Add short notes to `docs/spec/ui-style.md` and `docs/spec/api.md`. | Docs match the shipped UI and API behaviour. |

### Phase 3 — Tests, docs, minimal verification

**Owner:** `qa-tester` + `verifier` + `security-auditor`

| # | Task | AC |
|---|---|---|
| T1 | Complete `tests/conformance/test_ui_search.py` per §9.1. | All UI/search tests pass in the minimal gate. |
| T2 | Complete `tests/conformance/test_search_api.py` per §9.2. | All API/search tests pass in the minimal gate. |
| T3 | Update `test_tenant_scoping.py` route classification and cross-org search tests. | Both routes are tenant-classified; cross-org values are absent. |
| T4 | Update `test_ui_urls_smoke.py` if the header form introduces a new template URL. | Smoke GET for `ui-search` renders 200. |
| T5 | Run the minimal gate in §9.4; no full suite. | Targeted tests and `make check` pass; report states full suite was skipped by instruction. |
| T6 | `security-auditor`: review scoping, query cap, highlighting/XSS, CSP, viewer, superAdmin/API audit. | No new High/Medium; accepted residuals documented. |
| T7 | `verifier`: check every AC against the diff; write `docs/planning/VERIFY-2026-11-01-global-search.md`. | All ACs met or deviations recorded. |

---

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `icontains` scans are slow on large Postgres tables | Medium | Medium | Cap query/terms; top-10 sections; defer index; measure in a Postgres follow-up. |
| Mixed-result UX becomes confusing | Medium | Medium | Grouped sections + type tabs; no mixed pager in v1. |
| Search leaks a shared/foreign case | Low | High | `case_visible_q`; cross-org tests with unique values; viewer/org-less tests. |
| Highlight filter opens an XSS path | Low | High | Escape every segment, `re.escape` terms, emit only `<mark>`, dedicated payload tests. |
| API and UI drift | Medium | Medium | Both call `query.search`; API/UI tests assert the same matching and counts. |
| Header form breaks page layout | Medium | Low | `.global-search` flex/wrap rules; check narrow viewport manually or via CSS review. |
| `/` shortcut interferes with list filters | Low | Medium | Focus the dedicated search input, not `main input`; preserve `isTyping`. |
| API envelope becomes a compatibility burden | Medium | Medium | Document it as a simple search endpoint; keep the TheHive `POST /query` untouched. |
| Scope creep into comments/tasks | Medium | Medium | Non-goals; defer to a separate full-text wave. |
| Full suite skipped hides collateral damage | Medium | Medium | User instruction; targeted tests + `make check`; record the skipped gate in the verification report. |

---

## 12. Approved decisions

- **Q1 — Search fields:** cases `number/title/description/summary`, alerts `title/description/summary`, observables `data`.
- **Q2 — SuperAdmin scope:** all orgs, owning organisation shown; UI list-style (no per-search audit); API cross-org hits audited like `/api/v1/query`.
- **Q3 — Results UX:** default `type=all` grouped sections (top 10 each, counts, “View all N”); typed views one paged section (50/page).
- **Q4 — Highlighting:** include safe server-side `<mark>` highlighting in the UI; API returns raw text.
- **Q5 — API:** add `GET /api/v1/search` alongside the UI; shared service; no changes to `POST /api/v1/query`.
- **Q6 — Query semantics:** case-insensitive substring, multiple terms ANDed, max 200 chars / 8 terms, no stemming or fuzzy matching.

---

## 13. Handoff brief

### `django-backend`
- Implement `query/search.py`, `query/search_views.py`, `ui/search.py`, `ui-search`, `search-api`, `search-api-slash`.
- Implement org scoping, query normalisation, DTOs, counts, paging, `X-Total`, superAdmin API audit.
- Add no schema changes and no changes to existing serializers or `POST /api/v1/query`.
- Add `tests/conformance/test_ui_search.py` backend/UI cases and `tests/conformance/test_search_api.py`; update `test_tenant_scoping.py` route classification.
- Constraints: no raw SQL; max query/terms; no result queries for blank `q`; API returns raw text, no HTML.
- Do not run the full suite; run the new test files and `make check` only if asked.

### `django-frontend`
- Add header form to `base.html`, results template, type tabs, empty states, CSS source rules, `/` shortcut focus, and `highlight` filter in `ui_tags.py`.
- Preserve existing `_list_summary.html`/`_pager.html` and `data-table` conventions.
- No inline JS/CSS; run `make css`; keep `test_ui_inline_assets.py` green.
- Do not invent a second table/pager style.
- Highlighting must escape all source/query content and emit only `<mark>`.

### `qa-tester`
- Add/verify the UI, API, highlight/XSS, and tenant search tests.
- Run only the minimal gate in §9.4.
- Report exact commands/results and note the full suite was intentionally skipped.

### `security-auditor` / `verifier`
- Audit scoping, counts, highlighting/XSS, CSP, viewer, superAdmin API audit, and query caps.
- Verify every AC against the plan; record deviations in the plan and a persistent report.

---

## 14. Decisions log

- `2026-11-01` — Plan drafted. No implementation started. Q1–Q6 pending.
- `2026-11-01` — User accepted Q1/Q2/Q3/Q6 defaults; Q4 changed to include highlighting; Q5 changed to add `GET /api/v1/search` alongside the UI. Plan approved for handoff.
- `2026-11-13` — Digit-only terms now also match case title/description/summary instead of only
  `Case.number`; exact-number ordering is unchanged. This closes the “also matches” ambiguity in §5.1.
  The header-search-box requirement remains superseded (see the 2026-11-02 rail-entry plan).

## 15. Test Results (working log)

- `2026-11-01` — First agent handoff produced defects (fixture removal, OR/AND mismatch, paging offset,
  DTO/contract drift, unsafe highlight). Repaired in integration.
- `2026-11-01` — Targeted suites pass: `28 passed` for `test_search_api.py` +
  `test_ui_search.py`; `103 passed` for `test_tenant_scoping.py`; 605 passed for
  `test_ui_urls_smoke.py` + `test_ui_nav.py` + `test_ui_inline_assets.py`; `make check`
  clean (ruff, mypy, Django check, migrations, CSS drift, playbook discovery).
- Full suite intentionally skipped per user instruction; final verification/QA/security review still
  to be recorded in `docs/planning/VERIFY-2026-11-01-global-search.md`.
- `2026-11-13` — Digit-only terms now also match case title/description/summary (in addition to
  `Case.number`); exact-number ordering unchanged. Regression tests added to
  `tests/conformance/test_search_api.py` and `test_ui_search.py`. Focused search suites **34 passed**; adjacent
  tenant/nav/URL/inline suites **779 passed**; `make check` green. Full suite on 2026-11-13:
  **3673 passed, 3 skipped, 2 subtests passed** (18m16s; 15 pre-existing warnings). Verification,
  security review, and Postgres measurement remain open in TODO §6.16-fu1.
