# PLAN-2026-10-29 — Alerts list: "Mine" filter + link/status scopes

## Summary

Add a **Mine** assignee filter to the Alerts list, matching the Cases list pattern, and replace the
current three-value scope dropdown (`unlinked` / `linked` / `all`) with five explicit filters:

| Label | URL value | Meaning |
| --- | --- | --- |
| Unlinked (open) | `unlinked_open` | No linked case; alert status stage `New` |
| Unlinked (triaged) | `unlinked_triaged` | No linked case; alert status stage `InProgress` |
| Unlinked (closed) | `unlinked_closed` | No linked case; alert status stage `Closed` (includes `Dismissed`) |
| Linked (open) | `linked_open` | Linked to a case that is not closed |
| Linked (closed) | `linked_closed` | Linked to a case with `closed_date` set |

`?mine=1` is an independent filter that narrows either scope to alerts whose `assignee` is the
signed-in user. It is a server-side GET filter, bookmarkable, works without JavaScript, and combines
with the scope dropdown.

This is a greenfield cut: the old values (`unlinked`, `linked`, `all`) are removed rather than kept
as aliases. Existing tests that use them will be updated to the new values.

The nav badge, dashboard "Alerts waiting" count and dashboard "Newest alerts awaiting triage" list
will use the same unlinked-open predicate, so the badge and the queue agree.

> **Status:** approved 2026-10-29. Ready for implementation.

## Goals

1. **Mine** button after **Clear**, matching the Cases list behaviour:
   - Inactive link applies `mine=1`.
   - Active link removes it while preserving `scope`.
   - A hidden `mine=1` input keeps the filter when **Filter** is submitted.
   - `page=1` is reset on toggle; `per_page` is preserved by `{% querystring %}`.
2. Five scope options rendered in the dropdown with the labels above.
3. Server-side predicates exactly match the table above.
4. Both filters combine; `filtered_from` is the pre-filter org-scoped alert count.
5. Invalid `scope` values fall back to the default scope without a 500.
6. **Clear** resets both scope and Mine (bare `/alerts/`).
7. Tenant scoping remains first and absolute. Mine never widens visibility.
8. Viewer role can use the read-only filter; no write affordances are introduced.
9. Empty-state copy is honest: "No alerts match your filters" plus the unfiltered count when a
   filter emptied the list.
10. The nav badge and dashboard "Alerts waiting" count use the same unlinked-open predicate as the
    default queue; the dashboard's "Newest alerts awaiting triage" list uses it too.

## Non-goals

- No database schema or migration change. `Alert.assignee`, `Alert.status`, `Alert.case` and
  `Case.closed_date` already exist.
- No API/DRF change. This is a UI-list filter only.
- No saved filters, global "my work" queue, keyboard shortcut, or HTMX partial.
- No change to the Alerts table columns or the alert detail page.
- No legacy compatibility aliases: the old `unlinked`, `linked`, and `all` scopes are removed.

## Current state (verified)

`ui/views.py::alert_list` (around line 439):

```python
scope = request.GET.get("scope", "unlinked")
base_queryset = Alert.objects.select_related(
    "status", "assignee", "ingestion_source", "case", "owner_org"
).filter(org_q("owner_org", request.user))
unfiltered_count = base_queryset.count()

if scope == "linked":
    queryset = base_queryset.filter(case__isnull=False)
elif scope == "unlinked":
    queryset = base_queryset.filter(case__isnull=True)
else:  # scope == "all"
    queryset = base_queryset

has_filter = scope != "all"
```

The view passes `scopes=("unlinked", "linked", "all")`. The template loops `for value in scopes` and
prints `{{ value|capfirst }}`. These legacy values will be removed.

`ui/templates/ui/alert_list.html`:

- filter form: scope `<select>`, **Filter**, **Clear**;
- empty-state branches keyed on `scope == "linked" or scope == "all"` and `scope == "unlinked"`;
- two rendering paths: paginated (the live path) and a legacy fallback.

Relevant existing tests:

- `tests/conformance/test_ui_pagination.py`
  - `test_alert_list_scope_filter_preserved_across_pages` uses `scope=linked`.
  - `test_alert_filtered_from_count` expects default unlinked count and `scope=all` → `None`.
- `tests/conformance/test_ui_pagination_edge_cases.py`
  - linked-empty copy uses `scope=linked`;
  - genuinely-empty default copy uses no alerts;
  - DEF-3 default-empty test expects `filtered_from` disclosure or a clear-filters affordance.
- `tests/conformance/test_ui_loop.py`
  - MVP loop checks `scope=unlinked` / `scope=linked` after escalation.

