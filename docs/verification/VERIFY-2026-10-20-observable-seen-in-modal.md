# Verification Report: Case Detail → Observables tab: clickable "Seen in" count → linked-cases modal

**Plan:** `docs/planning/PLAN-2026-10-20-observable-seen-in-modal.md` (APPROVED 2026-10-20)
**Verified:** 2026-10-20 14:30 UTC
**Status:** **Pass**

---

## Summary

| Metric | Count |
|--------|-------|
| Total ACs | 9 |
| Met | 9 |
| Not Met | 0 |
| Deviated | 0 |

All 9 acceptance criteria are satisfied. The implementation matches the approved plan exactly, including the two QA-caught defect fixes documented in plan §11.1 (deviations 3 and 4). All targeted tests pass; `make check` is green; the undefined-class backlog remains at 42.

---

## Criterion-by-Criterion

### AC-1: "Seen in" count is an interactive control opening modal `observable-cases-<id>`
**Status:** **Met**  
**Evidence:** `ui/templates/ui/_tab_observables.html:46-52` renders a `<button>` with `data-modal-open="observable-cases-{{ link.observable.id }}"` when `seen.cases` exists. Modal loop at lines 92-98 includes `ui/_modal.html` with matching `modal_id="observable-cases-"|add:oid` (where `oid=link.observable.id|stringformat:"s"`).  
**Test:** `test_seen_in_count_renders_trigger_and_matching_dialog` — asserts trigger exists, `modal_id` matches `observable-cases-<obs.id>`, and dialog with that id is present.

### AC-2: Modal lists each visible linked case as number (linking to `ui-case-detail`) + title
**Status:** **Met**  
**Evidence:** `ui/templates/ui/_observable_cases_modal_body.html:13-18` — both Case number and Title are `<a href="{% url 'ui-case-detail' c.number %}">` links; Status rendered as `<span class="sev sev-{{ c.severity }}">{{ c.status }}</span>`.  
**Test:** `test_seen_in_modal_lists_visible_cases_with_links_and_status` — asserts for each linked case: number link, title link, and status value present in modal.

### AC-3: Modal set is exactly `case_visible_q(user)` ∩ links of that observable (cross-org leak test green)
**Status:** **Met**  
**Evidence:** `ui/views.py:982-987` — payload built via `CaseObservable.objects.filter(observable_id__in=observable_ids).filter(case__in=Case.objects.filter(case_visible_q(request.user)))`. This uses the `case__in=Case.objects.filter(case_visible_q(...))` pattern (plan §11.1 deviation 3) because `case_visible_q` references `Case` fields; a direct filter on `CaseObservable` would raise `FieldError`.  
**Test:** `test_seen_in_modal_never_leaks_other_org_cases` — creates org-A and org-B cases linked to same observable; org-A user sees only org-A cases. Asserts: org-B case `href`, number, and title **absent** from entire HTML; button count == 2 (viewing + sibling). Non-vacuity: org-A sibling case IS listed.  
**Falsifiability (§7.5):** The test would fail if the `case_visible_q` filter were removed — the org-B case would appear in the modal and the assertions `foreign_url not in html`, `foreign.number not in html`, `foreign.title not in html`, and `button_count == 2` would all fail. The test is genuinely able to catch a missing guard.

### AC-4: Cell count == modal row count (for the same scope)
**Status:** **Met**  
**Evidence:** Cell shows `{{ seen.cases|length }}` (`_tab_observables.html:51`); modal body iterates `{% for c in seen.cases %}` (`_observable_cases_modal_body.html:13`). Both use the same capped `cases` list.  
**Test:** `test_seen_in_count_equals_modal_row_count` — asserts `shown == 4` (viewing case + 3 linked) and `body_rows == shown` (modal `<tr>` count minus header equals button count).

### AC-5: Viewers can open the modal; it is not inside `can_mutate`
**Status:** **Met**  
**Evidence:** Modal loop at `_tab_observables.html:92-98` is placed **before** the `{% if can_mutate %}` block at line 100. The Edit/Remove/Create modals are inside `can_mutate`; the "Seen in" modals are not.  
**Test:** `test_viewer_sees_trigger_and_modal_but_no_artifact_mutation_controls` — viewer (role=VIEWER) sees the trigger (`data-modal-open`) and dialog (`id="observable-cases-..."`), but **not** `observable-remove`, `observable-edit`, or `observable-create-modal`. Analyst (control) sees all mutation affordances.

### AC-6: Zero-visible-count behaviour per Q1 (plain `0 cases`, no trigger)
**Status:** **Met**  
**Evidence:** `_tab_observables.html:53-55` — `{% else %}<span class="dim">0 cases</span>{% endif %}` — no button, no modal trigger when `seen.cases` is empty/falsey.  
**Test:** `test_zero_visible_links_render_plain_text_without_trigger` — renders partial with empty `linked_cases_by_observable`; asserts "0 cases" present, no `observable-cases-<id>` trigger. Non-vacuity: same render with a populated bucket flips to button + dialog.

