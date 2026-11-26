# Verification — auto-bundle candidate benchmark

**Tracker:** `TODO.md` §12.3-fu1
**Plan:** `docs/planning/PLAN-2026-11-26-auto-ingest-bundling.md` P6 / AC-P6.1–AC-P6.4
**Status:** **MEASURED** — all p95 values are under the 500 ms gate and all indexed-seek plan checks pass. Strict index-name deviations are recorded below.

## Environment

| Field | Value |
|---|---|
| Date | 2026-11-26 |
| OS | macOS (darwin) |
| Python/Django | project `.venv`, Django 5.x |
| Database | Postgres 14.24 supplied by the local `.env` (values not recorded here) |
| `DJANGO_SETTINGS_MODULE` | `amalthea.settings.test_pg` |
| Benchmark output | `/tmp/auto-bundle-benchmark-v4.json` |
| Repeat | 10 |
| Threshold | 500 ms p95 per measured query |

The local `.env` was sourced via `set -a; . ./.env; set +a`. No `.env` contents, credentials, raw payloads, or exception text were written to the report or repository.

## Dataset

The benchmark seed is deterministic and idempotent. The final measured org contained:

| Item | Count |
|---|---:|
| Alerts | 50,001 including the incoming alert |
| Hot-key alerts | 1,001 (`BENCH_KEY` every 50th seeded alert) |
| Cases | 1,000 |
| Observables linked to incoming alert | 1,000 |
| Case-observable links | 1,000 |
| Noise alert-observable links | 50,000 |
| Window | 10 minutes (`CorrelationRule.window_minutes`) |

`alert_observable_ids` is measured with `MAX_CORRELATION_OBS` rows, matching the service cap, rather than a one-row sample. This avoids a misleading `LIMIT 1` sequential scan.

## Commands

Postgres-only refusal was verified first with SQLite/default settings:

```bash
.venv/bin/python manage.py benchmark_auto_bundle_candidates \
  --output /tmp/auto-bundle-candidate-benchmark.json
# CommandError: This benchmark is Postgres-only; run with
# DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg
```

Measured run:

```bash
set -a; . ./.env; set +a
DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_auto_bundle_candidates \
    --seed --org-name auto-bundle-benchmark-v2 \
    --repeat 10 --threshold-ms 500.0 \
    --output /tmp/auto-bundle-benchmark-v2.json

DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_auto_bundle_candidates \
    --org-name auto-bundle-benchmark-v2 \
    --repeat 10 --threshold-ms 500.0 \
    --output /tmp/auto-bundle-benchmark-v4.json
```

The second command re-measured the final seed without adding rows.

## AC-P6.1 — benchmark command

**Status: met.**

`cases/management/commands/benchmark_auto_bundle_candidates.py`:

- refuses non-Postgres connections;
- seeds deterministic alerts, cases, observables, alert-observable links, case-observable links and a bounded hot-key subset;
- reuses `PICKER_PAGE_SIZE` and `MAX_CORRELATION_OBS`;
- measures the exact three query shapes through `core.db_benchmark.measure_page`;
- captures `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans in the JSON report;
- writes JSON only under `/tmp`;
- records `index-candidate` when any p95 exceeds the threshold or any plan check fails.

## AC-P6.2 — plan proof

**Status: deviated but safe.** No `Seq Scan` appears on `alert`, `alert_observable` or `case_observable`, but Postgres chose equivalent available indexes instead of two index names named in the plan.

| Query | Indexes in captured plan | Note |
|---|---|---|
| `correlation_key_candidates` | `case_search_order_idx`, `case_status_pkey`, `alert_case_id_be755fe7` | No `Seq Scan` on `alert`. Planner preferred the case-driven path over `alert_corrkey_date_idx`. |
| `alert_observable_ids` | `uniq_alert_observable` (`Index Only Scan`) | Serves alert→observable direction as expected. |
| `shared_observable_candidates` | `case_search_order_idx`, `case_status_pkey`, `uniq_case_observable`, `observable_pkey` | Planner preferred the case-leading unique index over `caseobs_obs_case_idx`. |

Plan checks passed:

| Check | Result |
|---|---|
| `correlation_key_no_alert_seq_scan` | pass |
| `correlation_key_uses_alert_index_seek` | pass |
| `shared_observable_uses_alert_observable_index` | pass |
| `shared_observable_uses_case_observable_index` | pass |

Strict AC-P6.2 expected `alert_corrkey_date_idx`, `caseobs_obs_case_idx`/`alertobs_obs_alert_idx`; this is recorded in `docs/spec/deviations.md` as W63-16. No new index was added.

## AC-P6.3 — p95 threshold

**Status: met.**

| Case | p95 (ms) | Median (ms) | Result |
|---|---:|---:|---|
| `correlation_key_candidates` | 226.813 | 153.999 | pass |
| `alert_observable_ids` | 14.566 | 10.238 | pass |
| `shared_observable_candidates` | 81.022 | 72.238 | pass |

Decision from report: `accept-no-index`. No measured query exceeded 500 ms.

## AC-P6.4 — verification doc

**Status: met.**

This document records the environment, dataset, commands, p95 table, plan shapes, decision and deviations without credentials or raw payload data.

## Decision

Keep auto-bundling disabled by default (`CorrelationRule.enabled=False` and per-feed source flag off). The measured query shapes are bounded and under threshold; enable per organisation only after the owning team accepts the recorded index-name deviation. No new index is added.

## Residual risk

- The correlation-key plan may change as table statistics or org/case cardinality change. Re-run the benchmark after significant ingest-volume changes.
- Postgres-focused pytest was run against the `.env` `amalthea` dev database with `POSTGRES_TEST_DB_NAME=amalthea` and `--reuse-db` (`135 passed, 1 skipped`), after flushing the dev DB and restoring seed rows. No server auth or `.env` values were changed.