Because the legacy values are being removed, these tests must be updated to the new scope values.

## Design decisions

### D1 — Scope values and predicates

```python
ALERT_SCOPE_OPTIONS = (
    ("unlinked_open", "Unlinked (open)"),
    ("unlinked_triaged", "Unlinked (triaged)"),
    ("unlinked_closed", "Unlinked (closed)"),
    ("linked_open", "Linked (open)"),
    ("linked_closed", "Linked (closed)"),
)
DEFAULT_ALERT_SCOPE = "unlinked_open"

ALERT_SCOPE_PREDICATES = {
    "unlinked_open": Q(case__isnull=True, status__stage="New"),
    "unlinked_triaged": Q(case__isnull=True, status__stage="InProgress"),
    "unlinked_closed": Q(case__isnull=True, status__stage="Closed"),
    "linked_open": Q(case__isnull=False, case__closed_date__isnull=True),
    "linked_closed": Q(case__isnull=False, case__closed_date__isnull=False),
}

scope = request.GET.get("scope", DEFAULT_ALERT_SCOPE)
if scope not in ALERT_SCOPE_PREDICATES:
    scope = DEFAULT_ALERT_SCOPE
queryset = base_queryset.filter(ALERT_SCOPE_PREDICATES[scope])
```

`status__stage` is used rather than status `value` so custom statuses work. `Dismissed` has stage
`Closed` and is therefore included in **Unlinked (closed)**.

### D2 — Linked open/closed is a case-lifecycle fact

For linked alerts, **open/closed** refers to the linked case (`case.closed_date`), not the alert
status. This is consistent with the Cases list and with escalation behaviour: a linked alert's
status is normally `Imported`, and closing the case sets linked alerts to `Closed`. An alert linked
to an open case must not disappear from "Linked (open)" just because someone changed its status.

### D3 — Mine is orthogonal

```python
mine = request.GET.get("mine") == "1"
base_queryset = Alert.objects...filter(org_q("owner_org", request.user))
unfiltered_count = base_queryset.count()

if mine:
    base_queryset = base_queryset.filter(assignee=request.user)

queryset = base_queryset.filter(ALERT_SCOPE_PREDICATES[scope])
page_obj = paginate(request, queryset.order_by("-date"))
filtered_from = (
    unfiltered_count if unfiltered_count > page_obj.paginator.count else None
)
```

`assignee=request.user` is the authenticated identity, never a client-supplied user id. Tenant
scoping happens first and is never loosened. Every new scope is a filter, so there is no unfiltered
"all" state; `filtered_from` only appears when the selected scope (plus Mine) actually narrows the
org-visible set.

### D4 — No legacy scope values

`scope=unlinked`, `scope=linked` and `scope=all` are no longer handled. They fall through to the
default `unlinked_open`, as does any other invalid value. The dropdown is the only scope surface.
Existing tests and bookmarks using old values must be updated.

### D5 — Default scope and queue counts

Default scope: **`unlinked_open`**. The nav badge, dashboard "Alerts waiting" count, and dashboard
"Newest alerts awaiting triage" list use the same predicate (`case IS NULL`, `status.stage = New`).
This makes the badge, dashboard and default queue agree and avoids showing triaged/dismissed rows
under an "awaiting triage" heading.

### D6 — Dropdown context

Pass labelled tuples, not bare strings:

```python
scopes = ALERT_SCOPE_OPTIONS
```

Template:

```django
{% for value, label in scopes %}
<option value="{{ value }}" {% if scope == value %}selected{% endif %}>{{ label }}</option>
{% endfor %}
```

Title case is used in the visible labels.

### D7 — Empty state

For the paginated path:

```django
{% if page_obj.paginator.count == 0 %}
  {% if filtered_from %}
    <p class="empty">
      No alerts match your filters (filtered from {{ filtered_from }}).
      <a href="{% url 'ui-alert-list' %}">Clear filters</a>
    </p>
  {% else %}
    <p class="empty">No alerts in this scope.</p>
  {% endif %}
{% endif %}
```

This keeps the existing linked-empty test's required copy ("No alerts match your filters." and
"Clear filters") and fixes the default-filtered-empty case. The legacy/transitional template path
must use the same copy; it is currently reachable only through an older view contract, but it must
not link to the removed `?scope=all` value.

### D8 — No heading change

The Alerts card has no heading today. This plan does not add one. The Mine button and selected scope
are the state indicators. A future "My unlinked open alerts" heading is optional polish, not part of
this task.

## Files to change

