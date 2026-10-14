# PLAN-2026-10-14 — Split TTPs out of Observables into a dedicated `ttp` tab

**Status:** Approved — implemented (verification on hold per user freeze)
**Owner:** planner
**Approved decisions (2026-10-14):** TTPs tab after Observables (Q1); tab badge = procedures
count (Q2); **add form is a modal, not inline** (Q3, deviates from the draft recommendation);
`fa-sitemap` icon (Q4); card heading stays `Procedures`/`id="h-procs"` (Q5); cheat-sheet `1`–`9`
update deferred (Q6); test edits deferred to the user (Q7). No JS fallback is required for the
procedure form — JavaScript is mandatory in this UI (user ruling).
**Scope owner after approval:** `django-frontend` (with `qa-tester` for tests, `security-auditor` for the S-items)
**Related:** `AGENTS.md` §2 Modules B/C, §3 (`Procedure`/TTP), §6 (Design & Style);
`docs/planning/PLAN-2026-10-13-case-detail-tabs.md` §7.4 (procedures parked on Observables "provisional"),
§13 fixes 1–10; `PLAN-2026-10-14-case-pages-ui.md`; `docs/spec/deviations.md`.

---

## 1. Summary

The case-detail tab shell ships **8 tabs**. The **Procedures / TTP** card is currently nested inside
the **Observables** panel (`ui/templates/ui/_tab_observables.html` §L27–79), a placement the tab-shell
plan itself labelled *provisional* (§7.4). Analysts read a procedure (a described ATT&CK technique) as
a distinct object from an observable (a raw artifact such as an IP or hash), and mixing the two makes
both harder to scan.

This plan promotes Procedures into their **own ninth tab**, slug **`ttp`**, label **TTPs**. It is a
**pure relocation** plus the tab-shell bookkeeping that a new tab requires:

- a ninth entry in the `CASE_TABS` allowlist (`ui/views.py`),
- a ninth `<section role="tabpanel">` in `ui/case_detail.html`,
- the card body moved verbatim into a new `ui/templates/ui/_tab_ttp.html`,
- the form's hidden `tab` field changed from `observables` to `ttp`,
- the `tabs.js` number-key ceiling raised from **8** to **9**.

Nothing about the procedures feature changes: same model, same endpoints, same fields, same
permission gating. Existing deep links (`?tab=observables`) keep working — the Observables tab just
holds fewer things.

## 2. Goals

- **G1** — Procedures/TTP render on a dedicated tab: slug `ttp`, label **TTPs**, deep-linkable as
  `GET /cases/<number>?tab=ttp`.
- **G2** — Full relocation: the Procedures card **no longer appears inside the Observables panel**.
  Observables shows only the Artifacts table.
- **G3** — **Zero functional regression.** Every existing procedure selector, field name, route,
  message and permission gate is unchanged; `tests/conformance/test_ui_procedures.py` stays green
  without edits.
- **G4** — Server-rendered first paint: all nine panels (including `ttp`) are in the initial HTML of a
  plain `GET /cases/<n>`; `?tab=ttp` only chooses which one is not `hidden` (progressive enhancement,
  exactly as the existing shell).
- **G5** — Keyboard parity: the new tab is reachable by `←`/`→`/`Home`/`End`, has roving `tabindex`,
  and the numeric shortcut ceiling becomes 9.
- **G6** — Modal discipline preserved: no new native `confirm()`/`alert()`/`prompt()`; any confirmation
  uses the existing `<dialog>`/`_modal.html` pattern.

## 3. Non-goals

- **N1** — No new database models, fields, or migrations. `Procedure`, `TTP` and `case.procedures`
  are unchanged.
- **N2** — No API changes. `POST /case/{id}/procedures`, `GET /case/{id}/export`, etc. are untouched.
- **N3** — No change to the **Observables** content itself (Artifacts table stays as-is), and no new
  cross-reference between an observable and a procedure.
- **N4** — No redesign of the Procedures card, its fields, or the ATT&CK dropdown.
- **N5** — No new tab-shell architecture. Still client-side show/hide over server-rendered panels; no
  lazy `hx-get` panels.
- **N6** — Not touching `alert_detail`, the API `case_procedures` views, or the audit/query specs.
- **N7** — No rename/restructure of the `Procedure` model or the `/procedures` endpoints to "TTP".

## 4. Current-state assessment (the templates in question)

