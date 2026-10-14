# PLAN 2026-10-14 — Observables tab: add / edit / remove artifacts (case-scoped CRUD UI)

Status: **Implemented — pending the user's verification run.** Built on the Q1–Q6/R3 decisions (§11);
four defects found in the first cut were fixed (§13). Tests remain frozen (BINDING): the user runs
the suite. **Not committed.**
Owner: planner → `django-frontend` (+ `django-backend` for the views/urls)
Related: `PLAN-2026-10-14-case-pages-ui.md` (the tab-CRUD idiom this mirrors),
`PLAN-2026-10-14-ttp-tab.md` (modal-body `form=` contract), AGENTS.md §2 Module C (observables are
standalone, auto-linked entities), ADR-004 (per-org observable dedupe), ADR-006 (viewer read-only),
`PLAN-2026-10-12-viewer-role-enforcement.md`.

---

## 1. Summary

The case **Observables** tab can *list* artifacts but cannot *manage* them: `_tab_observables.html`
renders a read-only table (Type / Value / Seen in) with no create, edit or remove affordance. The only
places an artifact can be touched today are:

- **create** — `POST /api/v1/case/{id}/observable` (JSON) or the "Re-extract artifacts" button
  (`ui-case-extract`, extraction only, no manual value entry);
- **edit / delete** — the standalone observable detail page (`/observables/<id>` → `ui-observable-edit`,
  `ui-observable-delete`), which is a full-page detour from the case.

This plan adds the missing affordances **on the tab**, mirroring the Pages/Procedure idiom already
shipped on the General and TTP tabs (per-row action + shared `_modal.html` modal + plain `POST` →
`_case_detail_redirect`).

## 2. Goals

- **G1** Add one artifact to a case from the tab: choose a **type** from the observable vocabulary and
  enter a **value** (+ optional message, TLP, PAP, IOC/Sighted/Ignore-similarity, enrichment JSON).
- **G2** Edit an artifact's editable fields from the tab (value stays immutable — it is the per-org
  dedupe key).
- **G3** Remove an artifact from the case from the tab (semantics: §11 Q1).
- **G4** Keep every contract: `@not_viewer` on every mutating route, `resolve_case(..., write=True)`,
  CSRF via the modal footer form, `?tab=observables` preservation, no schema change.
- **G5** Full keyboard/a11y parity with the other tabs: real `<form>`/`<button>`, `<dialog>` modal,
  icons supplemental to labels, no colour-only signalling.

## 3. Non-goals

- **N1** No change to `Observable` / `CaseObservable` models; no migration.
- **N2** No new REST API surface (the JSON endpoints are unchanged). *(Exception only if Q1 lands on
  "unlink": see §6.)*
- **N3** No bulk-select / bulk-edit UI (the API has `observable/_bulk`; the tab stays single-row).
- **N4** No new tests (standing BINDING directive: tests frozen — user owns test edits).
- **N5** No per-case permission model beyond ADR-004/ADR-006 (org-owned artifacts).
- **N6** No live `observable` WebSocket frame (parity with today: the API publishes none; see §11 R4).

## 4. Architecture / approach

| Concern | Decision | Rationale |
| --- | --- | --- |
| Transport | plain `POST` → `_case_detail_redirect(request, case.number)` | matches Pages/procedures/every write on this page; degrades to a normal form |
| Create UI | "New artifact" button → `_modal.html` + new `ui/_observable_form_modal_body.html` | the app's documented create idiom; `modal.js` gives Esc/backdrop/focus-trap + `[autofocus]` |
| Edit UI | per-row **Edit** → same body template, prefilled | one shared form body, like `_page_form.html` |
| Remove UI | per-row confirmation modal (mirrors the detail-page delete modal) | AGENTS.md: no native `confirm()` |
| Write path | reuse the **shared appliers** so the UI and API cannot drift | `observables.extractor.add_observable` (create+link+automation) and `cases.views._set_observable_fields` (edit) |
| Guard | reuse `cases.views._observable_cross_case_guard` / `_observable_affected_cases` | already imported by `ui/views.py:74-75`; keeps the force rule in one place |

### 4.1 Routes (UI) — proposed

Case-scoped, POST-only, mirroring `ui-case-page-*`:

| Name | Method | Path | View |
| --- | --- | --- | --- |
| `ui-case-observable-create` | POST | `/cases/<case_id>/observable` | `case_observable_create` |
| `ui-case-observable-edit` | POST | `/cases/<case_id>/observable/<observable_id>/edit` | `case_observable_update` |
| `ui-case-observable-remove` | POST | `/cases/<case_id>/observable/<observable_id>/remove` | `case_observable_remove` |

