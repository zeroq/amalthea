# Verification Report: Case pages — create / edit / delete UI + its own card (§12.5)

Plan: `docs/planning/PLAN-2026-10-14-case-pages-ui.md`
Verified: 2026-10-17, at commit `ecbcef6` (§12.5 shipped entirely in that commit; HEAD `6712a42`, working tree clean)
Status: **Pass — provisional** (18/18 checked criteria Met, 1 new finding recorded, 5 observations)

> **Provenance / independence caveat (read first).** This verification was performed **inline by the
> planning agent**, not by the `verifier` subagent, because the subagent service was unavailable in
> this session (HTTP 503 `Upstream error from Nvidia: Service temporarily overloaded` on every
> attempt, including resume). The method is the verifier's — read each AC, read the cited test
> *assertions*, read the implementation, then drive the behaviour through the Django test client and
> mutate it to prove the checks discriminate — but **it is not independent**: the same agent that
> maintains the plan graded it. **Action:** re-run `verifier` against this plan when the service
> recovers and re-issue this file; until then treat "Pass" as provisional. No `security-auditor`
> pass has been run on this scope (see Follow-ups).

---

## Post-merge note — 2026-10-17, at `1f718e5` (after `git pull --ff-only`)

The §12.8 wave (`PLAN-2026-10-17-case-verdict-close-flow.md`, merged as `2476edb..1f718e5`) landed
**after** this verification and touches `ui/templates/ui/_tab_general.html`. Re-running the harness
against the merged tree gives **19/20** — every **Pages** assertion still holds; the single failure is
the harness's Tags assertion, because §12.8 deliberately **replaced the standalone Tags card with an
inline `.case-tags` row inside the meta card**, above the description
(`ui/templates/ui/_tab_general.html:78-105`, citing *"PLAN-2026-10-17 §12.8(2)"*). The General tab is
now **two** top-level `section.card`s — meta (heading at `:3`, the tags row at `:82`) and Pages
(`:123`, `#h-pages` at `:125`, `#case-pages` at `:138`, create modal at `:146`) — where this report
verified three at `ecbcef6` (`:3`/`:62`/`:91`).

**Impact on this report:** AC **2.1**'s Pages claim (Pages is its own card, not nested in the meta
card) is **unchanged and still Met**. Only its parenthetical about Tags is **superseded** — the Tags
card is gone by design, not by regression. All other ACs are unaffected (Pages card, modals, routes,
views, CSS, live frame). The committed guards passed on the merged tree:
`test_ui_loop.py`, `test_viewer_readonly_ui.py`, `test_ui_case_verdict.py`, `test_ui_icon_font.py`,
`test_ui_inline_assets.py`, `test_ui_urls_smoke.py`, `test_realtime.py` — **398 passed**. This note
does not re-open §12.5; it records that one of its structural observations was overtaken by §12.8.

---

## Summary

| Category | Count |
|----------|-------|
| Acceptance Criteria (Plan §7, P1–P4) | 14 |
| Met | 14 |
| Not Met | 0 |
| Deviated | 0 |
| Additional criteria checked (§5 convention, §8 S1–S6) | 7 |
| Met | 7 |
| New findings (not one of F11–F15) | 1 (V1, low severity) |
| Observations (no AC impact) | 5 |

The implementation delivers the plan's three UI affordances with the plan's route/gate/redirect
contracts, moves Pages (and Tags) into sibling cards, renders page content through the ADR-005
sanitized-Markdown pipeline, re-fetches `#case-pages` on a `page` frame instead of mirroring the entry
in JavaScript, and leaves the `Page` model, the API and the wire shape untouched. All five shared-layer
defects the plan records (F11–F15) are present and correct in the current code.

---

## Criterion-by-Criterion Evaluation

