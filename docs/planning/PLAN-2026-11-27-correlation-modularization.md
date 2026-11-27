# PLAN-2026-11-27 — Correlation modularization (§13.15)

**Status:** Implemented and verified; final user-owned full `pytest` is green.
**Owner:** Main agent; no subagents.
**Source:** `TODO.md` §13.15, §13 audit 2026-11-04.

## Summary
`cases/correlation.py` has grown from 518 to 1,215 lines since the §13 audit, driven by the auto-bundle service while the original similarity readers remained. §13.15 said to leave it unless correlation features expand; that trigger is met. Split by responsibility while preserving the `cases.correlation` import and monkeypatch surface.

## Goals
- Move shared constants and result dataclasses into small private modules.
- Move similarity readers into `similarity.py`.
- Move audited bundle writer and auto-bundle service into `bundling.py`.
- Keep every existing import and patch target working through `cases.correlation`.

## Non-goals
- No algorithm, threshold, audit or database changes.
- No changes to callers or URL/API contracts.
- No migration or model changes.

## Architecture
`cases/correlation/` with `_constants.py`, `_types.py`, `similarity.py`, `bundling.py`, and a compatibility `__init__.py`. The shim re-exports all 21 original public/private definitions plus `merge_alert_into_case`, and forwards assignments for patchable names (`MAX_CORRELATION_OBS`, `MAX_FUZZY_COMPARISONS`, `AUTO_BUNDLE_SOURCE`, `AUTO_BUNDLE_WINDOW_MAX_MINUTES`, `SIMILAR_CLOSED_ALERTS_LIMIT`, `record_alert_bundled`, `merge_alert_into_case`) to their owning modules. **ADR reference:** none.

## Data model / API / UI
No changes.

## Phases and tasks
- **T1 Baseline:** inventory 15 functions/classes and 5 constants; identify patch targets.
- **T2 Split:** move definitions verbatim by responsibility.
- **T3 Shim:** compatibility exports plus patch forwarding.
- **T4 Verification:** AST body comparison, constant parity, static gates, targeted correlation/UI/ingest/MCP suites.
- **AC:** no definition or constant missing/changed; old patch targets still affect moved code; targeted suites pass.

## Security
Auto-bundle org scoping, audit records, actor handling and rejection paths remain verbatim. No secrets introduced.

## Testing
- Structural AST comparison of functions/classes and module-level constants against `HEAD:cases/correlation.py`.
- Patch-target smoke test for `MAX_CORRELATION_OBS`, `record_alert_bundled` and `merge_alert_into_case`.
- Targeted suites: correlation, fuzzy similarity, similar alerts tab, auto-ingest bundling, UI alert/case views, MCP alerts tool, benchmark command import smoke.
- Final full `pytest` was run by the user after §13 closeout.
- **Result:** AST parity 15/15 definitions and 5/5 constants; patch-target smoke passed; targeted suites
  **177 passed in 98.27s**; consumer import smoke passed; `make check`, `git diff --check` and secret scan clean.
- **Final full `pytest`:** **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 670.27s (0:11:10)**. The
  three skips are the known Postgres-only cases; warnings are pre-existing naive-datetime and CSS-token warnings.

## Risks
- Patch-target drift is the main risk; mitigated by explicit shim forwarding and patch smoke checks.
- Cross-module import cycles; mitigated by keeping `_types.py`/`_constants.py` leaf modules.

## Deviations
- **D-13.15-1:** the compatibility module replaces its `ModuleType` class with a forwarding subclass so assignments to historical `monkeypatch.setattr("cases.correlation.*")` targets propagate to the owning submodules. A plain module-level `__setattr__` was insufficient because module objects do not honour it.

## Open Questions
- None.
