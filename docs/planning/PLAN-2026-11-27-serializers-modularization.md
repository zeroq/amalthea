# PLAN-2026-11-27 — Core serializers modularization (§13.6)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Planner/tech lead; implementation by the main agent (no subagents).
**Source:** `TODO.md` §13.6, §13 audit 2026-11-04.
**Batch note:** per user direction on 2026-11-27, §13 items are being implemented in a batch with one final user-owned full `pytest` after §13 is complete. §13.4 and §13.5 implementations are already pending that final gate.

## Summary

`core/serializers.py` is currently 777 lines (TODO says 720) of mostly independent wire renderers. This plan creates a `core/serializers/` package with domain modules and a compatibility `__init__.py`, preserving every import path, TheHive-shaped key, timestamp format and warning label. No API behavior changes.

## Goals

- G1 — Replace `core/serializers.py` with `core/serializers/` containing `_common.py`, `alerts.py`, `cases.py`, `observables.py`, `identity.py`, `taxonomy.py`, `automation.py` and `__init__.py`.
- G2 — Preserve all 28 currently imported names, plus internal helpers used by `case_json` and MCP tools.
- G3 — Keep the one-renderer-per-entity invariant: no serializer body duplicated.
- G4 — Keep the wire boundary explicit in `tests/conformance/test_wire_boundary.py`.

## Non-goals

- No schema/migration changes.
- No changes to serialized keys, `_id`/`id` duality, ISO timestamps, severity/TLP/PAP labels or field ordering.
- No changes to callers except the wire-boundary guard test.
- No full `pytest` run by the implementation agent.

## Architecture

Stack unchanged. Serializers remain pure renderers over model/API objects.

**ADR reference:** none.

### Proposed module ownership

| Module | Functions |
|---|---|
| `core/serializers/_common.py` | `_iso`, `_username` |
| `core/serializers/alerts.py` | `alert_json` |
| `core/serializers/observables.py` | `observable_json`, `observable_suggestion_json`, `rejected_observable_json` |
| `core/serializers/cases.py` | `timeline_event_json`, `task_json`, `custom_event_json`, `custom_field_json`, `custom_field_value_json`, `case_json`, `comment_json`, `page_json`, `share_json`, `attachment_json`, `case_template_json` |
| `core/serializers/identity.py` | `user_json`, `organisation_json`, `organisation_admin_json`, `user_admin_json` |
| `core/serializers/taxonomy.py` | `ttp_json`, `procedure_json`, `observable_type_json`, `_status_json`, `case_status_json`, `alert_status_json`, `tag_json` |
| `core/serializers/automation.py` | `automation_run_json`, `playbook_json` |
| `core/serializers/__init__.py` | Compatibility shim with explicit `__all__` |

**Import direction:** `_common.py` has no package imports; domain modules import only `_common` and the one cross-domain helper they need (`cases.py` imports `observable_json` and `automation_run_json`; `identity.organisation_admin_json` calls local `organisation_json`; status helpers call local `_status_json`). No domain module imports the package root.

## Data model

No model, migration, constraint or index changes.

## API

Wire representations remain byte-for-byte equivalent for the same inputs. `core.serializers` import paths must continue to resolve all 28 externally imported names:

- `_iso`, `_username`, `alert_json`, `alert_status_json`, `attachment_json`, `automation_run_json`, `case_json`, `case_status_json`, `case_template_json`, `comment_json`, `custom_event_json`, `custom_field_json`, `observable_json`, `observable_suggestion_json`, `observable_type_json`, `organisation_admin_json`, `organisation_json`, `page_json`, `playbook_json`, `procedure_json`, `rejected_observable_json`, `share_json`, `tag_json`, `task_json`, `timeline_event_json`, `ttp_json`, `user_admin_json`, `user_json`.

The shim also keeps `custom_field_value_json` and `_status_json` reachable to avoid narrowing the old module surface.

## UI / UX

No UI changes. Templates consume the same dictionaries through the same call sites.

## Phases and tasks

### Phase 1 — Baseline

