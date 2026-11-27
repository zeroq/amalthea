# PLAN-2026-11-27 — Tenant-scoping test modularization (§13.12)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Main agent; no subagents.
**Source:** `TODO.md` §13.12, §13 audit 2026-11-04.

## Summary
`tests/conformance/test_tenant_scoping.py` (3,702 lines) was split into `tests/conformance/test_tenant_scoping/` with shared factories/route helpers and four resource-oriented test modules. No production behaviour changed; all 103 collected tests remain.

## Goals
- Move shared factories and route-enumeration helpers into `helpers.py`.
- Split core truth-table/webhook tests, route-enumeration tests, write/sharing regression tests and UI tests into sibling modules.
- Preserve every test class/function name and body.

## Non-goals
- No production code changes.
- No new tenant-scoping assertions or route allow-list changes.
- No rewrite of historical review/planning documents that cite the old path.

## Architecture
`helpers.py` owns `_mk_*` factories, global/write-only route allow-lists, route discovery helpers and `_build_url`. `test_core.py`, `test_routes.py`, `test_regressions.py` and `test_ui.py` import only what they use. **ADR reference:** none.

## Data model / API / UI
No changes.

## Phases and tasks
- **T1 Baseline:** 24 top-level classes/functions inventoried.
- **T2 Split:** helpers extracted; test classes/functions moved verbatim by resource.
- **T3 Cleanup:** stale current-docstring path reference updated; old module removed.
- **T4 Verification:** AST body comparison, collection parity, static gates, full package run.
- **AC:** no class/function missing or body-changed; 103 tests collected and pass.

## Security
Tenant truth-table, route-enumeration, superAdmin-audit and cross-org write assertions are unchanged. No secrets introduced.

## Testing
- Structural AST comparison: 24/24 classes/functions present, zero body changes.
- Collection parity: **103 tests collected**.
- Full package run: **103 passed in 187.90s**.
- `make check`, `git diff --check`, secret scan clean.
- User-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green: **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**.

## Risks
- Import-shadowing drift during split: mitigated by AST comparison and `make check`.
- Historical path references: intentionally left in dated review/verification documents; current TODO/docstring reference updated.

## Deviations
- **D-13.12-1:** `test_core.py` keeps the original local migration import pair with `# noqa: F811`; the top-level `Organisation` compat import carries `# noqa: F401`. No runtime change.
- **D-13.12-2:** no compatibility module remains at the old `tests/conformance/test_tenant_scoping.py` path; no code imports it, and the one current docstring reference was updated.

## Open Questions
- None.
