# Plan: Search page + rail entry (replace top-nav search)

**Date:** 2026-11-02

**Status:** Implemented 2026-11-02 — frontend + conformance tests complete. Re-verified 2026-11-13:
`test_ui_nav`, `test_ui_search`, `test_ui_urls_smoke`, `test_ui_inline_assets`, and
`test_search_api` pass; `make check` (including `make css-check` and `manage.py check`) is green;
full suite **3673 passed, 3 skipped, 2 subtests passed** on 2026-11-13.

**Post-implementation note (2026-11-13):** search semantics changed independently — digit-only terms
now also match case title/description/summary (see `PLAN-2026-11-01-global-search.md`, decisions log).

**Supersedes:** `PLAN-2026-11-01-top-nav-global-search.md` (top-nav experiment reverted)

## Summary
Remove the top navigation bar experiment. Restore the account avatar/menu to the bottom of the left rail. Add a first-class **Search** rail item directly below **Cases**, and move the global search input + button onto the `/search` page itself. Search results behaviour (scoping, highlighting, tabs, pagination, API) is unchanged.

## Goals
1. Remove `.top-nav` markup and CSS; restore the original two-column shell (`rail` + `main`).
2. Restore `<details class="rail-user">` (avatar, username, dropdown) to `rail-foot` at the bottom of the rail.
3. Add a **Search** rail link directly below **Cases**, with active-page styling and a keyboard shortcut.
4. Put the search form (input `#global-search-q` + submit button) at the top of the `/search` page, above tabs/results.
5. Keep search results/blank state exactly as they are today.
6. Keep CSP compliance: no inline styles or inline scripts; no new assets.

## Non-goals
- No changes to `query/search.py`, `query/search_views.py`, API contracts, scoping, audit, highlighting, pagination.
- No changes to the search result partials except where the form needs a home.
- No new global search box on every page.
- No new backend endpoints.

## Decisions (user-confirmed)
1. **Remove top nav entirely** — yes.
2. **Search shortcut** — `s`. The existing `s` binding for **Sources** moves to `d`.
3. **Form placement** — top of the `/search` page content, inside the existing search card; remove the redundant card header `<h2>Search</h2>`.
4. **Autofocus** — autofocus `#global-search-q` only when the query is blank.
5. **Visibility** — Search rail item visible to every authenticated role (viewers included); route already requires login.
6. Search submit preserves the current `type` tab via a hidden `type` field.

## UI / UX
Rail order becomes:

```
Dashboard
Alerts
Cases
Search          ← new, below Cases
────────
Observables
Automation
Playbooks
────────
Sources
Tags
Case templates
Taxonomy
Settings (admin/superAdmin)
```

Search page:

```
[page heading] Search
┌─ card ──────────────────────────────────────────┐
│ [ search input ................ ] [ Search ]    │
│ (blank instruction, or result tabs + results)   │
└─────────────────────────────────────────────────┘
```

## Keyboard
- `ui/static/ui/keys.js`:
  - `ROUTES` gains `s: "/search"`; the existing Sources route changes from `s: "/sources"` to `d: "/sources"`.
  - `CHEATSHEET` gains `{ key: "s", label: "Search" }`; Sources changes to `{ key: "d", label: "Sources" }`.
  - Existing `/` behaviour stays: focus `#global-search-q` when present, else first main input.
- `ui/templates/ui/base.html`:
  - Search rail link uses `<span class="k">s</span> Search`.
  - Sources rail link uses `<span class="k">d</span> Sources`.
  - Cheat-sheet table gains `<kbd>s</kbd> Search`; Sources row changes to `<kbd>d</kbd> Sources`.

## CSS
- `ui/static/ui/app.src.css`:
  - Restore `.shell` to `grid-template-columns: 220px 1fr; min-height:100vh; display:grid` (remove top-nav row/areas).
  - Restore `.rail` to `height:100vh; position:sticky; top:0` (remove grid-row/grid-column/z-index additions).
  - Restore `.main` to `max-width:1500px; padding:24px 28px 64px` (remove grid-row/grid-column/height).
  - Delete `.top-nav` and all `.top-nav …` rules.
  - Keep existing `.rail-user*` rules (they already open the menu upward at the rail foot).
  - Add only minimal search-page form CSS if needed (e.g. `.search-form { margin-bottom: var(--gap); }`, `.search-form .btn { margin-top: 0; }`).
- Regenerate `ui/static/ui/app.css` with `make css`.

## Files expected to change
- `ui/templates/ui/base.html` — remove top nav; restore rail-foot account menu; add Search rail link; add cheat-sheet row.
- `ui/templates/ui/search.html` — add search form at top of content; remove redundant card header.
- `ui/static/ui/keys.js` — add `f` route/cheat-sheet entry.
- `ui/static/ui/app.src.css` + `ui/static/ui/app.css` — revert top-nav layout; minor search-form CSS.
- `docs/spec/ui-style.md` — update §8: no header form; Search rail entry + page form; shortcut notes.
- `tests/conformance/test_ui_nav.py` — replace top-nav account tests with rail-foot test; Search rail/route/cheatsheet parity.
- `tests/conformance/test_ui_search.py` — replace header-form test with Search-page-form test; assert no global search input on dashboard.
- `docs/planning/PLAN-2026-11-01-top-nav-global-search.md` — mark superseded.

## Acceptance criteria
- [x] No `.top-nav` element or CSS remains.
- [x] `.shell` / `.rail` / `.main` layout matches the pre-top-nav two-column layout.
- [x] Account avatar + username + dropdown are back in `rail-foot`, opening upward.
- [x] **Search** rail link appears directly after **Cases**, with `aria-current` on `/search`.
- [x] Search rail link has shortcut `s`; `keys.js` route and cheat-sheet agree (`s` → `/search`).
- [x] Sources shortcut is now `d` in the rail, `keys.js` route, and cheat-sheet (`d` → `/sources`).
- [x] `/search` renders the search form with `#global-search-q`, visible Search button, `method="get"`, `action="/search"`.
- [x] Search form prefills the current query; blank query shows existing instruction and autofocuses the input.
- [x] Submitting from a typed tab preserves `type` via hidden field.
- [x] Dashboard and other pages do **not** contain `#global-search-q`.
- [x] `/` still focuses `#global-search-q` on the Search page and falls back to the first main input elsewhere.
- [x] `make check-fast`, `make css-check`, `manage.py check` pass.
- [x] Targeted tests pass: `test_ui_nav`, `test_ui_search`, `test_ui_urls_smoke`, `test_ui_inline_assets`, `test_search_api`.

## Risks
- Rail/cheat-sheet parity tests are strict; adding Search without matching `keys.js`/cheat-sheet entries will fail.
- Reverting layout touches sticky rail/scroll behaviour; smoke tests catch template regressions but not pixel layout.
- The account menu CSS was built for the rail foot; restoring it should not need menu changes — do not “fix” it again.

## Handoff
- **django-frontend**: implemented Search `s` / Sources `d`, restored the accidentally deleted component CSS, verified layout, regenerated CSS, passed cheap checks.
- **qa-tester / django-backend**: updated `test_ui_nav.py` and `test_ui_search.py` for rail-foot account menu, Search/Sources shortcuts, and the `/search`-only form; corrected security assertions and removed duplicate tests.
- No commit/push unless explicitly requested.
