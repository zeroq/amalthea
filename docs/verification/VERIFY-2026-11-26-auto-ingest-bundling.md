# Verification — auto ingest-time alert bundling (12.3-fu1)

**Plan:** `docs/planning/PLAN-2026-11-26-auto-ingest-bundling.md`
**Implementation brief:** `docs/planning/IB-2026-11-26-auto-ingest-bundling.md`
**Date:** 2026-11-26
**Verifier:** project lead (manual). The `verifier`, `qa-tester` and `security-auditor` agents were unavailable in this session; their work was performed manually and is labelled as such. This is recorded as W63-17 in `docs/spec/deviations.md`.

## Evidence summary

| Command | Result |
|---|---|
| `.venv/bin/pytest -q tests/conformance/test_auto_ingest_bundling.py` | `25 passed in 33.94s` |
| `.venv/bin/pytest -q tests/conformance/test_auto_ingest_bundling_settings.py` | `16 passed in 28.91s` |
| Focused combined suite: auto bundle, settings, correlation key derivation, similar cases, indexes, ingest pipeline, ingest observables, webhook hardening, MVP loop, MVP automation, realtime, realtime org | `184 passed, 1 skipped in 101.89s` |
| Broader regression suite: `test_tenant_scoping.py::TestWebhookTokenRouting`, `test_alert_fuzzy_similarity.py`, `test_ledger_keyset.py`, `test_realtime.py`, `test_realtime_org.py`, `test_settings_ingestion_tokens.py`, `test_settings_audit_log.py` | `127 passed in 96.97s` |
| `make check` | all checks passed (ruff, format, mypy 154 files, Django checks, migrations, CSS, playbook scripts) |
| `.venv/bin/python manage.py benchmark_auto_bundle_candidates --output /tmp/auto-bundle-candidate-benchmark.json` | `CommandError` confirming Postgres-only refusal |
| `.env`-backed Postgres benchmark (`amalthea.settings.test_pg`, 50k alerts) | decision `accept-no-index`; p95 226.813/14.566/81.022 ms; all indexed-seek checks pass; two strict index-name deviations recorded |
| Postgres-focused conformance suites against the `.env` `amalthea` dev DB with `--reuse-db` | `135 passed, 1 skipped in 92.99s` |
| User-run full `pytest` (`/tmp/amalthea-full-pytest.log`) | `4441 passed, 3 skipped, 15 warnings, 2 subtests passed in 1508.09s`; no failures |
| Affected guard/asset remediation suite | `114 passed in 57.01s` |
| Dashboard layout repair verification | UI/dashboard targeted suites `147 passed`, `test_ui_loop.py` `41 passed`; the later user-run full suite above is green, though its CSS warning list still shows pre-repair `live-chip`, so dashboard-specific confirmation rests on the targeted suites |

## AC grading

Statuses: **24 met**, **5 deviated**, **0 not met** (29 total). Deviations W63-16 and W63-17 were accepted by the user on 2026-11-26; implementation commit `72c52e6f` satisfies AC-P7.5.

### P1 — Config model and migration

| AC | Status | Evidence |
|---|---|---|
| **AC-P1.1** one row per org, defaults, constraints, migration check | **Met** | `CorrelationRule` in `cases/models.py`; `cases/migrations/0029_correlation_rule.py`; `make check` migrations in sync; settings/model conformance and `test_indexes.py` green. |
| **AC-P1.2** data-model docs | **Met** | `docs/spec/data-model.md` documents the new table, defaults, constraints and `IngestionSource.correlation_enabled` relationship. |

### P2 — Settings UI

| AC | Status | Evidence |
|---|---|---|
| **AC-P2.1** view/update guards and invalid input writes nothing | **Met** | `tests/conformance/test_auto_ingest_bundling_settings.py`: admin own-org, short redirect, non-admin 404, cross-org 404, superadmin explicit org, invalid window/mode writes nothing. |
| **AC-P2.2** audit config changes | **Met** | Settings suite: one `case.correlation_rule_changed` row on actual change, no row on no-op, superadmin cross-org break-glass audit. |
| **AC-P2.3** source help text | **Met** | Settings suite asserts `correlation_enabled` kill-switch text on source pages; templates updated. |

