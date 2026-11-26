# Security review — auto ingest-time alert bundling (12.3-fu1)

**Date:** 2026-11-26
**Reviewer:** project lead (manual). The `security-auditor` agent was unavailable in this session; this report is not claimed as an independent agent review. Recorded as W63-17 in `docs/spec/deviations.md`.
**Scope:** plan S1-S10 in `docs/planning/PLAN-2026-11-26-auto-ingest-bundling.md`.

## Decision

**No High or Medium findings remain.** The implementation is acceptable for a default-disabled feature, subject to the independent Postgres/verification gates recorded in `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling.md`.

## Scope reviewed

- `cases/correlation.py` — decision/find/service/write path.
- `ingest/views.py` — webhook call site, response and warnings.
- `cases/models.py`, `cases/migrations/0029_correlation_rule.py` — per-org rule.
- `ui/views/correlation.py`, `ui/templates/ui/settings_correlation.html`, source templates — configuration authorization and help text.
- `tests/conformance/test_auto_ingest_bundling.py`, `tests/conformance/test_auto_ingest_bundling_settings.py` — tenant, refusal, replay and realtime assertions.
- `cases/management/commands/benchmark_auto_bundle_candidates.py` — output path and data handling.

## Findings

| Severity | Finding | Assessment |
|---|---|---|
| High | Cross-tenant auto-bundle | **None found.** `ingest/views.py:270-275` passes `token.organisation_id`, never a payload org. `cases/correlation.py:1016-1022` refuses alert/org mismatch; candidate seeks filter `owner_org_id=rule.organisation_id` and `case__owner_org_id=rule.organisation_id` (`cases/correlation.py:868`, `873`, `936`); the locked re-check repeats the org assertion at `cases/correlation.py:1108`. Shared cases are not selected: the target must be owned by the token org, not merely visible. |
| High | Closed/cross-org write after race | **None found.** Query filters `case__closed_date__isnull=True` and status stage `New/InProgress` (`cases/correlation.py:874-875`, `938-939`). Inside `transaction.atomic()`, the service re-fetches both rows with `select_for_update()` and repeats closed/org/linked checks (`cases/correlation.py:1096-1138`). `record_alert_bundled()` independently refuses closed and cross-org cases (`cases/correlation.py:549`, `553`). Rollback tests assert no partial link/ledger/audit/observable rows. |
| High | Replay triggers automatic mutation | **None found.** Only `stored.created=True` calls the service (`ingest/views.py:269-270`); the service also skips `created=False` (`cases/correlation.py:1000-1006`). Webhook replay test confirms `200`, `created=False`, `auto_bundle.status=None` and no duplicate rows. |
| Medium | Config authorization / cross-org rule change | **None found.** `_check_org_access()` permits own-org `admin` and any `superAdmin`, returning 404 otherwise (`ui/views/correlation.py:20-25`). SuperAdmin cross-org changes set `break_glass=True` in the audit row (`ui/views/correlation.py:185-197`). Tests cover non-admin 404, cross-org admin 404 and break-glass audit. |
| Medium | Secret or raw-payload leakage in response/audit | **None found.** Response warnings are fixed reason strings (`ingest/views.py:277-281`). Audit details contain source identifiers, correlation key/observable signal and reason (`cases/correlation.py:571-579`, `1142-1169`) as required by the plan's “never silent” rule; they do not include `raw_payload`, headers, tokens or exception text. Unexpected errors are logged with `logger.exception` (`cases/correlation.py:1209`) but returned to clients only as `bundle_failed`. |
| Medium | Unbounded fan-out / resource exhaustion | **None found.** Correlation-key candidates are ordered and sliced DB-side by `PICKER_PAGE_SIZE` (`cases/correlation.py:864-879`); shared observables by `MAX_CORRELATION_OBS` and `PICKER_PAGE_SIZE` (`cases/correlation.py:923-945`). Rule validation constrains `window_minutes=1..60` (`ui/views/correlation.py:127-130`, DB CHECK in `cases/models.py`). No Python-side unbounded scan was introduced. |
| Medium | Benchmark command writes into repo or leaks secrets | **None found.** The command refuses non-Postgres engines, derives `/tmp` from `os.sep`, rejects output paths outside `/tmp` unless `.json`, and seeds only synthetic benchmark values (`cases/management/commands/benchmark_auto_bundle_candidates.py`). |
| Low | Ledger choke-point bypass | **None found.** `record_alert_bundled()` writes through `append_timeline_event()` (`cases/correlation.py:583`); `cases/correlation.py:549` pointer corrected in deviations. `test_realtime.py` direct-create scan remains green. |
| Low | Viewer actor automatic write | **None found.** The service passes `actor=None`, and `record_alert_bundled()` refuses a viewer actor if one is ever supplied (`cases/correlation.py:556`). |
| Info | Accepts `uniq_alert_observable` as alert→observable plan evidence | The benchmark accepts either `alertobs_obs_alert_idx` or `uniq_alert_observable` because the alert→observable direction is served by the unique `(alert, observable)` index, while the named `alertobs_obs_alert_idx` is the reverse fan-out. No new index is added without captured plan evidence. |
| Info | Manual review substitute | Agent unavailable; manual conformance and source review were used. This reduces independence but does not change the code-level risk assessment. |

## Verification evidence used

- Auto-bundle conformance: `25 passed`.
- Settings conformance: `16 passed`.
- Focused combined suite: `184 passed, 1 skipped`.
- Broader regression suite: `127 passed`.
- `make check`: all checks passed (154 files under mypy).
- Failure/replay/realtime cases explicitly assert no partial rows and no cross-org delivery.

## Residual risk

The feature remains disabled by default. The `.env`-backed Postgres benchmark has now run with all p95 values under 500 ms and all indexed-seek checks passing; the planner chose equivalent indexes rather than two plan-named indexes (W63-16). Postgres-focused pytest also passed against the `amalthea` dev database using `--reuse-db` after a flush and seed restoration (W63-18 resolved). A user-run full `pytest` exposed 8 guard failures (enum/FK inventory, UI route inventory, `fa-link` subset CSS); all were remediated and the final full rerun is green (`4441 passed, 3 skipped`). No High/Medium security finding blocks the implementation; deviations W63-16 and W63-17 were accepted by the user on 2026-11-26 and the tracker item is closed in `COMPLETED.md`, as tracked in the verification report.