### P1 — routes + views

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **1.1** | 3 routes named `ui-case-page-{create,edit,delete}`; `manage.py check` clean; every route `@not_viewer @login_required @require_POST` | **Met** | `ui/urls.py:95` `cases/<str:case_id>/page`, `:97` `…/<uuid:page_id>/edit`, `:102` `…/delete` (UUID converter, so a malformed id never reaches a queryset). Decorator stack on all three views, outermost first: `@not_viewer @login_required @require_POST` — `ui/views.py:1318-1320` (create), `:1379-1381` (update), `:1415-1417` (delete). `manage.py check` clean (0 issues, first-hand at HEAD). Harness: analyst POST → 302. |
| **1.2** | `case_page_create` — title required, `max(order)+1`, `created_by` | **Met (with note)** | `ui/views.py:1347-1349` blank title → `messages.error` + `_case_detail_redirect` (no row); `:1359-1360` `Max("order")` then `0 if last is None else last + 1`; `:1367` `created_by=request.user`; `:1361-1366` `title[:500]`, `category[:100]`, `content` stripped. Harness: appends after `max(order)` (0→1, 7→8), empty case starts at 0, `created_by` set, crafted `order=0` in the POST is ignored (order becomes 6). **Note:** the plan text says "`created_by`/`updated_by`"; the view sets only `created_by`, exactly as the API's create does (`cases/views.py:1455-1462` sets `created_by` only) — API parity, and `updated_by` is stamped on edit, so "edited by" appears only after a real edit. Not a deviation. |
| **1.3** | `case_page_update` — title/content/category, `updated_by`, `update_fields`; no schema change | **Met** | `ui/views.py:1384` `get_object_or_404(Page, pk=page_id, case=case)`; `:1397-1400` writes all three fields unconditionally, sets `updated_by`, and saves with `update_fields=["title","content","category","updated_by","updated_at"]` — **`order` excluded** (plan N1). Harness: all three fields persist, `updated_by` = analyst, `updated_at` advanced, `order` unchanged at 3 when the POST carries `order=99`; clearing the body writes empty; a refused blank title leaves the row untouched (no partial write). Mutation: removing the `updated_by` assignment fails the AC check (the checks discriminate). **No migration** — `Page` is unchanged (`cases/models.py:422-451`), `makemigrations --check` in `make check`. |
| **1.4** | `case_page_delete`; deleting a page of another case 404s | **Met** | `ui/views.py:1420` `get_object_or_404(Page, pk=page_id, case=case)` — pk **and** parent. Harness: own-org delete removes the row (302); a page id belonging to another case → 404 on both edit and delete, the foreign row untouched; a page in another **org** → 404 on both. Mutation: dropping `case=case` makes the foreign-page check fail (discriminates). |
| **1.5** | Viewer denial — `test_viewer_readonly_ui.py` stays green | **Met** | `tests/conformance/test_viewer_readonly_ui.py:83-93` enumerates every `ui-`-named route from the URLconf, so the three routes are covered without a test edit; `:107` POSTs each as a viewer demanding a non-2xx/3xx; `:140` `test_the_invariant_is_not_vacuous` pins sensitivity. Harness (behavioural): viewer GET keeps `#case-pages` and the rendered content but has **no** "New page" button, no per-page Edit/Delete, no create modal; viewer POST to all three → 404. Committed run: `test_viewer_readonly_ui.py` 19 passed. |
| **1.6** | UI create/update publish `page_json` on commit (a second WS client sees a `page` frame) | **Met (delete publishes nothing, by design)** | `ui/views.py:1371-1373` (create) and `:1401-1403` (update) call `transaction.on_commit(partial(publish_case_event, str(case.id), "page", {"event": page_json(page)}))`. Harness (monkeypatched publisher + captured on-commit callbacks): create emits exactly one frame with room `str(case.id)`, `event_type == "page"`, `payload["event"]["_type"] == "page"`, matching `title`/`caseId`; update emits one; **delete emits none**. Delete-publishes-nothing is a documented deviation (plan P3-3, `deviations.md` **W104-5**, Q6 open) and matches the API `DELETE`. |

