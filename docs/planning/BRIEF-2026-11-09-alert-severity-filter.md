# Implementation Brief — Alert-list severity filter toggles

**Plan:** `docs/planning/PLAN-2026-11-09-alert-severity-filter.md` (approved 2026-11-09)
**Status:** Ready for implementation
**Scope:** Alert list only. Case list is a later follow-up.
**No migrations. No API changes. No commits unless explicitly requested.**

Preserve existing uncommitted work in the tree. The repository currently contains completed
source-observable-apply changes that must not be reverted.

## Shared contract

Backend supplies exactly these keys to `ui/templates/ui/alert_list.html`:

```python
selected_severities: list[int]  # valid, deduplicated, ascending
severity_filter_links: list[dict[str, object]]
# each item: {"value": int, "label": str, "selected": bool, "url": str}
```

`url` is a local path plus query string. It already represents the toggled state:

- inactive severity: current query + this severity;
- active severity: current query - this severity only;
- all other query parameters preserved;
- `page` removed;
- `severity` key removed when the new set is empty.

Wire format: repeated params, for example `?severity=1&severity=3`. OR semantics.

## Backend tasks (`django-backend`)

File: `ui/severity_filter.py` (new)

1. `parse_severity_filter(get: QueryDict) -> list[int]`
   - read `get.getlist("severity")`;
   - accept only integer values in `SEVERITY_MIN..SEVERITY_MAX`;
   - ignore invalid values;
   - deduplicate;
   - return ascending order.
2. `build_severity_filter_links(request: HttpRequest, selected: list[int]) -> list[dict[str, object]]`
   - iterate `SEVERITY_CHOICES` so order and labels cannot drift;
   - toggle one value at a time (`selected_set ^ {value}`);
   - copy `request.GET` and use `setlist`/`pop` for `severity`;
   - remove `page`;
   - preserve every other query parameter, including repeated ones;
   - return `{value, label, selected, url}` for all four severities;
   - use `request.path`, never an external/redirect URL.

File: `ui/views/alerts.py`

3. In `alert_list`:
   - parse `selected_severities` once;
   - apply `queryset.filter(severity__in=selected_severities)` after the org, `mine`, and scope
     filters, before pagination;
   - add `selected_severities` and `severity_filter_links` to `_base_context`;
   - update the docstring to mention OR-combined repeated `severity` parameters.
4. Do not change ordering, pagination, scope predicates, or `filtered_from` behaviour unless a test
   proves a necessary correction.

File: `tests/conformance/test_alert_list_severity_filter.py` (new)

5. Cover:
   - no severity filter returns all alerts in scope;
   - `severity=1` returns only Low;
   - `severity=1&severity=3` returns Low and High, excludes Medium and Critical;
   - filtering intersects with each selected scope and with `mine=1`;
   - organisation isolation is preserved;
   - invalid (`abc`, `0`, `5`, blank) and duplicate values are ignored/deduped without a 500;
   - toggle links preserve `scope`, `mine`, `per_page`, and other severities; remove only the clicked
     value; remove `page`; remove the parameter when the last severity is toggled off;
   - all four options are present in context in fixed order even when no matching alerts exist;
   - viewer access works.

Run focused tests:

```bash
.venv/bin/pytest tests/conformance/test_alert_list_severity_filter.py tests/conformance/test_alert_list_mine_scope.py
.venv/bin/ruff check ui/views/alerts.py ui/severity_filter.py tests/conformance/test_alert_list_severity_filter.py
.venv/bin/ruff format --check ui/views/alerts.py ui/severity_filter.py tests/conformance/test_alert_list_severity_filter.py
```

## Frontend tasks (`django-frontend`)

File: `ui/templates/ui/alert_list.html`

1. Change the filter form into two stacked rows inside the same card:
   - first row: existing controls (`scope`, Filter, Clear, Mine, Import JSON);
   - second row: persistent label “Severity” and all four toggle links.
2. Recommended structure:

```html
<form method="get" class="filter-bar" aria-label="Alert filters">
  <div class="control-row">
    <!-- existing controls, including .btn-end on Import JSON -->
  </div>
  <div class="control-row severity-filter-row"
       role="group" aria-labelledby="severity-filter-label">
    <span id="severity-filter-label" class="dim">Severity</span>
    {% for option in severity_filter_links %}
      <a id="severity-filter-{{ option.value }}"
         class="btn btn-sm severity-filter severity-filter--{{ option.value }}"
         href="{{ option.url }}"
         data-severity="{{ option.value }}"
         {% if option.selected %}aria-current="true"{% endif %}
         aria-label="{% if option.selected %}Remove{% else %}Add{% endif %} {{ option.label }} severity filter">{{ option.label }}</a>
    {% endfor %}
  </div>
  {% for severity in selected_severities %}
    <input type="hidden" name="severity" value="{{ severity }}">
  {% endfor %}
</form>
```

3. Do not add JavaScript, counts, or inline styles. Clicking a link applies immediately.
4. Keep the hidden severity inputs inside the form so the existing **Filter** submit preserves active
   severities when Scope changes.
5. Keep **Clear** as the unfiltered list URL; it must remove severity.

File: `ui/static/ui/app.src.css`

6. Add:
   - spacing for `.severity-filter-row`;
   - selected-state outline per severity, keeping the neutral button fill:
     `.severity-filter--1[aria-current="true"]` etc. using `--color-sev-1..4`;
   - no new colour-only signal: text label + aria state remain present.
7. Run `make css` and commit nothing; leave the regenerated artifact in the worktree.

File: `tests/conformance/test_alert_list_severity_filter_ui.py` (new)

8. Cover:
   - exactly four severity controls, in Low/Medium/High/Critical order;
   - each carries the expected severity class and `data-severity`;
   - selected controls have `aria-current="true"`; unselected do not;
   - accessible names say Add/Remove severity filter;
   - selected severities render matching hidden inputs inside the filter form;
   - inactive links preserve current scope/mine/per_page and add the value;
   - active links remove only that value;
   - no count text is rendered on the buttons;
   - no inline `<script>`/`style` was added.

Run focused tests:

```bash
.venv/bin/pytest tests/conformance/test_alert_list_severity_filter_ui.py tests/conformance/test_alert_list_mine_scope.py
make css-check
.venv/bin/ruff check tests/conformance/test_alert_list_severity_filter_ui.py
```

## Acceptance criteria

- `AC-SEV-1..10` from the plan.
- `?severity=1&severity=3` means Low OR High, not AND.
- Low + High excludes Medium and Critical.
- All four severity controls are always visible, with no counts.
- Clicking toggles immediately, preserves existing scope/Mine/page-size/other severities, and resets
  pagination.
- Selected buttons remain neutral with a severity-coloured outline and accessible selected state.
- The Scope form's **Filter** submission preserves active severities via hidden inputs.
- Invalid/duplicate severity values never 500 and never escape org/scope isolation.
- Viewers can use the filter; no write path is introduced.
- No migration, API change, JavaScript, inline CSS, or new dependency.

## Verification

Run focused tests, then:

```bash
.venv/bin/pytest
make check-fast
make css-check
```

Hand off to `verifier` with the plan and this brief. Record any deviation in the plan document.
