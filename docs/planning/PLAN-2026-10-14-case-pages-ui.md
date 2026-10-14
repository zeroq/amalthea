# PLAN 2026-10-14 — Case Pages: create / edit / delete UI + its own card

Status: **draft — awaiting answers to §10 (implementation started on the documented defaults)**
Owner: planner → `django-frontend` (+ `django-backend` for the views/urls)
Related: `PLAN-2026-10-13-case-detail-tabs.md` (§7.4 provisional placement, §13.x fixes 1–10),
`PLAN-2026-10-09-t2-endpoints.md` (P2 page model, deviations P2-6/P2-7), ADR-005 (Markdown/nh3),
ADR-006 (viewer read-only).

---

## 1. Summary

The case detail page can *show* pages but cannot *manage* them: `POST /api/v1/case/{id}/page` exists and
the UI renders whatever the API creates, yet an analyst sitting on the case page has no way to add,
edit or delete a page. This plan adds the three UI affordances and gives Pages a card of its own — today
it is a `section.card` **nested inside the description's card**, so the case narrative and its child
documents share one visual container.

Everything here is UI-only: the `Page` model, the API routes and the `page_json` wire shape are already
in place and are **not** touched.

## 2. Goals

- **G1** Create a page from the case page (title, Markdown body, optional category).
- **G2** Edit a page's title / body / category from the case page.
- **G3** Delete a page with a real confirmation modal (AGENTS.md: no native `confirm()`).
- **G4** Pages become a **top-level card** sibling to the case description card, not a card inside it.
- **G5** Page content rendered as sanitized Markdown (same pipeline as `Case.description`), because a
  page is a document — today it renders as a `<pre>` of raw Markdown.
- **G6** Keep every existing contract: `#case-pages`, `data-page-id="…"`, the `page` WebSocket frame,
  `@not_viewer` on every mutating route, `?tab=` preservation, no new migrations.

## 3. Non-goals

- **N1** No `order` / drag-and-drop reordering UI. `order` stays what the API set; a UI-created page is
  appended (`max(order)+1`).
- **N2** No rich-text/MDX editor; a plain `<textarea>` (consistent with the note composer).
- **N3** No page *detail* page/tab; a page stays a block inside the Pages card.
- **N4** No API changes (`PATCH`/`DELETE` behaviour, wire shapes, scopes, audits untouched).
- **N5** No new tests (standing BINDING directive: tests are frozen).
- **N6** No permission model beyond ADR-006: any non-viewer analyst with case access may edit or delete
  any page of that case.

## 4. Architecture / approach

| Concern | Decision | Rationale |
| --- | --- | --- |
| Transport | plain `POST` → `_case_detail_redirect` (no HTMX) | matches every other write on this page (`case-task-create`, `case-comment`, `case-attachment-delete`); degrades to a normal form |
| Create/edit UI | one shared modal body template `ui/_page_form.html` via `_modal.html` `body_template` | the app's documented create idiom (`_case_create_fields.html`, `_settings_org_create_form.html`); `modal.js` focuses `[autofocus]`, `<dialog>` gives Esc/backdrop/focus-trap |
| Delete UI | per-page confirmation modal | identical to the per-attachment delete modal in `_tab_attachments.html` |
| Markdown | `{{ page.content|markdown }}` inside `<div class="body md">` | ADR-005: CommonMark + `nh3` allow-list, the single XSS boundary; same markup as the description 20 lines above |
| Live sync | live.js **re-fetches** `#case-pages` on a `page` frame instead of hand-building the entry | removes the JS↔template mirror the old code itself flagged as fragile, and renders Markdown that JS cannot |
| Fan-out | UI create/update publish the same `page` frame the API publishes | the "collaborative ledger" promise: a second analyst's open tab refreshes |

### 4.1 Routes (UI)