### P2 — templates

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **2.1** | Un-nest Pages (and Tags) into sibling cards; `#case-pages` not inside the `#h-meta` card; `test_ui_loop.py:404-405` unaffected | **Met** | `ui/templates/ui/_tab_general.html` has three sibling `<section class="card">` at `:3` (meta+description), `:62` (Pages), `:91` (Tags) **— as of `ecbcef6`; the Tags card was later replaced by an inline row — see the post-merge note**. Harness (parsed DOM): `ul#case-pages` (`:77`) is a direct child of the Pages `<section class="card">`, which contains `#h-pages`; **no ancestor of that card is another card**; the `aria-labelledby="h-meta"` card is a distinct sibling (both are direct children of the same tab container); the Tags card is a sibling too, not nested. Committed `tests/conformance/test_ui_loop.py:396-408` still passes (`#case-pages`, `data-page-id`, title, content). |
| **2.2** | Create modal + header button, `can_mutate` only; modal opens with focus in Title | **Met** | `_tab_general.html:65-68` the "New page" button is wrapped in `{% if can_mutate %}`; `:85` the create modal (also gated). `_page_form.html:21` the Title input carries `autofocus` (and `required`, `maxlength="500"`), which `modal.js` focuses on open; the dialog is `<dialog class="modal modal--lg">` (`_modal.html:70`). Harness (parsed DOM): button present for an analyst, dialog + `form#page-create-form` present with `action`/`method` = the create route; **viewer** page has no modal and no button. |
| **2.3** | `_page_entry.html` — Markdown body + Edit/Delete + extended meta; `data-page-id` preserved | **Met** | `_page_entry.html:15` `data-page-id="{{ page.id }}"`; `:18-27` `can_mutate`-gated Edit/Delete buttons opening the per-page edit/delete modals; `:29` `{{ page.content|markdown }}` inside `<div class="body md">`; `:30-35` meta = `category · created · author · edited by`. Harness: `data-page-id` present; `**bold**` renders to `<strong>`; `category`/created timestamp in the meta; "edited by analyst" appears once `updated_by` is set; both per-page modals and their openers exist. |
| **2.4** | `_page_form.html` shared by create and edit; edit modal prefilled; both submit to their own route with `tab=general` | **Met** | One template serves both paths (`_page_form.html:4-7` explains the `{{ page.… }}` prefill); `_page_entry.html:38` hosts the edit modal with `form_id="page-edit-form-<id>"` and `:39` the delete modal. Every control carries `form="{{ form_id }}"` (`:17/21/26/31`) because the footer form owns the submit (`_modal.html:88-98`). Harness: create modal's hidden `tab` and Title input both target `page-create-form` and carry `value="general"`; the edit modal exists per page with its own form id. |

### P3 — live + CSS

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **3.1** | `live.js` `refreshPages()` on a `page` frame; API-created page appears with rendered Markdown without a reload; **no `innerHTML` sink** | **Met (static)** | `ui/static/ui/live.js:146-166` `refreshPages()` re-fetches the Pages card and swaps it via `DOMParser` + `replaceWith`; the body of the function contains **no `innerHTML`**; the frame dispatch routes `page` to it. The deleted `pageEntry()`/`appendPage()` mirror is gone (`deviations.md` **W104-6**). **Caveat:** verified by source inspection, not by executing JavaScript in a browser this pass (see Observation O5 / Follow-ups). |
| **3.2** | Appended CSS: card heading keeps its uppercase dim look; button row has no stray `margin-top:14px` | **Met** | `ui/static/ui/app.css`: `.card > .page-row` restates the heading treatment via `.card > .page-row > h2` (`text-transform: uppercase`, `--color-text-dim`, `font-size: 13px`, `letter-spacing: 0.06em`, `font-weight: 650`) — with a comment explaining why the restatement is needed; `.page-row .btn { margin-top: 0 }` neutralises `.btn`'s standalone `margin-top: 14px`. First-hand read of the source region. |

