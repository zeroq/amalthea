# Implementation Brief — Clickable "Seen in" count → linked-cases modal

**Plan:** `docs/planning/PLAN-2026-10-20-observable-seen-in-modal.md` (APPROVED 2026-10-20)
**Status:** APPROVED — ready to implement. **No schema migration. No API change. No new endpoint.**
**Agents:** `django-backend` (Phase A: view context) · `django-frontend` (Phase B: template + modal
partial). One agent may own both. Then `qa-tester` (Phase C tests).

> Any deviation from this brief must be **recorded in the plan §11 Deviations** and reported back.
> Do not silently diverge.

---

## 0. Context (read these first)

- `ui/views.py:_case_detail` — builds the observables rail at `~966-970`:
  `observables_qs = case.case_observables.select_related("observable__data_type").order_by("-created_at")`,
  `observables_total = observables_qs.count()`, `observables = list(observables_qs[:RAIL_CAP])`.
  `RAIL_CAP` is imported **inside** the function at `~958` (`from ui.pagination import RAIL_CAP`;
  `RAIL_CAP = 200`, `ui/pagination.py:67`).
- `ui/templates/ui/_tab_observables.html` — the target. Line **34** `<th>Seen in</th>`; line **45**
  `<td class="mono dim">{{ link.observable.case_observables.count }} case(s)</td>`. The per-row
  Edit/Remove modals (`~74-91`) sit inside `{% if can_mutate %}` — **do not** put the new modal there.
- **Reference markup to mirror exactly:** `ui/templates/ui/observable_detail.html` "Linked Cases"
  table (`~178-215`) — columns Case/Title/Status/Severity/Created/Open, `pill sev-*`,
  `truncatechars:50`, `list-summary dim` truncation line, `table-wrap` + `data-table` + `sr-only`
  caption. Reuse this pattern.
- **Model facts:**
  - `cases/models.py:CaseObservable` — join row `case ↔ observable`, both FKs `related_name="case_observables"`.
    `observable.case_observables` → the links to cases.
  - `Case.status` is an **FK** to `CaseStatus` (`cases/models.py:102`); `CaseStatus.value` is the label
    (`cases/models.py:47`). Display via **`case.get_status_display`** → renders `CaseStatus.value`.
  - `Case` has `.number`, `.title`, `.severity`, `.status`, `.start_date`, `.created_at`.
- **Scoping** `identity/scoping.py`: `case_visible_q(user)` (own org OR `Share` to org OR superAdmin-all).
  **Do NOT reuse `cases/views.py:_observable_affected_cases()` — it is unscoped (API 409 body).**
- **Template filter available:** `ui/templatetags/ui_tags.py:58` `get_item(mapping, key)` →
  `mapping.get(key)` (returns `None` if missing, never raises). Use it; **key the dict by `str(uuid)`
  and look up with `str(...)`** (see caveat A2).
- **Modal idiom:** `ui/templates/ui/_modal.html` supports `body_template=` (read-only body) +
  `show_cancel=False` + `close_url=` (single "Close" anchor that closes in place with JS, navigates
  without it). No new JS/CSS needed.

---

## 1. Phase A — backend view (`ui/views.py:_case_detail`)  → AC-2, AC-3, AC-4, AC-7

### A1 — build the scoped, capped payload
After `observables = list(observables_qs[:RAIL_CAP])` (`~970`), add:

```python
# "Seen in" modal payload (plan 2026-10-20): the cases each rendered artifact has been seen in.
# Scoped through case_visible_q so a share/cross-org link can never leak a case number/title.
# Bounded by RAIL_CAP observables; replaces the per-row case_observables.count() N+1.
# Keyed by the observable's UUID (not str()) so the template looks it up directly with
# `{{ linked_cases_by_observable|get_item:link.observable.id }}` — no string formatting, and no
# filter-chain precedence trap (`|get_item:…|stringformat:"s"` stringifies the *result*, not the key).
observable_ids = [link.observable_id for link in observables]
seen_links = (
    CaseObservable.objects.filter(observable_id__in=observable_ids)
    .filter(case_visible_q(request.user))
    .select_related("case__status")
    .order_by("-case__start_date")
)
linked_cases_by_observable: dict[uuid.UUID, dict] = {}
for cl in seen_links:
    bucket = linked_cases_by_observable.setdefault(cl.observable_id, {"cases": [], "total": 0})
    bucket["total"] += 1
    if len(bucket["cases"]) < RAIL_CAP:
        bucket["cases"].append(
            {
                "number": cl.case.number,
                "title": cl.case.title,
                "status": cl.case.get_status_display(),
                "severity": cl.case.severity,
            }
        )
```

