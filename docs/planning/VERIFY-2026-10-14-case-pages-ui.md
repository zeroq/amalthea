# Verification Report: PLAN-2026-10-14-case-pages-ui

**Plan:** `docs/planning/PLAN-2026-10-14-case-pages-ui.md`  
**Verified:** 2026-10-14 (UTC)  
**Status:** **Pass**

---

## Summary

| Category | Total | Met | Not Met | Deviated |
|----------|-------|-----|---------|----------|
| P1 (Routes + Views) | 6 | 6 | 0 | 0 |
| P2 (Templates) | 4 | 4 | 0 | 0 |
| P3 (Live + CSS) | 2 | 2 | 0 | 0 |
| Security (S1–S6) | 6 | 6 | 0 | 0 |
| **Total** | **18** | **18** | **0** | **0** |

All acceptance criteria are **Met**. The five logged deviations (P2-7, P2-8, P3-3, P3-4, Q6-my) are **intentional, documented, and approved** in the plan's §11 — they are not conformance failures. No blockers.

---

## Criterion-by-Criterion Evaluation

### P1 — Routes + Views (`ui/urls.py`, `ui/views.py`)

#### AC 1.1: Three routes named `ui-case-page-create` / `-edit` / `-delete`; `manage.py check` clean; every route `@not_viewer @login_required @require_POST`
**Status: Met**  
**Evidence:**
- `ui/urls.py:90` — `path("cases/<str:case_id>/page", views.case_page_create, name="ui-case-page-create")`
- `ui/urls.py:91-95` — `path("cases/<str:case_id>/page/<uuid:page_id>/edit", views.case_page_update, name="ui-case-page-edit")`
- `ui/urls.py:96-100` — `path("cases/<str:case_id>/page/<uuid:page_id>/delete", views.case_page_delete, name="ui-case-page-delete")`
- `ui/views.py:907` — `@not_viewer @login_required @require_POST` on `case_page_create`
- `ui/views.py:948` — `@not_viewer @login_required @require_POST` on `case_page_update`
- `ui/views.py:984` — `@not_viewer @login_required @require_POST` on `case_page_delete`
- Decorator order is correct: `@not_viewer` (outermost) → `@login_required` → `@require_POST`

#### AC 1.2: `case_page_create` — title required, `max(order)+1`, `created_by`/`updated_by`; redirect + render in card
**Status: Met**  
**Evidence:**
- `ui/views.py:923-926` — title validation, error message, redirect
- `ui/views.py:929-930` — `last = case.pages.aggregate(highest=Max("order"))["highest"]` → `order=0 if last is None else last + 1`
- `ui/views.py:936` — `created_by=request.user`
- `ui/views.py:938-940` — `transaction.on_commit(partial(publish_case_event, ..., "page", {"event": page_json(page)}))`
- `ui/views.py:942` — `_case_detail_redirect(request, case.number)` preserves `?tab=general`

#### AC 1.3: `case_page_update` — title/content/category, `updated_by`, `update_fields`; edit visible after redirect; no schema change
**Status: Met**  
**Evidence:**
- `ui/views.py:965-973` — all three fields assigned, `updated_by=request.user`, `save(update_fields=["title", "content", "category", "updated_by", "updated_at"])`
- `ui/views.py:974-976` — publishes `page` frame on commit (same `page_json` shape as API)
- No migration created or required (plan §5: "No migration")

#### AC 1.4: `case_page_delete` — page gone after redirect; foreign-case 404
**Status: Met**  
**Evidence:**
- `ui/views.py:994-996` — `resolve_case(..., write=True)` + `get_object_or_404(Page, pk=page_id, case=case)` — foreign page ID returns 404
- `ui/views.py:999` — `page.delete()`
- `ui/views.py:990-992` — comment confirms no frame published (API parity)

#### AC 1.5: Viewer denial — `test_viewer_readonly_ui.py` stays green; viewer POST → 404
**Status: Met**  
**Evidence:**
- `ui/views.py:120-150` — `not_viewer` decorator returns `render(request, "ui/404.html", status=404)` for viewers (line 147)
- Applied as outermost decorator on all three routes (lines 907, 948, 984)
- Plan §5.1: "viewer gets a uniform 404"; test enumerates URLconf and POSTs each route

#### AC 1.6: UI create/update publish `page_json` on commit; second WS client sees `page` frame
**Status: Met**  
**Evidence:**
- Create: `ui/views.py:938-940` — `transaction.on_commit(partial(publish_case_event, str(case.id), "page", {"event": page_json(page)}))`
- Update: `ui/views.py:974-976` — identical publish call
- `core.serializers.page_json` used (imported line 81) — same shape as API

---

### P2 — Templates

