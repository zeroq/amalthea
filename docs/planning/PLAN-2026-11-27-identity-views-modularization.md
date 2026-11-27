# PLAN-2026-11-27 — Identity views modularization (§13.8)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Main agent; no subagents.
**Source:** `TODO.md` §13.8, §13 audit 2026-11-04.

## Summary
`identity/views.py` (617 lines) was split into `identity/views/` with `common.py`, `organisations.py` and `users.py` plus a compatibility shim. Superadmin guards, audit rows, error bodies and URL names are unchanged.

## Goals
- Separate shared response/resolver helpers into `common.py`.
- Move organisation endpoints to `organisations.py` and user endpoints to `users.py`.
- Keep `identity.views` exports working for `identity/urls.py` and any direct imports.

## Non-goals
- No API contract, status-code, serializer or audit change.
- No URL changes.
- No service-layer changes.

## Architecture
One-way imports: domain modules import from `common.py`; the shim imports both domain modules. **ADR reference:** none.

## Data model / API / UI
No changes. All route names and renderers stay identical.

## Phases and tasks
- **T1 Baseline:** 18 functions inventoried.
- **T2 Split:** functions moved verbatim; relative helper imports adjusted.
- **T3 Shim:** all 18 functions plus `DEFAULT_USER_ROLE` exported.
- **T4 Verification:** AST body comparison, static gates, targeted API/tenant suites.
- **AC:** no function missing or body-changed; all targeted suites pass.

## Security
Uniform 404 for non-superadmins, self-lockout and last-superadmin guards, and org scoping remain unchanged. No secrets introduced.

## Testing
- Structural AST comparison: 18/18 functions present, zero body changes.
- Targeted suites: **492 passed** plus tenant-scoping identity subset **44 passed, 59 deselected**.
- `make check`, `git diff --check`, secret scan clean.
- User-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green: **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**.

## Risks
- Guard-order drift: mitigated by verbatim moves and hardening suites.
- Import-path drift: mitigated by package shim and zero caller edits.

## Deviations
- **D-13.8-1:** shim `__all__` was expanded from 6 to 19 entries so all historical helper functions remain explicitly exported under mypy/ruff. Additive only.

## Open Questions
- None.