All three: `@not_viewer` (outermost) → `@login_required` → `@require_POST`, `resolve_case(user, id,
write=True)`, the observable reached through the case (`CaseObservable.objects.get(case=case,
observable_id=…)`, so a foreign link id 404s), `messages.*`, `_case_detail_redirect`.

> The existing observable-scoped routes (`ui-observable-edit` / `ui-observable-delete`) are **kept** for
> the standalone detail page (`/observables/<id>`). They are *not* reused here because both render/redirect
> to the detail page, not back to the case tab; the tab route calls the same applier functions, so only
> payload assembly is small-duplicated, never the field logic.

## 5. Data model

**No migration.** Existing shapes used as-is:

- `observables.models.Observable` — `data_type` (FK `ObservableType`), `data` (dedupe key, immutable),
  `normalized_data`, `message`, `tlp`/`pap` (0–3 for observables), `ioc`, `sighted`, `ignore_similarity`,
  `enrichment_data` (JSON), `owner_org`.
- `observables.models.ObservableType` — seeded `ip, domain, fqdn, url, mail, file, hash, user, other`
  (`0002_seed.py`); user-extensible (ADR-002 §D4).
- `cases.models.CaseObservable` — the link row (`case`, `observable`); its existence is "linked to this
  case". Removing the link is a row-`delete()`, **not** an `Observable` delete.

## 6. API contracts

Unchanged unless Q1 = "unlink", in which case the *UI-only* view mutates `CaseObservable` directly (no
new REST route; the API has no unlink endpoint today — the ambiguous naming note below stands).