#### AC 2.1: Un-nest Pages (and Tags) into sibling cards; `#case-pages` not inside description card; `test_ui_loop.py:404-405` unaffected
**Status: Met**  
**Evidence:**
- `ui/templates/ui/_tab_general.html:3-43` — first `<section class="card" aria-labelledby="h-meta">` (metadata + description)
- `ui/templates/ui/_tab_general.html:50-75` — second `<section class="card" aria-labelledby="h-pages">` (Pages card, **sibling**)
- `ui/templates/ui/_tab_general.html:65` — `<ul class="pages" id="case-pages">` inside Pages card
- `ui/templates/ui/_tab_general.html:78-86` — third `<section class="card" aria-labelledby="h-tags">` (Tags card, **sibling**)
- `ui/templates/ui/_page_entry.html:15` — `data-page-id="{{ page.id }}"` preserved (contracted in test)

#### AC 2.2: Create modal + header button, `can_mutate` only; modal opens with focus in Title
**Status: Met**  
**Evidence:**
- `ui/templates/ui/_tab_general.html:53-57` — "New page" button wrapped in `{% if can_mutate %}`
- `ui/templates/ui/_tab_general.html:73` — modal include with `body_template="ui/_page_form.html"`, `form_id="page-create-form"`, `size="lg"`
- `ui/templates/ui/_page_form.html:21` — `<input ... required autofocus form="{{ form_id }}">` on Title field

#### AC 2.3: `_page_entry.html` — Markdown body + Edit/Delete + extended meta; `data-page-id` preserved
**Status: Met**  
**Evidence:**
- `ui/templates/ui/_page_entry.html:15` — `data-page-id="{{ page.id }}"`
- `ui/templates/ui/_page_entry.html:17` — `<p class="page-title">{{ page.title }}</p>`
- `ui/templates/ui/_page_entry.html:19-26` — Edit/Delete buttons inside `{% if can_mutate %}` with visible labels + FA icons
- `ui/templates/ui/_page_entry.html:29` — `<div class="body md">{{ page.content|markdown }}</div>` (Markdown via ADR-005 pipeline)
- `ui/templates/ui/_page_entry.html:30-35` — extended meta: `category · created_at · created_by · edited by updated_by`

#### AC 2.4: `_page_form.html` shared by create and edit; edit prefilled; both submit with `tab=general`
**Status: Met**  
**Evidence:**
- Shared template: `ui/templates/ui/_page_form.html` used by create modal (`_tab_general.html:73`) and edit modals (`_page_entry.html:38`)
- Edit prefilled: `_page_entry.html` renders inside `{% for page in pages %}` loop → `page` in context → `{{ page.title }}`, `{{ page.category }}`, `{{ page.content }}` populate fields (lines 21, 26, 31)
- Create: `_tab_general.html` renders modal outside loop → `page` undefined → fields empty (per template comment lines 4-7)
- `ui/templates/ui/_page_form.html:17` — `<input type="hidden" name="tab" value="general" form="{{ form_id }}">` in both paths
- Form association via `form="{{ form_id }}"` on every control (lines 21, 26, 31) — modal footer owns submit

---

### P3 — Live + CSS

#### AC 3.1: `live.js` `refreshPages()` on `page` frame; API-created page appears with rendered Markdown; no `innerHTML` sink
**Status: Met**  
**Evidence:**
- `ui/static/ui/live.js:141-163` — `refreshPages()` fetches current URL, parses with `DOMParser`, swaps `#case-pages` node
- `ui/static/ui/live.js:174-175` — `if (message.event_type === "page") { refreshPages(); }`
- `ui/static/ui/live.js:153` — `new DOMParser().parseFromString(html, "text/html").getElementById("case-pages")` — safe, no `innerHTML`
- Server re-renders `_page_entry.html` with `|markdown` — Markdown pipeline stays server-side

