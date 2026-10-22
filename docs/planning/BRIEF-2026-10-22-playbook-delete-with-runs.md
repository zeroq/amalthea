# Brief — Delete playbooks with run history

**Date:** 2026-10-22
**Status:** Approved (user chose option 1: delete the runs with the playbook)
**Owner:** planner → implementation

## Goal

Make the playbook trash affordance honest for playbooks that have runs:

- show the trash icon on every list row for mutating roles (not only `runs.count == 0`);
- keep the `Delete playbook` button on the detail page;
- warn in the confirmation modal when runs exist;
- when confirmed, permanently delete the playbook **and its run history** in one transaction.

This is a deliberate policy: the Automation run ledger is deleted with the playbook. Case timeline
entries are not FK-linked to runs and survive; their `metadata.runId` becomes historical only.

## Non-goals

- Changing `AutomationRun.playbook` away from `PROTECT`. Direct `playbook.delete()` remains
  protected; the explicit service removes the runs first.
- Preserving `AutomationRun` rows after playbook deletion (the alternative, `SET_NULL`).
- Changing the delete role gate. UI/API stay analyst/admin/superAdmin; viewer remains denied.
- Deleting case/alert/observable data or timeline entries.
- Any migration.

## Design

### Backend

New `automation/services.py`:

- `ACTIVE_RUN_STATUSES` lives in `automation/models.py` as `("Pending", "Running")` so the model,
  service, list annotation and detail view share one definition.
- `PlaybookDeleteBlockedError(active_runs: int, message: str | None = None)`.
- `delete_playbook(playbook) -> int`:
  - `@transaction.atomic`;
  - re-selects the playbook with `select_for_update()` so a concurrent dispatch cannot insert a
    run between the check and the delete;
  - refuses if any run is `Pending`/`Running` (a worker may still write to it);
  - counts all runs, deletes them, deletes the playbook, returns the run count;
  - catches `ProtectedError` as a defensive backstop and raises the blocked error (the atomic
    block rolls the partial delete back).

### API (`automation/views.py`)

- `DELETE /api/v1/playbook/{idOrName}` calls `delete_playbook()`.
- Active runs continue to return **400** (existing contract) with a clear `_id` detail.
- A terminal-run-only playbook returns **204** and its runs are gone.
- Docstring/comment updated; `PROTECT` remains the schema backstop.

### UI (`ui/views.py`)

- `playbook_delete` calls `delete_playbook()`:
  - blocked → error message, redirect to detail;
  - success → success message with removed run count, redirect to list.
- `playbook_list` annotates `run_count` and `active_run_count` (filtered `Count`) so the modal
  has truthful counts without N+1 queries.
- `playbook_detail` passes `run_count` and `active_run_count` (global counts, not just the
  visible-cases run table) to the template.

### Templates

- New `ui/templates/ui/_playbook_delete_action.html`: modal + trigger, parameterised by
  `pb`, `run_count`, `active_run_count`, `icon_only`.
- New `ui/templates/ui/_playbook_delete_body.html`: three bodies:
  - active runs: deletion blocked until they finish; suggest deactivate;
  - past runs: "N past runs … indicates it is in use"; runs and history permanently removed;
    case timeline entries kept;
  - no runs: standard "cannot be undone".
- List: render the partial for every row inside `{% if can_mutate %}`; remove the
  `runs.count == 0` gate. Use `pb.run_count` for the Runs column.
- Detail: keep the `Delete playbook` button inside `{% if can_mutate %}`, replace the hard-coded
  modal with the partial. The active-run case uses `confirm_disabled=True`.
- Keep every template tag on one line (multiline-tag guard).

## Acceptance criteria

- **AC1** — List shows the trash icon for a playbook with runs when the user can mutate.
- **AC2** — List/detail modal shows the run count and warns that run history is deleted; the
  no-run copy remains.
- **AC3** — A playbook with only terminal (`Success`/`Failed`) runs is deleted via UI/API, and
  every `AutomationRun` for it is deleted in the same transaction.
- **AC4** — A playbook with any `Pending`/`Running` run is refused via UI/API; playbook and all
  runs remain; list/detail modal disables confirmation for that playbook.
- **AC5** — `AutomationRun.playbook` stays `PROTECT`; direct `playbook.delete()` still refuses.
- **AC6** — Viewer sees no playbook delete trigger or modal; server-side 404/403 behaviour is
  unchanged.
- **AC7** — Targeted tests pass, `make check` is green, and the automation spec documents the
  delete semantics.

## Files

- `automation/models.py` (constant, comment)
- `automation/services.py` (new)
- `automation/views.py`
- `ui/views.py`
- `ui/templates/ui/playbook_list.html`
- `ui/templates/ui/playbook_detail.html`
- `ui/templates/ui/_playbook_delete_action.html` (new)
- `ui/templates/ui/_playbook_delete_body.html` (new)
- `tests/conformance/test_playbook_authoring.py`
- `tests/conformance/test_playbook_delete_ui.py` (new)
- `tests/conformance/test_viewer_affordances.py` (extend if needed)
- `docs/spec/automation.md`

## Verification

```
.venv/bin/pytest tests/conformance/test_playbook_authoring.py \
  tests/conformance/test_playbook_delete_ui.py \
  tests/conformance/test_viewer_affordances.py \
  tests/conformance/test_ui_inline_assets.py -q
make check
```

## Risks / notes

- Deleting runs removes the Automation run ledger and frees their `idempotency_key`s. Case
  timeline entries survive but point at deleted `runId`s (metadata only).
- `select_for_update()` is a no-op on SQLite; the `ProtectedError` backstop covers the test
  backend. Postgres serialises against new run inserts.
- A stale `Pending` run blocks deletion indefinitely; the UI tells the user to deactivate or wait.