### AC-7: Large fan-out behaviour per Q4 (cap at `RAIL_CAP` + honest "Showing latest N of M cases")
**Status:** **Met**  
**Evidence:** View caps at `RAIL_CAP` (`ui/views.py:992`: `if len(bucket["cases"]) < RAIL_CAP:`); `bucket["total"]` counts all visible links. Modal template lines 23-25: `{% if seen.total > seen.cases|length %}<p class="list-summary dim">Showing latest {{ seen.cases|length }} of {{ seen.total }} cases</p>{% endif %}`.  
**Test:** `test_seen_in_modal_caps_rows_and_shows_honest_total` — creates `RAIL_CAP + 1` linked cases; asserts button count == `RAIL_CAP`, modal rows == `RAIL_CAP`, and "Showing latest {RAIL_CAP} of {RAIL_CAP+1} cases" text present in modal.

### AC-8: `make check` green; undefined-class backlog unchanged (42)
**Status:** **Met**  
**Evidence:** `make check` output (ran 2026-10-20):
```
── ruff check → All checks passed!
── ruff format → (clean)
── mypy → Success: no issues found in 110 source files
── django check → System check identified no issues (0 silenced)
── migrations in sync → No changes detected
── css drift check → ✓ css-check: artifact is up to date
✓ check passed
```
CSS token test (`test_ui_css_tokens.py`) warns: "Pre-existing project classes referenced in templates but not defined in app.css (**42**)" — same 42 as before; no new undefined classes introduced.

### AC-9: No API/schema/migration change
**Status:** **Met**  
**Evidence:** `git diff HEAD` shows changes only to:
- `ui/views.py` (view context)
- `ui/templates/ui/_tab_observables.html` (template)
- `ui/templates/ui/_observable_cases_modal_body.html` (new partial)
- `tests/conformance/test_ui_observable_seen_in_modal.py` (new tests)
- `tests/conformance/test_ui_loop.py` (updated assertion)
- `COMPLETED.md` (documentation)
No migrations, no `urls.py` changes, no model changes, no API serializers/views added.

---

## Test Run Results

| Test Module | Command | Result |
|-------------|---------|--------|
| `test_ui_observable_seen_in_modal.py` | `.venv/bin/python -m pytest tests/conformance/test_ui_observable_seen_in_modal.py -q` | **7 passed** (2.07s) |
| `test_ui_loop.py` | `.venv/bin/python -m pytest tests/conformance/test_ui_loop.py -q` | **37 passed** (7.24s) |
| `test_ui_css_tokens.py` | `.venv/bin/python -m pytest tests/conformance/test_ui_css_tokens.py -q` | **7 passed**, 1 warning (pre-existing 42 undefined classes) |
| `make check` | `make check` | **Pass** (ruff, ruff format, mypy, django check, migrations, css-check) |

---

## Residual Risks / Verification Gaps

1. **Falsifiability of cross-org test** — Confirmed: the test asserts *absence* of org-B data in the *entire* HTML response (not just the modal). Removing the `case_visible_q` filter would cause the org-B case to appear in the modal, triggering all three isolation assertions (`href`, number, title) and the count assertion (`button_count == 2` would become 3). The test is not vacuous.

2. **N+1 elimination** — The plan claimed the new payload replaces the per-row `case_observables.count()` N+1. The template no longer uses `link.observable.case_observables.count` (removed in diff). The single query in the view (`seen_links`) is bounded by `RAIL_CAP` observables. Verified by inspection.

3. **UUID key convention** — The view keys `linked_cases_by_observable` by `cl.observable_id` (UUID object), and the template looks up with `link.observable.id` (same UUID object). The `stringformat:"s"` is used **only** for the `modal_id` concatenation (`add:`), not for the dict lookup. This avoids the filter-chain precedence trap documented in plan §11.1 deviation 1. Verified by inspection.

4. **`Case.get_status_display()` fix** — The view uses `cl.case.status.value` (plan §11.1 deviation 4). `CaseStatus` has no `choices`, so Django does not generate `get_status_display()`. The modal template renders `{{ c.status }}` which is the string value. Verified by inspection.

---

## Final Verdict

**READY TO COMMIT** — All 9 ACs met, all targeted tests pass, `make check` green, no blockers, no deviations from the approved plan (the two QA-caught defects were fixed *during* implementation and documented in the plan's §11.1 Deviations section as required).

---

**Report path:** `docs/verification/VERIFY-2026-10-20-observable-seen-in-modal.md`