### 4.1 `ui/templates/ui/case_detail.html` (121 lines)

- §L34–56: `<div class="tabs" role="tablist">`; one `<a class="tab" role="tab" href="?tab={{ slug }}">`
  per tab. Icons are hard-coded per slug (`{% if tab.slug == "observables" %}…`), and every tab gets
  `aria-keyshortcuts="{{ forloop.counter }}"`.
- §L58–90: `<div class="tabpanels">` with one `<section class="tabpanel" role="tabpanel"
  id="panel-<slug>" aria-labelledby="tab-<slug>" tabindex="0" {% if active_tab != "<slug>" %}hidden{% endif %}>`
  per tab, each `{% include %}`-ing a `_tab_<slug>.html` partial.
- §L114: the edit-case `_modal.html` include; §L92–112: the merge `<dialog>`.
- §L119–120: `live.js` + `tabs.js`, both `defer`, page-scoped.

### 4.2 `ui/templates/ui/_tab_observables.html` (79 lines)

- §L2–25: the **Artifacts** card (`id="h-obs"`) — observables table.
- §L27–79: the **Procedures** card (`id="h-procs"`), heading `Procedures <span class="count">{{ procedures|length }}</span>`,
  `_procedure_entry.html` rows, empty-state `No procedures attached yet.`, and — when `can_mutate` — an
  inline add form posting to `{% url 'ui-case-procedure-create' case.number %}` with a hidden
  `name="tab" value="observables"` (must become `ttp`).

### 4.3 `ui/templates/ui/_procedure_entry.html` (28 lines)

One `<li class="procedure">` row: ATT&CK-ID chip (linked `ttp.ttp_code` wins over `pattern_id`),
name, tactic, markdown description, occurrence date, linked TTP name. **Moves unchanged**; it is
already a standalone partial.

### 4.4 `ui/views.py`

- `CASE_TABS` (§L667–676) is the **single allowlist** (8 tuples); `CASE_TAB_SLUGS` is derived from it.
- `case_detail` (§L709–786) resolves `active_tab` against `CASE_TAB_SLUGS`, builds per-tab counts,
  and already puts `procedures=case.procedures.select_related("ttp").order_by("occur_date", "id")`
  and `ttps=TTP.objects.order_by("name")` into the context. **No query changes are needed.**
- `_case_detail_redirect` (§L680–691) validates the submitted `tab` against `CASE_TAB_SLUGS`, so adding
  `ttp` to the tuple makes the redirect land on `?tab=ttp` automatically.

### 4.5 `ui/static/ui/tabs.js` (153 lines)

Generic over the DOM: it reads `[role="tab"]` and their `aria-controls`, so it needs **no change** for
a ninth tab except one constant: the number-key guard at §L116 (`event.key >= "1" && event.key <= "8"`)
must become `<= "9"`. Arrow/Home/End already wrap over `tabs.length`.

### 4.6 Tests that constrain the move

- `tests/conformance/test_ui_procedures.py:150,206` assert `id="h-procs">Procedures <span class="count">N</span>`
  and the empty-state string **against the full page body** (`GET /cases/<n>`, no `?tab=`). Because all
  panels are server-rendered, moving the card to a different panel keeps these assertions true — the
  card is still in the response. This is the safety property that makes the move low-risk.
- `tests/conformance/test_ui_procedures.py:160` asserts the add form's `action` is
  `reverse("ui-case-procedure-create")`; the route and action string are unchanged.
- `tests/conformance/test_tenant_scoping.py` and `test_viewer_readonly_ui.py` enumerate the
  `ui-case-procedure(s)-create` routes — unchanged (not touched) — so no allowlist breakage.

## 5. Architecture decision

**Reuse the existing tab shell verbatim; the new tab is data (allowlist entry + panel + partial), not
new mechanism.**

| Option | Verdict |
| --- | --- |
| **A. Add a 9th entry to `CASE_TABS`, a 9th panel to `case_detail.html`, and a new `_tab_ttp.html` holding the relocated card; server-rendered like the other eight; `?tab=ttp` selects it.** | **CHOSEN.** |
| B. Reuse the `ttp` slug but keep procedures rendering in `_tab_observables.html` and merely alias the tab to that panel | **Rejected.** Violates G2 (Observables must not contain procedures) and produces two tabs controlling one panel. |
| C. Lazy-load the TTP panel with `hx-get` | **Rejected.** Breaks the server-rendered first-paint contract (G4) and the body-scoped procedure tests; inconsistent with the other 8 panels. |
| D. Rename the model/endpoints to TTP | **Rejected (N7).** `Procedure` is the TheHive-aligned name and is API-visible; a rename is out of scope. |

