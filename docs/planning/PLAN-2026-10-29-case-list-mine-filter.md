# PLAN-2026-10-29 — Cases list: "Mine" assignee filter

## Summary

Add a **Mine** control to the Cases list filter card, immediately to the right of **Clear**. It
narrows the list to cases whose `assignee` is the signed-in user. It is a second, independent filter:
it combines with the existing `scope` dropdown (`open` / `all` / `closed`) rather than replacing it,
and the scope control never clears it.

The filter is a normal GET query parameter, `?mine=1`, so it is bookmarkable, works without
JavaScript, survives pagination through Django's `{% querystring %}` tag, and is enforced entirely
server-side after the existing tenant-visibility filter.

## Goals

1. A **Mine** button sits directly after **Clear** in the Cases filter bar.
2. `?mine=1` returns only cases assigned to `request.user`, never another user's or unassigned cases.
3. `mine` combines independently with `scope`:
   - `?mine=1` → my open cases (default scope)
   - `?mine=1&scope=closed` → my closed cases
   - `?mine=1&scope=all` → all of my cases
4. The control is a toggle: clicking it while active removes the filter while preserving scope.
5. Scope is preserved when Mine is applied/removed; Mine is preserved when **Filter** is submitted;
   pagination and per-page links preserve both.
6. **Clear** resets both filters to the bare `/cases` list (open, no Mine).
7. The heading, empty state and "(filtered from N)" count stay honest when Mine is active.
8. Existing behaviour, roles and tenant isolation are unchanged.

## Non-goals

- No database schema or migration change. `Case.assignee` already has an FK index.
- No API change. This is a UI-list filter only; DRF case-list filtering is out of scope.
- No global "my work" queue, saved filters, session persistence or keyboard shortcut.
- No changes to the New-case modal's error re-render path (it already re-renders from `request.GET`;
  preserving filters across that POST is a separate concern).
- No new JavaScript or HTMX dependency; the control works as a plain link.

## Current state (verified)

`ui/views.py::_render_case_list` currently:

```python
scope = request.GET.get("scope", "open")
if scope not in ("open", "all", "closed"):
    scope = "open"

base_queryset = Case.objects.select_related("status", "assignee", "owner_org").filter(
    case_visible_q(request.user)
)
unfiltered_count = base_queryset.count()

if scope == "open":
    queryset = base_queryset.filter(closed_date__isnull=True)
elif scope == "closed":
    queryset = base_queryset.filter(closed_date__isnull=False)
else:
    queryset = base_queryset

has_filter = scope != "all"
page_obj = paginate(request, queryset.order_by("-severity", "-start_date"))
filtered_from = (
    unfiltered_count if has_filter and unfiltered_count > page_obj.paginator.count else None
)
```

`ui/templates/ui/case_list.html` renders the filter bar (`form.filter-bar`), the scope `<select>`,
**Filter**, **Clear**, then the `btn-end` **New case** action. The heading is a scope-only conditional;
the empty state is also scope-only.

## Design decisions

### D1 — Query parameter: `mine=1`

Only the exact string `"1"` enables the filter; every other value (`0`, `true`, empty, hostile input)
is treated as off. This mirrors the existing defensive style for `scope` and cannot raise.

URL examples:

| Intent | URL |
| --- | --- |
| My open cases | `/cases?mine=1` |
| My closed cases | `/cases?scope=closed&mine=1` |
| All my cases | `/cases?scope=all&mine=1` |
| Remove Mine, keep closed | `/cases?scope=closed` |
| Clear everything | `/cases` |

### D2 — Server-side filter placement

Apply `mine` to the already tenant-scoped queryset, after `case_visible_q`:

```python
mine = request.GET.get("mine") == "1"
base_queryset = Case.objects...filter(case_visible_q(request.user))
unfiltered_count = base_queryset.count()

if mine:
    base_queryset = base_queryset.filter(assignee=request.user)

# scope filter applied to base_queryset as today
```

`assignee=request.user` is the authenticated identity, never a user-supplied id, so the parameter
cannot be used to inspect another analyst's queue. Tenant visibility is applied first and is never
loosened. For `superAdmin`, `case_visible_q` already spans organisations; Mine then means "cases
assigned to me" across that global view, which is consistent.

