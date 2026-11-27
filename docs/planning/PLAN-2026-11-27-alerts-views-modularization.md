# PLAN-2026-11-27 — Alerts API views modularization (real §13.4)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Planner/tech lead; implementation by the main agent (no subagents).
**Source:** `TODO.md` §13.4, §13 audit 2026-11-04.
**Prior numbering note:** the ledger-package split was approved as “§13.4” by mistake and is recorded separately. This plan is for the real, still-open §13.4: splitting `alerts/views.py`.

## Summary

`alerts/views.py` is currently 893 lines (the TODO still says 782) and mixes alert CRUD, observables, merge/import, bulk updates, procedures, statuses, tags and comments. This plan moves those handlers into an `alerts/views/` package with domain modules, leaves `alerts/views.py` as a compatibility shim, and preserves the API surface byte-for-behavior: URL names, URL order, tenant/auth checks, status codes, request parsing and response bodies.

No schema, endpoint, UI or product behavior changes are in scope.

## Goals

- G1 — Replace `alerts/views.py` with an `alerts/views/` package that mirrors the `cases/views/` layout.
- G2 — Preserve every routed handler and every existing import via compatibility re-exports.
- G3 — Preserve `alerts/urls.py` route names, order and handler identities.
- G4 — Keep all auth, org-scoping, superadmin and transaction behavior unchanged.
- G5 — Update only the guard tests whose patch/source paths necessarily move with the code.
- G6 — Record deviations in this plan and close §13.4 only after green verification.

## Non-goals

- No new API endpoints, serializers, filters, permissions or database migrations.
- No changes to alert ingestion, escalation, observables extraction or automation logic.
- No UI/template/Tailwind/HTMX work.
- No reformatting or behavior “cleanups” beyond what the move requires.
- No full `pytest` run by the implementation agent; the user owns that gate. Targeted suites only.

## Architecture

Stack and design remain exactly as `AGENTS.md` specifies: Django 5.x + DRF, PostgreSQL, Redis/Celery/Channels, server-rendered templates + HTMX. This is a presentation-layer refactor only.

**ADR reference:** none. No architectural decision changes; no new ADR is needed.

### Proposed module ownership

| Module | Functions / constants |
|---|---|
| `alerts/views/_common.py` | `_actor`, `_bad`, `_require_superadmin_for_write`, `_not_found_alert`, `_resolve_alert_scoped` |
| `alerts/views/alerts.py` | `alert_detail`, `alert_raw`, `alert_list`, `_create_alert`, `_set_alert_fields`, `_update_alert` |
| `alerts/views/observables.py` | `alert_observable_add` |
| `alerts/views/merge.py` | `alert_merge`, `alert_import` |
| `alerts/views/bulk.py` | `_resolve_alert_for_bulk`, `alert_bulk_update`; imports `_set_alert_fields` from `alerts/views/alerts.py` |
| `alerts/views/procedures.py` | `_resolve_alert_ttp`, `_parse_alert_procedure`, `alert_procedure_create`, `alert_procedures_create` |
| `alerts/views/statuses.py` | `_as_uuid`, `_resolve_alert_status`, `_create_alert_status`, `_update_alert_status`, `alert_status_collection`, `alert_status_detail` |
| `alerts/views/tags.py` | `_requested_tags`, `alert_tag_link`, `alert_tag_add` |
| `alerts/views/comments.py` | `alert_comment_list` |
| `alerts/views/__init__.py` | Compatibility shim: unchanged `__all__` plus routed-only `alert_bulk_update`, `alert_procedure_create`, `alert_procedures_create` |

**D-13.4-1 (proposed deviation):** the TODO target list omits a tags module, but the file contains two tag handlers and `_requested_tags`. Add `alerts/views/tags.py` to mirror `cases/views/tags.py` instead of putting tag logic in `alerts.py`.