### P4 — verification

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **4.1** | Throwaway harness renders the real case page and asserts card nesting + modal hit-tests | **Met** | The plan's §11 records two scratch harnesses (`verify_pages.py` 63 checks, `verify_pages_browser.py` 31 checks in headless Edge) plus `verify_modal_widths.py`. Those files live outside the repo and are not reproducible here, so this pass **re-ran an equivalent harness** (`test_case_pages_verify.py`, 20 tests, outside the repo): card nesting, modal/form associations, create/edit/delete round-trips, `order`/`updated_by` semantics, foreign-case + cross-org 404s, viewer controls + 404s, `page`-frame emission, Markdown/XSS, CSS source rules — **20/20 pass**. Two behavioural mutations (drop `case=case`; drop the `updated_by` write) each failed exactly the predicted check, so the harness discriminates. |
| **4.2** | `ruff format --check`, `ruff check`, `mypy`, `manage.py check` | **Met** | First-hand at HEAD: `ruff format --check .` → "310 files already formatted"; `ruff check .` → "All checks passed!"; bare `mypy` → "Success: no issues found in 109 source files"; `manage.py check` (test settings) → "System check identified no issues (0 silenced)". The full `make check` gate was green on the last push (`6712a42`), and only a docs-only commit has landed since. |

### §5 data-model conventions

| Check | Status | Evidence |
|-------|--------|----------|
| UI list and API both order by `order, created_at` | **Met** | `ui/views.py:888-889` `.order_by("order", "created_at")`; `cases/views.py:1444-1446` identical. `ecbcef6` changed the UI from `order, "-created_at"` to match the API, closing the divergence the plan flagged. |
| UI create derives `order = max(order)+1` | **Met** | `ui/views.py:1359-1360`; the API requires a client-supplied `order` (`cases/views.py:1451-1453`) — both per plan. |
| No migration | **Met** | `Page` unchanged; no page migration in `ecbcef6`; `makemigrations --check` clean. |

### §8 security (S1–S6)

| Check | Status | Evidence |
|-------|--------|----------|
| **S1** Authorization | **Met** | `@not_viewer` + `resolve_case(..., write=True)` + `get_object_or_404(..., case=case)`; viewer → uniform 404, cross-tenant id → 404 (harness). |
| **S2** XSS | **Met** | `{{ page.content|markdown }}` → `ui.markdown.render_markdown` (CommonMark, raw HTML escaped, `nh3` allow-list, ADR-005); no `|safe`. Harness: `<script>`, `<img onerror>`, and a `javascript:` link all render **inert** (parsed DOM has no `script`/`img` element, no `on*` attribute, no `javascript:` href; raw markup survives only as escaped text). `live.js::refreshPages` has no `innerHTML` sink. |
| **S3** CSRF | **Met** | `{% csrf_token %}` in the footer form (`_modal.html:91`); no new JSON surface. |
| **S4** CSP | **Met** | `refreshPages` uses same-origin `fetch` (`connect-src 'self'`); no inline script/style in the new templates (`_modal.html:6-7`). |
| **S5** Enumeration | **Met** | Uniform 404s — foreign-page, cross-org and viewer paths are indistinguishable (harness). |
| **S6** Audit | **Met (accepted gap)** | Page create/edit/delete are not `AuditLog`ged — recorded as R3 / `deviations.md` **W104-8**, not fixed here. Confirmed: no audit call in the three views. |

---

## Findings

### V1 — the modal heading and the Title input share one DOM id (new; low severity; a11y)

`_modal.html:73` gives every dialog heading `id="{{ modal_id }}-title"` and points the dialog's
`aria-labelledby` at it; `_page_form.html:21` gives the **Title input the same id**. A document cannot
have two elements with one id, so:

- the dialog's accessible name is still correct (`aria-labelledby` resolves to the heading, which is
  first in tree order);
- but the `<label for="{{ modal_id }}-title">Title</label>` (`_page_form.html:20`) also resolves to
  the heading — which is **not a labelable element** — so the Title input loses its programmatic
  accessible name (WCAG 3.3.2 / 4.1.2), and clicking the visible "Title" label no longer focuses it.

Reproduced on **both** the create modal and every per-page edit modal (harness
`test_v1_the_modal_heading_and_the_title_input_share_one_dom_id` asserts two claimants per modal and
that the first is the `<h2>`).

**Not one of F11–F15**, and the plan's own `verify_pages.py` (63 checks, described as covering "the
three modals and their form associations") did not catch it.