**ADR:** none required. This is a data-level extension of an already-decided UI pattern
(`PLAN-2026-10-13-case-detail-tabs.md` §4). The tab shell is not yet site-wide navigation, so the
ADR-008 trigger condition in that plan is not met.

## 6. Data model & schema

None. No migrations. The new tab reads the same relations the page already reads:

- `case.procedures.select_related("ttp")` (already in the `case_detail` context),
- `TTP.objects.order_by("name")` for the optional vocabulary dropdown (already in context).

## 7. API / routing contract

No new endpoints. One additional legal value for an existing query parameter:

```
GET /cases/<number>?tab=ttp
```

| Aspect | Contract |
| --- | --- |
| `slug` value | exactly `ttp` |
| Allowlist | added to `CASE_TABS` → `CASE_TAB_SLUGS` (frozenset, server-side binding; never interpolated into a template path) |
| Absent / unknown / malformed | unchanged: falls back to `general` with HTTP 200 |
| Response | unchanged shape — `200` `ui/case_detail.html`; `panel-ttp` not `hidden`, the other eight `hidden` |
| Auth | unchanged (`login_required`, `resolve_case(..., write=False)`); no new route, so no authz surface added |
| POST return | `_case_detail_redirect` now accepts `tab=ttp`, so the add-procedure form returns to `?tab=ttp` |
| Existing `?tab=observables` | still valid; renders the Artifacts-only Observables panel |

## 8. UI / UX

### 8.1 Tab placement and ordering

Insert **TTPs** immediately **after Observables** and before Attachments:

```
General · Alerts · Tasks · Observables · TTPs · Attachments · Similar · Comments · Audit
```

Rationale: TTPs is conceptually adjacent to Observables (both are case artifacts), and inserting at
position 5 keeps every existing tab's relative order, so muscle memory and numeric shortcuts for
Attachments…Audit shift by exactly one. (Confirm in Q1 — an alternative is last.)

New tab markup mirrors the existing pattern:

```html
<a class="tab" role="tab" id="tab-ttp" href="?tab=ttp"
   aria-controls="panel-ttp" aria-selected="{% if active_tab == 'ttp' %}true{% else %}false{% endif %}"
   tabindex="{% if active_tab == 'ttp' %}0{% else %}-1{% endif %}"
   aria-keyshortcuts="5">
  <i class="fa-solid fa-sitemap" aria-hidden="true"></i> TTPs
</a>
```

Icon: **`fa-sitemap`** (recommended; `fa-diagram-project` is already the Similar tab and the submit
button). Confirm in Q4.

### 8.2 Panel

```html
<section class="tabpanel" role="tabpanel" id="panel-ttp" aria-labelledby="tab-ttp" tabindex="0"
         {% if active_tab != "ttp" %}hidden{% endif %}>
  {% include "ui/_tab_ttp.html" %}
</section>
```

`_tab_ttp.html` is the Procedures card **moved verbatim** from `_tab_observables.html` §L27–79:
same `id="h-procs"`, same heading/count, same `_procedure_entry.html` loop, same empty state, same
`can_mutate` gating, same form fields. The **only** textual change is the form's hidden field:
`value="observables"` → `value="ttp"`.

### 8.3 Observables panel after the move

`_tab_observables.html` becomes the Artifacts card only (§L2–25). No other change. The stale template
comment `{# Procedures / TTP — provisional placement per §7.4 #}` is removed with the card (and the
standing rule from `PLAN-2026-10-13` §13.2 fix 6 applies: never write a multi-line `{# … #}` — use
`{% comment %}`).

### 8.4 Keyboard

- `←`/`→` wrap across all 9 tabs, `Home`/`End` jump to first/last — **already generic in `tabs.js`**.
- Number keys: `1`–`9`. One edit: `tabs.js` §L116 `event.key <= "8"` → `<= "9"`. The guard against
  form-field focus (`isTyping`) is unchanged.
- `aria-keyshortcuts` on each tab is already `{{ forloop.counter }}`, so tab 9 advertises `9` for free.
- `Esc` stays unbound (reserved for modals).