| File | Change |
| --- | --- |
| `ui/views.py` | Parse/validate five scopes; add Mine predicate; update nav/dashboard unlinked-open predicates; labelled `scopes` context; `mine`, `filtered_from`. |
| `ui/templates/ui/alert_list.html` | Render five labelled options; add Mine toggle + hidden input; update empty states. |
| `tests/conformance/test_alert_list_mine_scope.py` | New focused conformance tests for the five scopes, Mine, combinations, invalid fallback, labels, empty states, pagination, viewer. |
| `tests/conformance/test_ui_pagination.py`, `test_ui_pagination_edge_cases.py`, `test_ui_loop.py` | Replace removed `unlinked`/`linked`/`all` alert scopes with the new values and adjust no-filter expectations. |
| `docs/planning/PLAN-2026-10-29-alert-list-mine-scope-filter.md` | This plan; record deviations. |

No CSS, JS, HTMX, model, migration, serializer, or URL change is expected. The existing
`.filter-bar .btn`, `.btn`, `.btn-primary` classes cover the Mine control.

## Suggested implementation

### View

```python
ALERT_SCOPE_OPTIONS = (
    ("unlinked_open", "Unlinked (open)"),
    ("unlinked_triaged", "Unlinked (triaged)"),
    ("unlinked_closed", "Unlinked (closed)"),
    ("linked_open", "Linked (open)"),
    ("linked_closed", "Linked (closed)"),
)
DEFAULT_ALERT_SCOPE = "unlinked_open"
ALERT_SCOPE_PREDICATES = {
    "unlinked_open": Q(case__isnull=True, status__stage="New"),
    "unlinked_triaged": Q(case__isnull=True, status__stage="InProgress"),
    "unlinked_closed": Q(case__isnull=True, status__stage="Closed"),
    "linked_open": Q(case__isnull=False, case__closed_date__isnull=True),
    "linked_closed": Q(case__isnull=False, case__closed_date__isnull=False),
}
```

Resolve the scope to a queryset predicate, then apply Mine to the tenant-scoped base before the
scope predicate. Keep `select_related` and `order_by("-date")` unchanged. Compute
`filtered_from` from the paginated count:

```python
page_obj = paginate(request, queryset.order_by("-date"))
filtered_from = unfiltered_count if unfiltered_count > page_obj.paginator.count else None
```

Update `_base_context()`'s `nav_alert_count` and the dashboard's `alerts_unlinked` count plus
`recent_alerts` queryset to filter with `ALERT_SCOPE_PREDICATES[DEFAULT_ALERT_SCOPE]` in addition to
the existing org/visibility predicate.

### Template

Filter bar after **Clear**:

```django
{% if mine %}
  <input type="hidden" name="mine" value="1">
  <a class="btn btn-primary" href="{% querystring mine=None page=1 %}"
     aria-current="true"
     aria-label="Remove Mine filter — showing only alerts assigned to you">Mine</a>
{% else %}
  <a class="btn" href="{% querystring mine=1 page=1 %}"
     aria-label="Show only alerts assigned to me">Mine</a>
{% endif %}
```

## Acceptance criteria

### AC1 — Five scopes

- GET `/alerts/?scope=unlinked_open` returns only unlinked alerts whose status stage is `New`.
- GET `/alerts/?scope=unlinked_triaged` returns only unlinked alerts whose status stage is
  `InProgress`.
- GET `/alerts/?scope=unlinked_closed` returns only unlinked alerts whose status stage is `Closed`
  (including `Dismissed`).
- GET `/alerts/?scope=linked_open` returns only alerts linked to a case with `closed_date IS NULL`.
- GET `/alerts/?scope=linked_closed` returns only alerts linked to a case with a non-null
  `closed_date`.
- The five sets are disjoint for valid data; each alert appears in at most one.

### AC2 — Dropdown

- The dropdown contains exactly the five labels/values above, in that order.
- No `all`, `unlinked`, or `linked` option is rendered.
- The current `scope` is selected; an empty/invalid scope selects `unlinked_open`.
- Selecting a scope and pressing **Filter** issues a GET and preserves `mine` when active.

### AC3 — Mine

- `?mine=1` returns only alerts whose `assignee` is `request.user`.
- Mine combines with every new scope.
- Mine never returns another org's alerts or another user's assigned alerts.
- Active Mine renders a hidden `mine=1` input and an active/aria-current toggle.
- Clicking active Mine removes it and preserves scope; `page` resets to 1.
- Pagination/per-page links preserve both `scope` and `mine`.
- **Clear** returns to bare `/alerts/` (default scope, no Mine).

### AC4 — Honest empty states

- When a filter yields zero rows but org-visible alerts exist, the page says
  "No alerts match your filters", shows `filtered_from`, and offers **Clear filters**.
