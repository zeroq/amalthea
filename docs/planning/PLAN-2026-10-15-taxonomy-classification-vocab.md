# PLAN-2026-10-15 — Taxonomy page: replace Case Templates/TTPs with classification vocabularies

Status: Draft (awaits user approval)
Owner: planner
Implementer: django-frontend
Verifier: qa-tester
Source: User request 2026-10-15

## 1. Summary

The `/taxonomy` UI page currently shows six cards: Case Statuses, Alert Statuses, Observable Types, Case Templates, Tags, TTPs (ATT&CK). The user wants: remove the Case Templates and TTPs cards from that page, and instead add cards showing the taxonomy for **scope**, **phase**, **type** (case_type), and **data classification**. The page is a read-only aggregate of vocabularies (no CRUD) and already exists as `ui/taxonomy.html` with view `ui.views.taxonomy`.

The classification vocabularies already exist in code: `core.enums.CASE_SCOPE_CHOICES`, `core.enums.CASE_PHASE_CHOICES`, `core.enums.CASE_TYPE_CHOICES`, `core.enums.DATA_CLASSIFICATION_CHOICES` (all include an explicit `unset` entry). The Case model already has these fields. The plan does not add new data, only changes what the taxonomy page displays.

## 2. Goals

- G1 — Modify `ui/taxonomy.html` to drop the "Case Templates" and "TTPs (ATT&CK)" cards.
- G2 — Add four new cards on the taxonomy page: "Case Scope", "Case Phase", "Case Type", "Data Classification", each rendering its enum choices (value → display label) in a table.
- G3 — Keep the remaining cards unchanged (Case Statuses, Alert Statuses, Observable Types, Tags).
- G4 — Update `ui/views.py::taxonomy` only if it needs to pass additional context (the four choice tuples). The view currently passes `case_templates` and `ttps` to the template; we should stop passing what we no longer render (optional cleanup, not required for correctness).
- G5 — Respect the project's conventions: no inline scripts/styles, use existing table patterns (`data-table`, `table-wrap`), dark theme, accessible.
- G6 — No schema/migrations, no API changes.

## 3. Non-goals

- CRUD UI for these vocabularies (the page is read-only; they are closed enums).
- Changes to case create/edit/display (those already show the classification fields).
- Changes to `TTP` or `CaseTemplate` models/views beyond not rendering them on `/taxonomy`.
- Adding "unset" special handling beyond displaying the existing enum labels.

## 4. Current state

**Template:** `ui/templates/ui/taxonomy.html` — 165 lines, renders 6 cards in 3 columns (stack layout). The last two cards are Case Templates and TTPs (lines ~89-164).

**View:** `ui/views.py::taxonomy` (lines ~3058-3085) passes:
```python
case_statuses, alert_statuses, observable_types, case_templates, tags, ttps
```
from the respective models (`CaseStatus`, `AlertStatus`, `ObservableType`, `CaseTemplate`, `Tag`, `TTP`).

**Enums (single source):**
```python
core.enums.CASE_SCOPE_CHOICES
core.enums.CASE_PHASE_CHOICES
core.enums.CASE_TYPE_CHOICES
core.enums.DATA_CLASSIFICATION_CHOICES
```

## 5. Proposed changes

### 5.1 Template (`ui/templates/ui/taxonomy.html`)

- Remove the Case Templates card (starts at line ~89: `<div class="card"><h3>Case Templates</h3>...`)
- Remove the TTPs card (starts at line ~142: `<div class="card"><h3>TTPs (ATT&CK)</h3>...`)
- After the Observable Types card (or in the second/third column as fits the 3-column layout), add four new cards. Suggested layout: keep the first column as is (Case Statuses, Alert Statuses). Second column: Observable Types, Tags (or swap Tags into third). Third column: Case Scope, Case Phase, Case Type, Data Classification (compact). The exact column order is flexible; the key change is which cards appear.
  - "Case Scope": table with `Value`, `Label` (or just show the human label; the choices are `(value, label)`, so display `value` and `label` or just `label` as the primary column).
  - "Case Phase": similar.
  - "Case Type": similar.
  - "Data Classification": similar.

Each card follows the existing pattern: `<div class="card">`, `<h3>...</h3>`, optional `table-wrap`, `data-table`, empty state if needed (but enums are non-empty). Since these are constants, just iterate the choice tuples.

