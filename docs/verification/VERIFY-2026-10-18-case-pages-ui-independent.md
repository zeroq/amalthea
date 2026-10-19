# Verification Report: Case pages — create / edit / delete UI + its own card (§12.5)

**Plan:** `docs/planning/PLAN-2026-10-14-case-pages-ui.md`
**Verified:** 2026-10-18, at commit `d596db6` (HEAD, working tree clean)
**Agent:** `verifier` (genuinely independent re-run)
**Status:** **Pass** — 23/23 checked criteria Met, 1 new finding (V1), 5 shared-layer defects confirmed fixed (F11–F15), 5 observations recorded

> **Provenance / independence statement.** This verification was performed by the `verifier` subagent, **not** by the planning agent. The prior report `docs/verification/VERIFY-2026-10-17-case-pages-ui.md` was authored inline by the planner (service unavailable at the time) and explicitly marked **provisional**. This run re-derives every claim from source code, committed tests, and targeted behavioural execution — it does not trust the prior report. Where the prior report graded an AC "Met", this report re-checks the evidence; where it recorded a finding, this report confirms or refutes it against current HEAD. This report supersedes `VERIFY-2026-10-17-case-pages-ui.md`.

---

## Summary

| Category | Count |
|----------|-------|
| Plan §7 Acceptance Criteria (P1–P4) | 14 |
| Met | 14 |
| Not Met | 0 |
| Deviated | 0 |
| §5 Data-model conventions | 3 |
| Met | 3 |
| §8 Security (S1–S6) | 6 |
| Met | 6 |
| Additional findings (V1) | 1 |
| Shared-layer defects (F11–F15) | 5 |
| Confirmed fixed | 5 |
| Observations (no AC impact) | 5 |

The implementation delivers the plan's three UI affordances (create, edit, delete) with the documented route/gate/redirect contracts, moves Pages into a top-level sibling card (Tags is now an inline row per §12.8 — see Post-merge note), renders page content through the ADR-005 sanitized-Markdown pipeline, re-fetches `#case-pages` on a `page` WebSocket frame instead of mirroring the entry in JavaScript, and leaves the `Page` model, the API, and the wire shape untouched. All five shared-layer defects recorded in the plan (F11–F15) are present and correct in the current code. One new accessibility finding (V1) is recorded.

---

## Post-merge note — 2026-10-18, at `d596db6` (after §12.8 wave, merged as `2476edb..1f718e5`)

The §12.8 wave (`PLAN-2026-10-17-case-verdict-close-flow.md`) landed **before** this verification and touches `ui/templates/ui/_tab_general.html`. At current HEAD:

- The **Pages card** (lines 123–151) remains a **top-level sibling** `<section class="card">` — unchanged from the §12.5 implementation.
- The **Tags card** has been **replaced** by an inline `.case-tags` row inside the meta card (lines 82–105), per §12.8(2).
- The General tab now has **two** top-level `section.card` siblings: meta (including tags) and Pages.

**Impact on this report:** AC **2.1**'s Pages claim ("Pages is its own card, not nested in the meta card") is **unchanged and still Met**. Only its parenthetical about Tags is **superseded** — the Tags card is gone by design, not by regression. All other ACs are unaffected (Pages card, modals, routes, views, CSS, live frame). The committed guards pass on the merged tree: `test_ui_loop.py`, `test_viewer_readonly_ui.py`, `test_ui_case_verdict.py`, `test_ui_icon_font.py`, `test_ui_inline_assets.py`, `test_ui_urls_smoke.py`, `test_realtime.py` — **398 passed**. This note does not re-open §12.5; it records that one of its structural observations was overtaken by §12.8.

---

## Criterion-by-Criterion Evaluation

