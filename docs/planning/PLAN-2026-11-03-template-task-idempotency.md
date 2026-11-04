# PLAN-2026-11-03 — Idempotent task materialization when a template is re-applied

## Summary

Applying or re-applying a case template currently copies the template's tasks unconditionally
unless the internal `resync=True` path is used. The normal UI apply path and the API bulk-apply
path both pass the default `resync=False`, so applying the same template twice creates duplicate
tasks. The template form already promises that re-applying "adds tasks that are missing" and that
"existing tasks ... are not duplicated"; the implementation does not match that promise.

This plan makes task materialization idempotent on every apply path. Tags and custom-field values
are already idempotent (`get_or_create`); tasks are the remaining gap.

## Goals

- Applying a template twice must not create a second task with the same identity.
- The same rule must hold for the UI "Apply template" path, the API
  `POST /api/v1/case/_bulk/caseTemplate` path, and the template-edit "Update & Apply to Cases"
  resync path.
- Existing tasks are left untouched: no status, assignee, description, order, or flag overwrite.
- Re-applying a changed template can still add genuinely new tasks.

## Non-goals

- No schema change, migration, or task uniqueness constraint.
- No cleanup of duplicate tasks that were already created before this fix.
- No update/upsert of existing task fields.
- No change to how tags or custom-field values are materialized.
- No change to the template task JSON contract.

## Root cause

In `cases/views.py::_apply_template`:

```python
existing_tasks: set[tuple[str, str]] = set()
if resync:
    existing_tasks = set(case.tasks.values_list("title", "group"))
...
if resync and key in existing_tasks:
    continue
```

The duplicate check exists only on the `resync=True` path. The UI apply view and the API bulk
apply view call `_apply_template` without `resync`, so every application creates fresh task rows.
The template-edit path uses `resync=True` and is already idempotent.

## Proposed design

Build the existing-task identity set on **every** apply, not only on resync:

```python
existing_tasks = set(case.tasks.values_list("title", "group"))
...
key = (title, group)
if key in existing_tasks:
    continue
Task.objects.create(...)
existing_tasks.add(key)
```

`resync` remains a force-reapply flag for the template-edit path. Additionally, a normal apply is
recorded as a re-apply when `case.source_template_id == template.id` before the provenance field is
updated. That makes the ledger distinguish `template-applied` from `template-reapplied` even when
the caller did not pass `resync=True`.

### Task identity

Confirmed identity is the existing resync key: `(title, group)`, both exact and case-sensitive.

- `title` is truncated to 500 chars and `group` to 100 chars, matching the model and the create
  path.
- Same title in two different groups remains two distinct tasks. This preserves the current
  documented resync semantics and the existing test that deliberately uses the same title in `G1`
  and `G2`.
- A task created manually in the UI has `group=""`, so it only matches a template task whose group
  is also empty. This is accepted; the UI does not expose `group` today.
- A case with duplicate existing rows for the same key is not repaired; the check only prevents
  new duplicates.

## API / UI behaviour

- `POST /cases/<number>/apply-template` — second application adds zero duplicate tasks.
- `POST /api/v1/case/_bulk/caseTemplate` — second application adds zero duplicate tasks.
- Template edit → "Update & Apply to Cases" — unchanged, remains idempotent.
- Existing task values are never overwritten; a re-apply is additive only for missing identities.
- A normal UI/API re-apply of an already-seeded case records `template-reapplied`; the first apply
  records `template-applied`.

## Data model

No migration. `Task` has no unique constraint on `(case, title, group)` and this plan does not add
one; a constraint would reject legitimate same-title tasks and would require resolving historical
duplicates.

## Testing

Add regression coverage in `tests/conformance/test_case_template_defaults.py` (or the template
surface test file):

1. **UI double apply:** apply the same template twice; task count remains one for a single-task
   template.