### 8.5 Accessibility

- `aria-selected` (not the CSS class) is the source of truth for the active tab; the class is styling.
- Panel `id`/`aria-labelledby` pairing follows the existing `panel-<slug>` / `tab-<slug>` convention.
- Icon is `aria-hidden="true"`; the label "TTPs" is text, so no icon-only control.
- No severity/status is signalled by colour; the count is text.

### 8.6 Modal reuse

- **Resolved (Q3): the add-procedure form is a modal, not inline.** The attach form moved into a
  reusable `ui/templates/ui/_modal.html` dialog (`ui/templates/ui/_tab_ttp.html:21`,
  `modal_id="procedure-create-modal"`, `form_id="procedure-create-form"`,
  `body_template="ui/_procedure_form_modal_body.html"`). This deviates from the draft recommendation
  in the earlier version of this section (which said "stays inline") — Q3 supersedes it.
- It **reuses** the native `<dialog>` pattern (`data-modal-open` / `data-modal-close`, Esc to close,
  Tab trap) and introduces no `confirm()`/`alert()`/`prompt()`.
- The modal footer remains a real `<form method="post" action="…/procedure">` with a CSRF token, so
  `test_ui_procedures.py::test_case_detail_procedure_form_is_real_form_with_csrf` still verifies the
  same invariant; it was updated to match the modal markup (`_modal.html` renders
  `<form id="…" method="post" action="…">` — `id` first), not to relax it.
- No destructive action is added, so no confirmation dialog is needed on this tab (procedures have no
  delete UI today — see N/A in Q5).

### 8.7 Content distribution (as-built target)

| Tab | Body |
| --- | --- |
| Observables | Artifacts table (`id="h-obs"`) only |
| **TTPs** | Procedures card (`id="h-procs"`) + inline attach form (gated by `can_mutate`) |

## 9. Phases, tasks and acceptance criteria

### P1 — Backend allowlist (backend)

- **T1-a** Add `("ttp", "TTPs")` to `CASE_TABS` at position 5 (`ui/views.py`).
  - **AC1** `GET /cases/<n>?tab=ttp` returns 200 and does **not** fall back to General.
  - **AC2** `GET /cases/<n>?tab=bogus` and `?tab=` still render General (allowlist unchanged in spirit).
  - **AC3** No query changes: `case_detail` issues the same queryset count as before (procedures were
    already loaded).
- **T1-b** (Optional, Q2) Add `"ttp": procedures count` to the per-tab `counts` dict.
  - **AC4** The TTPs tab renders a `<span class="count">N</span>` matching `case.procedures.count()`;
    when zero it renders `0` (consistent with Observables/Tasks).

### P2 — Template relocation (frontend)

- **T2-a** Create `ui/templates/ui/_tab_ttp.html` containing the Procedures card, moved from
  `_tab_observables.html` §L27–79, with hidden `tab` value changed to `ttp`.
  - **AC5** `_tab_observables.html` no longer contains `id="h-procs"`, the attach form, or the word
    "Procedures"; it contains only the Artifacts card.
  - **AC6** `_tab_ttp.html` contains `id="h-procs"`, the count span, the `_procedure_entry.html` loop,
    the `No procedures attached yet.` empty state, and the form posting to
    `{% url 'ui-case-procedure-create' case.number %}` with hidden `name="tab" value="ttp"`.
- **T2-b** Add the TTPs tab link + `panel-ttp` section to `ui/case_detail.html` at position 5, with the
  icon chosen in Q4.
  - **AC7** A plain `GET /cases/<n>` renders **9** `role="tab"` links and **9** `role="tabpanel"`
    sections; exactly one panel lacks `hidden`.
  - **AC8** `?tab=ttp` leaves only `panel-ttp` visible; `?tab=observables` leaves only `panel-observables`
    visible and that panel shows Artifacts but **no** Procedures card.
- **T2-c** Inter-tab order is stable: Attachments → Similar → Comments → Audit labels keep their
  relative order (only their numeric shortcut and position shift by one).

### P3 — Keyboard/JS (frontend)

- **T3-a** Raise the numeric shortcut ceiling in `ui/static/ui/tabs.js` §L116 to `9`.
  - **AC9** With focus outside a form, pressing `5` activates TTPs; pressing `5` inside a text input
    types `5`. Pressing `9` activates Audit.
  - **AC10** No other `tabs.js` behaviour changes (arrow wrap, `pushState`, `popstate` sync, focus
    retention).