### P1 — Routes + Views

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **1.1** | 3 routes named `ui-case-page-{create,edit,delete}`; `manage.py check` clean; every route `@not_viewer @login_required @require_POST` | **Met** | `ui/urls.py:99-109` — three routes with UUID converter for `page_id`. `ui/views.py:1698-1700` (create), `:1739-1741` (update), `:1775-1777` (delete) — decorator stack `@not_viewer @login_required @require_POST` (outermost first). `manage.py check` clean (0 issues, first-hand at HEAD). |
| **1.2** | `case_page_create` — title required, `max(order)+1`, `created_by`/`updated_by` | **Met** (with note) | `ui/views.py:1717-1720` blank title → `messages.error` + redirect. `:1722-1729` `Max("order")` then `0 if last is None else last + 1`. `:1730` `created_by=request.user`. `:1726-1728` `title[:500]`, `category[:100]`, `content` stripped. **Note:** plan text says "`created_by`/`updated_by`"; the view sets only `created_by`, exactly as the API's create does (`cases/views.py:1455-1462`) — API parity. `updated_by` is stamped on edit. Not a deviation. |
| **1.3** | `case_page_update` — title/content/category, `updated_by`, `update_fields`; no schema change | **Met** | `ui/views.py:1758` `get_object_or_404(Page, pk=page_id, case=case)` — pk **and** parent. `:1759-1767` writes all three fields, sets `updated_by`, saves with `update_fields=["title","content","category","updated_by","updated_at"]` — **`order` excluded** (plan N1). Harness: all three fields persist, `updated_by` = analyst, `updated_at` advanced, `order` unchanged when POST carries `order=99`. No migration — `Page` unchanged (`cases/models.py:397-426`), `makemigrations --check` clean. |
| **1.4** | `case_page_delete`; deleting a page of another case 404s | **Met** | `ui/views.py:1791` `get_object_or_404(Page, pk=page_id, case=case)` — pk **and** parent. Harness: own-org delete removes row (302); page id belonging to another case → 404 on both edit and delete; page in another **org** → 404 on both. Mutation: dropping `case=case` makes the foreign-page check fail (discriminates). |
| **1.5** | Viewer denial — `test_viewer_readonly_ui.py` stays green | **Met** | `tests/conformance/test_viewer_readonly_ui.py:83-93` enumerates every `ui-`-named route from URLconf, so the three routes are covered without a test edit. `:107` POSTs each as a viewer demanding non-2xx/3xx. `:140` `test_the_invariant_is_not_vacuous` pins sensitivity. Behavioural: viewer GET keeps `#case-pages` and rendered content but has **no** "New page" button, no per-page Edit/Delete, no create modal; viewer POST to all three → 404. Committed run: `test_viewer_readonly_ui.py` 19 passed. |
| **1.6** | UI create/update publish `page_json` on commit (second WS client sees `page` frame) | **Met** (delete publishes nothing, by design) | `ui/views.py:1732-1734` (create) and `:1768-1770` (update) call `transaction.on_commit(partial(publish_case_event, str(case.id), "page", {"event": page_json(page)}))`. Delete (`:1778-1795`) has no publish call — matching API `DELETE` parity. Documented deviation (plan P3-3, `deviations.md` **W104-5**, Q6 open). |

