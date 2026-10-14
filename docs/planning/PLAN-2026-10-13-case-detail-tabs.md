# PLAN-2026-10-13 — Case detail as a tabbed surface

**Status:** Draft — awaiting approval
**Owner:** planner
**Scope owner after approval:** `django-frontend`
**Related:** `AGENTS.md` §2 (Modules A–D), §6 (Design & Style); `PLAN-2026-10-13-cases-view-fixes.md`;
ADR-003 (frontend assets), ADR-006 (role semantics). TODO §12.3 (correlation surface).

---

## 1. Summary

Replace the single-scroll case detail page with a tabbed surface of exactly eight tabs:

**General · Alerts · Tasks · Observables · Attachments · Similar · Comments · Audit**

This plan delivers the **tab shell**: routing, deep-linking, ARIA/keyboard behaviour, the panel
container layout, and a provisional distribution of the content that already exists on the page. The
**content of each tab is deliberately provisional** — the user will define what each tab holds once the
shell exists. Existing content is never dropped; anything without an obvious home is parked in a
labelled provisional location and listed in §11 for relocation.

## 2. Goals

- G1 — One case = one tabbed surface with the eight tabs above, in that order.
- G2 — Tabs are **deep-linkable** (`/cases/<number>?tab=<slug>`), bookmarkable and reload-stable.
- G3 — Tabs are **keyboard-operable** with the WAI-ARIA tabs pattern, and remain usable with **JS
  disabled** (progressive enhancement).
- G4 — Every selector that exists on the page today keeps existing on first render (≈20 test files
  depend on them — see §10 R1).
- G5 — The shell is reusable: a second page can adopt the same markup/script without a rewrite.

## 3. Non-goals

- **N1 — Not** defining the final content of any tab. Alerts/Similar/Audit get only a minimal or
  placeholder body; the user specifies the real content afterwards.
- **N2 — Not** implementing the correlation/"Similar" query (TODO §12.3). The tab ships with an
  explicit "not yet implemented" empty state.
- **N3 — No** new database models, fields, or migrations.
- **N4 — No** API changes. UI-only.
- **N5 — No** lazy/on-demand panel loading (see §4 rationale).
- **N6 — Not** redesigning the case list, alert detail, or any other page.

## 4. Architecture decision

**Tabs = client-side show/hide over content that is fully server-rendered in the initial HTML.**
Deep-linking is the `?tab=<slug>` query parameter on the existing `GET /cases/<number>` route.

| Option | Verdict |
| --- | --- |
| **A. All panels in the initial HTML; `?tab=` selects the visible one; a ~1 KB script toggles `hidden` and pushes history state; `<a href="?tab=…">` is the no-JS fallback.** | **CHOSEN.** |
| B. Lazy HTMX panels (`hx-get` per tab) | **Rejected.** The initial render would lose the non-active panels' selectors, breaking the existing conformance suite and the no-JS fallback. |
| C. Path segments `/cases/<id>/<tab>` | **Rejected.** 8 new routes for no benefit; `tasks` would sit confusingly beside the existing POST-only `/cases/<id>/task`, `/cases/<id>/status`, `/cases/<id>/comment`. |

Rationale for A:

- **Progressive enhancement by construction.** Each tab is a real `<a href="?tab=…">`; with JS off the
  browser performs a normal navigation and the server renders that tab active. Nothing is unreachable.
- **Zero test churn.** Today the page already renders timeline, observables, procedures, runs, tasks,
  pages and attachments. Option A keeps all of it on first render, so `#timeline-events`,
  `#case-procedures`, `#case-pages`, `#case-merge-modal` etc. are still found by the ~20 test files that
  assert on them.
- **No added latency** for keyboard switching (the content is already in the DOM).
- **Cheaply reversible** — a later tab that turns out to be expensive (e.g. a real "Similar" query) can
  add `hx-get`/`hx-trigger="load"` to *that one panel* without changing the other seven.

**ADR:** none required — this is a UI pattern, not an architectural commitment. If tabs become the
site-wide navigation idiom (settings, alerts), file **ADR-008** then.

## 5. Database schema

None. No migrations. The shell reads only relations that already exist:

- `case.alerts` (`Alert.case` FK, `related_name="alerts"`)
- `case.tasks`, `case.case_observables`, `case.attachments`, `case.procedures`, `case.pages`
- `case.timeline_events`, `Comment` (case FK)
- `AuditLog` filtered on `target_type="Case"`, `target_id=str(case.id)` (the convention already used by
  `ui/views.py:634` `case.created` and `:1829` `case.updated`)