Example for one card:
```django
<div class="card">
  <h3>Case Scope</h3>
  <div class="table-wrap">
    <table class="data-table">
      <thead><tr><th scope="col">Code</th><th scope="col">Label</th></tr></thead>
      <tbody>
        {% for val, label in scope_choices %}
          <tr><td class="mono">{{ val }}</td><td>{{ label }}</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

### 5.2 View (`ui/views.py::taxonomy`)

Add the four choice tuples to context. Also, we can stop passing `case_templates` and `ttps` (no longer used) — minimal cleanup. Import choices from `core.enums`.

```python
from core.enums import (
    CASE_SCOPE_CHOICES,
    CASE_PHASE_CHOICES,
    CASE_TYPE_CHOICES,
    DATA_CLASSIFICATION_CHOICES,
)

return render(
    request,
    "ui/taxonomy.html",
    _base_context(
        request,
        case_statuses=CaseStatus.objects.filter(hidden=False).order_by("order", "value"),
        alert_statuses=AlertStatus.objects.filter(hidden=False).order_by("order", "value"),
        observable_types=ObservableType.objects.all().order_by("name"),
        tags=Tag.objects.all().order_by("name"),
        scope_choices=CASE_SCOPE_CHOICES,
        phase_choices=CASE_PHASE_CHOICES,
        case_type_choices=CASE_TYPE_CHOICES,
        data_classification_choices=DATA_CLASSIFICATION_CHOICES,
    ),
)
```

## 6. Acceptance Criteria

- [ ] `/taxonomy` renders successfully for an authenticated user.
- [ ] Case Templates card is **not** present.
- [ ] TTPs (ATT&CK) card is **not** present.
- [ ] Four new cards are present with correct headers: "Case Scope", "Case Phase", "Case Type", "Data Classification".
- [ ] Each new card displays all choices from the corresponding enum (including `unset`).
- [ ] Existing cards (Case Statuses, Alert Statuses, Observable Types, Tags) remain present and unchanged in behavior.
- [ ] No inline scripts/styles introduced; follows existing table/CSS patterns.
- [ ] `make check` passes (ruff, mypy, tests, migrations, css).

## 7. Risks / notes

- **Low risk:** purely presentational change to a single template/view. No data model/API impact.
- **Cleanup:** stopping to pass `case_templates`/`ttps` is safe since template no longer references them. If other code expects them via this view context (unlikely), but this is the only use of that specific context.
- **Consistency:** the case create/edit forms already use these same choice tuples; displaying them here is consistent.

## 8. Questions for the user

1. **Column layout** — keep the 3-column stack as-is, or reorganize? E.g. put Scope/Phase/Type/Classification in the rightmost column as a "Classification" group? The current layout is flexible; any preference?
2. **Table columns** — for each new card, show "Code" (the value like `single_asset_user`) and "Label" (human text), or just "Label"? Showing Code is useful for reference; current proposal shows both.
3. **Do we want to keep Case Templates/TTPs visible anywhere else?** They remain accessible via their own list/detail pages (and TTPs are used in the Procedures/TTP UI). The request only says "remove from that taxonomy page" — so other surfaces unchanged.
4. **Should we also remove the `case_templates`/`ttps` queryset construction from the view?** Harmless to keep (just unused in template), but cleaner to remove. Preference?
5. **Any renaming of card headers?** e.g. "Incident Type" instead of "Case Type"? The enums use "Case Type" terminology; matching is fine.

## 9. Implementation steps

1. Edit `ui/views.py::taxonomy` to import the four choice enums and pass them; drop `case_templates`, `ttps` from context (confirm no other template needs them).
2. Edit `ui/templates/ui/taxonomy.html` to remove Case Templates and TTPs cards; add the four new cards with tables iterating the choice tuples.
3. Run `make check` locally.
4. Commit with message like: `refactor(ui): taxonomy page shows classification vocabularies; drop templates/TTP cards`

## 10. Verification

- Manual: visit `/taxonomy` as analyst; verify cards present/absent and values match `core.enums.*`.
- Tests: if there is a test asserting the taxonomy page contains specific card headers, update it. (No obvious taxonomy-specific conformance test found by quick scan; existing nav tests still pass.)
