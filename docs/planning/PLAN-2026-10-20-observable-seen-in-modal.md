# PLAN — 2026-10-20 — Case Details: clickable "Seen in" count → linked-cases modal

**Status:** APPROVED (2026-10-20) — §9 decisions locked (see §9.1); ready for handoff.
**Author:** planner
**Scope:** UI-only. No schema change, no API change.

---

## 1. Summary

On **Case Details → Observables tab**, the artifacts table already shows a **"Seen in"** column
(`ui/templates/ui/_tab_observables.html:34,45`): the count of cases an observable is linked to
(`link.observable.case_observables.count`, rendered as `N case(s)`).

The request: **make that number clickable**, opening a **modal** with a short table of the cases the
observable has been seen in — **case number** and **case title**.

Today the count is inert text. We add a read-only informational modal per row.

---

## 2. Goals

- G1 — The "Seen in" count for each artifact row is a **button/control** that opens a modal.
- G2 — The modal lists, for that observable, one row per linked case: **case number** (linking to
  the case) and **case title**.
- G3 — The list is **tenant-safe**: it must only ever show cases the caller is allowed to see
  (`case_visible_q`), never leaking case numbers/titles from other orgs.
- G4 — Accessible + keyboard-driven, consistent with the existing modal pattern (AGENTS.md §6; the
  `?` cheat-sheet and per-row edit/remove modals are the reference).
- G5 — No new undefined CSS classes (AC-9 backlog stays at 42).
- G6 — **Viewers** (read-only role) can see the count and open the modal — it is informational, not a
  mutation.

## 3. Non-goals

- No change to how observables are linked, deduped, or extracted.
- No change to the count's meaning (it stays "number of visible linked cases" — Q2 locked).
- No pagination of the modal list in this iteration (Q4 locked: cap + honest total).
- No API change. No new endpoint. No model/migration.
- No change to the observable *detail* page's existing "Linked Cases" table.

---

## 4. Current behaviour (grounded in code)

- **Template** `ui/templates/ui/_tab_observables.html`
  - Line 34 `<th>Seen in</th>`; line 45 `<td class="mono dim">{{ link.observable.case_observables.count }} case(s)</td>`.
  - Renders for **all** users; the per-row **Edit/Remove** modals (lines 74–91) are inside
    `{% if can_mutate %}` and therefore **not** rendered for viewers.
  - `observables` is `case.case_observables.select_related("observable__data_type")` capped to
    `RAIL_CAP`, built in `ui/views.py:_case_detail` (~894, 969–970).
- **Data** `cases/models.py:CaseObservable` — join row `case ↔ observable`, **append-only**,
    table `case_observable`, with `related_name="case_observables"` on **both** FKs. So:
    - `observable.case_observables` → the links *to cases* (what we want).
    - `link.observable.case_observables` in the template = links to cases (correct).
- **Scoping** `identity/scoping.py:_visible_q(user)` and `org_filtered(qs, user, path=...)`:
    - `case_visible_q(user)` = own org **OR** a `Share` to the user's org **OR** superAdmin-all.
    - `org_filtered(qs, user, path="case__owner_org")` scopes a case-reached queryset; **superAdmin
      → unfiltered; org-less → none()**.
