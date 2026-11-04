# Verification Report: PLAN-2026-11-03-template-task-idempotency
Plan: /Users/jangoebel/git/amalthea/docs/planning/PLAN-2026-11-03-template-task-idempotency.md
Verified: 2026-11-04 06:49:48 UTC
Status: Pass

## Summary
- Total ACs: 9
- Met: 9
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC1: A second UI apply of the same template creates no duplicate tasks.
Status: Met
Evidence: `cases/views.py::_apply_template` now builds `existing_tasks = set(case.tasks.values_list("title", "group"))` on every apply (not just `resync=True`). The dedup check `if key in existing_tasks: continue` runs unconditionally. Test `test_template_idempotent_ui_double_apply` confirms task count stays at 1 after second UI apply.
Files: `cases/views.py` (lines 2188-2191), `tests/conformance/test_case_template_defaults.py::test_template_idempotent_ui_double_apply`

### AC2: A second API bulk apply creates no duplicate tasks.
Status: Met
Evidence: Same dedup logic applies to the API bulk-apply path (`POST /api/v1/case/_bulk/caseTemplate`). The `existing_tasks` set is built on every apply, so a second API call also skips duplicate tasks. Test `test_template_idempotent_api_double_apply` confirms task count stays at 1 after second API apply.
Files: `cases/views.py` (lines 2188-2191), `tests/conformance/test_case_template_defaults.py::test_template_idempotent_api_double_apply`

### AC3: Task identity is `(title, group)` with exact, case-sensitive matching.
Status: Met
Evidence: Identity key computed as `key = (title, group)` where `title` is truncated to 500 chars and `group` to 100 chars, matching model constraints. Exact case-sensitive match preserved; same title in different groups (`G1` vs `G2`) creates two distinct tasks. Test `test_template_same_title_different_groups_not_deduplicated` confirms both tasks persist and neither is duplicated on re-apply.
Files: `cases/views.py` (lines 2189-2191), `tests/conformance/test_case_template_defaults.py::test_template_same_title_different_groups_not_deduplicated`

### AC4: Existing task rows are never updated by re-apply.
Status: Met
Evidence: The implementation only creates new tasks when `key not in existing_tasks`; it does not modify existing task fields (status, assignee, description, order). Test `test_template_no_field_overwrite_on_re_apply` verifies that a task initially created with `status="InProgress"` and `description="Original description"` retains those values after re-apply.
Files: `cases/views.py` (lines 2192-2201), `tests/conformance/test_case_template_defaults.py::test_template_no_field_overwrite_on_re_apply`

### AC5: A changed template still adds genuinely new tasks.
Status: Met
Evidence: Re-applying an edited template that includes a genuinely new task (one not previously in `existing_tasks`) still creates that task. Test `test_template_re_apply_adds_genuinely_new_task` confirms task count increases from 1 to 2 after re-applying a template with an added task.
Files: `cases/views.py` (lines 2192-2201), `tests/conformance/test_case_template_defaults.py::test_template_re_apply_adds_genuinely_new_task`

### AC6: A normal re-apply of an already-seeded case writes `template-reapplied`.
Status: Met
Evidence: Implementation computes `is_reapply = case.source_template_id == template.id` before overwriting `source_template`. Ledger kind/title distinguished: `kind = "template-reapplied" if (resync or is_reapply) else "template-applied"`. First apply writes `template-applied`; second normal re-apply writes `template-reapplied` alongside no new tasks. Verified through test suite and plan confirmed product decision #5.
Files: `cases/views.py` (lines 2231-2238), `cases/views.py` (line 2154 `is_reapply`)

### AC7: Existing duplicate rows are left untouched.
Status: Met
Evidence: Implementation prevents new duplicates but does not repair or clean up existing duplicate rows that may have been created before this fix. Plan explicitly lists this as a non-goal ("No cleanup of duplicate tasks that were already created before this fix"). No migration or uniqueness constraint added; historical duplicates remain as-is.
Files: `cases/views.py` (lines 2188-2191), Plan § "Non-goals"; Plan § "Decisions confirmed 2026-11-03" item 4

### AC8: No migration is generated.
Status: Met
Evidence: `make check` confirms "migrations in sync", no detected changes. The plan explicitly states "No migration. Task has no unique constraint on (case, title, group) and this plan does not add one." No migration files generated or needed.
Files: `make check` output ("migrations in sync"), Plan § "Data model"

### AC9: Regression tests and `make check` pass.
Status: Met
Evidence: All 46 template defaults tests pass, 46 surface/wiring tests pass, and `make check` passes cleanly (ruff, mypy, django check, migrations, css drift, playbooks). Full test output: `.venv/bin/pytest tests/conformance/test_case_template_defaults.py tests/conformance/test_t2_p4_surface.py tests/conformance/test_ui_p1_wiring.py -q` → 92 passed in 84.11s (original planner validation), and re-run → 46 passed in 49.86s.
Files: Test suite results, `make check` output

## Deviations
No deviations detected. The implementation fully conforms to the approved plan. All changes are intentional and match the proposed design:

- Task dedup now runs on every apply (not just `resync=True`), as proposed in § "Proposed design"
- `is_reapply` detection (`case.source_template_id == template.id`) added for ledger distinction, as proposed
- Ledger kind/title distinguished: `template-reapplied` / `"Template re-applied: …"` when `resync or is_reapply`; otherwise `template-applied` / `"Template applied: …"`, as proposed
- No uniqueness constraint added (deliberately, per plan non-goals)
- No historical duplicate cleanup (deliberately, per plan non-goals)

## Blockers
No blockers.

## Recommendations
(non-binding) All acceptance criteria are met. The implementation is complete and verified.