| Name | Method | Path | View |
| --- | --- | --- | --- |
| `ui-case-page-create` | POST | `/cases/<case_id>/page` | `case_page_create` |
| `ui-case-page-edit` | POST | `cases/<case_id>/page/<page_id>/edit` | `case_page_update` |
| `ui-case-page-delete` | POST | `cases/<case_id>/page/<page_id>/delete` | `case_page_delete` |

All three: `@not_viewer` (outermost) → `@login_required` → `@require_POST`, `resolve_case(user, id,
write=True)`, `get_object_or_404(Page, pk=…, case=case)` (reached *through* its case, so a foreign page
id cannot be addressed), `messages.success`, `_case_detail_redirect(request, case.number)`.

`test_viewer_readonly_ui.py` enumerates the URLconf and POSTs every `ui-` route as a viewer, so the three
new routes are covered the day they land — `@not_viewer` (404) is the gate, no test edit required.

## 5. Data model

**No migration.** `Page` (`cases/models.py:397-426`) already carries `case`, `title` (500),
`content` (TextField, Markdown), `order` (int, default 0), `category` (100), `created_by`, `updated_by`
and `page_case_order_idx (case, order, created_at)`.

Two conventions this plan pins:

- **Ordering.** UI list and API **both** order by `order, created_at`. (`ui/views.py` currently uses
  `order, "-created_at"`, a silent divergence from `cases/views.py` that produces two different orders
  for pages sharing an `order`.)
- **`order` on create**: `max(order)+1` for the case (never reorders an existing page); the API's
  `order` is required, the UI's is derived.

## 5.1 Route contracts

| Route | Method | Gate | Success | Validation failure |
| --- | --- | --- | --- | --- |
| `ui-case-page-create` | POST | `@not_viewer` → 404 for viewers (the route-enumerating test POSTs it as one) | 302 → case page (`?tab=general` preserved) | `title` blank → `messages.error` + 302 (mirrors `case_task_create`); `required` on the input makes this unreachable from a browser |
| `ui-case-page-edit` | POST | `@not_viewer` | 302 → case page | same |
| `ui-case-page-delete` | POST | `@not_viewer` | 302 → case page | — |

All three: `resolve_case(..., write=True)` (404 for a case the analyst cannot write), `Page` reached via
`get_object_or_404(Page, pk=…, case=case)` (pk **and** parent, so a foreign page id cannot be addressed),
CSRF via `{% csrf_token %}` inside the modal footer form (`_modal.html:60`), and a hidden `tab=general`
so the redirect lands back on General.

## 5. API contracts

Unchanged. For reference, the UI writes the same fields the API accepts
(`model` → `Page`):

| Field | Create (UI) | Edit (UI) | Notes |
| --- | --- | --- | --- |
| `title` | required, ≤500 | required, ≤500 | truncated with `[:500]` like the API |
| `content` | optional (Markdown) | optional | stripped; empty → falls back to today's "no body" rendering |
| `category` | optional, ≤100 | optional, ≤100 | free text; rendered as a dim chip |
| `order` | `max(order)+1` | untouched | see N1 |

## 6. UI/UX

Structure of the General tab after this change (three sibling cards, no nesting):

```
<section class="card">            ← case metadata + status control + description + warnings
<section class="card page-card">  ← NEW: Pages, own card, header row + list + create modal
<section class="card">            ← Tags
```

Files:

- `ui/templates/ui/_tab_general.html` — un-nest Pages **and** Tags from inside the meta card; Pages gains
  a `.page-row` header (`<h2 id="h-pages">Pages <span class="count">N</span></h2>` + **New page** button)
  and the create modal.
- `ui/templates/ui/_page_entry.html` — title + per-page actions (Edit / Delete) in a `.page-row`,
  Markdown body in `<div class="body md">`, meta line extended to `category · created · author · edited by`.
- `ui/templates/ui/_page_form.html` — **new**: the shared create/edit modal body (`title`, `category`,
  `content`). Mirrors `_case_create_fields.html`: every control carries `form="{{ form_id }}"` when
  `form_id` is set (the modal footer owns the submit), and the body carries the hidden `tab=general`.