## 6. API contract

No new endpoints. One parameter added to an existing one:

```
GET /cases/<number>?tab=<slug>
```

| Aspect | Contract |
| --- | --- |
| `slug` | One of `general`, `alerts`, `tasks`, `observables`, `attachments`, `similar`, `comments`, `audit` |
| Absent | `general` |
| Unknown / malformed | **`general`** (never a 400, never an error page) |
| Binding | Allowlist lookup `TAB_PARTIALS: dict[str, str]`; the parameter is **never** interpolated into a template path |
| Response | Unchanged status/shape — `200` with `ui/case_detail.html`; the active panel is not `hidden` |
| Auth | Unchanged (`login_required`, `resolve_case(..., write=False)`) |

## 7. UI / UX

### 7.1 Markup contract

```html
<div class="tabs" role="tablist" aria-label="Case sections">
  <a class="tab" role="tab" id="tab-general" href="?tab=general"
     aria-controls="panel-general" aria-selected="true" tabindex="0">
    <i class="fa-solid fa-circle-info" aria-hidden="true"></i> General
  </a>
  <a class="tab" role="tab" id="tab-alerts" aria-controls="panel-alerts" aria-selected="false" tabindex="-1" href="?tab=alerts">
    Alerts <span class="count">3</span>
  </a>
  …
</div>

<div class="tabpanels">
  <section class="tabpanel" role="tabpanel" id="panel-general" aria-labelledby="tab-general" tabindex="0">
    …
  </section>
  <div role="tabpanel" id="panel-alerts" aria-labelledby="tab-alerts" tabindex="0" hidden>
    …
  </div>
  …
</div>
```

- Inactive panels carry the **`hidden`** attribute (not `display:none` via a class) so the accessibility
  tree and the no-JS render agree.
- Counts render as a `<span class="count">` inside the tab label where a count exists today
  (Alerts, Tasks, Observables, Attachments, Comments).

### 7.2 Keyboard

- **WAI-ARIA APG tabs pattern**: `←`/`→` move between tabs (wrapping), `Home`/`End` jump to first/last,
  roving `tabindex` (`0` on the active tab, `-1` on the rest), activation follows focus (panels are
  already in the DOM, so this is instantaneous).
- `Esc` is deliberately **not** bound (reserved for modals).
- Optional: number keys `1`–`8` switch tabs **only** when focus is not in a form control. Update the `?`
  cheat-sheet (`keys.js` + its `_NON_NAV_KEYS`-adjacent conformance test) if this is adopted (§11 Q4).
- Tabbing out of the tablist enters the active panel (`tabindex="0"` on the panel).

### 7.3 Accessibility

- Severity/status never signalled by colour alone (existing rule) — tab counts are text, not dots.
- Active tab is exposed via `aria-selected`, not only via a CSS class; the class is for styling.
- The tablist is a `<div role="tablist">`, not a `<nav>`: it is a widget, not navigation; it must not
  appear in the rail or as a landmark.
- Icons are `aria-hidden="true"`.

### 7.4 Provisional content distribution

| Tab | Provisional body | Source |
| --- | --- | --- |
| **General** | Metadata grid (severity, status, owner, TLP/PAP, dates), description (markdown), ingestion warnings, status control, *Re-extract artifacts* control, **tags**, **custom fields**, **Pages** (provisional) | existing `case_detail.html` §L35–71 |
| **Alerts** | Minimal real list of `case.alerts` (source, title, severity, status, received) → link to alert detail | new; minimal by design (§3 N1) |
| **Tasks** | Existing tasks list + add-task form | existing §L207–245 |
| **Observables** | Existing artifacts table; **Procedures / TTP** (provisional) | existing §L109–126, §L128–178 |
| **Attachments** | Existing upload form + attachments table + per-attachment delete modals | existing §L260–313 |
| **Similar** | Explicit "not yet implemented" empty state referencing TODO §12.3 | new (§3 N2) |
| **Comments** | The note stream (`TimelineEvent` kind = `comment` / `Comment` rows) + composer, live via the existing case WebSocket | existing §L74–96, narrowed |
| **Audit** | `AuditLog` rows where `target_type="Case"` and `target_id=str(case.id)`; **Automation runs** (provisional); non-comment timeline events (provisional) | new + existing §L180–205 |

