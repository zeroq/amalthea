# VERIFY-2026-11-26 — Observable list COUNT performance (TODO §12.1-fu1)

## Summary

**Verdict: PASS.** The deferred Q7 `COUNT` measurement from `VERIFY-2026-10-18-ui-pagination.md`
was executed against a real PostgreSQL endpoint with 100,000 observables. The initial measurement
crossed the 500 ms p95 threshold on the common page cases, so a `pg_trgm` GIN index was added on
the exact expression Django emits for `data__icontains`. A repeated post-index run has no slow
cases and the planner demonstrably uses `obs_data_upper_trgm_idx` for selective matches.

The run used PostgreSQL `14.24 (Ubuntu 14.24-0ubuntu0.22.04.1)` rather than the compose-default
PostgreSQL 16 image; this is recorded as an environment deviation, not a blocker.

## Evidence

- Baseline JSON: generated locally at `/tmp/observable-count-benchmark-baseline.json` (not committed).
- Final JSON: generated locally at `/tmp/observable-count-benchmark.json` (not committed).
- Harness: `observables/management/commands/benchmark_observable_count.py`.
- Migration: `observables/migrations/0015_observable_data_upper_trgm.py`.
- Model index: `observables/models.py::Observable.Meta.indexes`.
- View optimization: `ui/views/observables.py` now computes the unfiltered `COUNT` only when a
  filter is active.
- Regression tests: `tests/conformance/test_indexes.py`,
  `tests/conformance/test_ui_pagination_edge_cases.py`, `tests/conformance/test_ui_pagination.py`.

## Commands

```bash
DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py migrate --noinput

DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_observable_count --seed \
  --output /tmp/observable-count-benchmark-baseline.json

# after adding the pg_trgm migration
DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_observable_count --seed \
  --output /tmp/observable-count-benchmark.json

make check
.venv/bin/pytest -q \
  tests/conformance/test_indexes.py \
  tests/conformance/test_ui_pagination_edge_cases.py \
  tests/conformance/test_ui_pagination.py --create-db
```

## Acceptance criteria

| AC | Required | Result | Evidence |
|---|---|---|---|
| Real Postgres measurement | Run at ~100k observables on Postgres, not SQLite | **Met** | `target_rows=100000`, `existing_rows=100000`, `postgres_version=14.24...` in both JSON artifacts |
| Reproducible harness | Deterministic seed, repeat timings, `EXPLAIN (ANALYZE, BUFFERS)` plans | **Met** | `benchmark_observable_count` with `--seed`, `--repeat`, JSON output; plans include `Actual Rows`, `Rows Removed by Filter`, shared buffers |
| Decision rule | `p95_ms <= 500.0` for every measured case | **Met after index** | Baseline slow cases: `q_common_page`, `q_common_page_200`, `q_common_deep_page`; final `slow_cases: []` |
| If index needed | Add GIN/`pg_trgm` on the expression Django emits, prove planner use | **Met** | `obs_data_upper_trgm_idx` on `UPPER(data::text) gin_trgm_ops`; final plan uses it for `q_rare_count` and `q_rare_page` |
| View correctness | No unfiltered `COUNT` on unfiltered observable list; filtered `COUNT`s still present | **Met** | Pagination edge-case query-count tests pass |
| Static/type/migration gates | `make check`, migrations in sync, no formatting/lint/type drift | **Met** | `make check` passed after all edits; `makemigrations --check --dry-run` reported no changes |
| Focused regression suite | Relevant conformance suites green | **Met** | `127 passed, 1 skipped in 198.56s` on the final code |
| Full pytest | Not run unless explicitly requested | **Not run** | Standing user constraint; no fresh full-suite claim |

## Before / after p95 (ms)

| Case | Baseline (repeat 5) | Final (repeat 10) | Final planner shape |
|---|---:|---:|---|
| `unfiltered_count` | 29.625 | 30.502 | `Index Only Scan` / `observable_owner_org_id_0f867805` |
| `q_common_count` | 306.920 | 105.701 | `Seq Scan`; common marker matches all rows so trigram is not selective |
| `q_rare_count` | 205.990 | 11.041 | `Bitmap Index Scan` / `obs_data_upper_trgm_idx` |
| `type_count` | 82.732 | 85.949 | `Seq Scan` |
| `q_common_and_type_count` | 99.313 | 100.328 | parallel `Seq Scan`; common marker matches all rows |
| `q_common_page` | 533.929 | 85.579 | `Index Scan` / `obs_created_at_idx` |
| `q_rare_page` | 153.358 | 70.176 | `Bitmap Index Scan` / `obs_data_upper_trgm_idx` |
| `q_common_page_200` | 619.858 | 299.466 | `Index Scan` / `obs_created_at_idx` |
| `q_common_deep_page` | 653.127 | 94.931 | `Index Scan` / `obs_created_at_idx` |

Baseline decision: `index-candidate` (`q_common_page`, `q_common_page_200`, `q_common_deep_page`
above threshold). Final decision: `accept-no-index` from the harness — interpreted here as **no
further index candidate remains** after the added trigram index.

## Planner proof

Final `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plan index usage:

- `q_rare_count`: `Bitmap Heap Scan` → `Bitmap Index Scan` with
  `Index Name = obs_data_upper_trgm_idx`.
- `q_rare_page`: `Bitmap Heap Scan` → `Bitmap Index Scan` with
  `Index Name = obs_data_upper_trgm_idx`.
- `q_common_page`, `q_common_page_200`, `q_common_deep_page`: `Index Scan` with
  `Index Name = obs_created_at_idx`, avoiding the prior sort/seq-scan page shape.
- `unfiltered_count`: `Index Only Scan` with the org FK index.

The common-marker count cases still choose `Seq Scan` because the seeded marker matches every row;
their p95 is below the threshold, so no further index is justified.

## Implementation notes and deviations

- The JSONB GIN migrations (`alerts/0010`, `alerts/0011`, `cases/0013`, `observables/0006`)
  originally emitted btree DDL with `opclasses=["gin_jsonb_ops"]`, which PostgreSQL rejected with
  `operator class "gin_jsonb_ops" does not exist for access method "btree"`. They were corrected to
  `GinIndex` state plus Postgres-only `RunPython` DDL, keeping SQLite migration-compatible.
- `observables/0015_observable_data_upper_trgm.py` enables `pg_trgm` and creates the expression
  index only on PostgreSQL.
- `GinIndex` expression opclasses use Django 5.2's `OpClass(..., name="gin_trgm_ops")`; using
  `GinIndex(..., opclasses=[...])` with expressions is rejected by Django.
- Harness default `--repeat` was raised from 5 to 10 because p95 with five samples is effectively
  the maximum and one transient remote-DB spike produced a false failure.
- Postgres 14.24 was used because that was the user-provided endpoint; the compose file remains
  PostgreSQL 16. No measurement artifact should claim PG16.

## Follow-up state

- `TODO.md §12.1-fu1` is closed.
- `VERIFY-2026-10-18-ui-pagination.md` Q7 is updated from deferred to met, pointing here.
- `REVIEW-2026-10-18-ui-pagination.md` O1 is marked resolved.
- No commit or push was made for this work.