**Pre-existing pattern, replicated.** `_task_form_modal_body.html:25,32` has the same collision on two
task modals, so this is a shared-layer smell this wave copied rather than introduced. **Recommendation
(follow-up, not fixed in this verification pass):** give modal *body* fields a distinct suffix —
e.g. `id="{{ modal_id }}-title-field"` — in `_page_form.html` **and** `_task_form_modal_body.html`, and
add a small guard test asserting one element per id per rendered modal. Fixing it touches a pre-existing
template, which is why it is recorded rather than patched here.

---

## Observations (no AC impact)

- **O1 — cross-tenant coverage for the three UI routes is implicit, not enumerated.** Enforcement is the
  shared `resolve_case(..., write=True)` guard (correct; harness-verified for the cross-org case *and*
  cross-tenant page id). But `test_tenant_scoping.py`'s three write-route sweeps
  (`:1856`, `:1940`, `:2052`) all filter to API routes (`not name.startswith("ui-")`), and the three
  names appear only in `_UI_WRITE_ONLY_ROUTES` (`:876-878`), which merely excludes them from the **read**
  sweep. So no committed test asserts cross-tenant behaviour for these routes, even though the file has
  per-route UI cross-tenant tests for other routes. Recommend adding the page routes to a UI write
  sweep.
- **O2 — the Title input has `required`, so the blank-title branch is browser-unreachable** but still
  reachable from a crafted POST; the branch is covered by the harness. Good defence in depth.
- **O3 — `title[:500]` / `category[:100]` truncation** is unreachable from the browser (the inputs carry
  `maxlength`) but reachable from a crafted POST, matching the API's truncation.
- **O4 — delete publishes no frame** (plan P3-3 / W104-5 / **Q6** open): a colleague with the case open
  keeps seeing the deleted page until their next load. Confirmed behaviourally. Q6 remains open.
- **O5 — P3.1 is verified statically only.** The plan's §11 claims a headless-Edge harness
  (`verify_pages_browser.py`, 31 checks: open/submit/close, focus, `required` blocking an empty submit,
  footer on screen) that this pass could not re-run (no browser harness available). Treat the
  browser-level claims as author-reported until re-run. The `make css-check` compile check was green in
  the last `make check`.

---

## Plan status notes

The plan is still marked **Status: draft — awaiting answers to §10** although it is implemented and
committed. Q1/Q2 (card on General; modal idiom) are answered by the implementation; **Q6** (whether a
UI delete should publish a frame) is the only substantively open question. The §7 phase table's ACs are
all satisfied.

---

## Follow-ups

1. **Re-run `verifier`** against `PLAN-2026-10-14-case-pages-ui.md` when the service recovers, to
   replace this provisional report with an independent one. (The same applies to the still-provisional
   §10a–§10c report.) Re-run it **at the merged HEAD** (`1f718e5`), where the Tags card has been
   superseded by §12.8 — see the post-merge note.
2. **`security-auditor` pass** on §12.5 (authz/CSRF/XSS on the three routes) and, overdue, on §10a–§10c.
3. **Fix V1** — de-collide modal body-field ids (`_page_form.html`, `_task_form_modal_body.html`) and
   add a one-id-per-element guard test.
4. **Record V1 and O1** in `docs/spec/deviations.md` / `TODO.md`.
5. Decide **Q6** (does a UI delete publish a `page` frame?), and revisit **R3/S6** (page writes absent
   from the Audit tab, W104-8).
6. Re-run the headless-browser harness (O5) if browser-level assurance is required.

---

## Evidence log

| Artifact | Result |
|----------|--------|
| Scratch harness `/var/folders/…/opencode/test_case_pages_verify.py` (outside the repo) | **20 passed** |
| Same harness with `case=case` dropped from `case_page_delete` | 1 failed (as predicted) — discriminates |
| Same harness with the `updated_by` write removed from `case_page_update` | 1 failed (as predicted) — discriminates |
| Harness reverted; `git status --porcelain` on the repo | **clean** |
| `pytest test_ui_loop.py::test_case_pages_are_rendered_on_the_case_page test_viewer_readonly_ui.py test_t2_p2_surface.py` | **19 passed** |
| `ruff format --check .` | 310 files already formatted |
| `ruff check .` | All checks passed |
| `mypy` (pinned scope) | Success: no issues found in 109 source files |
| `manage.py check` (test settings) | System check identified no issues |