Everything marked *provisional* is a §11 decision, not a commitment.

## 8. Phases, tasks and acceptance criteria

### P1 — Tab shell (frontend)

- **T1-a** Add `CASE_TABS` / `TAB_PARTIALS` allowlist constants and pass `active_tab` into the
  `case_detail` context (`ui/views.py`).
  - **AC1** Unknown/absent/typo'd `?tab=` renders `general` with HTTP 200.
  - **AC2** An allowlist miss is impossible to turn into a template include (no f-string path).
- **T1-b** Restructure `ui/templates/ui/case_detail.html` into the tablist + 8 panels; extract each panel
  body into `ui/templates/ui/_tab_<slug>.html` partials.
  - **AC3** The page renders 8 tabs / 8 panels; the inactive 7 carry `hidden`.
  - **AC4** `#timeline-events`, `#case-procedures`, `#case-pages`, `#case-merge-modal` still exist on
    first render (unchanged selectors).
- **T1-c** Add the tablist + panel CSS to `app.css` (dark theme, WCAG-AA focus ring, no layout shift).
  - **AC5** Visible focus indicator on the active tab; contrast checked with the existing palette.
- **T1-d** Add `ui/static/ui/tabs.js`: click/`pushState` toggling, arrow/Home/End keys, `aria-selected`
  + roving tabindex bookkeeping, sync on `popstate`.
  - **AC6** With JS: switching a tab updates the URL and does not reload. With JS disabled: clicking a
    tab performs a normal navigation and lands on the same tab via `?tab=`.
- **AC7** A viewer (`role=viewer`) sees the same tabs; every write control inside every panel stays
  hidden/absent for them (`can_mutate` gating preserved panel-by-panel).

### P2 — Keyboard + discoverability (frontend)

- **T2-a** (Optional, §11 Q4) number shortcuts `1`–`8` guarded against form-field focus; update
  `keys.js` and the cheat-sheet modal.
  - **AC8** Pressing `3` outside a form focuses/activates Tasks; pressing `3` inside a text input types
    `3`. Cheat-sheet lists the binding.

### P3 — Minimal bodies (frontend; backend only if a query needs optimizing)

- **T3-a** Alerts tab: minimal list from `case.alerts`.
  - **AC9** A case with N alerts shows N rows and each links to its alert detail; a case with none shows
    an empty state. No N+1 (use `select_related`).
- **T3-b** Similar tab: empty state only.
  - **AC10** No query is issued for this tab; the empty state names the gap (TODO §12.3).
- **T3-c** Audit tab: `AuditLog` rows for the case, newest first.
  - **AC11** `case.created` and a subsequent `case.updated` both appear, with actor and timestamp; a case
    with no audit rows shows an empty state.

### P4 — Tests (qa-tester, lean)

- **T4-a** Conformance: the 8-tab shell — tab labels/order, `hidden` placement, `?tab=` allowlist
  (unknown → general), active-tab `aria-selected`/`tabindex`.
- **T4-b** Regression: every existing case-detail selector still present on first render.
- **T4-c** No-JS: `GET /cases/<n>?tab=audit` returns the audit panel **not** `hidden`.
- **AC12** `make check` green at ≥ the current baseline (1699 passed / 3 skipped) plus the new tests.

### P5 — Docs (planner)

- **T5-a** `COMPLETED.md`, `TODO.md` (supersede §12 items that this shrinks), `docs/spec/deviations.md`,
  and a short `docs/spec/ui-case-detail-tabs.md` describing the tab shell for reuse.
  - **AC13** The plan document records any deviation actually made.

## 9. Security

- **S1 — Parameter binding.** `?tab=` is resolved through a `dict` allowlist. It must never reach
  `render()`, a template path, or a template tag as a raw value. (Unknown → `general`.)
- **S2 — Server-side authz is unchanged.** `case_detail` keeps `login_required` and
  `resolve_case(..., write=False)`; the tabs are presentation only and must not become a second,
  weaker access path to case data.
- **S3 — Per-panel gating preserved.** Moving content into partials must not move a `can_mutate`/`is_owner`
  check out of scope. Each write control is re-verified after the extraction.
- **S4 — Cross-org reads.** `is_cross_org` stays on the page and its audit behaviour
  (`record_cross_org_read`) is untouched.
- **S5 — No secrets.** No new config.