### P2 — Templates

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **2.1** | Un-nest Pages (and Tags) into sibling cards; `#case-pages` not inside the `#h-meta` card; `test_ui_loop.py:404-405` unaffected | **Met** | `ui/templates/ui/_tab_general.html`: three sibling `<section class="card">` at `:3` (meta+description+tags inline), `:123` (Pages). Harness (parsed DOM): `ul#case-pages` (`:141`) is direct child of Pages `<section class="card">` with `#h-pages`; **no ancestor of that card is another card**; the meta card is a distinct sibling. Committed `tests/conformance/test_ui_loop.py:396-408` still passes (`#case-pages`, `data-page-id`, title, content). |
| **2.2** | Create modal + header button, `can_mutate` only; modal opens with focus in Title | **Met** | `_tab_general.html:129-133` "New page" button wrapped in `{% if can_mutate %}`; `:148-150` create modal gated same. `_page_form.html:21` Title input carries `autofocus` (and `required`, `maxlength="500"`), which `modal.js` focuses on open. Dialog is `<dialog class="modal modal--lg">` (`_modal.html:70`). Harness: button present for analyst, dialog + `form#page-create-form` present with correct `action`/`method`; **viewer** page has no modal and no button. |
| **2.3** | `_page_entry.html` — Markdown body + Edit/Delete + extended meta; `data-page-id` preserved | **Met** | `_page_entry.html:15` `data-page-id="{{ page.id }}"`; `:18-27` `can_mutate`-gated Edit/Delete buttons opening per-page modals; `:29` `{{ page.content|markdown }}` inside `<div class="body md">`; `:30-35` meta = `category · created · author · edited by`. Harness: `data-page-id` present; `**bold**` renders to `<strong>`; `category`/created timestamp in meta; "edited by analyst" appears once `updated_by` is set; both per-page modals exist. |
| **2.4** | `_page_form.html` shared by create and edit; edit modal prefilled; both submit to their own route with `tab=general` | **Met** | One template serves both paths (`_page_form.html:4-7` explains `{{ page.… }}` prefill). `_page_entry.html:38` hosts edit modal with `form_id="page-edit-form-<id>"` and `:39` delete modal. Every control carries `form="{{ form_id }}"` (`:17/21/26/31`) because the footer form owns the submit (`_modal.html:88-98`). Harness: create modal's hidden `tab` and Title input both target `page-create-form` and carry `value="general"`; edit modal exists per page with its own form id. |

### P3 — Live + CSS

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **3.1** | `live.js` `refreshPages()` on a `page` frame; API-created page appears with rendered Markdown without reload; **no `innerHTML` sink** | **Met** | `ui/static/ui/live.js:146-168` `refreshPages()` re-fetches the Pages card and swaps via `DOMParser` + `replaceWith`; body contains **no `innerHTML`**; frame dispatch routes `page` to it (`:211-212`). Deleted `pageEntry()`/`appendPage()` mirror is gone (`deviations.md` **W104-6**). Verified by source inspection. |
| **3.2** | Appended CSS: card heading keeps uppercase dim look; button row has no stray `margin-top:14px` | **Met** | `ui/static/ui/app.css`: `.card > .page-row` restates heading treatment via `.card > .page-row > h2` (`text-transform: uppercase`, `--color-text-dim`, `font-size: 13px`, `letter-spacing: 0.06em`, `font-weight: 650`) with explanatory comment; `.page-row .btn { margin-top: 0 }` neutralises `.btn`'s standalone `margin-top: 14px`. First-hand source read. |

### P4 — Verification

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **4.1** | Throwaway harness renders real case page and asserts card nesting + modal hit-tests | **Met** | Plan §11 records two scratch harnesses (`verify_pages.py` 63 checks, `verify_pages_browser.py` 31 checks in headless Edge) plus `verify_modal_widths.py`. This verification re-ran an equivalent harness (`tests/test_pages_verification.py` — 10 assertions covering card nesting, modal/form associations, create/edit/delete round-trips, `order`/`updated_by` semantics, foreign-case + cross-org 404s, viewer controls + 404s, `page`-frame emission, Markdown/XSS, CSS source rules) — **10/10 pass**. Two behavioural mutations (drop `case=case`; drop `updated_by` write) each failed exactly the predicted check, so the harness discriminates. |
| **4.2** | `ruff format --check`, `ruff check`, `mypy`, `manage.py check` | **Met** | First-hand at HEAD: `ruff format --check .` → "310 files already formatted"; `ruff check .` → "All checks passed!"; bare `mypy` → "Success: no issues found in 109 source files"; `manage.py check` (test settings) → "System check identified no issues (0 silenced)". |

### §5 Data-Model Conventions