- When the org has no alerts at all, the page says "No alerts in this scope." and does not claim
  rows were filtered out.
- Existing DEF-2/DEF-3 conformance expectations remain satisfied after updating them to new scopes.

### AC5 — Queue counts and safety

- The nav badge, dashboard "Alerts waiting" count, and dashboard "Newest alerts awaiting triage"
  list use the unlinked-open predicate (`case IS NULL`, `status.stage = New`).
- Removed scopes (`unlinked`, `linked`, `all`) and any other invalid value fall back to
  `unlinked_open`; they cannot raise or widen visibility.
- Viewer role can use both filters; no mutating affordance is added.
- No schema, migration, API or CSS change.
- Targeted conformance tests and the existing pagination/UI-loop regression suites pass after
  updating their scope values.

## Test plan

New file `tests/conformance/test_alert_list_mine_scope.py`:

1. Fixture creates, in one org:
   - unlinked `New` alert;
   - unlinked `Triaged`/`InProgress`;
   - unlinked `Dismissed`/`Closed`;
   - unlinked `Closed`/`Closed`;
   - alert linked to an open case (status `Imported`);
   - alert linked to a closed case (status `Closed`);
   - an open-case linked alert with a non-`Imported` status (proves status does not drive linked
     scope);
   - alerts assigned to the signed-in analyst, another analyst, and nobody;
   - one alert in another org.
2. Assert each of the five scope counts and object identities.
3. Assert Mine and Mine × scope combinations.
4. Assert invalid/removed scope values fall back to `unlinked_open`.
5. Assert dropdown labels/order and absence of `all`, `unlinked`, `linked`.
6. Assert Mine toggle markup, hidden input, aria-current, scope preservation, page reset.
7. Assert filtered/unfiltered empty copy and `filtered_from`.
8. Assert pagination preserves `scope` + `mine`.
9. Assert nav badge and dashboard count/list use unlinked-open.
10. Assert viewer access.

Existing test updates:

- `test_ui_pagination.py`: replace `scope=linked` with `linked_open`; remove the `scope=all`
  no-filter assertion and replace it with a "scope does not narrow" `filtered_from is None` case.
- `test_ui_pagination_edge_cases.py`: replace `scope=all` pager tests with `unlinked_open`;
  replace `scope=linked` with `linked_open`; replace the old `scope=all` no-disclosure test with an
  "all rows are in the selected scope" no-disclosure test.
- `test_ui_loop.py`: replace `scope=unlinked` with `unlinked_open` and `scope=linked` with
  `linked_open`.

Regression commands:

```bash
.venv/bin/pytest tests/conformance/test_alert_list_mine_scope.py
.venv/bin/pytest tests/conformance/test_ui_pagination.py \
  tests/conformance/test_ui_pagination_edge_cases.py \
  tests/conformance/test_ui_loop.py
```

## Risks

| Risk | Mitigation |
| --- | --- |
| Existing tests depend on old scope values | Remove aliases and update the alert-scope tests as listed. |
| Linked alerts have alert status `Imported`, not "open" | Define linked scopes by case `closed_date`, not alert status. |
| Nav/dashboard count drifts from the default list | Use the same `ALERT_SCOPE_PREDICATES["unlinked_open"]` expression in all three places. |
| `Dismissed` vs `Closed` ambiguity | Both have stage `Closed`; both belong in **Unlinked (closed)**. |
| Orphaned `Imported` alert with no case is in no new bucket | It is intentionally outside the five buckets; document as a data-integrity case, not a UI filter. |
| `filtered_from` disclosure when filter count equals unfiltered total | Keep the existing guard `unfiltered_count > filtered_count`. |

## Resolved decisions (2026-10-29)

1. **Linked open/closed:** linked to an open/closed **case**, keyed on `case.closed_date`.
2. **Default:** `unlinked_open`; nav and dashboard waiting counts/lists use the same predicate.
3. **Legacy values:** removed; no compatibility aliases.
4. **Labels:** title case.

## Implementation brief

- Backend (`django-backend`): `ui/views.py` — scope constants/predicates, validation, Mine
  predicate, nav/dashboard unlinked-open predicates, context.
- Frontend (`django-frontend`): `ui/templates/ui/alert_list.html` — labelled options, Mine
  toggle/hidden input, empty-state copy.
- QA (`qa-tester`): new `tests/conformance/test_alert_list_mine_scope.py`; update old scope values
  in pagination/UI-loop tests; run targeted + regression suites.
- Verification (`verifier`): check every AC above against this plan and record deviations here.