## 10. Risks

| ID | Risk | Mitigation |
| --- | --- | --- |
| R1 | Lazy panels would delete selectors ≈20 test files depend on | Design choice A keeps all panels in the initial HTML (§4) |
| R2 | `live.js` WebSocket appends to `#timeline-events`, which is inside the (possibly inactive) Comments panel | Panel stays in the DOM; guard the append against a `null` target and/or refresh the Comments count. Verify `live.js` behaviour explicitly. |
| R3 | `?tab=` becomes an injection/streaming path | Allowlist + §9 S1 |
| R4 | Layout regressions from unwrapping the current 2-column `.cols` | Each panel keeps a sane single-column internal layout; visual check per panel |
| R5 | Provisional placements (Procedures, Pages, Runs) churn once the user defines content | Placements are explicitly marked provisional in §7.4 + §11; moving a partial later is a one-line change |
| R6 | The `?` cheat-sheet conformance test (`keys.js` route audit) breaks if shortcuts are added | Update `_NON_NAV_KEYS`/cheat-sheet test in the same commit (P2) |

## 11. Open questions (need an answer before P1 lands, or explicitly deferred)

- **Q1** Confirm deep-linking via `?tab=` (vs path segments). *Recommendation: `?tab=`.*
- **Q2** Where do these existing sections go? **Procedures/TTP**, **Pages**, **Automation runs**,
  **Tags**, **Custom fields** currently on the page and in no named tab.
  *Recommendation:* Procedures → Observables; Pages → General; Automation runs → Audit; Tags + Custom
  fields → General.
- **Q3** Confirm the **Comments vs Audit** split: Comments = analyst notes only; Audit = case `AuditLog`
  rows. *Recommendation: yes — and note the case audit log today records only `case.created` /
  `case.updated`, so Audit will be thin until more mutations are audited.*
- **Q4** Adopt number-key tab shortcuts (`1`–`8`)? *Recommendation: yes, guarded against form focus.*
- **Q5** **Similar** tab: ship the placeholder now, or implement the §12.3 correlation query as part of
  this work? *Recommendation: placeholder now, correlation as its own plan.*
- **Q6** Default tab = **General**? *Recommendation: yes.*

## 12. Implementation brief (for `django-frontend`)

Read §4, §6, §7, §9 first — the design choice and the security notes are binding.

- `ui/views.py` → `case_detail` (currently ~L654): add the allowlist, resolve `active_tab`, pass it in
  `_base_context`. Keep every existing context key.
- `ui/templates/ui/case_detail.html` → becomes the shell (`tablist` + 8 panels). Move panel bodies into
  `ui/templates/ui/_tab_general.html`, `_tab_alerts.html`, `_tab_tasks.html`, `_tab_observables.html`,
  `_tab_attachments.html`, `_tab_similar.html`, `_tab_comments.html`, `_tab_audit.html`.
- `ui/static/ui/app.css` (or `ui/static/ui/tabs.css` + a `<link>` if the project prefers asset split —
  see TODO re Tailwind source/artifact split) → tablist/panel styling.
- `ui/static/ui/tabs.js` → new; loaded `defer` from `case_detail.html` only (like `live.js`).
- Do **not** touch `identity/`, `cases/`, `alerts/`, or the API.
- **Constraints:** `ruff format` any `.md` you edit; run `make check` before reporting; do not commit.
- **Report:** files changed, the exact AC-by-AC status, `make check` summary line, and any deviation.

## 13. As-built notes and deviations

Recorded after implementation. Everything here is a deliberate, reviewable choice — not drift.

### 13.1 Deviations from the plan

| # | Plan said | Built | Why |
|---|-----------|-------|-----|
| D1 | AC8: advertise the `1`–`8` shortcuts in the site-wide `?` cheat-sheet modal | `aria-keyshortcuts="1"…"8"` on each tab; cheat-sheet untouched | `keys.js` / `base.html` cheat-sheet contents are asserted by `tests/conformance/test_ui_nav.py`. Editing it would have forced a test change during the test freeze. Screen-reader users still get the binding. **Follow-up:** add the row when the freeze lifts. |
| D2 | Q3: Comments = analyst notes only, Audit = `AuditLog` | Split implemented 2026-10-13 — see §13.5 | The original build kept the full timeline in Comments; the user has since decided the split. |
| D3 | New tab CSS is additive | Appended to the generated `ui/static/ui/app.css` artifact | The repo has no separate authored stylesheet; `Makefile:css` compiles `app.css` in place (input == output). The appended block **survives a recompile** (verified against `scripts/tailwindcss`). Note the block redefines the shared `.tabs` / `.tab` classes, so its padding/border/etc. also apply to the scope-filter tabs on the case and alert lists — same class name, one visual language. |
| D4 | (implied) new `.active` class | Reuses the existing `.tab-on` idiom from `case_list.html` / `alert_list.html` | One class name for "active tab" across the app instead of two. |