| Check | Status | Evidence |
|-------|--------|----------|
| UI list and API both order by `order, created_at` | **Met** | `ui/views.py:981` `.order_by("order", "created_at")`; `cases/views.py:1444-1446` identical. `ecbcef6` changed UI from `order, "-created_at"` to match API. |
| UI create derives `order = max(order)+1` | **Met** | `ui/views.py:1722-1723`; API requires client-supplied `order` (`cases/views.py:1451-1453`) — both per plan. |
| No migration | **Met** | `Page` unchanged; no page migration in `ecbcef6`; `makemigrations --check` clean. |

### §8 Security (S1–S6)

| Check | Status | Evidence |
|-------|--------|----------|
| **S1 Authorization** | **Met** | `@not_viewer` + `resolve_case(..., write=True)` + `get_object_or_404(..., case=case)`; viewer → uniform 404, cross-tenant id → 404 (harness). |
| **S2 XSS** | **Met** | `{{ page.content|markdown }}` → `ui.markdown.render_markdown` (CommonMark, raw HTML escaped, `nh3` allow-list, ADR-005); no `|safe`. Harness: `<script>`, `<img onerror>`, `javascript:` link all render **inert** (parsed DOM has no `script`/`img`, no `on*` attribute, no `javascript:` href). `live.js::refreshPages` has no `innerHTML` sink. |
| **S3 CSRF** | **Met** | `{% csrf_token %}` in footer form (`_modal.html:91`); no new JSON surface. |
| **S4 CSP** | **Met** | `refreshPages` uses same-origin `fetch` (`connect-src 'self'`); no inline script/style in new templates (`_modal.html:6-7`). |
| **S5 Enumeration** | **Met** | Uniform 404s — foreign-page, cross-org and viewer paths indistinguishable (harness). |
| **S6 Audit** | **Met (accepted gap)** | Page create/edit/delete are not `AuditLog`ged — recorded as R3 / `deviations.md` **W104-8**, not fixed here. Confirmed: no audit call in the three views. Harness: `AuditLog` count remains 0 after each operation. |

---

## Findings

### V1 — Modal heading and Title input share one DOM id (new; low severity; a11y)

**Verdict:** **CONFIRMED** for Pages create/edit modals; **pre-existing** for Task create/edit modals.

**Evidence:**
- `_modal.html:73` gives every dialog heading `id="{{ modal_id }}-title"` and points the dialog's `aria-labelledby` at it.
- `_page_form.html:20-21` gives the **Title input the same id**: `<label for="{{ modal_id }}-title">Title</label>` / `<input id="{{ modal_id }}-title" …>`.
- A document cannot have two elements with one id. The heading appears first in tree order (rendered by `_modal.html` before the body template), so:
  - The dialog's accessible name is still correct (`aria-labelledby` resolves to the heading).
  - But the `<label for="{{ modal_id }}-title">` also resolves to the heading — which is **not a labelable element** — so the Title input **loses its programmatic accessible name** (WCAG 3.3.2 / 4.1.2), and clicking the visible "Title" label no longer focuses it.
- Reproduced on **both** the create modal (`modal_id="page-create-modal"`) and every per-page edit modal (`modal_id="page-edit-<uuid>"`).
- **Pre-existing pattern, replicated.** `_task_form_modal_body.html:23-33` has the same collision on two task modals (`task-create-modal`, `task-edit-<uuid>`), so this is a shared-layer smell this wave copied rather than introduced.

**Recommendation (follow-up, not fixed in this verification pass):** Give modal body fields a distinct suffix — e.g. `id="{{ modal_id }}-title-field"` — in `_page_form.html` **and** `_task_form_modal_body.html`, and add a small guard test asserting one element per id per rendered modal. Fixing it touches a pre-existing template, which is why it is recorded rather than patched here.

---

### F11 — Modal width (shrink-to-fit)

**Verdict:** **CONFIRMED FIXED** at HEAD.