- **T3-b** (Optional, Q6) Advertise the tab number shortcuts in the `?` cheat-sheet.
  - **AC11** If adopted: `keys.js` `CHEATSHEET` and the server-rendered table in `base.html` both gain
    the same row, and the conformance test that keeps those two in sync is updated in the same change.
    (Deferred by default — `PLAN-2026-10-13` D1 is still open.)

### P4 — Tests (qa-tester)

> **Freeze caveat:** check the current test-edit policy before starting. The 2026-10-13 shell work ran
> under a test freeze; if the freeze is still in force, P4 is limited to running the existing suite and
> the manual smoke in P5, and the new assertions below become a documented follow-up.

- **T4-a** Extend the case-detail tab conformance (wherever the 8-tab shell is asserted, if anywhere;
  the shell tests were deferred per `PLAN-2026-10-13` §13.4) to expect 9 tabs and the `ttp` slug.
- **T4-b** Regression: `tests/conformance/test_ui_procedures.py` passes **unmodified** after the move
  (proves the card is still server-rendered and the action string is unchanged).
- **T4-c** New: `?tab=ttp` renders `panel-ttp` not `hidden`; `?tab=observables` renders Artifacts with no
  `h-procs` inside `panel-observables`; unknown tab → General.
- **AC12** `make check` green at **≥ the current baseline** plus the new tests (report the exact
  summary line).

### P5 — Frontend verification & docs (frontend + planner)

- **T5-a** Manual smoke render: 9 `?tab=` variants + absent + unknown/hostile (`?tab='><script>…`);
  assert HTTP 200, exactly one visible panel each, `id="h-procs"` present on every full page, and the
  hidden `tab=ttp` round-trip (POST the attach form → `302` to `?tab=ttp`).
  - **AC13** All ten requests behave as specified; no template path is built from `?tab=`.
- **T5-b** Update `COMPLETED.md`, `TODO.md` (supersede the "Procedures → Observables" provisional
  placement), and `docs/spec/deviations.md`; add a one-paragraph note to
  `docs/spec/ui-case-detail-tabs.md` recording the ninth tab.
  - **AC14** Any deviation actually made is recorded in §13 of this plan.

## 10. Security

- **S1 — Parameter binding unchanged.** `?tab=` is resolved through `CASE_TAB_SLUGS`; adding `ttp`
  widens the allowlist by exactly one literal. Never interpolate the value into a template path.
- **S2 — No new route, no new authz surface.** The procedures endpoints are untouched, so the tenant
  scoping + viewer route-enumeration tests keep their exact coverage.
- **S3 — Per-panel gating preserved.** The attach form keeps its `{% if can_mutate %}` guard; moving it
  between partials must not move the guard out of scope. Re-verify after the move (viewer sees no form;
  `@not_viewer` on the POST remains the enforcement point).
- **S4 — CSRF unchanged.** The form keeps `{% csrf_token %}`; the hidden `tab` field is not trusted for
  anything but choosing a redirect target within the allowlist.
- **S5 — Redirect allowlist.** `_case_detail_redirect` now accepts `tab=ttp`; a hostile/unknown value
  still drops to the bare `/cases/<n>` URL.
- **S6 — No secrets.** No new configuration, env var, or committed value.

## 11. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Moving the card breaks the body-scoped `test_ui_procedures.py` assertions | Low | High | All panels are server-rendered, so the card stays in the `GET /cases/<n>` body; P4 T4-b verifies unmodified tests. |
| R2 | Attachments…Audit numeric shortcuts shift by one, surprising power users | Med | Low | Documented in §8.1; cheat-sheet row optional (P3 T3-b); arrows/Home/End unaffected. |
| R3 | A stale `tab=observables` link (bookmark) no longer shows procedures | Low | Low | Expected: Observables intentionally no longer owns procedures (G2); the TTPs tab is discoverable and `?tab=ttp` documented. |
| R4 | Hard-coded number-key ceiling silently caps at 8 (the 9th tab unreachable by keyboard number) | Med | Med | Explicit P3 T3-a edit + AC9. |
| R5 | Multi-line `{# … #}` leak when editing the partial (standing gotcha) | Low | Med | Use `{% comment %}`; carried from `PLAN-2026-10-13` §13.2 fix 6. |
| R6 | Tab count badge added but not matching the panel contents | Low | Low | Only add the count if Q2 says yes, and count exactly `procedures|length` (single source, same as the heading). |
| R7 | Test freeze still in force blocks P4 | Med | Low | P4 marked conditional; manual smoke (P5) covers the behaviour and the assertions become a tracked follow-up. |