### 13.2 Fixes applied during review

The tab shell was first written by a run that was cancelled mid-flight; review of its output found
and fixed five defects before this plan was considered done:

1. **Fabricated provenance** — `_tab_similar.html` linked `https://github.com/amalthea/amalthea/issues/123`,
   an invented issue in a repository that does not exist. Removed; the placeholder is now prose.
2. **Badge/body mismatch** — the Comments badge counted `kind="comment"` while the panel rendered every
   event. The badge now counts exactly what the panel shows.
3. **Focus stealing** — `tabs.js` moved focus into the panel on *initial page load*. Removed; focus stays
   on the tab per WAI-ARIA APG (the panel keeps `tabindex="0"` so Tab still reaches it).
4. **Duplicate history entries** — the initial sync and `popstate` both called the function that pushes
   state, so Back needed two presses to leave the page. Both now pass `push=false`.
5. **Inline styles / dead classes** — three `style="margin-top: 1.5rem"` attributes replaced with the
   existing `.card-spaced` utility, and `pill-{{ log.action|slugify }}` (which renders as the never-defined
   `pill-casecreated`) replaced with plain `.pill`.
6. **Leaked template comments (found by the user, 2026-10-13)** — Django's `{# … #}` is *single-line
   only*: a `{#` whose `#}` sits on a later line is not parsed as a comment and the text is emitted into
   the page. Six such comments existed (`ui/templates/ui/_page_entry.html`,
   `_tab_audit.html`, `_tab_comments.html`, `_tab_general.html`, `case_detail.html`,
   `observable_detail.html`) — four written during this work, two pre-existing. All six were converted to
   `{% comment %} … {% endcomment %}` blocks with their text preserved (the notes document real
   contracts: the `live.js` mirror obligation, the Comments/Audit socket split, the delete-modal
   acknowledgement, the Pages-card id).

  **Standing rule:** never write a multi-line `{# … #}`. The invariant is *every* `{#` must have its
  `#}` on the same line; anything longer uses `{% comment %}`. A static scan for this belongs in CI —
  `{% comment %}` / `{% endcomment %}` count balance is a useful cheap proxy.
7. **The active tab was lost on every POST (found by the user, 2026-10-13)** — all ~23 case-detail
   redirects called `redirect("ui-case-detail", case_id=…)` with no `?tab=`, so submitting *any* panel
   form (status, extract, task create/toggle, procedure create, comment, attachment upload/delete)
   dropped the user back on General. Fixed with one shared helper, `_case_detail_redirect(request,
   case_id)`, plus a hidden `tab` input on each of the eight forms that live inside a panel. The
   contract is what keeps the frozen tests green: the helper appends `?tab=<slug>` **only** when the
   submitted body carries a value present in `CASE_TAB_SLUGS`, so a request with no `tab` (which is
   what `tests/conformance/test_realtime.py:535` and `test_ui_loop.py:270` send) still receives the
   bare URL they assert. The merge form in `case_detail.html` deliberately gets no hidden input — it
   lives outside the panels and redirects to a *different* case.
8. **A rejected attachment upload returned HTTP 500 (found while verifying fix 7)** —
   `case_attachment_upload` read `response.content` off the DRF `Response` returned by
   `cases.views.case_attachment_list`, which is called here as a plain function and therefore never
   rendered; `.content` raises `ContentNotRenderedError`. The happy path (201) never touched `.content`
   and no test covered the failure path, so every *rejected* upload — disallowed content type, too
   large, empty, or content/type mismatch — crashed instead of showing "Upload failed: …". Fixed with
   the `_api_error_detail(response)` helper, which reads the already-populated `.data["message"]`
   (falling back to the status line) rather than `.content`.