**T1.1 — Inventory before move.**
- Capture function names, internal call graph and external import list.
- Baseline: 30 top-level functions, 28 external imports.
- **AC:** plan records both inventories; no caller edits beyond the guard test.

### Phase 2 — Package split

**T2.1 — Create domain modules.**
- Move function bodies verbatim into the ownership table; preserve dict key order and helper call sites.
- **AC:** all 30 functions exist exactly once under `core/serializers/`; no response shape changes.

**T2.2 — Compatibility shim.**
- Delete `core/serializers.py`; add `core/serializers/__init__.py` re-exporting all names with explicit `__all__` (including `_iso`/`_username` for MCP tools and mypy).
- **AC:** all 28 external imports and the two internal-only names resolve from `core.serializers`; no import cycles.

**T2.3 — Wire boundary guard.**
- In `tests/conformance/test_wire_boundary.py`, add `"core/serializers/"` to `WIRE_BOUNDARY` and assert `is_wire_boundary("core/serializers/cases.py")`.
- Keep the existing `"core/serializers.py"` entry for historical-path clarity.
- **AC:** wire-literal detector still passes without weakening any assertion.

### Phase 3 — Verification and closeout

**T3.1 — Static/structural.**
- AST inventory; `git diff --check`; `make check`; secret scan.
- **AC:** no function missing; no duplicates; static gates clean.

**T3.2 — Targeted suites.**
- Run affected serializer consumers: `tests/conformance/test_wire_boundary.py`, `test_query_api.py`, `test_t1_surface.py`, `test_unknown_fields.py`, `test_case_verdict_close.py`, `test_org_user_management.py`, `test_mvp_loop.py`, `test_realtime.py`, `test_ui_loop.py`, plus UI task/artifact suites as needed.
- **AC:** all selected suites pass; no full `pytest` by implementation agent.

**T3.3 — Final §13 full suite.**
- User supplies one full `pytest` log after §13 is complete; close §13.4, §13.5 and §13.6 together.
- **AC:** full-suite log green with zero failures/errors; TODO/COMPLETED updated with implementation commit hashes.

## Security

- No secrets introduced.
- Wire renderers keep current field exposure; no new fields are added to responses.
- Secret scan remains mandatory; no `--no-verify`.

## Testing

- Structural inventory and targeted suites above.
- `make check`, `git diff --check`, secret scan.
- Final full `pytest` is user-owned after §13 completion.

## Risks

- **Response-shape drift:** dict key order and labels are load-bearing; mitigated by verbatim moves and consumer suites.
- **Private helper imports:** MCP tools import `_iso`/`_username`; mitigated by explicit shim exports.
- **Wire-boundary false positive:** package path no longer matches exact `core/serializers.py`; mitigated by T2.3.
- **Import cycles:** `case_json` depends on observable/automation renderers; mitigated by one-way imports.

## Open Questions

- None. Defaults: add `_common.py` despite the TODO target list, keep seven domain modules, leave callers unchanged, and expand the wire boundary only.

## Implementation status

- Implemented 2026-11-27: `core/serializers.py` replaced by `core/serializers/` with `_common.py`, `alerts.py`, `cases.py`, `observables.py`, `identity.py`, `taxonomy.py`, `automation.py` and compatibility `__init__.py`.
- **D-13.6-1:** explicit `__all__` with 30 names was added to the shim for mypy/export clarity, including private helpers `_iso` and `_username` used by MCP tools. Additive only.
- **D-13.6-2:** `procedure_json` is owned by `taxonomy.py` (with `ttp_json`) rather than `cases.py`; its body is unchanged.
- Structural verification: all 30 original functions exist exactly once, no duplicates, no AST-body changes, and `__all__` matches the original function surface exactly.
- Wire boundary guard updated to recognize `core/serializers/`.
- `make check`, `git diff --check`, and secret scan clean.
- Targeted consumer suites: **513 passed** in 250.64s.
- Closed 2026-11-27: user-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green — **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**. Committed as `6b47c4be` (`refactor: complete section 13 modularization backlog`).
