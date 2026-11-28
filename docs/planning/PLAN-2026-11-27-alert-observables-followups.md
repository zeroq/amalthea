# PLAN-2026-11-27 — Alert observables §15 follow-ups

**Status:** Implemented and verified 2026-11-27; user-owned full `pytest` green. Uncommitted pending user acceptance.
**Owner:** Main agent; no subagents.
**Sources:** `TODO.md` §15, `PLAN-2026-11-06-alert-observables.md` §§8/14, `SECURITY-2026-11-06-alert-observables.md` §7.

## Summary
Close the three residual §15 items: add the deferred alert-observable read endpoint, record the provenance-storage decision, and remove raw payload values from mapping-warning text.

## Goals
- **15.7:** add `GET /api/v1/alert/{alertId}/observable` on the existing route/view with tenant scoping and the same `OutputObservable[]` shape as POST.
- **15.8:** decide whether `AlertObservable.source_path` is needed; document the rationale without a migration unless justified.
- **15.10:** ensure `FieldWarning` never exposes raw values through `ingestion_warnings["warnings"]` or structured mapping warnings.

## Non-goals
- No changes to `AlertObservable` schema or alert/case observable copying behavior.
- No change to `POST /api/v1/alert/{alertId}/observable` request body, validation or automation behavior.
- No raw payload values in new docs, tests or commit messages.

## Architecture / ADR
- Reuse `alerts/views/observables.py` and `core.serializers.observable_json`; no new app or service layer.
- **15.8 decision:** do **not** add a per-link `source_path` column. `AlertObservable` already identifies the source alert; `Alert.raw_payload` remains canonical, and paths are derived by mapping/extraction logic when needed. A column would require backfill, go stale on remap/replay, and duplicate state already reconstructible from the config and payload. Reopen only for a migration-backed provenance wave with a concrete consumer.
- **ADR reference:** ADR-008 governs webhook identity; no new ADR required.

## Data model
No migrations.

## API
- `alert/<id>/observable`: methods become `GET, POST`. GET resolves the alert with the existing scoped resolver, returns `200` with the full ordered `observable_json` list, and returns the same `404` for missing/cross-tenant alerts as POST.
- Structured mapping warning shape remains key-compatible but redacts values:
  - `value_preview` becomes `"[redacted]"`.
  - `value_fingerprint` is added as `sha256:<16 hex chars>`.
  - Free text becomes `... held [redacted] (sha256:<16>)`.

## UI
Update `_mapping_resolution.html` to show the fingerprint instead of the raw preview. No styles/tokens change.

## Phases and tasks
1. **T1 API read parity**
   - Extend `alert_observable_add` to GET; update docs/spec and auth/tenant route matrices.
   - **AC:** GET own = 200 list; GET cross-org/missing = 404; POST behavior unchanged.
2. **T2 Provenance decision**
   - Record the no-migration decision above in plan/TODO/COMPLETED.
   - **AC:** no schema change; rationale and reopen condition recorded.
3. **T3 Warning redaction**
   - Add stable non-reversible fingerprint helper to `FieldWarning`; remove raw values from `as_dict()` and `as_text()`.
   - **AC:** no known raw email/IP/string appears in structured warnings or warning text; reason and paths are preserved.
4. **T4 Verification**
   - Targeted mapping/ingest/UI/API/authz/tenant suites, `make check`, `git diff --check`, secret scan; full `pytest` user-owned.
   - **AC:** all gates green; deviations recorded.
    - **Result:** targeted suites **859 passed in 412.55s**; `make check`, `git diff --check` and secret scan clean.
    - **Full gate:** user-provided full `pytest`: **4470 passed, 3 skipped, 15 warnings, 2 subtests passed in 630.05s (0:10:30)**.

## Security
Raw payload values are treated as potentially sensitive. Fingerprints are one-way digests for operator correlation only; no reversible preview is stored. Tenant resolution is unchanged.

## Testing
- Update characterization tests for new warning text and redacted fingerprint.
- Add GET alert-observable conformance test; update authz and tenant route discovery expectations.
- Run mapping engine/remap, UI loop/markdown/settings/source mapping, T1 surface, authz, tenant routes, and unknown-fields suites.
- Final full `pytest` was user-supplied and green (4470 passed, 3 skipped, 15 warnings, 2 subtests passed).

## Risks
- Existing tests pin `value_preview` and exact warning strings; update intentionally and record as deviation.
- Tenant route discovery derives read/write status from view methods; updating allow-lists is required to keep conformance meaningful.

## Deviations
- **D-15.1:** structured mapping warnings retain the historical `value_preview` key, now with a constant redacted marker, and add `value_fingerprint`; this avoids breaking consumers while removing raw values.

## Open Questions
- None; accepted defaults from `PLAN-2026-11-06-alert-observables.md` §14 remain in force.