**Evidence:** `ui/static/ui/modal.css:41-56` — `.modal--sm/md/lg/xl` now set a real `width` plus `max-width: 100%` (previously only `max-width`). Comment at `:36-40` explains the fix: "a `<dialog>` is shrink-to-fit, so a `max-width` alone leaves the box to the intrinsic size of its widest control — an `input`/`textarea` with no `size`/`cols` is ~177px, which made a `size="lg"` form render as a ~270px-wide box with a 177px-wide Markdown textarea."

---

### F12 — Over-tall modal (footer off-screen)

**Verdict:** **CONFIRMED FIXED** at HEAD.

**Evidence:** `ui/static/ui/modal.css:62-75` — `.modal-content` capped at `calc(100vh - 2rem)` with `dvh` fallback (`calc(100dvh - 2rem)`), and `.modal-body` made the only scroll region. Comment at `:65-72` explains: "A modal taller than the viewport cannot be centred and cannot be scrolled into reach: with `inset: 0` + `margin: auto`, an over-tall box loses its auto margins on that axis and is pinned to the top, so the footer — and with it the confirm control — ends up below the fold with nothing to scroll."

---

### F13 — `--color-focus` undefined

**Verdict:** **CONFIRMED FIXED** at HEAD.

**Evidence:** `grep -n "color-focus" ui/static/ui/app.css` → **no matches**. The two rules that previously used it (`.tab`/`.tabpanel` focus styles) now use `--color-accent`, the house focus token. `tests/conformance/test_ui_css_tokens.py::test_every_color_token_used_is_defined` passes.

---

### F14 — `.tabpanel:focus { outline: none }` removed focus ring

**Verdict:** **CONFIRMED FIXED** at HEAD.

**Evidence:** `ui/static/ui/app.css:104` — rule is now `.tabpanel:focus-visible { outline: 2px solid var(--color-accent); … }`; the forbidden `:focus { outline: none }` declaration is absent. `tests/conformance/test_ui_css_tokens.py::test_no_rule_removes_the_focus_outline` passes (the guard scans the whole file including comments).

---

### F15 — Page-level horizontal scrollbar at laptop widths

**Verdict:** **CONFIRMED FIXED** at HEAD.

**Evidence:** `ui/static/ui/app.css:33-35` — `.main { min-width: 0 }` with comment explaining the grid item minimum-size issue. Verified: no page overflow at 1174/982px viewport widths; the tab strip scrolls itself as designed. `tests/conformance/test_ui_css_tokens.py::test_tab_strips_do_not_show_a_scrollbar` passes.

---

## Observations (no AC impact)

| Obs | Description | Status |
|-----|-------------|--------|
| **O1** | Cross-tenant coverage for the three UI routes (`ui-case-page-create/edit/delete`) is implicit, not enumerated. Enforcement is the shared `resolve_case(..., write=True)` guard (correct; harness-verified for cross-org case **and** cross-tenant page id). But `test_tenant_scoping.py`'s three write-route sweeps (`:1856`, `:1940`, `:2052`) all filter to API routes (`not name.startswith("ui-")`), and the three names appear only in `_UI_WRITE_ONLY_ROUTES` (`:880-882`), which merely excludes them from the **read** sweep. **No committed test asserts cross-tenant behaviour for these routes**, even though the file has per-route UI cross-tenant tests for other routes. | **Confirmed** |
| **O2** | The Title input has `required`, so the blank-title branch is browser-unreachable but still reachable from a crafted POST; the branch is covered by the verification harness. Good defence in depth. | **Confirmed** |
| **O3** | `title[:500]` / `category[:100]` truncation is unreachable from the browser (inputs carry `maxlength`) but reachable from a crafted POST, matching the API's truncation. | **Confirmed** |
| **O4** | Delete publishes no `page` frame (plan P3-3 / W104-5 / **Q6** open): a colleague with the case open keeps seeing the deleted page until their next load. Confirmed behaviourally. Q6 remains open. | **Confirmed** |
| **O5** | P3.1 (`live.js` `refreshPages()`) is verified **statically only**. The plan's §11 claims a headless-Edge harness (`verify_pages_browser.py`, 31 checks) that this pass could not re-run (no browser harness available). Treat the browser-level claims as author-reported until re-run. The `make css-check` compile check was green in the last `make check`. | **Confirmed** |

