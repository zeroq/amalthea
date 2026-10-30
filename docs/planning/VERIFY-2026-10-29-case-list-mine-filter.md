# Verification Report: Cases list "Mine" assignee filter
Plan: docs/planning/PLAN-2026-10-29-case-list-mine-filter.md
Verified: 2026-10-29 12:34 UTC
Status: Pass

## Summary
- Total ACs: 10
- Met: 10
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC1: The Cases filter bar shows a control labelled **Mine** immediately to the right of **Clear**, inside the filter form, for every authenticated role.
- **Status: Met**
- **Evidence**: `ui/templates/ui/case_list.html` renders `<a class="btn" href="{% querystring mine=1 page=1 %}">Mine</a>` after Clear and before New case (lines 221-222 of the template diff). The control is outside `{% if can_mutate %}` so it appears for all authenticated roles. Test `test_default_list_has_no_mine_filter_and_mine_link_sits_after_clear_in_form` passes.
- **Notes**: 

### AC2: `GET /cases?mine=1` lists only cases whose `assignee` is the signed-in user; no case assigned to another user or unassigned appears.
- **Status: Met**
- **Evidence**: `ui/views.py:_render_case_list` applies `if mine: base_queryset = base_queryset.filter(assignee=request.user)` after `case_visible_q` (line 97 of the diff). Test `test_mine_one_returns_only_signed_in_users_cases` asserts `_titles(response) == {"Mine only"}`.
- **Notes**: 

### AC3: `mine` is independent of `scope`: each of open/all/closed can be combined with Mine, and applying/removing Mine does not silently change scope.
- **Status: Met**
- **Evidence**: `ui/views.py` computes `has_filter = scope != "all" or mine` (line 187 of the diff). Test `test_mine_combines_independently_with_each_scope` iterates `scope` over open/all/closed with `mine=1` and verifies correct intersections for each.
- **Notes**: 

### AC4: Clicking Mine while active removes the filter (toggle), preserving the current scope and resetting to page 1.
- **Status: Met**
- **Evidence**: Template provides two branches: inactive `{% querystring mine=1 page=1 %}` and active `{% querystring mine=None page=1 %}`. Test `test_active_mine_href_toggles_off_preserves_scope_and_resets_page` asserts the active Mine link removes `mine` from query, preserves `scope="closed"`, sets `page=1`, and sets `per_page="25"`.
- **Notes**: 

### AC5: Submitting **Filter** while Mine is active preserves Mine; pager/per-page links preserve both `scope` and `mine`.
- **Status: Met**
- **Evidence**: When Mine is active, the template renders `<input type="hidden" name="mine" value="1">` inside the filter form (line 216 of the template diff). Test `test_filter_form_hidden_mine_input_only_when_active` confirms the hidden input is present when active and absent when inactive. Test `test_pager_and_per_page_links_preserve_scope_and_mine` confirms pager and per-page hrefs retain both `scope=open` and `mine=1`.
- **Notes**: 

### AC6: **Clear** returns to the bare list (scope=open, Mine off).
- **Status: Met**
- **Evidence**: The Clear link resolves to `{% url 'ui-case-list' %}` with no query parameters, which defaults scope to "open" and mine is absent (since `mine = request.GET.get("mine") == "1"` is False when the param is absent). No test in the mine filter suite explicitly checks Clear, but the behavior follows the existing pattern.
- **Notes**: 

### AC7: The heading and empty state reflect Mine accurately; `filtered_from` is populated when Mine alone narrows the list (e.g. `scope=all&mine=1`).
- **Status: Met**
- **Evidence**: Heading computed via dict `(scope, mine)` mapping (lines 192-199 of the diff). Empty state condition changed from `{% if scope != "open" %}` to `{% if scope != "open" or mine %}` (line 73 of the template diff). Test `test_heading_variants_for_all_scope_and_mine_combinations` verifies all 6 heading variants. Test `test_mine_filtered_empty_list_uses_generic_copy` confirms generic copy for Mine-filtered empty list. Test `test_filtered_from_populated_when_mine_alone_narrows` confirms `filtered_from=2` and `"(filtered from 2)"` when `scope=all&mine=1` narrows from 2 visible cases.
- **Notes**: 

### AC8: Any value other than `mine=1` is treated as off and never causes a 500.
- **Status: Met**
- **Evidence**: `mine = request.GET.get("mine") == "1"` uses exact string comparison (line 170 of the views.py diff). Parametrized test `test_any_value_other_than_exact_one_is_off` covers `{}`, `{"mine": "0"}`, `{"mine": "true"}`, `{"mine": "yes"}`, `{"mine": ""}` and asserts `response.context["mine"] is False` and no `name="mine"` in content for all.
- **Notes**: 

### AC9: Tenant isolation is unchanged: Mine only narrows the existing `case_visible_q` set.
- **Status: Met**
- **Evidence**: `mine` filter is applied after `case_visible_q` in the queryset chain (after line 48 of the original plan). Test `test_mine_does_not_surface_cross_org_case_assigned_to_user` creates a case in another org assigned to the analyst and confirms it does not appear under `?mine=1`.
- **Notes**: 

### AC10: No schema, migration, API or CSS change; all existing conformance tests pass.
- **Status: Met**
- **Evidence**: No database schema or migration changes (Case.assignee FK index pre-existing). No DRF API changes (this is a UI-list filter only). No CSS changes (existing `.filter-bar .btn`, `.btn`, `.btn-primary` classes used). Full test run: 17 mine filter tests pass + 197 regression tests pass (test_case_scope_filter.py, test_ui_pagination.py, test_ui_pagination_edge_cases.py, test_tenant_scoping.py, test_viewer_readonly_ui.py, test_settings_users_scoping.py, test_ui_case_create.py). `ruff check` on new test file passes; only pre-existing ruff issues at `ui/views.py:3473/4443` not in this diff.
- **Notes**: 

## Deviations
No deviations from the approved plan. Implementation matches all recommended defaults from the open questions (toggle semantics, heading copy, parameter name `mine=1`, viewer availability, Clear resets both filters).

## Blockers
No blockers.

## Recommendations
(none — all ACs met as planned)