**D-13.4-2 (correction recorded during verification):** the initial hand-count said 29 functions / 14 decorated functions. The AST baseline is 30 functions / 15 decorated functions; the extra decorated function is the private `_update_alert` (`transaction.atomic`). The 14 routed API handlers and 21 route names were correct.

**D-13.4-3 (implementation detail):** the compatibility shim uses redundant aliases for `alert_bulk_update`, `alert_procedure_create` and `alert_procedures_create` so mypy sees them as explicit exports while the legacy `__all__` stays exactly as before.

**Import-cycle constraint:** domain modules must not import `alerts.views` (the package root). `bulk.py` imports `_set_alert_fields` from `alerts.views.alerts`; `alerts.py` must not import `bulk.py`.

## Data model

No model, migration, constraint or index changes.

## API contracts

`alerts/urls.py` remains byte-identical. It currently exposes 14 routed handlers through 21 route names:

- Handlers: `alert_list`, `alert_raw`, `alert_import`, `alert_observable_add`, `alert_merge`, `alert_tag_link`, `alert_tag_add`, `alert_comment_list`, `alert_procedure_create`, `alert_procedures_create`, `alert_bulk_update`, `alert_detail`, `alert_status_collection`, `alert_status_detail`.
- Route names: `alert-list`, `alert-list-slash`, `alert-raw`, `alert-import`, `alert-observable-add`, `alert-merge`, `alert-import-into`, `alert-tag-link`, `alert-tag-link-slash`, `alert-tag-add`, `alert-comment-list`, `alert-comment-list-slash`, `alert-procedure-create`, `alert-procedures-create`, `alert-bulk-update`, `alert-bulk-update-slash`, `alert-detail`, `alert-status-collection`, `alert-status-collection-slash`, `alert-status-detail`, `alert-status-detail-slash`.

The compatibility `__all__` remains exactly:
`alert_comment_list`, `alert_detail`, `alert_import`, `alert_list`, `alert_merge`, `alert_observable_add`, `alert_raw`, `alert_status_collection`, `alert_status_detail`, `alert_tag_add`, `alert_tag_link`.

## UI / UX

No UI changes. Dark theme, keyboard shortcuts, modals and accessible icon rules are unaffected.

## Phases and tasks

### Phase 1 — Baseline and plan acceptance

**T1.1 — Record the move-only baseline.**
- Capture current function list, decorated handlers, `__all__`, route names/order and guard-test references.
- Current baseline: 30 functions, 15 decorated functions (14 routed handlers), 21 route names, 11 `__all__` entries.
- **AC:** plan records all four inventories; no implementation begins before user approval.

### Phase 2 — Create the package

**T2.1 — Add `alerts/views/` domain modules.**
- Move function bodies verbatim into the ownership table above.
- Preserve decorators, helper order within each module and all docstrings containing wire-shape notes.
- Keep `alerts/urls.py` unchanged.
- **AC:** every one of the 30 functions exists exactly once under `alerts/views/`; no function bodies are rewritten.

**T2.2 — Replace `alerts/views.py` with the compatibility package shim.**
- Delete the old module file and add `alerts/views/__init__.py` re-exporting the unchanged `__all__` plus the three routed-only handlers.
- **AC:** `from alerts import views` still resolves every routed handler; `views.__all__` is unchanged; `alerts.urls` is byte-identical to the pre-change file.

**T2.3 — Update necessary guard tests.**
- `tests/conformance/test_realtime_org.py`:
  - Change patch target `alerts.views.publish_org_event` → `alerts.views.comments.publish_org_event`.
  - Change source-scan path `alerts/views.py` → `alerts/views/alerts.py`.
- `tests/conformance/test_wire_boundary.py`:
  - Add `"alerts/views/"` to `WIRE_BOUNDARY`.
  - Add `assert is_wire_boundary("alerts/views/alerts.py")`.