2. **API double apply:** call `POST /api/v1/case/_bulk/caseTemplate` twice; task count remains one.
3. **Same title, different groups:** both are created on first apply and neither is duplicated on
   the second.
4. **Manual pre-existing task:** a task already on the case with the same `(title, group)` is
   skipped; a same-title task in another group is added.
5. **No overwrite:** existing task status/assignee/description remains unchanged after re-apply.
6. **Resync path:** existing resync test still passes.
7. **Positive addition:** changing a template to add a new task still adds it on re-apply.
8. **Ledger wording:** first apply writes one `template-applied` event; a second apply writes one
   `template-reapplied` event and no additional task.

Run at least:

```bash
.venv/bin/pytest \
  tests/conformance/test_case_template_defaults.py \
  tests/conformance/test_t2_p4_surface.py \
  tests/conformance/test_ui_p1_wiring.py -q
```

and `make check`.

## Acceptance criteria

- [x] A second UI apply of the same template creates no duplicate tasks.
- [x] A second API bulk apply creates no duplicate tasks.
- [x] Task identity is `(title, group)` with exact, case-sensitive matching.
- [x] Existing task rows are never updated by re-apply.
- [x] A changed template still adds genuinely new tasks.
- [x] A normal re-apply of an already-seeded case writes `template-reapplied`.
- [x] Existing duplicate rows are left untouched.
- [x] No migration is generated.
- [x] Regression tests and `make check` pass.

## Decisions confirmed 2026-11-03

1. **Identity:** exact `(title, group)`.
2. **Matching:** exact and case-sensitive.
3. **Existing task content:** leave untouched.
4. **Historical duplicates:** leave them; no cleanup.
5. **Ledger wording:** record `template-reapplied` for a normal re-apply of an already-seeded case.

## Implementation verification (2026-11-03)

**Changes made:**

- `cases/views.py::_apply_template`: Task dedup now runs on **every** apply (not just `resync=True`).
  - Captures `is_reapply = case.source_template_id == template.id` before overwriting `source_template`.
  - Builds `existing_tasks = set(case.tasks.values_list("title", "group"))` on every apply.
  - For each template task, computes `key = (title[:500], group[:100])`; skips if `key in existing_tasks`; otherwise creates the task and adds the key to the set.
  - Ledger kind/title distinguished: `template-reapplied` / `"Template re-applied: …"` when `resync or is_reapply`; otherwise `template-applied` / `"Template applied: …"`.
  - Docstring updated to reflect that tasks are deduplicated across all apply paths.

**Tests added:** 6 new regression tests in `tests/conformance/test_case_template_defaults.py`; the full file has 46 tests.

| # | Test | AC covered |
|---|------|-----------|
| 1 | `test_template_idempotent_ui_double_apply` | UI double-apply: task count stays 1; `template-applied` then `template-reapplied` |
| 2 | `test_template_idempotent_api_double_apply` | API double-apply: task count stays 1 |
| 3 | `test_template_same_title_different_groups_not_deduplicated` | Same title in different groups: both created first time, neither duplicated |
| 4 | `test_template_manual_task_skipped_same_title_different_group` | Manual task with same (title, group) skipped; same title in another group added |
| 5 | `test_template_no_field_overwrite_on_re_apply` | Existing task fields (status/description) not overwritten |
| 6 | `test_template_re_apply_adds_genuinely_new_task` | Edited template adding new task still adds it on re-apply |
| 7 | `test_template_resync_path_still_works` | *(original resync test still passes, verified via existing `test_edit_apply_to_existing_resyncs_seeded_cases_additively`)* |

**Verification commands:**

```bash
.venv/bin/pytest tests/conformance/test_case_template_defaults.py -q
.venv/bin/pytest tests/conformance/test_t2_p4_surface.py tests/conformance/test_ui_p1_wiring.py -q
make check
```

All 46 template defaults tests pass, 46 surface/wiring tests pass, and `make check` passes cleanly.
