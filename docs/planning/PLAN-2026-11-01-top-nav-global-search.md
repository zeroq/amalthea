# Plan: Top navigation bar for global search

**Date:** 2026-11-01

**Status:** Superseded — reverted in favor of `PLAN-2026-11-02-search-page-rail-entry.md`

**Scope:** Frontend only — `ui/templates/ui/base.html`, `ui/static/ui/app.src.css`, regenerated `ui/static/ui/app.css`.

## Summary
Move the existing global search input/button out of `page-head` into a dedicated top navigation bar. The top nav holds the search field and button; the input and button have the same height. No new API endpoints, no backend changes, no search semantics changes.

## Goals
1. Add a `.top-nav` bar at the top of the page shell containing only the global search form.
2. Move `<form class="global-search inline-form" ...>` from `page-head` into `.top-nav`; keep the same `id`, `name`, action, placeholder, and accessible label.
3. Make `<input>` and `<button>` the same rendered height (explicit `height`, matching border-radius/font/padding approach; override `.btn` top margin in the top nav).
4. Preserve keyboard behavior: `/` still focuses `#global-search-q`; Tab order input → button; skip link remains first focusable.
5. Keep CSP-compliant: no inline styles, no inline JS, no new static assets.
6. Keep dark theme and existing severity/contrast conventions unchanged.

## Non-goals
- No changes to `GET /api/v1/search`, `query/search.py`, `query/search_views.py`, or search result templates.
- No changes to search result counts, scoping, audit, pagination, or highlighting.
- No changes to the left rail beyond layout grid adjustments required to place the top bar above it.
- No new search shortcuts or cheat-sheet entries.

## Assumptions
- The top bar is full-width above the rail/main shell, inside `.shell` as a grid row spanning both columns: `grid-template-areas: "top-nav top-nav" "rail main"`.
- `request.GET.q` prefilling remains on the form; on the search results page this continues to echo the current query.
- Existing `.global-search` styles remain useful; top-nav overrides only height/margin/flex details.
- Tailwind binary `scripts/tailwindcss` is available to regenerate `app.css` (it was in prior `make check`).

## Data model / API
- None.

## UI/UX
- `.top-nav`: dark inset background, bottom border, sticky top, flex row, accessible `role="search"` form with `aria-label`.
- Input: `id="global-search-q"`, `name="q"`, `type="search"`, `autocomplete="off"`, explicit height matching button.
- Button: visible text `Search`, `type="submit"`, same height as input, `.btn-primary` styling.
- Label remains `.sr-only`; decorative icons none.
- `page-head` keeps `<h1>` and `{% block actions %}` only.

## CSS changes
- `ui/static/ui/app.src.css`:
  - `.shell`: `grid-template-rows: auto 1fr`; `grid-template-areas: "top-nav top-nav" "rail main"`.
  - `.rail`: `grid-area: rail`; `height: 100%` (was `100vh`).
  - `.main`: `grid-area: main`; `height: 100%`.
  - Add `.top-nav` rules: grid area, background, border-bottom, padding, sticky top, z-index; flex layout.
  - Add `.top-nav .global-search input[type="search"]` and `.top-nav button.btn` height equality (e.g. `height: 40px`), matching `border-radius: 6px`, font size, and `.top-nav button.btn { margin-top: 0; display: inline-flex; align-items: center; justify-content: center; }`.
- Regenerate `ui/static/ui/app.css` with `make css`.

## Phases / tasks
1. Create plan doc (this file).
2. Edit `ui/templates/ui/base.html`: insert `.top-nav` before `<nav class="rail">`; remove form from `page-head`.
3. Edit `ui/static/ui/app.src.css`: shell grid areas + `.top-nav` + equal-height input/button overrides.
4. Run `make css` to regenerate `ui/static/ui/app.css`.
5. Run `make check-fast`, `make css-check`, `manage.py check`.
6. Run targeted UI tests: `test_ui_urls_smoke.py`, `test_ui_nav.py`, `test_ui_inline_assets.py`.
7. Present diff to user; commit only on explicit approval.

## Acceptance criteria
- [ ] Base template renders with `.top-nav` containing `#global-search-q` and a submit button.
- [ ] `page-head` no longer contains the global search form.
- [ ] Input and button have identical rendered height (same explicit `height`, border-radius, font size).
- [ ] `/` focuses `#global-search-q` (already covered by `keys.js`; no regression).
- [ ] No inline styles or inline scripts introduced.
- [ ] `make check-fast` and `make css-check` pass.
- [ ] Targeted UI smoke/nav/inline-asset tests pass.

## Risks
- Grid change to `.shell` could affect rail sticky behavior on very short pages; mitigated by keeping `position:sticky; top:0` and testing smoke renders.
- Full-width top bar may make search input very wide on large viewports; mitigated by `max-width` on the form inside the top bar.
- Regenerated `app.css` must be byte-identical to `make css` output; mitigated by running `make css` and `make css-check`.

## Open questions
- None required to proceed; if the top bar should sit inside `main` rather than above the shell, say so and I will adjust.