- `ui/static/ui/live.js` — `refreshPages()` replaces `pageEntry()`/`appendPage()`.
- `ui/static/ui/app.css` — 4 appended rules (`.card > .page-row`, `.page-row`, `.page-row .btn`).

Keyboard/accessibility:

- Every action is a real button/form reachable by Tab; `Esc` closes a modal (`<dialog>`), `Tab` is trapped
  (`modal.js`), the backdrop click closes unless `data-modal-static`.
- Icon-only controls get an accessible name; icons supplement labels, never replace them
  (`aria-label="Edit page: <title>"` on the two icon buttons is redundant with the visible text, so the
  buttons keep **visible** labels plus a Font Awesome icon — AGENTS.md §6).
- The page title is an `<h3>`-equivalent `<p class="page-title">` (unchanged) — the card heading stays
  `<h2 id="h-pages">`, preserving the existing outline.

Mockup: `mockup/case-general-pages.html` (scratch, outside the repo) — open directly in a browser; it links
the real `app.css`/`modal.css` so what it shows is what ships.

## 7. Phases & acceptance criteria

### P1 — routes + views (`ui/urls.py`, `ui/views.py`)

| # | Task | AC |
| --- | --- | --- |
| 1.1 | 3 routes named `ui-case-page-create` / `-edit` / `-delete` | `manage.py check` clean; every route `@not_viewer @login_required @require_POST` |
| 1.2 | `case_page_create` (title required, `max(order)+1`, `created_by`/`updated_by`) | creating a page on a case redirects to the case page and the page renders in the card |
| 1.3 | `case_page_update` (title/content/category, `updated_by`, `update_fields`) | edit is visible after redirect; `updated_by`/`updated_at` change; **no** schema change |
| 1.4 | `case_page_delete` | page is gone after redirect; deleting a page of another case 404s |
| 1.5 | viewer denial (implied by 1.1) | `test_viewer_readonly_ui.py` stays green: a viewer POST to each route answers 404 |
| 1.6 | UI create/update publish `page_json` on commit | a second WS client sees a `page` frame (same shape as the API's) |

### P2 — templates

| # | Task | AC |
| --- | --- | --- |
| 2.1 | Un-nest Pages (and Tags) into sibling cards | `#case-pages` is **not** inside the card that holds `#h-meta`'s description; `test_ui_loop.py:404-405` unaffected |
| 2.2 | Create modal + header button, `can_mutate` only | no "New page" control for a viewer; modal opens with focus in Title |
| 2.3 | `_page_entry.html`: Markdown body + Edit/Delete + extended meta | content renders `<p>`/`<strong>` etc.; `data-page-id` preserved |
| 2.4 | `_page_form.html` shared by create and edit | edit modal is prefilled; both submit to their own route with `tab=general` |

### P3 — live + CSS

| # | Task | AC |
| --- | --- | --- |
| 3.1 | `live.js` `refreshPages()` on a `page` frame | an API-created page appears with rendered Markdown, without a manual reload; no `innerHTML` sink |
| 3.2 | appended CSS | the card heading keeps its uppercase dim look; the button row has no stray `margin-top:14px` |

### P4 — verification

| # | Task | AC |
| --- | --- | --- |
| 4.1 | throwaway harness: render the real case page, assert card nesting + modal hit-tests | documented evidence in §11 |
| 4.2 | `ruff format --check`, `ruff check`, `mypy`, `manage.py check` | all clean |

## 8. Security

- **S1 Authorization**: all three routes `@not_viewer` (ADR-006) + `resolve_case(..., write=True)` +
  `get_object_or_404(..., case=case)`. A viewer gets a uniform 404; a cross-tenant id gets a 404.
- **S2 XSS**: page content is analyst-authored and rendered with `|markdown` → `ui.markdown.render_markdown`
  (CommonMark, raw HTML escaped, `nh3` allow-list, ADR-005). No `|safe`, no `innerHTML` in `live.js`
  (`refreshPages` parses fetched HTML with `DOMParser` and swaps a node it did not author — the string is
  server-rendered by the same template, not attacker-supplied markup).
- **S3 CSRF**: the modal footer form carries `{% csrf_token %}`; no new JSON API surface.
- **S4 CSP**: unchanged; the new `fetch` is same-origin (`connect-src 'self'`), no inline script.
- **S5 Enumeration**: uniform 404s (existence is not distinguishable from "not yours").
- **S6 Audit**: UI writes are *not* `AuditLog`ged today (the case audit log records only
  `case.created`/`case.updated`). A page edit/delete is therefore invisible in the Audit tab — recorded
  as R3, not fixed here.

## 9. Testing

- **No new tests** (frozen). The suite that must stay green:
  `test_ui_loop.py::test_case_pages_are_rendered_on_the_case_page` (`#case-pages`, `data-page-id`, title,
  content text — Markdown rendering keeps the raw text as a substring), `test_ui_loop.py` (task/add-note
  flows), `test_realtime.py` (script tag, `?tab=` preservation), `test_ui_p1_wiring.py`,
  `test_viewer_readonly_ui.py` (route enumeration — new routes are covered the day they are added),
  `test_t2_p2_surface.py` (API unchanged), `test_ui_markdown.py` (the filter is unchanged).
- Verification harness (scratch, outside `tests/`): render the real case page through the Django test
  client, assert (a) the Pages card is a sibling of the description card, (b) the create/edit/delete
  controls and modals are present for an analyst and absent for a viewer, (c) headless-browser hit-tests
  for the modal (reusing the fix-#9/#10 harness).

## 10. Risks & open questions

| # | Risk / question | Mitigation / recommendation |
| --- | --- | --- |
| R1 | A page created by the **API** while the tab is open used to be appended by JS; it is now re-fetched | one extra full page render per page frame; collapsed if several arrive at once |
| R2 | Delete publishes no frame (API parity) → another analyst sees the deletion on their next reload | publish a frame from the UI? **Q6** |
| R3 | Page edits/deletes are not in the Audit tab | pre-existing (case audit covers only create/update); note in `TODO.md` |
| R4 | `_page_entry.html` had to stay byte-compatible with `pageEntry()`; that mirror is now gone | removing the mirror *is* the mitigation |
| R5 | A viewer must not see the write controls | every affordance wrapped in `{% if can_mutate %}` |
| Q1 | Pages: own card on **General** (default) or an own tab? | stay on General (§7.4) |
| Q2 | Create/edit via **modal** (default) or an inline always-visible form like the note composer? | modal, app idiom |
| Q3 | Render page content as **Markdown** (default)? | yes — matches the description and ADR-005 |
| Q4 | Expose `category` (default yes) and `order` (default no)? | as stated |
| Q5 | Un-nest the **Tags** card too (default yes, same smell, 1 line)? | as stated |
| Q6 | Should the UI publish `page` frames on edit/delete? | create+edit: yes; delete: ask |

## 11. Deviations log

_(filled in during implementation; deviations from this plan are recorded here and in
`docs/spec/deviations.md`.)_

### Deviations from this plan

| # | Deviation | Why |
| --- | --- | --- |
| P2-7 | `live.js` lost `pageEntry()`/`appendPage()` (the plan assumed the mirror stayed) | the mirror was byte-coupled to `_page_entry.html`; `refreshPages()` replaces both, so deleting it removes a whole class of drift |
| P2-8 | The **Tags** card was un-nested too (Q5 default) | same smell, one line, and the two cards now share the `.page-row` header shape |
| P3-3 | UI **create + edit** publish a `page` frame; **delete** does not | API parity for delete; **Q6** still open |
| P3-4 | `category` is exposed in the form, `order` is not | `order` is API-managed; reordering UI is N1 |
| Q6-my | `ui/` sits outside the pinned mypy scope (`pyproject.toml` `files`), so the new views are not type-checked by `make check` | pre-existing scope decision, not changed here; `mypy ui/views.py` reports only 4 pre-existing settings-view errors |

### Defects found during verification (fixed here, in scope)

| # | Defect | Fix |
| --- | --- | --- |
| F11 | A `<dialog>` is **shrink-to-fit**: `size="lg"` modals only set `max-width`, so with no `size`/`cols` on their controls they rendered **270px wide with a 177px Markdown textarea** (hit every `_modal.html` modal: new case, new user, page form, org delete) | `.modal--sm/md/lg/xl` now set a real `width` plus `max-width: 100%` (`modal.css`) — measured 560/400 afterwards, still inside a 482px viewport |
| F12 | An over-tall modal (page form: 3 fields + an 8-row textarea) exceeded the viewport; with `inset: 0` + `margin: auto` an over-tall box loses its auto margins and is pinned to the top, so the footer's confirm button was **off-screen with nothing to scroll** | `.modal-content` capped at `calc(100dvh - 2rem)` (with a `vh` fallback) and `.modal-body` made the only scroll region, so header + footer stay on screen |
| F13 | `app.css` referenced `--color-focus`, **defined nowhere in the repo** → `test_ui_css_tokens.py::test_every_color_token_used_is_defined` fails, and the invalid `var()` drops the declaration, so `.tab`/`.tabpanel` had **no focus ring** (WCAG 2.4.7 / AGENTS §6) | both rules use `--color-accent`, the house focus token |
| F14 | `.tabpanel:focus { outline: none }` → `test_ui_css_tokens.py::test_no_rule_removes_the_focus_outline` fails, and it outranked the global `:focus-visible` ring for Tab-reachable panels (`tabindex="0"`) | rule removed; `:focus-visible` keeps the ring. **Gotcha:** that guard scans the whole file *including comments* — never write the forbidden declaration even in a comment |
| F15 | The case detail page carried a **page-level horizontal scrollbar** at laptop widths (≈1174px): `.main` is a grid item with the automatic minimum size, and the 8-tab strip's min-content (`.tab` is `nowrap`) exceeded the column, so the strip's own `overflow-x: auto` never got to work | `.main { min-width: 0 }` in `app.css`; the strip scrolls itself as designed (verified: no page overflow at 1174/982, strip scrolls) |

### Verification evidence (all local, scratch harnesses outside `tests/`)

- `verify_pages.py` — 63 checks against the real rendered page: card nesting, Markdown, the three
  modals and their form associations, create/edit/delete round-trips, `order`/`updated_by` semantics,
  foreign-case + cross-tenant 404s, viewer reads without controls and 404s on every POST, empty state.
- `verify_pages_browser.py` — 31 checks in headless Edge on that same page: each modal opens,
  autofocuses its title, submits through its own footer form, closes on a backdrop click; the create
  form's `required` title blocks an empty submit; footer button on screen; body scrolls; centred.
- `verify_modal_widths.py` — every `dialog.modal` on `/cases/1`, `/cases`, `/settings/users`,
  `/settings/organisations` (7 dialogs) at 1174px and 982px: exact intended width, centred (`dx=dy=0`),
  inside the viewport, footer visible; plus a **12-page overflow sweep** (dashboard, alerts, observables,
  case templates, tags, taxonomy, automation, sources, cases, case detail, settings users/organisations)
  with no page-level horizontal overflow at either width — i.e. `.main { min-width: 0 }` (F15) fixed the
  case page without exposing overflow on the other nine page types.
- `make css-check` clean (the guard is a compile check, not a drift check — the appended block lives in
  the compiler's own input, so it survives `make css`); the two `app.css` guards above reimplemented
  statically: both pass.
- `ruff format .` (278 files unchanged), `ruff check .`, bare `mypy` (107 files), `manage.py check`:
  clean. Only CSS/JS changed after the last Python change, so these are unchanged.