### D3 — Independent filters, not a combined state machine

Treat `scope` and `mine` as two orthogonal predicates:

```python
has_filter = scope != "all" or mine
```

`filtered_from` remains the pre-filter visible count (`unfiltered_count`) so the summary can say
"(filtered from N)" when either filter narrows the set. The template's existing
`filtered_from != total` guard prevents a pointless disclosure when the result count equals the
visible total.

### D4 — Toggle with a link; form keeps Mine via a hidden input

- Inactive: `<a class="btn" href="{% querystring mine=1 page=1 %}">Mine</a>`
- Active: `<a class="btn btn-primary" href="{% querystring mine=None page=1 %}"
  aria-current="true" aria-label="Remove Mine filter — showing only cases assigned to you">Mine</a>`
- When active, render `<input type="hidden" name="mine" value="1">` inside the filter form so the
  **Filter** submit button preserves the filter.

An anchor is used (not a checkbox) because it applies immediately, has URL state, and works with
JavaScript disabled — the same pattern as the existing **Clear** link. `page=1` is set on every Mine
toggle so a deep page number cannot land on an empty page. `per_page` is preserved by
`{% querystring %}`; the **Filter** form's existing reset-to-default per-page behaviour is unchanged.

### D5 — Honest copy when Mine is active

Compute the card heading in the view (keeps template logic simple and testable):

| scope | no Mine | Mine active |
| --- | --- | --- |
| open | `Open cases` | `My open cases` |
| all | `All cases` | `My cases` |
| closed | `Closed cases` | `My closed cases` |

Empty-state branch changes from `scope != "open"` to `scope != "open" or mine`, so a Mine-filtered
empty list gets the generic "No cases match your filters. Clear filters" copy instead of the
misleading "No cases open."

### D6 — Availability

The Mine filter is a **read-only** filter and is visible to every authenticated role, including
`viewer`. It is not gated on `can_mutate` (that flag only controls write affordances such as
**New case**).

## Files to change

| File | Change |
| --- | --- |
| `ui/views.py` | Parse `mine`, apply the assignee predicate, compute `heading`, update `has_filter`, add `mine`/`heading` to context. |
| `ui/templates/ui/case_list.html` | Add the Mine control after Clear, hidden `mine` input when active, heading variable, empty-state condition. |
| `tests/conformance/test_case_mine_filter.py` | New conformance tests (see below). |
| `docs/planning/PLAN-2026-10-29-case-list-mine-filter.md` | This plan; record any deviations here. |

No CSS change is required: `.filter-bar .btn`, `.btn`, `.btn-primary` and `.filter-bar .btn-end`
already exist.

## Suggested implementation (concrete)

In `_render_case_list`:

```python
mine = request.GET.get("mine") == "1"

base_queryset = Case.objects.select_related("status", "assignee", "owner_org").filter(
    case_visible_q(request.user)
)
unfiltered_count = base_queryset.count()

if mine:
    base_queryset = base_queryset.filter(assignee=request.user)

if scope == "open":
    queryset = base_queryset.filter(closed_date__isnull=True)
elif scope == "closed":
    queryset = base_queryset.filter(closed_date__isnull=False)
else:
    queryset = base_queryset

has_filter = scope != "all" or mine
page_obj = paginate(request, queryset.order_by("-severity", "-start_date"))
filtered_from = (
    unfiltered_count if has_filter and unfiltered_count > page_obj.paginator.count else None
)
heading = {
    ("open", False): "Open cases",
    ("all", False): "All cases",
    ("closed", False): "Closed cases",
    ("open", True): "My open cases",
    ("all", True): "My cases",
    ("closed", True): "My closed cases",
}[(scope, mine)]

context = {
    "page_obj": page_obj,
    "filtered_from": filtered_from,
    "scope": scope,
    "mine": mine,
    "heading": heading,
    "open_new_case": open_new_case,
}
```

In `case_list.html` filter bar, between Clear and New case:

```django
<a class="btn" href="{% url 'ui-case-list' %}">Clear</a>
{% if mine %}
  <input type="hidden" name="mine" value="1">
  <a class="btn btn-primary" href="{% querystring mine=None page=1 %}"
     aria-current="true"
     aria-label="Remove Mine filter — showing only cases assigned to you">Mine</a>
{% else %}
  <a class="btn" href="{% querystring mine=1 page=1 %}"
     aria-label="Show only cases assigned to me">Mine</a>
{% endif %}
{% if can_mutate %}
  <a class="btn btn-primary btn-end" ...>New case</a>
{% endif %}
```

Heading and empty state:

```django
<h2>{{ heading }}</h2>
...
{% if scope != "open" or mine %}
  <p class="empty">No cases match your filters. <a href="{% url 'ui-case-list' %}">Clear filters</a></p>
{% else %}
  <p class="empty">No cases open.{% if can_mutate %} <a href="{% url 'ui-case-create' %}">Open one</a>.{% endif %}</p>
{% endif %}
```

## Security

- **Authorisation / tenant isolation:** `mine` is applied *after* `case_visible_q`. It narrows, never
  widens. A user cannot pass an assignee id; the server compares against `request.user`.
- **Input validation:** `mine` is allow-listed to `"1"`; malformed values are off, never a 500.
- **No injection surface:** no raw SQL, no `eval`, no dynamic query field names.
- **Read-only:** no state change, no CSRF-relevant action, no audit entry needed.
- **Information disclosure:** `filtered_from` remains a count of cases the caller can already see, not
  of cases outside their visibility. Cross-org sharees see only the same visible set as before.
- **Accessibility:** visible "Mine" label; active state is conveyed by `aria-current` and an
  accessible label, not colour alone; control is keyboard-operable as a normal link.

## Testing

New file `tests/conformance/test_case_mine_filter.py`, following the fixture style of
`test_case_scope_filter.py`:

1. Default list has no Mine filter and renders the Mine link.
2. `?mine=1` returns only cases assigned to the signed-in user; excludes another user's cases and
   unassigned cases.
3. `?mine=1` combined with `scope=open`, `closed`, `all` returns the correct intersection.
4. `?mine=0`, `?mine=true`, `?mine=yes`, `?mine=` and absent all behave as off.
5. `?mine=1` does not surface a case from another organisation that is assigned to the user but not
   visible through `case_visible_q` (defence-in-depth regression).
6. Mine link is rendered after Clear, inside the filter form, and before New case.
7. Inactive Mine href contains `mine=1`; active Mine href removes `mine`, preserves `scope`, and sets
   `page=1`; active control has `aria-current="true"` / distinct styling.
8. When Mine is active, the filter form contains `name="mine" value="1"`, so submitting Filter
   preserves it; when inactive, it does not.
9. Pager and per-page hrefs preserve both `scope` and `mine`.
10. Heading variants: Open/All/Closed and My open/My/My closed.
11. Empty-state copy for a Mine-filtered empty list is the generic "No cases match your filters."
12. `filtered_from` is set when only Mine is active (`scope=all&mine=1`) and rendered as
    "(filtered from N)".
13. A `viewer` sees the Mine control and can apply it (read-only).
14. Existing `test_case_scope_filter.py`, pagination, viewer and tenant-scoping tests still pass.

Run:

```bash
.venv/bin/pytest tests/conformance/test_case_mine_filter.py -q
.venv/bin/pytest tests/conformance/test_case_scope_filter.py \
  tests/conformance/test_ui_pagination.py \
  tests/conformance/test_ui_pagination_edge_cases.py \
  tests/conformance/test_tenant_scoping.py -q
```

## Acceptance criteria

- **AC1** The Cases filter bar shows a control labelled **Mine** immediately to the right of
  **Clear**, inside the filter form, for every authenticated role.
- **AC2** `GET /cases?mine=1` lists only cases whose `assignee` is the signed-in user; no case
  assigned to another user or unassigned appears.
- **AC3** `mine` is independent of `scope`: each of open/all/closed can be combined with Mine, and
  applying/removing Mine does not silently change scope.
- **AC4** Clicking Mine while active removes the filter (toggle), preserving the current scope and
  resetting to page 1.
