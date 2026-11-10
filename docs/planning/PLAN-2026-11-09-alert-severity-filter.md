# Plan — Alert-list severity filter toggles

**Status:** Implemented and verified 2026-11-09 (AC-SEV-1..10 MET); full-suite result pending
user-run log.
**Verification:** `docs/verification/VERIFY-2026-11-09-alert-severity-filter.md`
**Scope:** Alerts list only. Case-list equivalent follows separately.
**No migrations. No API changes.**

## Summary

Add an always-visible severity toggle row beneath the existing alert-list filter row. Each of the four
severity values — Low, Medium, High, Critical — is a link styled as a neutral button. Clicking an
inactive severity adds it to the active filter; clicking an active severity removes only that value.
Multiple selected severities combine with OR.

Example: `?scope=unlinked_open&severity=1&severity=3` shows Low and High alerts in the selected
scope, excluding Medium and Critical.

## Decisions

- Severity selection is multi-select and OR-combined.
- All four severity buttons are always shown, independent of result data.
- No counts are shown on the buttons in v1.
- Clicking a severity applies the filter immediately via a normal link; no JavaScript.
- Buttons stay neutral; the selected button gets an outline in its semantic severity colour.
- The existing **Clear** control clears all filters, including severity.
- Toggling severity resets pagination to page 1.
- Existing scope, `Mine`, `per_page`, and other query parameters are preserved when toggling severity.
- Active severity values are emitted as hidden form fields so the existing **Filter** submit keeps
  them when the user changes Scope.
- The case list gets the same concept later; it is out of scope for this implementation brief.

## Goals

- Add a complete severity filter to the alerts list without changing alert data or APIs.
- Keep the interaction keyboard-accessible and usable without JavaScript.
- Preserve existing filter state when toggling severity or submitting the Scope form.
- Build the URL/parsing logic so the case list can reuse it later.

## Non-goals

- Case-list severity filtering.
- Counts/badges on severity buttons.
- Saved filters, chips, or a separate “clear severity” control.
- Changes to alert ordering, API endpoints, models, migrations, or automation behaviour.
- A new global design-system component beyond the small reusable helper and row styles.

## Architecture

No new architectural boundary and no ADR required. The implementation stays inside the existing
server-rendered UI:

- A small reusable helper parses and validates severity query values and builds toggle URLs.
- `alert_list` filters the already org- and scope-scoped queryset with `severity__in`.
- The template renders the toggle links and hidden form values from context.
- CSS source changes are compiled into the committed Tailwind artifact.

Recommended helper: `ui/severity_filter.py`

```python
SEVERITY_PARAM = "severity"


def parse_severity_filter(get: QueryDict) -> list[int]:
    """Return valid, de-duplicated severities in ascending order."""


def build_severity_filter_links(request: HttpRequest, selected: list[int]) -> list[dict[str, Any]]:
    """Return ordered {value, label, selected, url} toggle options."""
```

`build_severity_filter_links` must:

- use `SEVERITY_CHOICES` for labels and order;
- copy `request.GET`, replace all `severity` values with the toggled valid set, and remove `page`;
- preserve every other query parameter;
- remove the `severity` key entirely when the toggled set becomes empty;
- return a local path plus query string, never an external URL.

### View context contract

`ui/views/alerts.py::alert_list` provides:

```python
selected_severities: list[int]
severity_filter_links: list[dict[str, object]]  # value, label, selected, url
```

Query behaviour:

1. Keep the existing org filter and scope/`mine` filters unchanged.
2. Parse `severity` with `request.GET.getlist("severity")`.
3. Ignore non-integers and values outside `SEVERITY_MIN..SEVERITY_MAX`; de-duplicate.
4. If at least one valid severity is selected, apply `queryset.filter(severity__in=selected)`.
5. Paginate after all filters, preserving existing page-size behaviour.

## Data model

No changes. `Alert.severity` already uses the shared `SEVERITY_CHOICES` vocabulary.

## API contract

No REST/webhook changes. The UI list continues to use `GET /ui/alerts/` (route name
`ui-alert-list`) with an additive repeated `severity` parameter:

```text
?scope=unlinked_open&mine=1&severity=1&severity=3&per_page=50
```

Invalid or duplicate severity values are non-fatal and ignored/deduplicated.

## UI/UX

Inside the existing filter card:

```text
[Scope ▼] [Filter] [Clear] [Mine] [Import JSON]
Severity: (Low) (Medium) (High) (Critical)
```

- Wrap the existing controls and the new row in separate `.control-row` elements inside the form.
- The form no longer relies on `.inline-form` for its own layout; update the related CSS comment if
  its “filter bar markup is also `.inline-form`” note becomes stale.
- Severity controls are anchors with `class="btn btn-sm severity-filter severity-filter--<n>"`.
- Inactive buttons keep the neutral `.btn` appearance.
- Active buttons keep the neutral fill and receive a severity-coloured border/inset outline.
- Active buttons carry `aria-current="true"`; each control has an accessible name such as
  “Add High severity filter” or “Remove High severity filter”.