9. **Every `<dialog>` was painted in the top-left corner (found by the user, 2026-10-13)** — a native
   `<dialog>` is centred *only* by the UA stylesheet's `margin: auto`, together with the `inset: 0`
   that `dialog:modal` supplies. Tailwind v4's Preflight in `app.css` resets `margin`/`padding` on `*`
   inside `@layer base`, and author declarations beat the UA stylesheet, so that margin collapsed to
   `0` and every dialog rendered in the corner — all 13 `_modal.html` call sites, the
   attachment-delete / case-merge / observable-delete dialogs, and the `?` cheat sheet. Neither
   `.modal` (`modal.css`) nor `dialog.cheatsheet-modal` (`app.css`) declared a margin and `margin:auto`
   appeared in neither file, so nothing restored it. Fixed with one unlayered rule in `modal.css`
   covering both selectors (`inset: 0; margin: auto`). This is inherited from the Tailwind build, not
   introduced by the tab work — every modal in the app was affected. Verified in a real browser, see
   §13.3.
10. **Every click inside every modal hit the backdrop, so no modal could be used (found while comparing
    our implementation against the Flowbite modal, 2026-10-13)** — each dialog carried a
    `<div class="modal-backdrop" tabindex="-1">` sibling *before* `.modal-content`. `modal.css` made it
    `position: fixed; inset: 0`, and a positioned sibling paints **above** static content inside the
    dialog's stacking context, so that div covered its own dialog's content — every button, every input,
    the title text. `modal.js` then closed the dialog whenever a click's target had class
    `modal-backdrop`, so the practical effect was:

    - clicking **Delete/Cancel/×** closed the dialog *without submitting* → attachment delete, observable
      delete, and all 13 `_modal.html` call sites (org/user/playbook deletes, org create, …) were silent
      no-ops, and
    - clicking any input inside a modal closed it, making every modal-hosted form unfillable,
    - plus double dimming: the div's `rgba(0,0,0,.5)` stacked on `::backdrop`'s.
    - `showModal()` also focused the div (it is `tabindex="-1"`-focusable), so `document.activeElement`
      was not a control.

    Fix 9 is what exposed it: until centering worked the dialog was a corner sliver nobody could aim at.
    Fixed by deleting the overlay and letting the native dialog be its own outside-click target — clicks on
    `::backdrop` are dispatched *to the `<dialog>`*, so `event.target === dialog` is the correct test
    (Flowbite needs a real backdrop element only because its modal is a `<div>`, not a native dialog).
    Three parts: the div removed from all five template sites (`_modal.html`, `base.html` cheat sheet,
    `_tab_attachments.html`, `observable_detail.html`, `case_detail.html`); `modal.js` now matches
    `dialog.modal, dialog.cheatsheet-modal` with `event.target === modal`, skipping dialogs marked
    `data-modal-static` (our `data-modal-backdrop="static"` equivalent, offered but not yet used); and the
    now-dead `.modal-backdrop` rule removed from `modal.css` (a comment records why it must not come back).
    No test referenced `modal-backdrop`, so nothing was pinned to the old behaviour. Verified by hit-test,
    see §13.3.