### P3 — Correlation service

| AC | Status | Evidence |
|---|---|---|
| **AC-P3.1** deterministic non-secret decisions and pre-query skips | **Met** | `AutoBundleDecision` returns short reasons; service skips replay, already-linked, tenant mismatch, rule/source disabled, missing key/observables. Covered by `test_auto_ingest_bundling.py`. |
| **AC-P3.2** correlation-key seek and Postgres index plan | **Deviated** | Query shape uses `Alert`, org/window/open-case/shared-case filters, `-date,-id`, DB-side cap; captured Postgres plan has no `Seq Scan` on `alert` but uses `alert_case_id_be755fe7` instead of the plan-named `alert_corrkey_date_idx`. W63-16 records the equivalent-index deviation. |
| **AC-P3.3** shared-observable seek | **Met** | Shared mode uses `AlertObservable`, `MAX_CORRELATION_OBS`, `PICKER_PAGE_SIZE`, ignores `ignore_similarity`, excludes cross-org/stale links; tests cover each branch. Plan-shape proof remains tracked under P6. |
| **AC-P3.4** atomic bundle write | **Met** | Service re-fetches with `select_for_update()`, re-checks closed/cross-org/linked state, calls `merge_alert_into_case()` and `record_alert_bundled()`; rollback tests assert no link/ledger/audit/observable rows. |
| **AC-P3.5** actor typing and ledger choke point | **Met** | `actor: User | None`; `record_alert_bundled()` uses `append_timeline_event()`; `test_realtime.py` choke-point scan and focused suite pass. |

### P4 — Ingest integration and API response

| AC | Status | Evidence |
|---|---|---|
| **AC-P4.1** call after successful store only | **Met** | `ingest/views.py` calls service only when `stored.created`; token org is passed; webhook replay test shows no second evaluation. |
| **AC-P4.2** response contract and warnings | **Met** | Every response includes `auto_bundle`; known refusals add `auto_bundle_skipped: <reason>`; no payload or exception text is exposed. |
| **AC-P4.3** failure containment | **Met** | Webhook rule-disabled test returns `201` with warning and no partial rows; service tests cover `PermissionDenied` and unexpected exception rollback. |
| **AC-P4.4** API docs | **Met** | `docs/spec/api.md` documents `auto_bundle` fields and warning semantics. |

### P5 — Tests

| AC | Status | Evidence |
|---|---|---|
| **AC-P5.1** model/config tests | **Met** | `test_auto_ingest_bundling_settings.py` (16 passed) plus model defaults/constraints in plan-era settings suites. |
| **AC-P5.2** correlation-key behavior | **Met** | Dedicated tests cover in-window, out-of-window, blank key, closed, cross-org/shared case, unlinked alert, duplicates, deterministic tie-break, DB-side cap; `test_indexes.py` covers index guard. |
| **AC-P5.3** shared-observable behavior | **Met** | Dedicated tests cover shared match, no observables, `ignore_similarity`, cross-org, stale window, DB-side observable cap. |
| **AC-P5.4** bundle trail | **Met** | Success test asserts exactly one `correlation-bundle`, one `case.alert_bundled`, one `alert-merged`, linked alert and copied observable; dispatch called once. |
| **AC-P5.5** replay/idempotence | **Met** | Webhook replay returns `200`, `created=False`, `auto_bundle.status=None`, no duplicate rows; manual retry after forced refusal yields one complete trail. |
| **AC-P5.6** failure/realtime | **Met** | Known refusal webhook warning with no partial rows; socket test asserts case room receives `alert-merged` then `correlation-bundle`, owner org receives `case-updated` refresh, foreign org receives nothing. |

### P6 — Postgres benchmark and database proof