- **AC5** Submitting **Filter** while Mine is active preserves Mine; pager/per-page links preserve
  both `scope` and `mine`.
- **AC6** **Clear** returns to the bare list (scope=open, Mine off).
- **AC7** The heading and empty state reflect Mine accurately; `filtered_from` is populated when Mine
  alone narrows the list (e.g. `scope=all&mine=1`).
- **AC8** Any value other than `mine=1` is treated as off and never causes a 500.
- **AC9** Tenant isolation is unchanged: Mine only narrows the existing `case_visible_q` set.
- **AC10** No schema, migration, API or CSS change; all existing conformance tests pass.

## Risks / open questions

1. **Toggle semantics.** Default in this plan: second click turns Mine off. If a one-way filter is
   preferred, only the active-state branch of the template changes; the view is unaffected.
2. **Unsaved dropdown value.** Mine is a link, so it applies the last-submitted `scope`, not a
   dropdown value the user has changed but not submitted with **Filter**. This is normal for
   link-based chips. If "apply both immediately" is desired, Mine must become a submit button; that
   is a UX choice, not a backend change.
3. **Heading copy.** Default: `My open cases` / `My cases` / `My closed cases`. Alternative: keep
   `Open cases` and show a separate "Mine" chip. Tests should be updated with the chosen copy.
4. **Role visibility.** Default: show to viewers. If Mine should be analyst-only, gate it and add a
   test.
5. **Parameter name.** Default: `mine=1`. Alternative: `assigned=me`; code/tests would rename only
   the query key.
6. **Count semantics.** `filtered_from` remains the count of *all visible* cases for the caller.
   With `scope=open&mine=1` it therefore reports the all-scope visible total, matching today's
   `scope=open` behaviour rather than a multi-stage "open, then mine" count.

## Implementation brief

**Owner:** `django-backend` for `ui/views.py`; `django-frontend` for the template; `qa-tester` for the
new conformance file. Verification by `verifier` against AC1–AC10 after the diff lands.

**Order of work**

1. Backend: parse `mine`, apply `filter(assignee=request.user)` after `case_visible_q`, add `mine`,
   `heading` and updated `has_filter`/`filtered_from` to context.
2. Template: add Mine link + hidden input, use `heading`, update empty-state condition.
3. Tests: new `tests/conformance/test_case_mine_filter.py` covering the list above.
4. Run targeted and regression suites; record any deviation in this plan.

**Constraints**

- Do not change the `scope` vocabulary or API.
- Do not add JS, CSS, migrations or new dependencies.
- Keep the control inside the existing `<form method="get" class="inline-form filter-bar">`.
- Use `{% querystring %}` for hrefs; do not string-concatenate query strings.
- Preserve the existing `btn-end` placement of **New case**.

## Implementation status (2026-10-29)

Implemented as planned, with the recommended defaults from the open questions:

- Mine toggles off on a second click.
- Heading reads `My open cases` / `My cases` / `My closed cases`.
- The control is available to viewers.
- Parameter is `mine=1`.
- Mine applies the last-submitted scope (not an unsaved dropdown value).
- Clear resets both filters.

Files changed:

- `ui/views.py` — `mine` parsing, post-`case_visible_q` assignee narrowing, `heading`, updated
  `has_filter`/`filtered_from`.
- `ui/templates/ui/case_list.html` — Mine toggle, hidden input, `{{ heading }}`, empty-state branch.
- `tests/conformance/test_case_mine_filter.py` — 17 new conformance cases.

Verification run:

```text
tests/conformance/test_case_mine_filter.py
tests/conformance/test_case_scope_filter.py
tests/conformance/test_ui_pagination.py
tests/conformance/test_ui_pagination_edge_cases.py
→ 125 passed

tests/conformance/test_tenant_scoping.py
tests/conformance/test_viewer_readonly_ui.py
tests/conformance/test_settings_users_scoping.py
tests/conformance/test_ui_case_create.py
→ 197 passed
```

No plan deviations. `ruff check` reports one pre-existing import-order warning at `ui/views.py:4443`
and `ruff format --check` reports pre-existing formatting at `ui/views.py:3473,4446`; neither line is
part of this change.