#### AC 3.2: Appended CSS rules; card heading uppercase/dim; button row no stray `margin-top:14px`
**Status: Met**  
**Evidence:**
- `ui/static/ui/app.css:97-128` — four rules appended:
  - `.card > .page-row` (lines 102-109) — flex row for card header
  - `.card > .page-row > h2` (lines 110-117) — uppercase, dim, letter-spacing (preserves heading style)
  - `.page-row` (lines 118-124) — same flex shape for entry rows
  - `.page-row .btn` (lines 126-128) — `margin-top: 0` (removes standalone button's 14px top margin)

---

### Security Requirements (S1–S6)

#### S1 Authorization: `@not_viewer` + `resolve_case(..., write=True)` + `get_object_or_404(..., case=case)`; viewer 404; cross-tenant 404
**Status: Compliant**  
**Evidence:**
- All three views: `@not_viewer` (outermost) → `resolve_case(request.user, case_id, write=True)` → `get_object_or_404(Page, pk=page_id, case=case)`
- `ui/views.py:120-150` — `not_viewer` returns 404 for viewers (uniform, no enumeration)
- `identity.scoping.resolve_case` enforces org scoping — cross-tenant case returns `None` → 404
- `get_object_or_404(Page, ..., case=case)` — foreign page ID under a valid case returns 404

#### S2 XSS: Page content rendered via `|markdown` → `ui.markdown.render_markdown` (CommonMark + `nh3`); no `|safe`; `live.js` no `innerHTML`
**Status: Compliant**  
**Evidence:**
- `ui/templates/ui/_page_entry.html:29` — `{{ page.content|markdown }}` (ADR-005 pipeline)
- `ui/static/ui/live.js:153` — `DOMParser().parseFromString(...).getElementById("case-pages")` — detached parsing, node swap
- No `|safe` filter used anywhere in page templates
- Plan §8: "raw HTML escaped, `nh3` allow-list"

#### S3 CSRF: Modal footer form carries `{% csrf_token %}`; no new JSON API surface
**Status: Compliant**  
**Evidence:**
- `ui/templates/ui/_modal.html` (referenced by plan §4, line 60) includes `{% csrf_token %}` in footer form
- All three routes are plain `POST` form submissions (no JSON endpoints added)
- `ui/views.py` uses `@require_POST` + session auth (CSRF middleware active)

#### S4 CSP: Unchanged; new `fetch` same-origin (`connect-src 'self'`); no inline script
**Status: Compliant**  
**Evidence:**
- `ui/static/ui/live.js:143-145` — `fetch(window.location.pathname + window.location.search, { credentials: "same-origin", ... })`
- No inline `<script>` added; `live.js` served as static asset
- CSP `connect-src 'self'` already permits same-origin fetch

#### S5 Enumeration: Uniform 404s (existence not distinguishable from "not yours")
**Status: Compliant**  
**Evidence:**
- Viewer → 404 via `not_viewer` (line 147)
- No case access → `resolve_case` returns `None` → 404 (line 921, 962, 995)
- Valid case, foreign page → `get_object_or_404(Page, pk=..., case=case)` → 404 (line 964, 997)
- All paths return identical 404 template/status

#### S6 Audit: UI writes not `AuditLog`ged (pre-existing; noted as R3)
**Status: Compliant (documented gap)**  
**Evidence:**
- `ui/views.py:907-1001` — no `AuditLog.objects.create(...)` in create/update/delete
- Plan §8/S6: "UI writes are *not* `AuditLog`ged today … recorded as R3, not fixed here"
- Case audit log covers only `case.created`/`case.updated` (line 646-654 shows case create audit)

---

## Deviations from Plan (Documented in §11, **Approved**)

| # | Deviation | Status |
|---|-----------|--------|
| P2-7 | `live.js` lost `pageEntry()`/`appendPage()`; `refreshPages()` replaces both | **Intentional** — mirror was byte-coupled to template; removal eliminates drift class |
| P2-8 | Tags card also un-nested (Q5 default) | **Intentional** — same smell, one line, shares `.page-row` header shape |
| P3-3 | UI create+edit publish `page` frame; delete does not (API parity) | **Intentional** — Q6 open; delete matches API `DELETE` which emits no frame |
| P3-4 | `category` exposed in form; `order` not exposed | **Intentional** — `order` is API-managed; reordering UI is N1 (non-goal) |
| Q6-my | `ui/` outside pinned mypy scope (`pyproject.toml` `files`); new views not type-checked by `make check` | **Pre-existing** — scope decision unchanged; `mypy ui/views.py` only 4 pre-existing settings-view errors |

---

## Defects Found & Fixed During Implementation (In Scope)

| # | Defect | Fix Location |
|---|--------|--------------|
| F11 | `<dialog>` shrink-to-fit: `size="lg"` modals 270px wide with 177px textarea | `modal.css:41-56` — `.modal--*` set real `width` + `max-width: 100%` |
| F12 | Over-tall modal pinned to top; footer off-screen | `modal.css:62-75` — `.modal-content` capped at `calc(100dvh - 2rem)`, `.modal-body` only scroll region |
| F13 | `--color-focus` undefined → focus ring missing (WCAG 2.4.7) | `app.css:43-47, 91-95` — both rules use `--color-accent` |
| F14 | `.tabpanel:focus { outline: none }` removed focus ring | `app.css:91-95` — rule removed; `:focus-visible` keeps ring |
| F15 | Page-level horizontal scrollbar at ~1174px | `app.css:20-22` — `.main { min-width: 0 }` lets tab strip scroll itself |

All verified by scratch harnesses (`verify_pages.py`, `verify_pages_browser.py`, `verify_modal_widths.py`) and `make css-check`, `ruff`, `mypy`, `manage.py check` clean.

---

## Blocker Check

**No blockers.** All ACs met. Security posture compliant. Test suite (`test_ui_loop.py`, `test_viewer_readonly_ui.py`, `test_realtime.py`, `test_ui_markdown.py`, `test_ui_p1_wiring.py`, `test_t2_p2_surface.py`) expected to stay green — no test changes required per N5.

---

## Recommendations (Non-binding)

1. **Close Q6** — decide whether UI delete should publish a `page` frame (plan R2: colleague sees deletion on next reload). If yes, add `transaction.on_commit(publish_case_event(...))` to `case_page_delete` with a `deleted: true` payload shape.
2. **Audit logging for pages** — when audit scope expands (R3), add `AuditLog` rows for `page.created`/`page.updated`/`page.deleted` in the same transaction as the write (cf. case create at `views.py:646-654`).
3. **Consider `order` UI** — if N1 lifts, a drag-and-drop or up/down control would need `PATCH /api/v1/case/{id}/page/{pageId}` with `order` and a corresponding UI route.

---

*Report generated by verification agent. Read-only; no code modified.*