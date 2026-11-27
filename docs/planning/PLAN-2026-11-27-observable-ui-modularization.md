# PLAN-2026-11-27 — Observable UI modularization (§13.9)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Main agent; no subagents.
**Source:** `TODO.md` §13.9, §13 audit 2026-11-04.

## Summary
`ui/views/observables.py` (726 lines) was split into `ui/views/observables/` with global CRUD/bulk/extract views and case-link actions separated. Cross-case guards, `dataType` form contract and ledger events are unchanged.

## Goals
- Move list/detail/edit/delete/bulk/extract and the edit-form renderer into `observables.py`.
- Move `case_observable_create/update/remove` into `case_observables.py`.
- Preserve all imports from `ui.views.observables` through a package shim.

## Non-goals
- No URL changes in `ui/urls.py`.
- No cross-case guard or audit behavior changes.
- No template/form-field changes.

## Architecture
Two sibling modules with no imports between them; both import shared helpers from `..common`. The shim re-exports 11 functions plus `logger` and `_observable_bulk_redirect` via explicit `__all__`. **ADR reference:** none.

## Data model / API / UI
No changes. All status codes, messages, redirect targets and context dictionaries are unchanged.

## Phases and tasks
- **T1 Baseline:** 11 functions and `logger` inventoried.
- **T2 Split:** functions moved verbatim into CRUD vs case-link ownership.
- **T3 Shim:** compatibility exports added.
- **T4 Verification:** AST body comparison, static gates, targeted UI and tenant suites.
- **AC:** no function missing or body-changed; all targeted suites pass.

## Security
Cross-case force guards, owner-org-only case edits, uniform 404s and delete audit rows remain unchanged. No secrets introduced.

## Testing
- Structural AST comparison: 11/11 functions present, zero body changes.
- Targeted suites: **281 passed** plus tenant observable subset **7 passed, 96 deselected**.
- `make check`, `git diff --check`, secret scan clean.
- User-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green: **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**.

## Risks
- Case-link guard drift: mitigated by verbatim moves and tenant/UI suites.
- Import-depth mistakes: mitigated by static gates.

## Deviations
- **D-13.9-1:** explicit `__all__` with 12 names added to the shim for mypy/ruff export clarity. Additive only.

## Open Questions
- None.