## 12. Open questions (need answers before P1 lands, or explicit deferral)

- **Q1 — Tab order.** TTPs at position 5, right after Observables (recommended), or last (position 9)?
- **Q2 — Tab badge.** Show a procedures count on the TTPs tab (recommended: yes, parity with other
  resource tabs), or rely on the in-card `Procedures <span class="count">` only (no change to the view)?
- **Q3 — Add form placement.** **Resolved 2026-10-14: move it into a reusable `_modal.html` dialog**
  (the draft recommendation was to keep it inline; the user chose the modal for consistency with the
  other create forms). See §8.6.
- **Q4 — Icon.** `fa-sitemap` (recommended) vs another Font Awesome Free icon for the TTPs tab?
- **Q5 — Heading/wording.** Keep the card heading `Procedures` and `id="h-procs"` (recommended —
  zero test churn; the *tab* is what is named "TTPs"), or rename the card to "TTPs / Procedures"
  (would require editing `test_ui_procedures.py`)?
- **Q6 — Number shortcuts in the cheat-sheet.** Advertise `1`–`9` in the `?` modal now (would touch
  `keys.js` + `base.html` + the sync test), or leave as the existing D1 follow-up? (Recommend defer.)
- **Q7 — Test-edit policy.** Is the test freeze from 2026-10-13 lifted, so P4 tests can be added now?
  **Resolved 2026-10-14: lifted.** The freeze was an implementation-time hold, not a prohibition on
  conformance-test maintenance. Under it the suite was not run, which is how the Q3 modal change left
  `test_ui_procedures.py::test_case_detail_procedure_form_is_real_form_with_csrf` asserting the old
  inline markup. On unfreeze that test — and the new-route enumeration in `test_tenant_scoping.py` —
  were updated to match the shipped behaviour without weakening any invariant (see §8.6).

## 13. Implementation brief (for `django-frontend`)

Read §5, §7, §8, §10 first — the design choice and the security notes are binding.

1. `ui/views.py` → `CASE_TABS` (currently ~L667): insert `("ttp", "TTPs")` after `("observables", "Observables")`.
   If Q2 = yes, add `"ttp": <procedures count>` to the `counts` dict in `case_detail`. Do not change the
   querysets (procedures/ttps are already in context).
2. `ui/templates/ui/_tab_ttp.html` → **new**, the Procedures card moved from `_tab_observables.html`
   §L27–79, hidden `tab` value `observables` → `ttp`. Use `{% comment %}` for any multi-line comment.
3. `ui/templates/ui/_tab_observables.html` → delete the Procedures card and the stale placement
   comment; keep the Artifacts card exactly as-is.
4. `ui/templates/ui/case_detail.html` → add the TTPs tab link (position 5, icon per Q4) and the
   `panel-ttp` section with the same `hidden`/`aria-*` pattern as its siblings.
5. `ui/static/ui/tabs.js` → change the numeric guard ceiling `"8"` → `"9"` (§L116). No other change.
6. Do **not** touch `cases/`, `alerts/`, `observables/`, `identity/`, the API, or any migration.
7. **Constraints:** `ruff format` any `.md` you edit; run `make check` before reporting; **do not commit**.
8. **Report:** files changed, AC-by-AC status, the `make check` summary line, the ten-request smoke
   result, and any deviation (to be recorded in §13 here).

## 14. As-built notes and deviations

*(Empty — to be filled after implementation. All deviations must be recorded here, not silently
absorbed.)*

## 15. References

- `AGENTS.md` §2 (Modules B/C/D), §3 (schema), §6 (design/style, modals, keyboard).
- `docs/planning/PLAN-2026-10-13-case-detail-tabs.md` — the shell, §7.4 provisional placements,
  §13.2 fixes 1–10 (multi-line-comment rule, POST-redirect `tab` contract), §13.5 Comments/Audit split.
- `docs/planning/PLAN-2026-10-14-case-pages-ui.md` — sibling case-page work.
- Tests: `tests/conformance/test_ui_procedures.py`, `test_viewer_readonly_ui.py`,
  `test_tenant_scoping.py`.
