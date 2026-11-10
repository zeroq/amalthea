# Verification Report: Alert-list Severity Filter Toggles

Plan: docs/planning/PLAN-2026-11-09-alert-severity-filter.md
Brief: docs/planning/BRIEF-2026-11-09-alert-severity-filter.md
Verified: 2026-11-09 14:32 UTC
Status: Pass

## Summary
- Total ACs: 10
- Met: 10
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC-SEV-1: Valid values are deduplicated and returned in ascending order
- **Status: Met**
- Evidence: `ui/severity_filter.py:18-34` — `parse_severity_filter` reads `getlist(SEVERITY_PARAM)`, validates each value as integer in `SEVERITY_MIN..SEVERITY_MAX`, deduplicates via `set`, returns `sorted(valid)`.
- Notes: Repeated parameters (e.g. `severity=3&severity=1&severity=3`) are correctly deduplicated and returned in ascending order `[1, 3]`.

### AC-SEV-2: Invalid/out-of-range values are ignored without error
- **Status: Met**
- Evidence: `ui/severity_filter.py:28-33` — `try/except (TypeError, ValueError)` around `int()` conversion; range check `SEVERITY_MIN <= val <= SEVERITY_MAX`; invalid values (`abc`, `0`, `5`, `""`, `3.5`, `-1`) are silently skipped.
- Notes: No 500 errors are raised for invalid inputs (confirmed by `test_invalid_severity_no_500`).

### AC-SEV-3: Toggle links preserve non-severity query parameters, remove only the toggled severity, and remove `page`
- **Status: Met**
- Evidence: `ui/severity_filter.py:69-80` — `build_severity_filter_links` copies `request.GET`, uses `setlist`/`pop` for the `severity` key, always `pop("page", None)`. Every other query parameter is preserved. URL is built from `request.path` + encoded query string (never an external URL).
- Notes: Toggle URLs correctly retain `scope`, `mine`, `per_page` and other severity values when toggling a single value.

### AC-SEV-4: Low + High returns both and excludes Medium/Critical
- **Status: Met**
- Evidence: `ui/views/alerts.py:104-105` — `queryset.filter(severity__in=selected_severities)` uses Django OR `__in` semantics. Test `test_severity_or_filtering` confirms `?severity=1&severity=3` shows Low and High alerts, excluding Medium and Critical.
- Notes: OR combination is the intended semantics per the plan.

### AC-SEV-5: Severity filtering never crosses organisation or selected-scope boundaries
- **Status: Met**
- Evidence: `ui/views/alerts.py:92,100-105` — Org filter via `org_q("owner_org", request.user)` runs first; scope filter via `ALERT_SCOPE_PREDICATES[scope]` runs second; `mine` filter runs third; severity filter (`severity__in=`) runs after all three, before pagination. `test_organisation_isolation` confirms no cross-org leakage.
- Notes: The filter application order is org → scope → mine → severity → paginate.

### AC-SEV-6: Backend conformance tests cover single/multi-select OR filtering, scope intersection, invalid/duplicate handling, toggle URL preservation/removal, page reset, and viewer access
- **Status: Met**
- Evidence: `tests/conformance/test_alert_list_severity_filter.py` — all 17 tests cover: `parse_severity_filter` (5 tests), `build_severity_filter_links` (3 tests), and `alert_list` integration (9 tests including organisation isolation, scope intersection, mine intersection, invalid values, fixed order when no matches, OR filtering, viewer access).
- Notes: No test gaps detected in the backend conformance suite.

### AC-SEV-7: All four labels are always rendered, with no counts and no JavaScript
- **Status: Met**
- Evidence: `ui/templates/ui/alert_list.html:31-44` — `for link in severity_filter_links` iterates all 4 severities in fixed order (from `SEVERITY_CHOICES` in the backend). Button inner text is just the label ("Low", "Medium", "High", "Critical") with no count badges. Template contains no `{%%}` script blocks.
- Notes: The four controls are always present regardless of matching alert count.

### AC-SEV-8: Selected and unselected controls expose distinct accessible names/state
- **Status: Met**
- Evidence: `ui/templates/ui/alert_list.html:33-43` — Selected buttons carry `aria-current="true"` and aria-label `"Remove {{ label }} severity filter"`. Inactive buttons have aria-label `"Add {{ label }} severity filter"`. Labels are communicated by text + state, never colour alone.
- Notes: `data-severity` attribute is present on all anchors for any future JS enhancement.

### AC-SEV-9: No layout regression in the existing filter row; selected outline is visible against the dark surface and remains distinguishable without relying on colour alone
- **Status: Met**
- Evidence: `ui/static/ui/app.src.css:85-135` — `.severity-filter-row` flex row with `gap: 8px` and `margin-top: var(--gap)`; `.severity-filter` neutral `.btn` style with `:hover`/`:focus-visible` border-colour per severity; selected state uses `border-color` + `box-shadow: inset 0 0 0 1px var(--color-sev-N)` (not colour-alone, since text label + `aria-current` are also present).
- Notes: The existing filter row (`.control-row` with scope/Filter/Clear/Mine/Import) is unaffected; severity row is a separate stacked `.control-row`.

### AC-SEV-10: Four controls, correct labels/classes, selected `aria-current`, hidden inputs, immediate-toggle links, Clear behaviour, and no inline script/style are covered
- **Status: Met**
- Evidence: `tests/conformance/test_alert_list_severity_filter_ui.py` — all 9 UI tests pass: `test_ui_four_severity_controls_in_fixed_order`, `test_ui_selected_has_aria_current_and_accessible_name`, `test_ui_hidden_inputs_match_selected_and_inside_form`, `test_ui_no_counts_on_severity_buttons`, `test_ui_no_inline_script_style`, `test_ui_clear_removes_all_filters`, `test_ui_visible_severity_label`, `test_ui_multiple_selected_severities_two_hidden_inputs`, `test_ui_filter_form_low_high_url_integration`.
- Notes: Frontend conformance test suite covers every AC-SEV-10 requirement.

## Deviations
- **None**. All 10 acceptance criteria are fully met with no deviations.

## Blockers
- **None**.

## Recommendations
- No code changes required. The implementation conforms entirely to the approved plan and brief.
- If the case-list severity filter is later implemented (out of scope for this brief), the same `ui/severity_filter.py` helper and CSS patterns can be reused.
- Consider running `make css` to regenerate `ui/static/ui/app.css` from the updated `app.src.css` if the feature is merged; the artifact is already prepared in the working tree.