11. **Two template-scoping defects found by the user (2026-10-14), both silent no-ops.**
    - **A tag must not span a newline.** Django's lexer regex (`{%.*?%}|{{.*?}}|{#.*?#}`) has **no
      `re.DOTALL`**, so a tag whose delimiters are on different lines is never tokenised as a tag — it
      degrades to literal page text. Three `{% include "ui/_modal.html" … %}` calls (the tasks-tab
      create + per-row edit/delete dialogs, and the task-detail edit dialog) had been wrapped across
      lines, so the `<dialog>`s were never rendered while their `data-modal-open` buttons (single-line
      `{% if %}` branches) still were — the buttons "did nothing" and the raw `{% include … %}` text
      printed on the page. Fixed by keeping each tag on one line. Same family as fix 6: `{# … #}` must
      also close on its line; use `{% comment %}` for anything longer.
      **Detector:** tokenise each template and flag any `TokenType.TEXT` token containing `\{[#%]` — a
      leaked tag is by definition TEXT. A compiled-node walk is *not* reliable for this (it reported
      0 leaks here and missed all four).
    - **A `{% url … as X %}` assigned in one block is not in scope in another.** `case_detail.html`
      resolved `case_edit_url` inside `{% block actions %}` but consumed it in `{% block content %}`;
      Django renders each block inside `context.push()` (`loader_tags.BlockNode.render`), so the
      variable is popped before the other block renders. Result: the "Edit case" modal got
      `form_action=""`, rendered a plain button instead of a `<form>`, and its body fields carried
      `form="case-edit-form"` pointing at a form that never existed → the edit dialog was dead and
      `/cases/<n>/edit` vanished from the page (tripping `test_ui_p2_wiring` and
      `test_viewer_affordances`). Fixed by resolving the URL in the same block as its use; a sweep of
      all 16 other `{% url … as %}` sites found no further instance. (An `{% include %}` **without**
      `only` inherits the caller's context, so an assignment *before* an include is fine.)
    Both were live in the shipped page and were only found by rendering it; neither is caught by the
    suite without the fixes above. The test freeze that deferred these checks was lifted on
    2026-10-14 (see `PLAN-2026-10-14-ttp-tab.md` Q7).

### 13.3 Verification performed

Test freeze in effect (user directive: no new or modified tests, no pytest runs), so verification was
`manage.py check`, `ruff format --check`, `ruff check`, `mypy` (repo-wide), a Tailwind recompile probe,
and a throwaway manual smoke render of `/cases/<n>` for seven `?tab=` variants.

Result: HTTP 200 for all; 8 tabs / 8 panels in the DOM; exactly one visible panel per variant; the
allowlist falls back to General for missing, unknown, empty, and hostile (`?tab='><script>…`) input;
`#timeline-events`, `#case-pages`, `#case-procedures` and `#case-merge-modal` all preserved.
`mypy`: no issues in 107 source files. No fabricated URL or inline `margin-top` reaches the response.

A second throwaway smoke render covered the POST-redirect contract (fixes 7 and 8): each of the eight
panel endpoints was POSTed with its own `tab` and returned `302` to `/cases/<n>?tab=<slug>`; the same
endpoints POSTed without a `tab` returned `/cases/<n>` exactly (the two frozen exact-`Location`
assertions still hold); and seven hostile or unknown values (`../../admin/`, `general&x=1`, `settings`,
`'"><script>alert(1)</script>`, `General`, ` attachments`, `#audit`) were all dropped to the bare URL.
A rejected upload now answers `302` to `?tab=attachments` and renders "Upload failed: content type
application/x-msdownload is not allowed" instead of a 500.

Dialog centering (fix 9) was verified in a real browser rather than by reading CSS. A standalone
harness loading the two shipped stylesheets opened both dialogs with `showModal()` and reported
`getBoundingClientRect()` and the computed `margin` from headless Edge (`--dump-dom`), against a
992×616 viewport. Before: both dialogs at `left/top = 0`, centre `(160,85)` and `(103,52)`, computed
`margin: 0px` — the corner. After: centre `(496,308)` for both, i.e. exactly the viewport centre.

Modal *interaction* (fix 10) was verified by hit-testing, not by reading markup — geometry alone is
what let fix 9's bug ship. A throwaway script rendered the **real** `ui/_modal.html` through Django
(`render_to_string`) into a page loading the real `app.css` + `modal.css`, then queried
`document.elementFromPoint()` in headless Edge — the same hit-test the browser uses to pick an event
target:

  | probe | before | after |
  |---|---|---|
  | centre of the Delete button | `div.modal-backdrop` | `button.btn.btn-err` |
  | centre of the Cancel button | `div.modal-backdrop` | `button.btn.modal-cancel` |
  | centre of the × close button | `div.modal-backdrop` | `button.modal-close` |
  | title / body text | `div.modal-backdrop` | `h2.modal-title` / `p.modal-body-text` |
  | cheat-sheet close button | `div.modal-backdrop` | `button.cheatsheet-close` |
  | `document.activeElement` after `showModal()` | `div.modal-backdrop` | `button.modal-close` |
  | point outside the dialog | `div.modal-backdrop` | the `<dialog>` itself |

Behavioural checks against the new handler (with `form.submit` intercepted so nothing navigates):
clicking Delete reaches the button **and submits the form** while the dialog stays open; clicking
outside closes the dialog and submits nothing; a `data-modal-static` dialog survives an outside click;
the cheat-sheet's own control is reachable. The render assertion also confirms no `.modal-backdrop`
is emitted and the `<dialog>`'s first child is `.modal-content`.

### 13.4 What is still open

- **Content definition** — the user will specify what the remaining tabs contain next; the §11 Q2
  placements (Procedures/TTP → Observables, Pages/Tags/custom fields → General, automation runs →
  Audit) are still provisional.
- **Cheat-sheet row** for D1, once tests may be edited again.
- **Conformance tests** for the shell (`?tab=` allowlist, panel visibility, preserved ids) — deferred by
  the freeze; the manual smoke above covers them informally. This now also covers the POST contract
  from fix 7: "a form carrying `tab=tasks` returns to `?tab=tasks`; a request without it stays bare"
  deserves a permanent test, as does the rejected-upload 302 from fix 8 (currently uncovered).
- **Custom fields** have no UI; the General tab does not invent one. TODO §5.7 tracks it.
- **`modal.css` has no guard at all** — `tests/conformance/test_ui_css_tokens.py` reads only
  `app.css`, so the authored modal stylesheet (undefined `--color-*` tokens, focus-outline removal)
  is unchecked, and nothing asserts dialog centering. Extend both when tests may be edited again.
- **Nothing in the suite could have caught fixes 9 or 10** — 1699 tests passed while every dialog was
  a corner sliver whose controls were unclickable, because every modal assertion is DOM-only
  (`<dialog>` present, `data-modal-open` present, `ui/modal.css` referenced). The cheap permanent guard
  is a template assertion that **no `<dialog>` contains an element with `class="modal-backdrop"`**
  (fix 10 would have been caught instantly); a real guard needs a browser-level hit-test, which is a
  dependency decision, not a tab-shell one.
- **Modal gaps vs the Flowbite reference, deliberately not fixed here** (fix 10 was the bug; these are
  the differences that are *choices*): no body scroll lock (a native modal dialog makes the page behind
  it inert but still scrollable, which Flowbite's `fixed inset-0` wrapper hides); no enter/exit
  transition; no gutter on narrow viewports (`.modal { max-width: 100% }`, Flowbite puts `p-4` on its
  positioner); no `closable: false` equivalent beyond the new, currently unused `data-modal-static`;
  and `modal.js` still duplicates what the native element already does (Escape close at `modal.js:85-89`,
  the Tab trap at `:89-105`), where `focusable()` also does not filter `[hidden]`/disabled ancestors.
  The centering and outside-click mechanics are now equivalent to Flowbite's, via `margin: auto` and the
  dialog being its own backdrop target respectively.
- `make check` has not been run since the freeze (it would run pytest).

### 13.5 The Comments / Audit split (decided 2026-10-13)

The user's instruction: *"analyst comments timeline do not need WS and the audit timeline can go to the
audit tab."* Landed as:

- **Comments** (`_tab_comments.html`) — analyst notes only (`kind="comment"`), rendered once, **no
  WebSocket**. Heading renamed from "Timeline" to "Comments". Still owns `id="timeline-events"`, which
  is *forced*: `tests/conformance/test_realtime.py:516,518` asserts `id="timeline-events"` exists on the
  page and that the note form carries `hx-target="#timeline-events"`. The tab badge counts notes only.
- **Audit** (`_tab_audit.html`) — gains the **full ledger** (`#audit-timeline`, every `kind`, comments
  included) above the `AuditLog` table and the automation runs. Audit is the superset; comments appear
  in both tabs by design.
- **`live.js`** now anchors on `#audit-timeline`, which is where `data-ws-url` lives. `#timeline-events`
  (`test_realtime.py:515-516`) and the form's `action` / `hx-*` attributes are unchanged.

Two constraints drove this shape and both are load-bearing:

1. `test_realtime.py:516-518` pins `id="timeline-events"` **and** `hx-target="#timeline-events"`, so the
  note list — the one the form appends into — must keep that id. That is why the audit ledger gets a
  new id rather than the other way round.
2. `live.js` needs a single list to anchor its `sync`/dedupe on. Anchoring on the audit ledger is
  *conservative*: comments are rendered on load and by the HTMX append, and `rendered()` is scoped to
  the list, so a stale anchor can only re-send events the client already has — never drop one.

Verified with the throwaway smoke render: `live.js` invariants intact, Comments = notes only with no
`data-ws-url` and a badge of 2, audit ledger = all four events oldest-first with `data-ws-url`, and the
four `?tab=` variants each showing exactly one panel.

**Not addressed by this split:** a live event landing in the (usually inactive) Audit panel is invisible
until the user opens that tab. Plan risk R2 flagged this; a tab badge that updates on arrival is the
obvious follow-up, not part of this decision.