| AC | Status | Evidence |
|---|---|---|
| **AC-P6.1** benchmark command | **Met** | `cases/management/commands/benchmark_auto_bundle_candidates.py` implemented; registered by Django; SQLite refusal verified; output restricted to `/tmp`; executed against `.env`-backed Postgres. |
| **AC-P6.2** plan proof | **Deviated** | Captured plans have no `Seq Scan` on measured tables and all indexed-seek checks pass, but Postgres chose `alert_case_id_be755fe7` and `uniq_case_observable` instead of the two plan-named indexes. See W63-16 and the performance verification. |
| **AC-P6.3** p95 threshold | **Met** | `--repeat 10`: p95 226.813 ms correlation key, 14.566 ms alert observables, 81.022 ms shared observable; all `<= 500 ms`; decision `accept-no-index`. |
| **AC-P6.4** performance verification doc | **Met** | `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling-performance.md` records environment, dataset, commands, p95 table, plan shapes, decision and deviations. |

### P7 — Verification, security, closeout

| AC | Status | Evidence |
|---|---|---|
| **AC-P7.1** focused tests and static gates | **Met** | SQLite focused suites (184 passed, 1 skipped), broader regression suite (127 passed), `make check`, Postgres-focused suites against the `.env` `amalthea` dev DB (`135 passed, 1 skipped`), and the user-run full `pytest` (`4441 passed, 3 skipped`) all pass. Postgres used `--reuse-db` with `POSTGRES_TEST_DB_NAME=amalthea` because the remote `pg_hba.conf` permits only that database; the dev DB was flushed and seed rows restored before the run. The earlier full-suite guard failures (enum/FK inventory, UI route inventory, `fa-link` subset CSS) were remediated (`114 passed`) and the final full run is green. |
| **AC-P7.2** independent verifier | **Deviated** | This report is a manual project-lead verification because the verifier agent was unavailable; W63-17 records it. |
| **AC-P7.3** security review | **Deviated** | Manual security review written to `docs/reviews/REVIEW-2026-11-26-auto-ingest-bundling.md`; W63-17 records the unavailable agent. No High/Medium findings remain. |
| **AC-P7.4** QA confirmation | **Deviated** | Manual conformance tests cover the ingest → bundle → ledger → audit → realtime loop and replay; qa-tester agent unavailable. |
| **AC-P7.5** tracker closeout | **Met** | §12.3-fu1 moved to `COMPLETED.md` after the user accepted W63-16 and W63-17 on 2026-11-26, the user-owned full `pytest` gate is green (`4441 passed, 3 skipped`), and the implementation is committed as `72c52e6f`. |

## Deviations and residual risk

- W63-11 pointer corrected from `cases/correlation.py:549` to `cases/correlation.py:583`.
- W63-15 corrected: configuration auditing is implemented in the settings UI, no longer “deferred to P2 UI”.
- W63-16 records the measured Postgres benchmark, the equivalent-index deviation and the decision to keep the feature disabled by default. Accepted by the user 2026-11-26.
- W63-17 records the manual substitute for unavailable verifier/QA/security agents. Accepted by the user 2026-11-26.
- W63-18 is resolved: `POSTGRES_TEST_DB_NAME=amalthea` plus `--reuse-db` allowed the Postgres-focused suite to run in the dev database without altering server auth or dropping/recreating the DB.
- W63-19 is resolved by implementation commit `72c52e6f`; the requested commit-hash evidence is now attached to AC-P7.5.
- The first user-run full `pytest` log recorded `8 failed, 4431 passed, 3 skipped`; all failures were guard inventories/assets introduced by this feature (enum CHECK inventory, FK/on_delete inventory including one-to-one handling, UI route inventories, `fa-link` Font Awesome subset CSS). They were remediated (`114 passed`) and the final user-run full suite is green: `4441 passed, 3 skipped`.
- No new database index is added without planner evidence.
- No secrets were introduced; no raw payload or exception text is exposed by the new webhook response.

## Final verdict

Implementation, SQLite conformance, static gates, `.env`-backed Postgres benchmark, Postgres-focused conformance suites, dashboard repair targeted suites, and the user-run full `pytest` (`4441 passed, 3 skipped`) are green. The earlier 8 guard failures were remediated; the full-suite CSS-token warning still listed `live-chip`, so dashboard-specific confirmation rests on the post-repair targeted suites. Deviations W63-16 and W63-17 were accepted by the user on 2026-11-26, `TODO.md` §12.3-fu1 is closed in `COMPLETED.md`, and implementation commit `72c52e6f` supplies the AC-P7.5 commit-hash evidence.