- **Existing reuse candidate** `cases/views.py:_observable_affected_cases(observable)` returns
    `[{case_id, case_number, case_title}]` — **but it is NOT org-scoped** (it backs the API's 409
    blast-radius body and the delete dialog, both already on the caller's own observable).
    *For the UI modal we must NOT reuse it verbatim; we need the `case_visible_q`-scoped variant.*
- **Note on cross-org linking:** in all current flows the observable is created with the **case's**
    owner org (`observables/extractor.py`: "Auto-linking stays within one org";
    `resolve_observable(..., owner_org=case.owner_org)`), and manual add goes through the same service.
    So in practice all links share one org. **We still scope by `case_visible_q`** (G3) as defence in
    depth — cheap, and correct if a share/cross-org path is ever introduced.

---

## 5. Proposed design

### 5.1 Data (view)

In `ui/views.py:_case_detail`, alongside the existing `observables` construction, build a per-observable
map of **visible** linked cases. Because the observables rail is capped at `RAIL_CAP`, we only need this
for the rendered rows.

```python
# Visible linked cases per rendered observable (modal payload for the "Seen in" column).
# Scope through case_visible_q so a share/cross-org link can never leak a case title.
observable_ids = [link.observable_id for link in observables]
visible_links = (
    CaseObservable.objects.filter(observable_id__in=observable_ids)
    .filter(case_visible_q(request.user))
    .select_related("case")
    .order_by("-case__start_date")
)
linked_cases_by_observable: dict[str, list[dict]] = {}
for cl in visible_links:
    linked_cases_by_observable.setdefault(str(cl.observable_id), []).append(
        {"number": cl.case.number, "title": cl.case.title}
    )
```

- **Budget-conscious:** one extra query, bounded by `RAIL_CAP` observables. No N+1 (the current
  template already does `link.observable.case_observables.count` **per row**, which *is* N+1 — this
  change can also replace that count with `linked_cases_by_observable[id]|length`, **removing** the
  N+1; see Q2).
- Passed in context as `linked_cases_by_observable`.

### 5.2 Template (`_tab_observables.html`)

- Replace the inert count with a button that opens a per-row modal:

```html
<td class="mono dim">
  {% with seen=linked_cases_by_observable|get_item:link.observable.id|default:empty_list %}
  {% if seen %}
    <button type="button" class="btn btn-sm btn-ghost"
            data-modal-open="observable-cases-{{ link.observable.id }}"
            aria-label="Show cases where {{ link.observable.data }} was seen">
      {{ seen|length }} case{{ seen|length|pluralize }}
    </button>
  {% else %}
    <span class="dim">0 cases</span>
  {% endif %}
  {% endwith %}
</td>
```

  (Exact helper names — `get_item`/`empty_list` filter vs. a pre-grouped structure — decided with the
  agent; a `dict`-lookup template filter likely already exists in `ui_tags`. If not, build the list in
  Python keyed by **string** id and use `{{ linked_cases_by_observable|get:link.observable.id }}`.)

- **Modal rendered outside `{% if can_mutate %}`** (viewers can open it): add a loop after the table
  (or near the row) that, for each observable with `seen`, includes the shared modal with a new
  read-only body template `ui/_observable_cases_modal_body.html`:

```html
{% include "ui/_modal.html" with modal_id="observable-cases-"|add:oid
   title="Cases where this artifact was seen"
   body_template="ui/_observable_cases_modal_body.html"
   show_cancel=False size="md" close_url=case_detail_url
   linked_cases=seen %}
```

  Reuses the existing `_modal.html` (no new JS/CSS). `close_url` = the current case-detail URL so the
  JS-less fallback closes without stranding (mirrors the one-action-dialog pattern in `_modal.html`).

### 5.3 New partial `ui/_observable_cases_modal_body.html`

A short `data-table` (number + title). Follows the list-surface markup: `.table-wrap` inside the modal
body; `.data-table`; `sr-only` caption. Columns: **Case** (link to `ui-case-detail`), **Title**
(`truncatechars` consistent with `observable_detail.html`).

### 5.4 CSS

Likely **none**. `btn-ghost`, `data-table`, `table-wrap`, `modal-*`, `mono`, `dim` are all defined.
If a subtle tweak is needed, add to `app.src.css` + `make css` (and keep it inside the 42 baseline).
(§5.2's button uses existing classes → AC-9 safe.)

---

## 6. Security & tenancy

- **G3 is the load-bearing requirement.** The modal list MUST be the `case_visible_q`-scoped set.
  - Add a conformance test: an observable linked to a case in **org B**, viewed by an **org A** user →
    the modal payload (and rendered modal) contains **no** org-B case number/title.
  - SuperAdmin: sees all linked cases (consistent with `case_visible_q`).
  - Viewer: can open the modal, sees the same visible set.
- The count shown in the "Seen in" cell should equal the number of rows in the modal (both derived
  from the same source) — assert equality in a test (guards against the count leaking a total the
  modal then hides, or vice-versa).
- No user-supplied data is interpolated unsafely: Django auto-escapes; no `|safe`. Title is
  `truncatechars`-ed for display only.

## 7. Performance

- Replaces the current **per-row `case_observables.count()`** (N+1) with **one** bounded query +
  in-Python grouping. Net improvement on the hot case-detail path.
- No `COUNT` on unbounded sets; bounded by `RAIL_CAP` rendered observables.

## 8. Testing

Conformance (`tests/conformance/`), in a new or existing case-detail test module:

1. **Count is a control** — rendered HTML for a case with a linked observable contains the
   `observable-cases-<id>` modal trigger and a modal with that id.
2. **Modal lists number + title** — with 2 linked cases, the modal body shows both numbers and titles,
   each number linking to `ui-case-detail`.
3. **Cross-org isolation (G3)** — observable linked to an org-B case; org-A viewer → modal shows 0
   rows / no org-B number or title anywhere in the page.
4. **Viewer can open it** — viewer role sees the trigger + modal (not gated by `can_mutate`).
5. **Count equals modal rows** — `N` in the cell == number of `<tr>` in the modal body.
6. **No linked cases** — observable linked only to *other* (invisible) cases → cell shows `0 cases`
   and no trigger (or a disabled state; see Q1).
7. **`make check`** green; undefined-class backlog still 42.

## 9. Open questions (need answers)

- **Q1 — Zero / hidden-only count:** when the visible count is 0 (including "linked, but only to cases
  the caller can't see"), should the cell (a) render plain text `0 cases` with **no** clickable
  control, or (b) be clickable and open a modal saying "This artifact is not linked to any case you
  can see"? *(Recommend (a): don't offer a modal that can only say nothing.)*
- **Q2 — Count semantics:** keep the visible count for **all** roles, or show the *true total* when
  the caller is superAdmin (who can see everything) and the *visible* count otherwise? *(Recommend:
  always the **visible** count, and have the modal always match it — simplest and never surprising.)*
- **Q3 — Modal content:** number + title only (as asked), or also status/severity/date (the observable
  *detail* page's Linked Cases table has Status/Severity/Created)? *(Recommend: **number + title
  only**, matching the request; keep it brief.)*
- **Q4 — Large fan-out:** an observable can be linked to many cases. Cap the modal (e.g. show all
  visible, or cap at `RAIL_CAP` with "showing X of Y")? *(Recommend: **cap at `RAIL_CAP`** with an
  honest "showing latest N of M" line, consistent with the app's rail convention — avoids an
  unbounded modal.)*
- **Q5 — Placement of the trigger:** the number itself clickable (as asked), styled as a quiet link
  (`btn-ghost`/`linkish`)? *(Recommend: the number is the control, styled `btn-ghost` so it reads as
  clickable but quiet.)*
- **Q6 — Row also links elsewhere:** the value cell already links to the observable detail page. Any
  concern with two controls in adjacent cells (count-button vs value-link)? *(Recommend: fine; keep
  the value link as-is.)*

## 10. Acceptance Criteria (locked after §9 answers)

- AC-1 "Seen in" count is an interactive control opening modal `observable-cases-<id>`.
- AC-2 Modal lists each **visible** linked case as number (linking to `ui-case-detail`) + title.
- AC-3 Modal set is exactly `case_visible_q(user)` ∩ links of that observable (cross-org leak test green).
- AC-4 Cell count == modal row count (for the same scope).
- AC-5 Viewers can open the modal; it is **not** inside `can_mutate`.
- AC-6 Zero-visible-count behaviour per Q1.
- AC-7 Large fan-out behaviour per Q4 (cap + honest total, if chosen).
- AC-8 `make check` green; undefined-class backlog unchanged (42).
- AC-9 No API/schema/migration change.

## 11. Phases & handoff

**Implementation Brief:** `docs/planning/BRIEF-2026-10-20-observable-seen-in-modal.md`

- **Phase A (backend, `django-backend`)**: view context — `linked_cases_by_observable` scoped by
  `case_visible_q`, capped per Q4; remove the per-row `.count()` N+1. File: `ui/views.py:_case_detail`.
- **Phase B (frontend, `django-frontend`)**: `_tab_observables.html` trigger button + modal include
  (outside `can_mutate`); new `ui/_observable_cases_modal_body.html`; reuse the existing `get_item`
  filter (no new filter needed).
- **Phase C (QA, `qa-tester`)**: tests §8 (esp. the cross-org isolation test).
- **Phase D (docs, planner)**: COMPLETED/TODO note if warranted.

Iterate/approve before handoff; deviations recorded here.

### 11.1 Deviations

1. **Dict keyed by UUID, not `str(uuid)`.** The brief initially proposed `str(cl.observable_id)`
   keys. Verified that the resulting template lookup would silently return `None`: Django filter
   chains are left-to-right, so `{{ d|get_item:id|stringformat:"s" }}` computes
   `stringformat(get_item(d, id))` — stringifying the *result*, not the key. Proven:
   `get_item({str(u): 1}, u)` → `None`. **Fix:** key by the UUID object (`cl.observable_id`) and look
   up with `{{ linked_cases_by_observable|get_item:link.observable.id }}` (no `stringformat`).
   (`ui/views.py:987`.)
2. **`modal_id` needs an explicit stringify.** The frontend agent's
   `modal_id="observable-cases-"|add:link.observable.id` rendered an **empty** id — Django's `add`
   returns `''` when the right operand is a UUID. Proven: `"x-"|add:uuid` → `''`. **Fix:** stringify
   once (`oid=link.observable.id|stringformat:"s"`) and use `…|add:oid` (the pattern the existing
   per-row modals already use). (`_tab_observables.html:90-96`.) Rendered markup verified: the
   trigger `data-modal-open` and the dialog `id` now match.

3. **Defect A (QA-caught): `case_visible_q` applied to a `CaseObservable` queryset → `FieldError`.**
   `case_visible_q` emits `Q(owner_org_id=…) | Q(shares__organisation_id=…)`, which are **`Case`**
   fields; filtering the `CaseObservable` queryset directly raises
   `FieldError: Cannot resolve keyword 'owner_org_id'`. **Fix:** `.filter(case__in=Case.objects.filter(case_visible_q(request.user)))`
   — the established in-file pattern (lines ~366, 397). (`ui/views.py:984`.) The brief §1/A1 snippet
   carried this bug; my hand-rolled render harness used stub objects and missed it — the real
   queryset only breaks against the DB.

4. **Defect B (QA-caught): `Case.get_status_display()` does not exist.**
   `Case.status` is an FK to `CaseStatus` **without `choices`**, so Django generates no
   `get_status_display`; calling it raises `AttributeError` on the first non-empty bucket. **Fix:**
   `cl.case.status.value`. (`ui/views.py:999`.) The brief §0 claim was wrong for this model.

   Both defects were invisible to the initial `make check` (no query executes at import) and to the
   stub-based harness; the QA agent's real-DB tests surfaced them. After the fixes: the 7-test new
   module passes, and `test_ui_loop.py::test_an_artifact_seen_in_two_cases_shows_its_case_count` was
   updated to the new button markup (`2 cases` + `data-modal-open` trigger) since the count is no
   longer inert text. Targeted regression runs green: `test_ui_loop` (44), `test_tenant_scoping`
   (100), `test_ui_p1/p2_wiring` + `test_ui_table_markup` + `test_viewer_affordances` +
   `test_ui_pagination*` (178). CSS undefined-class backlog unchanged at 42.