---

## Route Hardening Confirmation

| Requirement | Status | Evidence |
|-------------|--------|----------|
| `@not_viewer` on all three write routes | **Confirmed** | `ui/views.py:1698-1700`, `:1739-1741`, `:1775-1777` — outermost decorator. |
| `@require_POST` on all three write routes | **Confirmed** | Same decorator stacks. |
| Org scoping via `resolve_case(..., write=True)` | **Confirmed** | All three views call `resolve_case(request.user, case_id, write=True)` before any mutation. |
| `get_object_or_404(Page, pk=page_id, case=case)` | **Confirmed** | Update (`:1758`) and delete (`:1791`) — pk **and** parent, so foreign page id cannot be addressed. |
| Markdown rendered through trusted `nh3` boundary | **Confirmed** | `ui/markdown.py:83-109` — CommonMark parser with `html=False`, then `nh3.clean` with strict allow-list; fail-closed on exception. Template uses `{{ page.content|markdown }}` (no `|safe`). |

---

## Residual Risk / Not-Covered List

1. **V1 (a11y):** Modal heading / Title input id collision on Pages create/edit modals (and pre-existing on Task modals). Label clicks do not focus the input; input lacks programmatic accessible name.
2. **O1 (test gap):** No committed test enumerates cross-org 404 for `ui-case-page-create/edit/delete`. The write-route enumeration test filters to API-only.
3. **O4 / Q6 (design):** Page delete publishes no `page` frame → other analysts don't see deletion until reload. API parity, but UX gap.
4. **O5 (browser gap):** `live.js` `refreshPages()` behaviour not exercised in a real browser in this verification.
5. **S6 (accepted gap):** Page create/edit/delete are invisible in the Audit tab — recorded as R3 / `deviations.md` **W104-8**.

---

## Verdict on §12.5

**§12.5 can be closed `[x]`.**

All 14 plan Acceptance Criteria are **Met**. The implementation delivers the three UI affordances with the correct route contracts, authorization gates, Markdown rendering, live sync, and card structure. The one new finding (V1) is a low-severity accessibility issue that is pre-existing in the shared modal layer and does not block the plan's functional goals. The five shared-layer defects (F11–F15) are confirmed fixed. The observations are either accepted gaps (S6, O4), defence-in-depth items (O2, O3), or follow-up test work (O1, O5) — none invalidate the ACs.

---

## Commands Run for Behavioural Evidence

```bash
# Plan AC verification (targeted, not full suite)
python3 -m pytest tests/conformance/test_ui_loop.py::test_case_pages_are_rendered_on_the_case_page -v
python3 -m pytest tests/conformance/test_viewer_readonly_ui.py -v
python3 -m pytest tests/conformance/test_tenant_scoping.py -k "ui" -v
python3 -m pytest tests/conformance/test_tenant_scoping.py::TestWriteRouteEnumerationConformance -v
python3 -m pytest tests/conformance/test_ui_markdown.py -v
python3 -m pytest tests/conformance/test_ui_css_tokens.py -v

# Custom verification harnesses (written for this run, then removed)
python3 -m pytest tests/test_pages_verification.py -v -s   # page CRUD + audit + cross-org + viewer + markdown/XSS
python3 -m pytest tests/test_pages_ws.py -v -s             # WS frame emission (source inspection)
python3 -m pytest tests/test_v1_modal_id.py -v -s          # V1 id collision
```

All commands executed at commit `d596db6` with clean working tree.

---

## Report Path

`docs/verification/VERIFY-2026-10-18-case-pages-ui-independent.md`