- `tests/conformance/test_unknown_fields.py`: update the docstring reference from `alerts/views.py` to `alerts/views/alerts.py`.
- **AC:** no other test is edited; each change is a path/target update caused by the move, not an assertion weakening.

### Phase 3 — Verification and closeout

**T3.1 — Structural verification.**
- Re-run the AST inventory and compare against T1.1.
- Confirm `git diff --check`, `make check` and secret scans are clean.
- **AC:** 30 functions, 15 decorated functions (14 routed handlers), 21 route names and 11 `__all__` entries are unchanged; `alerts/urls.py` has no diff; no forbidden wire literals outside the expanded boundary.

**T3.2 — Targeted test verification.**
- Run focused suites, for example:
  - `tests/conformance/test_t1_surface.py`
  - `tests/conformance/test_t2_p1_surface.py`
  - `tests/conformance/test_t2_p2_surface.py`
  - `tests/conformance/test_t2_p4_surface.py`
  - `tests/conformance/test_t2_p5_surface.py`
  - `tests/conformance/test_alert_tag_add_regressions.py`
  - `tests/conformance/test_realtime_org.py`
  - `tests/conformance/test_wire_boundary.py`
  - `tests/conformance/test_unknown_fields.py`
  - `tests/conformance/test_tenant_scoping.py`
  - `tests/conformance/test_ui_trailing_slash.py`
- **AC:** all selected suites pass; no full `pytest` is run by the implementation agent.

**T3.3 — User-owned full suite and closeout.**
- User supplies/confirms the full `pytest` result after implementation.
- Update `TODO.md` §13.4 to complete, add a `COMPLETED.md` entry, and record the implementation commit hash.
- **AC:** full-suite log is green with zero failures/errors; §13.4 is closed only after that evidence exists.

## Security

- No new secrets, tokens or credentials.
- Auth, tenant scoping and superadmin checks are moved verbatim; no permission branch is simplified or removed.
- Transaction boundaries (`transaction.atomic`, `transaction.on_commit`) remain attached to the same handlers.
- Realtime publisher calls remain after-commit and retain the same payload keys.
- Secret scan remains mandatory on staged/tracked changes; no `--no-verify`.

## Testing

- Structural AST inventory before and after.
- Focused conformance suites listed in T3.2.
- `make check` and `git diff --check`.
- Full `pytest` remains a user-owned gate.
- Do not print `.env`, secret values, raw payloads or exception text.

## Risks

- **Patch-target drift:** `publish_org_event` patch in `test_realtime_org.py` targets the old module. Mitigated by T2.3.
- **Wire-boundary false positive:** moving handlers into `alerts/views/` loses the `*/views.py` pattern match. Mitigated by the explicit package path in `WIRE_BOUNDARY`.
- **Circular imports:** `bulk.py` needs `_set_alert_fields`. Mitigated by one-way import from `bulk.py` → `alerts.py`.
- **URL-order regression:** `alert/_bulk` and tag/comment/procedure routes must remain above the bare detail route. Mitigated by not editing `alerts/urls.py` and asserting no diff.
- **Stale TODO count:** TODO says 782 lines; current file is 893. Mitigated by recording the current count in T1.1 and using function/route inventories, not line count, as the invariant.

## Open Questions

- None. Proceed with the defaults above unless the user redirects: add `tags.py`, change the two realtime-org patch/source paths, expand the wire boundary, and leave `alerts/urls.py` untouched.

## Implementation status

- Implemented 2026-11-27: `alerts/views.py` replaced by the `alerts/views/` package; guard paths/targets updated.
- Structural verification: 30 functions, 15 decorated functions (14 routed handlers), 21 route names, 11 `__all__` entries; no duplicates; `alerts/urls.py` unchanged.
- `make check`, `git diff --check`, and `scripts/secret-scan.sh --files` clean.
- Targeted conformance suites from T3.2: **292 passed** in 281.49s.
- Closed 2026-11-27: user-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green — **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**. Commit hash pending because changes are not yet committed.
