# IB-2026-11-27 — Alerts API views modularization (§13.4)

**Plan:** `docs/planning/PLAN-2026-11-27-alerts-views-modularization.md`
**Status:** Implemented 2026-11-27; awaiting user-owned full `pytest` before §13.4 closeout. No subagents were used; full `pytest` was not run by the implementation agent.

## Tasks

### IB-1 — Create `alerts/views/` package
- Create `alerts/views/` with:
  - `_common.py`: `_actor`, `_bad`, `_require_superadmin_for_write`, `_not_found_alert`, `_resolve_alert_scoped`
  - `alerts.py`: `alert_detail`, `alert_raw`, `alert_list`, `_create_alert`, `_set_alert_fields`, `_update_alert`
  - `observables.py`: `alert_observable_add`
  - `merge.py`: `alert_merge`, `alert_import`
  - `bulk.py`: `_resolve_alert_for_bulk`, `alert_bulk_update`
  - `procedures.py`: `_resolve_alert_ttp`, `_parse_alert_procedure`, `alert_procedure_create`, `alert_procedures_create`
  - `statuses.py`: `_as_uuid`, `_resolve_alert_status`, `_create_alert_status`, `_update_alert_status`, `alert_status_collection`, `alert_status_detail`
  - `tags.py`: `_requested_tags`, `alert_tag_link`, `alert_tag_add`
  - `comments.py`: `alert_comment_list`
  - `__init__.py`: compatibility shim
- Delete the old `alerts/views.py` module file.
- **AC:** 30 functions exist once; bodies/decorators/docstrings are move-only; `alerts/urls.py` is unchanged.

### IB-2 — Compatibility shim
- `alerts/views/__init__.py` preserves the existing `__all__` exactly and additionally re-exports routed-only `alert_bulk_update`, `alert_procedure_create`, `alert_procedures_create`.
- Domain modules must not import `alerts.views`; `bulk.py` imports `_set_alert_fields` from `alerts.views.alerts` only.
- **AC:** `from alerts import views` resolves every routed handler; no import cycles.

### IB-3 — Guard path updates
- `tests/conformance/test_realtime_org.py`: patch `alerts.views.comments.publish_org_event`; source-scan `alerts/views/alerts.py`.
- `tests/conformance/test_wire_boundary.py`: add `"alerts/views/"` to `WIRE_BOUNDARY` and assert `is_wire_boundary("alerts/views/alerts.py")`.
- `tests/conformance/test_unknown_fields.py`: update docstring path to `alerts/views/alerts.py`.
- **AC:** no assertions weakened; no other tests edited.

### IB-4 — Verification
- Re-run structural inventories: 30 functions, 15 decorated functions (14 routed handlers), 21 route names, 11 `__all__` entries.
- Run `git diff --check`, secret scan, `make check`, and targeted suites from plan §T3.2.
- **AC:** all green; no full `pytest` run by implementation agent; user full-suite gate remains pending before §13.4 closure.

## Dependencies and constraints
- Read/use `.venv/bin/python` and `.venv/bin/pytest`.
- Do not print `.env`, secrets, raw payloads or exception text.
- No model/migration/API/UI behavior changes.
- Record deviations in the plan. Deviations: D-13.4-1 (`tags.py`), D-13.4-2 (corrected baseline counts).
- Do not commit unless explicitly requested.