> **IMPLEMENTED as above** (see `ui/views.py` ~972-995). The dict is keyed by the **UUID object**
> `cl.observable_id`, **not** `str(...)`. This is deliberate: Django filter chains evaluate
> left-to-right, so `{{ d|get_item:id|stringformat:"s" }}` computes `stringformat(get_item(d, id))` —
> it stringifies the *result*, not the key, and `get_item` (`mapping.get`) would miss. Keying by UUID
> lets the template use `{{ linked_cases_by_observable|get_item:link.observable.id }}` directly
> (`link.observable.id` is the same UUID object). Verified: `get_item({str(u): "HIT"}, u)` → `None`.

- `CaseObservable` is already imported in `ui/views.py` (used by the Similar tab, `~1020`). If not in
  scope at that point, add `from cases.models import CaseObservable`.
- `case_visible_q` — import from `identity.scoping` (check existing imports; add if absent).
- **AC-7 (Q4):** `total` counts **all** visible links; `cases` is capped at `RAIL_CAP`. The template
  shows the truncation line when `total > cases|length`.

### A2 — key convention (FINAL, already implemented)
The view keys the dict by `cl.observable_id` (a **UUID object**), annotating
`dict[uuid.UUID, dict]`. This lets the template look up with the UUID directly:
`{{ linked_cases_by_observable|get_item:link.observable.id }}`. **No `str()` / `stringformat`** —
Django filter chains are left-to-right, so `|get_item:id|stringformat:"s"` would stringify the
*result*, not the key, and `get_item`'s `mapping.get()` would miss (proven: `get_item({str(u): 1}, u)`
→ `None`).

### A3 — pass to context
Add `linked_cases_by_observable=linked_cases_by_observable,` to the `_base_context(...)` call for
`case_detail.html` (alongside `observables=observables, observables_total=observables_total,`).

- [ ] AC-2: rendering a case whose observable links 2 visible cases → context bucket has both.
- [ ] AC-3: an observable linked to an **org-B** case, rendered for an **org-A** user → bucket has 0
      cases (and `total` 0) — no org-B number/title.
- [ ] AC-4: `total` equals the number of rows the template will render when under the cap.
- [ ] No `case_observables.count()` query remains in the template (the N+1 is gone).

---

## 2. Phase B — frontend (`_tab_observables.html` + new partial)  → AC-1, AC-2, AC-5, AC-6

### B1 — make the count a control (line 45)
Replace the inert `<td>` with a trigger that opens the per-row modal. **Only when the visible bucket is
non-empty** (AC-6 / Q1):

```html
<td class="mono dim">
  {% with seen=linked_cases_by_observable|get_item:link.observable.id %}
    {% if seen and seen.cases %}
      <button type="button" class="btn btn-sm btn-ghost"
              data-modal-open="observable-cases-{{ link.observable.id }}"
              aria-label="Show cases where this artifact was seen">
        {{ seen.cases|length }} case{{ seen.cases|length|pluralize }}
      </button>
    {% else %}
      <span class="dim">0 cases</span>
    {% endif %}
  {% endwith %}
</td>
```

> **FINAL key convention (do not change):** the view keys the dict by `cl.observable_id` (a **UUID
> object**), so the lookup is `{{ linked_cases_by_observable|get_item:link.observable.id }}` — **no**
> `stringformat`. `link.observable.id` is the same UUID object, so `.get()` hits. Do **not** add
> `|stringformat:"s"` (filter chains are left-to-right; it would stringify the *result*, not the key).

Intended output:
- **non-empty:** `<button type="button" class="btn btn-sm btn-ghost"
  data-modal-open="observable-cases-{{ link.observable.id }}" aria-label="Show cases where this
  artifact was seen">{{ n }} case{{ n|pluralize }}</button>` where `n = seen.cases|length`... but
  **the displayed count must equal the modal rows** (AC-4). Decide: show `seen.total` (may exceed the
  cap) or `seen.cases|length` (== rows). **Per AC-4, the cell count MUST equal the modal row count**,
  so use the **rendered row count** (`seen.cases|length`). The honest "of M" belongs in the modal's
  truncation line (AC-7).
- **empty (0):** `<span class="dim">0 cases</span>` — **no** button (AC-6).

