# PLAN-2026-11-27 — Automation UI modularization (§13.7)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Main agent; no subagents.
**Source:** `TODO.md` §13.7, §13 audit 2026-11-04.

## Summary
`ui/views/automation.py` (688 lines) was split into `ui/views/automation/` with parser helpers and request handlers separated. No behavior, URL, auth or template changes.

## Goals
- Move `_parse_playbook_config` and `_get_script_source` into `playbook_config.py`.
- Move all automation/playbook request handlers and `_resolve_playbook_ui` into `playbooks.py`.
- Keep `ui.views.automation` imports working through `__init__.py`.

## Non-goals
- No URL changes in `ui/urls.py`.
- No permission/superadmin guard changes.
- No form field or template contract changes.

## Architecture
Stack unchanged. `playbook_config.py` has no view dependencies; `playbooks.py` imports helpers from it. The package shim re-exports 11 functions plus `logger` via explicit `__all__` for mypy compatibility.

**ADR reference:** none.

## Data model / API / UI
No changes. All route names, decorators, redirect targets, error strings and rendered contexts remain identical.

## Phases and tasks
- **T1 Baseline:** AST inventory captured 11 functions and `logger`.
- **T2 Split:** functions moved verbatim; relative imports adjusted for package depth.
- **T3 Shim:** compatibility imports and `__all__` added.
- **T4 Verification:** AST body comparison, `make check`, secret scan, targeted UI suites.
- **AC:** no function missing or body-changed; `ui/views/__init__.py` import list unchanged; all targeted suites pass.

## Security
Role checks, org scoping and uniform 404 behavior are unchanged. No secrets introduced.

## Testing
- Structural AST comparison: 11/11 functions present, zero body changes.
- `make check`, `git diff --check`, secret scan clean.
- Targeted suites: **203 passed, 2 subtests passed** in 188.64s.
- User-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green: **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**.

## Risks
- Patch-path drift: mitigated by keeping the package-level shim and unchanged `ui.views.__init__` imports.
- Import-depth mistakes: mitigated by `make check` and targeted route suites.

## Deviations
- **D-13.7-1:** explicit `__all__` with 12 names (11 functions plus `logger`) added to the shim for mypy/export clarity. Additive only.

## Open Questions
- None.