- The row is a labelled group, e.g. `role="group" aria-labelledby="severity-filter-label"`.
- Severity is communicated by text label plus state, never colour alone.
- Render one hidden `<input type="hidden" name="severity" value="<n>">` per selected severity inside
  the form so **Filter** preserves the selection.
- The existing **Clear** link remains the unfiltered list URL.
- Keep the controls reachable and operable by keyboard; normal anchor focus styling applies.

## Phases and tasks

### Phase 1 — Backend contract and filtering (`django-backend`)

1. Add `ui/severity_filter.py` with the parsing and link-building helpers.
   - **AC-SEV-1:** Valid values are deduplicated and returned in ascending order.
   - **AC-SEV-2:** Invalid/out-of-range values are ignored without error.
   - **AC-SEV-3:** Toggle links preserve non-severity query parameters, remove only the toggled
     severity, and remove `page`.
2. Update `ui/views/alerts.py::alert_list`.
   - Parse/filter with `severity__in` after org, scope, and `mine` filtering.
   - Add `selected_severities` and `severity_filter_links` to `_base_context`.
   - **AC-SEV-4:** Low + High returns both and excludes Medium/Critical.
   - **AC-SEV-5:** Severity filtering never crosses organisation or selected-scope boundaries.
3. Add backend conformance tests in
   `tests/conformance/test_alert_list_severity_filter.py`.
   - **AC-SEV-6:** Single/multi-select OR filtering, scope intersection, invalid/duplicate handling,
     toggle URL preservation/removal, page reset, and viewer access are covered.

### Phase 2 — Template and styles (`django-frontend`)

1. Update `ui/templates/ui/alert_list.html`.
   - Restructure the filter form into two `.control-row` rows.
   - Render all four severity links from `severity_filter_links`.
   - Add selected-state semantics and accessible names.
   - Add hidden severity inputs for form submission.
   - **AC-SEV-7:** All four labels are always rendered, with no counts and no JavaScript.
   - **AC-SEV-8:** Selected and unselected controls expose distinct accessible names/state.
2. Update `ui/static/ui/app.src.css`.
   - Add severity-row spacing and neutral-button selected outlines per severity colour.
   - Run `make css` to regenerate `ui/static/ui/app.css`.
   - **AC-SEV-9:** No layout regression in the existing filter row; selected outline is visible against
     the dark surface and remains distinguishable without relying on colour alone.
3. Add UI conformance tests in
   `tests/conformance/test_alert_list_severity_filter_ui.py`.
   - **AC-SEV-10:** Four controls, correct labels/classes, selected `aria-current`, hidden inputs,
     immediate-toggle links, Clear behaviour, and no inline script/style are covered.

### Phase 3 — Verification

- Run focused tests, then `.venv/bin/pytest`, `make check-fast`, `make css-check`.
- Hand off to `verifier` with this plan and the brief.
- Record any deviation in this plan before closing.

## Security

- Severity values are parsed as bounded integers; never interpolated into SQL.
- Organisation scoping and existing scope predicates run before severity narrowing.
- Toggle URLs are generated from local request paths; no user-controlled redirect target.
- No new write path; viewers retain read-only access to the list and its filters.
- Labels come from the closed `SEVERITY_CHOICES` enum and are autoescaped.
- Hidden fields carry only validated integer severities.

## Testing

Backend:

- OR semantics for one and multiple severities.
- Intersection with each scope and `Mine`.
- Organisation isolation.
- Duplicate/invalid/out-of-range parameters.
- Toggle URL preserves `scope`, `mine`, `per_page`; removes `page`; removes only the clicked value.
- Empty selection removes the severity parameter.
- Viewer can filter.

Frontend:

- Four buttons always present in fixed order.
- Active state and accessible names.
- Hidden inputs match selected severities.
- Existing Filter submit keeps selected severities.
- Clear removes all filters.
- No inline JS/style; CSS artifact is current.

## Risks

- **Query-string handling:** repeated `severity` parameters can be accidentally collapsed. Mitigate
  with `getlist`/`setlist` and explicit tests.
- **Form submission:** links alone would lose severity when Scope is submitted. Hidden fields and an
  explicit test prevent this.
- **CSS drift:** the committed Tailwind artifact must be regenerated; `make css-check` is part of DoD.
- **Layout regression:** wrapping the existing row changes flex ownership. Verify the row still wraps
  and the Import action stays right-aligned.
- **Accessibility:** selected state must not depend on colour alone. Keep visible labels plus
  `aria-current` and descriptive accessible names.

## Case-list follow-up (not in this brief)

Apply the same helper and row to `_render_case_list` / `case_list.html` after the alert-list contract
is verified. The case-list implementation must also drop the `new=1` modal parameter when toggling a
severity so the New-case modal does not reopen unexpectedly.

## Open questions

None. Decisions are locked by the user’s 2026-11-09 answers.