### B2 — read-only modal, rendered **outside** `can_mutate`
For every artifact row whose bucket is non-empty, render the shared modal. Use a loop after the table
body (still inside the tab partial, but **not** inside `{% if can_mutate %}`):

```html
{% for link in observables %}
  {% with seen=linked_cases_by_observable|get_item:link.observable.id %}
    {% if seen and seen.cases %}
      {% include "ui/_modal.html" with modal_id="observable-cases-"|add:link.observable.id title="Cases where this artifact was seen" body_template="ui/_observable_cases_modal_body.html" show_cancel=False close_url=request.path seen=seen %}
    {% endif %}
  {% endwith %}
{% endfor %}
```

- `modal_id` must match the button's `data-modal-open` **exactly** (`observable-cases-<id>`).
- `close_url` = the current page (`request.path`) so the JS-less fallback reloads in place rather than
  stranding the user in the dialog.
- The body template receives `seen` (the `{"cases": [...], "total": N}` dict) via `{% include %}` /
  `_modal.html`'s `body_template` include.

### B3 — new partial `ui/templates/ui/_observable_cases_modal_body.html`
Mirror `observable_detail.html`'s "Linked Cases" table, trimmed to 3 columns (Q3):

```html
{% comment %}Read-only body for the "Seen in" modal on the case-detail Observables tab.{% endcomment %}
<div class="table-wrap">
  <table class="data-table">
    <caption class="sr-only">Cases where this artifact was seen</caption>
    <thead>
      <tr>
        <th scope="col">Case</th>
        <th scope="col">Title</th>
        <th scope="col">Status</th>
      </tr>
    </thead>
    <tbody>
      {% for c in seen.cases %}
      <tr>
        <td><a href="{% url 'ui-case-detail' c.number %}">{{ c.number }}</a></td>
        <td><a href="{% url 'ui-case-detail' c.number %}">{{ c.title|truncatechars:50 }}</a></td>
        <td><span class="sev sev-{{ c.severity }}">{{ c.status }}</span></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% if seen.total > seen.cases|length %}
  <p class="list-summary dim">Showing latest {{ seen.cases|length }} of {{ seen.total }} cases</p>
{% endif %}
```

- **AC-2:** number links to the case; **title is also a link to the case** (user-requested); status as
  a label.
- Use existing classes only (`table-wrap`, `data-table`, `sr-only`, `list-summary`, `dim`, `sev`,
  `sev-*`, `btn`, `btn-sm`, `btn-ghost`). **Do not invent classes** (AC-8).

- [ ] AC-1: cell count is a control that opens `observable-cases-<id>`.
- [ ] AC-2: modal lists number (link) + title (link) + status for each visible case.
- [ ] AC-5: a **viewer** sees the button + modal (not gated by `can_mutate`).
- [ ] AC-6: zero-visible-count → plain `0 cases`, no button, no modal.

---

## 3. Phase C — tests (`tests/conformance/`)  → all ACs

Add to an existing case-detail module (e.g. `test_ui_case_detail.py`) or a new
`test_ui_observable_seen_in_modal.py`. See plan §8 for the list; must include:

1. count-is-a-control (trigger + modal id present);
2. modal lists number + title + status, number **and** title link to `ui-case-detail`;
3. **cross-org isolation** (observable linked to an org-B case; org-A user sees no org-B number/title);
4. viewer can open (not behind `can_mutate`);
5. cell count == modal `<tr>` count;
6. zero-visible → `0 cases`, no trigger;
7. `[§7.5 falsifiability]` prove test 3 fails without the `case_visible_q` filter (temporarily remove it).
8. `make check` green; undefined-class backlog unchanged (**42**).

---

## 4. Verification checklist (agent to run before reporting done)

- [ ] `make check` green (ruff, ruff format --check, mypy, `manage.py check`, `makemigrations --check
      --dry-run`, `css-check`).
- [ ] No new undefined CSS classes (AC-9 backlog still 42).
- [ ] Targeted pytest on the new/changed conformance module(s) only (do **not** run the full suite).
- [ ] Confirmed no `case_observables.count()` remains in `_tab_observables.html`.
- [ ] Falsifiability of the cross-org test proven (plan §8.7).

---

## 5. Deliberately unchanged

- `cases/views.py:_observable_affected_cases()` (unscoped, API-only) — **not** touched.
- `observable_detail.html` "Linked Cases" table — **not** touched.
- No API, no schema, no migration, no new URL, no new JS/CSS.
