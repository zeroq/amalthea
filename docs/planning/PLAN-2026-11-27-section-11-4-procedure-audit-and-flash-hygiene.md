# PLAN-2026-11-27 — §11.4 Procedure audit and flash-message hygiene

**Status:** Implemented and verified by targeted suites; full `pytest` remains user-owned. Uncommitted.
**Sources:** `TODO.md` §11.4 (`L1`, `I1`), `REVIEW-2026-10-12-ui-completeness-p3.md`.
**Owner:** Main agent; no subagents.

## Summary
Close the two remaining P3 review follow-ups: make case-procedure attachment auditable and visible in the case ledger, and stop UI flash messages from reflecting internal exception text.

## Goals
- **L1:** every case-procedure attachment (single or bulk, UI or API) writes one `AuditLog` row and one case `TimelineEvent` entry through the ledger choke point.
- **I1:** no UI flash message contains raw `str(exc)` / `ValidationError` text from a caught exception; details stay in logs and the analyst sees a stable, safe message.
- Keep audit/ledger details non-secret: identifiers and ATT&CK metadata only, never the procedure's free-text description.

## Non-goals
- No schema migration, no new endpoint, no change to procedure request/response contracts.
- No audit/ledger changes for alert-scoped procedures (they have no parent case ledger); only case-scoped procedures.
- No changes to custom domain-exception messages that are intentionally user-facing (e.g. `PermissionDenied("Cross-org alert bundle refused")`, `CaseUpdateError.message`).
- No raw payloads, secrets, or exception text in docs/tests/commit messages.

## Architecture
- Add one shared case-procedure attach service in `cases/procedures.py` that wraps `create_procedure`, writes the audit row via `identity.audit.record_procedure_change`, and appends the ledger event via `cases.ledger.append_timeline_event`.
- UI and API case-procedure create paths call the shared service; alert-procedure paths keep `create_procedure`.
- Ledger writes use `append_timeline_event` only, preserving the realtime choke-point guard in `tests/conformance/test_realtime.py`.

## Data model
No migrations. `AuditLog.details` carries `{case_id, pattern_id, pattern_name, tactic, ttp_id}`; `TimelineEvent.metadata` carries `{procedure_id, pattern_id, pattern_name, tactic, ttp_id}`.

## API
No contract change. `POST /api/v1/case/{id}/procedure` and `POST /api/v1/case/{id}/procedures` keep their existing 201 bodies; the side effects are new.

## UI
No markup change. Flash messages for bulk case/task/alert updates and autonomy-policy validation become generic; exception details go to `logger.exception`.

## Phases and tasks
1. **T1 L1**
   - Add `record_procedure_change` to `identity/audit.py` and `attach_case_procedure` to `cases/procedures.py`; update UI and API call sites.
   - **AC:** single/bulk UI and single/bulk API creates each produce matching audit rows and ledger events; no direct `TimelineEvent.objects.create`.
2. **T2 I1**
   - Replace raw exception echo in `case_list.py`, `tasks.py`, `alerts.py`, `autonomy.py`; keep exception details in logs.
   - **AC:** injected `RuntimeError("SECRET_SQL_DETAIL")` and `ValidationError` never appear in response messages.
3. **T3 Verification**
   - Add conformance tests; run targeted suites, `make check`, `git diff --check`, secret scan; full `pytest` user-owned.
   - **AC:** all gates green; deviations recorded.
   - **Result:** 331 targeted tests passed in 482.71s; a further 97 autonomy/bulk-related tests passed in 87.49s; `make check` and static gates green.

## Security
Audit details must not include free-text procedure descriptions or request bodies. Flash-message changes reduce information disclosure through error text. Tenant resolution remains unchanged.

## Testing
- Extend `tests/conformance/test_ui_procedures.py` for single/bulk UI audit + ledger.
- Extend `tests/conformance/test_t2_p5_surface.py` for API audit + ledger parity.
- Add focused flash-hygiene tests with monkeypatched model/service failures.

## Risks
- Adding audit rows to API paths may affect tests that assume a clean audit table; update intentionally.
- `AuditLog.details` is JSONB and additive; keep values bounded and non-secret.
- Reusing a shared service changes side effects only, not response bodies; assert both.

## Deviations
None yet.

## Open Questions
- None; accepted defaults: audit all case-procedure creates, ledger one entry per created procedure, generic flash text for internal errors.