For reference, the wire keys the UI must speak (identical to the API's, via the shared appliers):

| Field | Create | Edit | Notes |
| --- | --- | --- | --- |
| `dataType` | required, must be in vocabulary | editable | unknown type → `messages.error`, no row created (`add_observable` returns `None`) |
| `data` | required | **immutable** (read-only control) | per-org dedupe key |
| `message` | optional | optional | ≤500 |
| `tlp` / `pap` | optional | optional | observable range **0–3** (the form offers `tlp ≤ 3`, matching `_render_observable_edit_form`) |
| `ioc` / `sighted` / `ignoreSimilarity` | optional | optional | checkbox → bool |
| `enrichment_data` | optional JSON | optional JSON | invalid JSON → `messages.error`; applied directly (plain column) |
| `force` | — | required when linked to >1 case | `_observable_cross_case_guard`, acknowledgement lives **inside** the form |

## 7. UI/UX

Structure of the Observables tab after this change:

```
<section class="card" aria-labelledby="h-obs">
  <h2 id="h-obs">Artifacts <span class="count">N</span></h2>
  <table>  Type | Value | Seen in | (chart link?) | Actions[Edit][Remove]   ← Actions column, can_mutate only
  </table>
  <button class="btn btn-primary" data-modal-open="observable-create-modal">
    <i class="fa-solid fa-plus"></i> New artifact</button>
</section>
<!-- create modal (can_mutate) -->
<!-- per-row edit + remove modals (can_mutate); Remove modal lists the shared cases + force ack -->
```

Files (proposed):

- `ui/templates/ui/_tab_observables.html` — add Actions column, the header button, and the per-row modals.
- `ui/templates/ui/_observable_form_modal_body.html` — **new**, the add/edit modal body. **Every control
  must carry `form="{{ form_id }}"`** (see §12 F1) and a hidden `tab=observables`; unique ids per modal
  instance (create vs edit) to avoid duplicate ids.
- `ui/templates/ui/_observable_remove_modal.html` — **new** (or inline), the confirmation with the
  affected-cases list + `force` checkbox when >1 case.
- `ui/urls.py`, `ui/views.py` — the three routes/views.
- Optionally link the row value to `ui-observable-detail` (full record + enrichment), so the tab is a
  jumping-off point without duplicating the detail page.

Keyboard: `n`/click opens the create modal (defer any shortcut-strip change, consistent with TTP Q6);
`Escape` closes; Tab is trapped; every action reachable without a mouse.

## 8. Phases & acceptance criteria

### P1 — routes + views
| # | Task | AC |
| --- | --- | --- |
| 1.1 | 3 routes named `ui-case-observable-create` / `-edit` / `-remove` | `manage.py check` clean; each `@not_viewer @login_required @require_POST` |
| 1.2 | `case_observable_create` → `add_observable(case, type, value)` | a valid type+value adds + links the artifact and redirects to `?tab=observables`; an unknown type flashes an error and creates nothing |
| 1.3 | `case_observable_update` via `_set_observable_fields` + `_observable_cross_case_guard` | edit persists; a >1-case artifact without `force` is refused with a message; `data` cannot be changed |
| 1.4 | `case_observable_remove` — semantics per Q1 | Q1=a: link row gone, `Observable` survives on its other cases; Q1=b: artifact deleted honouring the force guard |
| 1.5 | foreign / cross-tenant addressing | an id not linked to `case` → 404; a case the caller cannot write → 404 |
| 1.6 | viewer denial | the route-enumerating viewer test POSTs each new route and expects 404 (`@not_viewer`) |

## 8.1 Phases — templates / verification

### P2 — templates

| # | Task | AC |
| --- | --- | --- |
| 2.1 | "New artifact" button + create modal, `can_mutate` only | a viewer sees no create/edit/remove control or modal in the DOM |
| 2.2 | Actions column with Edit / Remove per row, `can_mutate` only | an analyst sees both; each opens its own modal |
| 2.3 | shared `_observable_form_modal_body.html`, correct `form="{{ form_id }}"` wiring | create and edit modals submit through their footer form; TTP regression (`f-p`) does not recur |
| 2.4 | remove/force modal | linked-to->1-case artifact shows the case list + required acknowledgement |
| 2.5 | value read-only on edit; type/keyboard hints | edit modal's value input is `readonly disabled` with a hint (parity with the detail page) |

### P3 — verification (executed by the **user**, tests frozen)

| # | Task | AC |
| --- | --- | --- |
| 3.1 | existing suite stays green | incl. `test_ui_procedures.py`, `test_viewer_affordances.py`, `test_viewer_readonly_ui.py`, `test_ui_loop.py`, `test_t2_*` |
| 3.2 | route enumeration | `test_viewer_readonly_ui.py` covers the three new routes automatically |

## 9. Security

- **S1 Authorization**: `@not_viewer` + `resolve_case(..., write=True)`; the link is reached *through*
  the case (`case=case, observable_id=…`) so a foreign id cannot be addressed. Uniform 404s.
- **S2 Cross-org (ADR-004)**: `Observable` rows are per-org. `add_observable` creates in `case.owner_org`
  (API parity), so a share-holder with write can add. **Edit/force-delete of an artifact owned by another
  org must be refused** (the detail page gates this on `is_owner`); Q5 confirms the exact rule.
- **S3 XSS/injection**: no `|safe`, no `innerHTML`; values are auto-escaped in templates; enrichment JSON
  is parsed with `json.loads`, never `eval`.
- **S4 CSRF**: `{% csrf_token %}` in each modal footer form; no new JSON surface.
- **S4b Automation safety**: create routes through `add_observable`, which fires `observable.created`
  automation — a hostile value therefore reaches a playbook. This is the **existing API contract**
  (same helper); the UI adds no new sink, but note it in review.
- **S5 Secrets**: none rendered.

## 10. Testing

- No new tests (frozen). The routes are covered the day they land by
  `tests/conformance/test_viewer_readonly_ui.py` (URLconf enumeration) if it enumerates `ui-` routes.
- The user runs the suite; the plan (§12) flags the two defects to check.

## 11. Risks & open questions

| # | Risk / question | **Resolved decision (2026-10-14: "proceed with recommendations")** |
| --- | --- | --- |
| Q1 | **Remove semantics** | **(a) unlink** — row action deletes the `CaseObservable` link only; the `Observable` survives on its other cases. Destructive artifact delete stays on `/observables/<id>`. |
| Q2 | **Edit surface** | **modal on the tab**, sharing `_observable_form_modal_body.html` with create; calls `_set_observable_fields`. |
| Q3 | **Type-specific input** | **generic** Type `<select>` + Value input; type-aware `placeholder`/`inputmode` only. Server-side `resolve_observable` is authoritative. |
| Q4 | **Optional fields on create** | Type + Value **required**; Message/TLP/PAP/IOC/Sighted/Ignore/Enrichment **optional** and **not** inherited from the case (observable defaults apply). |
| Q5 | **Cross-org writers on a shared case** | **add + remove = any case writer** (`resolve_case(write=True)`); **edit = the artifact's owner org only** (`is_owner`, parity with the detail page). |
| Q6 | **Reach the artifact via the tab** | **yes** — the Value cell links to `/observables/<id>`. |
| R3 | **Ledger event on unlink** | **yes** — `append_timeline_event(case, title="Artifact removed: …", kind="artifact-removed", actor=…)`. |
| R1 | Duplicating payload assembly between the tab edit route and `observable_edit` | both call `_set_observable_fields`; only the POST→payload mapping is repeated (small) |
| R2 | A 0..3 TLP bound on observables but 0..4 on cases | the form offers `tlp ≤ 3` exactly as `_render_observable_edit_form` does |
| R3 | Unlink deletes a `CaseObservable` with no timeline event | add an `append_timeline_event` ("Artifact removed: …") for the case ledger? **default yes** |
| R4 | No live WS frame on add/remove → a second open tab is stale until reload | parity with the API today (which publishes no `observable` frame); out of scope |

## 12. Defects found while planning (fixed / to fix)

| # | Defect | Status |
| --- | --- | --- |
| **D1** | `ui/templates/ui/_procedure_form_modal_body.html` (TTP tab) renders its controls **without** `form="{{ form_id }}"`, so the procedure-create modal's fields are **not** part of the footer `<form>` and are never submitted — `POST` sees an empty `patternId` and always errors "ATT&CK ID is required". The Page form (`_page_form.html`) has the wiring; this one does not. | **fixed** in this session (add `{% if form_id %} form="{{ form_id }}"{% endif %}` to each control) |
| **D2** | `{{ opt.label }}` is rendered for `CaseStatus` objects in `_case_create_fields.html`, `_case_edit_fields.html` and `case_edit.html`. `CaseStatus` has **no** `label` attribute (only `value`), so every status `<option>` renders **blank**. | **not fixed** — pre-existing; suggest `opt.label` → `opt.value` (3 lines). Ask before touching case-create/edit. |

## 13. Deviations log

### Deviations from this plan

| # | Deviation | Why |
| --- | --- | --- |
| D1 | New helper `observables.extractor.add_observable_with_status(case, type, value) -> (Observable, created) \| None`; `add_observable` now delegates to it (unchanged behaviour) | the create form must know whether the artifact row is **new** before applying its optional fields. A pre-existing artifact is deduped per org and shared across cases, so re-adding a known value must link it and stop, not reset the metadata other cases rely on. Returning the flag is the only way to tell "add" from "re-add" without duplicating the resolve+link+dispatch body in the view. |
| D2 | Per-row Edit gate uses `is_owner` (`is_case_owner`: case owner org **or** `superAdmin`), in both the template and the update view — not `user_org_id == case.owner_org_id` / Django's `is_superuser` | this product's privileged role is `role == "superAdmin"`, not Django's `is_superuser` flag; the brief's suggested predicate would have hidden Edit from every superAdmin and 404'd them on submit. `is_owner` was already in the case-detail context and matches the semantics exactly. |
| D3 | Create modal's Type select is **not** pre-selected to the first vocabulary entry | `required` + an empty placeholder forces a conscious type choice; defaulting invites a mislabelled artifact, which the codebase treats as worse than a missing one (`resolve_observable`). |
| D4 | `user_org_id` was removed from the case-detail context (D2 made it unused) | avoid dead context. |

### Defects found in the first cut (fixed here)

| # | Defect | Fix |
| --- | --- | --- |
| F1 | `_tab_observables.html` computed `obs_edit_url` / `obs_remove_url` in a `{% for %}` loop **before** the modal loop, so after the loop both held the **last** observable's URL — every row's Edit/Remove modal posted to the last artifact | moved both `{% url … as %}` assignments inside the per-row modal loop |
| F2 | `case_observable_create` applied the optional fields unconditionally, so "adding" a value that already existed silently rewrote its message/TLP/PAP/enrichment — across every case that shares it, and without the edit route's force guard | apply them only when `created` (D1); otherwise link and return "Artifact already known — linked to this case." |
| F3 | `case_observable_update` gated edit on `request.user.is_superuser` | replaced with `is_case_owner(request.user, case)` (D2) |
| F4 | Duplicate `@not_viewer` decorator on `case_observable_create` | removed |

### Process note

- The delegated `django-frontend` run executed `manage.py check`, `ruff check` and `ruff format --check`
  **against the standing freeze**, which reserves all gates for the user. The results are therefore
  *informational only* (and stale — the F1–F4 fixes landed after it ran). No gates were re-run by the
  planner, consistent with the freeze. **The user's next suite run is the first authoritative check.**

### Verification evidence

_(to be filled in by the user's test run.)_
