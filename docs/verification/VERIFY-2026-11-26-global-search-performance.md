# Verification: Global Search PostgreSQL Measurement (2026-11-26)

**Scope:** PLAN-2026-11-26 §6.16-fu1 and §6.16 P7 tracking closure
**Status:** Verified with a documented deviation for index adoption
**Endpoint:** PostgreSQL on the user-provided environment (`amalthea.settings.test_pg`)
**Evidence artifacts:**

- `/tmp/global-search-benchmark.json` -- initial measurement, before benchmark/query fixes
- `/tmp/global-search-stress-final.json` -- indexed stress rerun, common term retained before query/index tuning
- `/tmp/global-search-benchmark-baseline.json` -- realistic 10% common-term baseline, before new indexes
- `/tmp/global-search-benchmark-final.json` -- realistic 10% common-term final run, after indexes

These JSON artifacts are intentionally not committed. Each measured case includes the SQL, full `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plan, node types, and run samples.

---

## 1. Environment and commands

```bash
DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py migrate --noinput

DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_global_search --seed \
  --count 100000 --batch-size 2000 --repeat 10 \
  --threshold-ms 500.0 \
  --output /tmp/global-search-benchmark.json \
  --org-name global-search-benchmark
```

The final command family was rerun against the default benchmark organisation `global-search-benchmark-v2`, seeded to 100000 rows per entity. The final report metadata records:

```text
generated_at:       2026-11-26T11:54:00.968397+00:00
settings_module:    amalthea.settings.test_pg
postgres_version:   14.24 (Ubuntu 14.24-0ubuntu0.22.04.1)
target_rows:        100000
existing_rows:      case=100000, alert=100000, observable=100000
seeded_rows:        case=0, alert=0, observable=0 (already seeded)
repeat:             10
threshold_ms:       500.0
seed_distribution:  common_every=100, rare_every=1000
match_counts:       case=10000/100, alert=10000/100, observable=10000/100
acceptance:         accept-no-index, slow_cases=[]
```

---

## 2. Decision record

The benchmark gate uses the plan rule:

```text
accept-no-index if p95_ms <= threshold_ms for every measured case
index-candidate otherwise
```

Evidence sequence:

| Run | Result | Interpretation |
|---|---|---|
| `/tmp/global-search-benchmark.json` | Multiple p95 breaches at the original 100% common-term seed | Triggered the `index-candidate` path and benchmark/query rework. |
| `/tmp/global-search-stress-final.json` | `index-candidate`; three case page shapes exceed 500 ms | Confirmed residual stress risk for a 10% common term under the older plan/query shape. |
| `/tmp/global-search-benchmark-baseline.json` | `accept-no-index`; all 18 p95 values <= 500 ms | Realistic seeded workload passes without the new case/alert indexes. |
| `/tmp/global-search-benchmark-final.json` | `accept-no-index`; all 18 p95 values <= 500 ms | Final indexed state passes with selective-query p95s materially lower than baseline. |

Deviation: the plan said indexes would be added only if the measured decision were `index-candidate`. The initial and stress runs were `index-candidate`, so per-field trigram indexes were added. The later realistic baseline independently returned `accept-no-index`; the indexes were retained because the earlier breach evidence was real and selective queries improved substantially. This is recorded in `docs/spec/deviations.md`.

Residual risk: the old stress shape still has three p95 breaches in `/tmp/global-search-stress-final.json`. This is not the final shipped query shape, but it remains evidence that very common search terms can approach or exceed the 500 ms target.

---

## 3. Final p95 table (`/tmp/global-search-benchmark-final.json`)

All values are milliseconds. Threshold is 500.0 ms.

| Case | p95 | Plan node types (abridged) |
|---|---:|---|
| `case_text_count` | 309.4 | Aggregate, Gather, Aggregate, Seq Scan, Bitmap Heap Scan, Bitmap Index Scan |
| `case_text_page` | 316.3 | Limit, Gather Merge, Sort, Hash Join x3, Seq Scan, Bitmap Heap Scan, Bitmap Index Scan, Hash x3, Seq Scan x3 |
| `case_text_page_200` | 312.8 | Limit, Gather Merge, Sort, Hash Join x3, Seq Scan, Bitmap Heap Scan, Bitmap Index Scan, Hash x3, Seq Scan x3 |
| `case_text_deep_page` | 343.9 | Limit, Gather Merge, Sort, Hash Join x3, Seq Scan, Bitmap Heap Scan, Bitmap Index Scan, Hash x3, Seq Scan x3 |
| `case_rare_count` | 13.1 | Aggregate, Bitmap Heap Scan, BitmapOr, Bitmap Index Scan x5 |
| `case_rare_page` | 35.1 | Limit, Sort, Hash Join, Nested Loop x2, Bitmap Heap Scan, BitmapOr, Bitmap Index Scan x5, Memoize, Index Scan x2, Hash, Seq Scan |
| `case_digit_count` | 11.8 | Aggregate, Bitmap Heap Scan, BitmapOr, Bitmap Index Scan x6 |
| `case_digit_page` | 18.4 | Limit, Sort, Hash Join, Nested Loop x2, Bitmap Heap Scan, BitmapOr, Bitmap Index Scan x6, Memoize, Index Scan x2, Hash, Seq Scan |
| `alert_text_count` | 206.2 | Aggregate, Gather, Aggregate, Bitmap Heap Scan, Bitmap Index Scan |
| `alert_text_page` | 23.0 | Limit, Nested Loop x2, Index Scan, Materialize, Index Scan, Memoize, Index Scan |
| `alert_text_page_200` | 62.4 | Limit, Nested Loop x2, Index Scan, Materialize, Index Scan, Memoize, Index Scan |
| `alert_text_deep_page` | 50.1 | Limit, Nested Loop x2, Index Scan, Materialize, Index Scan, Memoize, Index Scan |
| `alert_rare_count` | 10.6 | Aggregate, Bitmap Heap Scan, BitmapOr, Bitmap Index Scan x5 |
| `alert_rare_page` | 25.1 | Limit, Sort, Nested Loop x2, Index Scan, Bitmap Heap Scan, BitmapOr, Bitmap Index Scan x5, Memoize, Index Scan |
| `observable_text_count` | 228.1 | Aggregate, Bitmap Heap Scan, Bitmap Index Scan |
| `observable_text_page` | 73.1 | Limit, Incremental Sort, Nested Loop x2, Index Scan, Materialize, Index Scan, Memoize, Index Scan |
| `observable_rare_count` | 11.7 | Aggregate, Bitmap Heap Scan, Bitmap Index Scan |
| `observable_rare_page` | 28.2 | Limit, Sort, Nested Loop x2, Index Scan, Bitmap Heap Scan, Bitmap Index Scan, Memoize, Index Scan |

No final case exceeds the threshold.

---

## 4. Baseline vs final selective-query improvement

The baseline run passed the absolute threshold, but final indexed plans removed a repeated `Seq Scan`/`Gather Merge` pattern from selective case and alert queries.

| Case | Baseline p95 | Final p95 | Change |
|---|---:|---:|---:|
| `case_rare_count` | 310.3 | 13.1 | -95.8% |
| `case_rare_page` | 315.3 | 35.1 | -88.9% |
| `case_digit_count` | 312.1 | 11.8 | -96.2% |
| `case_digit_page` | 311.4 | 18.4 | -94.1% |
| `alert_text_page` | 408.9 | 23.0 | -94.4% |
| `alert_text_page_200` | 361.2 | 62.4 | -82.7% |
| `alert_rare_count` | 219.9 | 10.6 | -95.2% |
| `alert_rare_page` | 206.6 | 25.1 | -87.8% |

Common-term count/page cases remain in the 200-350 ms range but stay below the agreed threshold.

---

## 5. Stress residual table (`/tmp/global-search-stress-final.json`)

| Case | p95 | Threshold | Status |
|---|---:|---:|---|
| `case_text_page` | 518.3 | 500.0 | breach |
| `case_text_page_200` | 616.8 | 500.0 | breach |
| `case_text_deep_page` | 616.1 | 500.0 | breach |

All other stress cases were below threshold. These breaches are retained as a known residual risk rather than hidden by changing the dataset after measurement.

---

## 6. Index guardrails and tests

Added/verified indexes:

- `observables.0015_observable_data_upper_trgm` owns `CREATE EXTENSION pg_trgm`.
- `alerts.0020_alert_upper_trgm` and `alerts.0021_alert_search_order_idx`.
- `cases.0026_case_upper_trgm` and `cases.0027_case_search_order_idx`.
- SQLite migrations are no-ops via `core.pg_indexes.gin_expression_index_operation()`.

Verification commands:

```bash
make check
.venv/bin/pytest -q tests/conformance/test_indexes.py
```

Both passed. `make check` also confirmed migrations are in sync under the checked settings.

---

## 7. AC evaluation (§6.16-fu1)

| AC | Result | Evidence |
|---|---|---|
| AC-P0-1.1 | Met | Plan explicitly names §6.16-fu1 and §6.16 P7. |
| AC-P0-1.2 | Met | OQ-1..OQ-6 recorded as approved defaults in the plan and implemented accordingly. |
| AC-P0-1.3 | Deviated | Search indexes were added after benchmark evidence was collected; this is the recorded deviation above. |
| AC-P1-1.1 | Met | Observable benchmark remained unchanged; shared helper extracted without schema/decision change. |
| AC-P1-1.2 | Met | `core/db_benchmark.py` owns timing/explain helpers; observable benchmark still runs. |
| AC-P1-2.1 | Met | Postgres-only guard in `query/management/commands/benchmark_global_search.py`. |
| AC-P1-2.2 | Met | Deterministic seed and `ANALYZE`; report records seed distribution and match counts. |
| AC-P1-2.3 | Met | Dedicated orgs and idempotent row-count checks; rerun reported `seeded_rows=0`. |
| AC-P1-2.4 | Met | Each case records a single SQL string and full JSON plan. |
| AC-P1-2.5 | Met | Page measurements re-chain querysets; warm-up placed before timing samples. |
| AC-P1-2.6 | Met | `index-candidate` only when at least one p95 exceeds `--threshold-ms`. |
| AC-P1-3.1 | Met | `migrate --noinput` succeeded under `amalthea.settings.test_pg`. |
| AC-P1-3.2 | Met | Benchmark wrote `/tmp/global-search-benchmark.json`; later artifacts also written to `/tmp`. |
| AC-P1-3.3 | Met | Postgres version, all p95 values, and node types are recorded in JSON artifacts and tables above. |
| AC-P1-4.1 | Deviated | Final decision is `accept-no-index`, but indexes were retained due to earlier `index-candidate` evidence. |
| AC-P1-4.2 | Met | Per-field `gin_trgm_ops` indexes added for alert/case text fields. |
| AC-P1-4.3 | Met | `test_indexes.py` and `makemigrations --check --dry-run` passed. |
| AC-P1-4.4 | Met | Final run has all p95 values below 500 ms and selective plans use bitmap/trigram index paths. |
| AC-P1-4.5 | Met | `make check` passed migration drift checks. |
| AC-P1-5.1 | Met | This report plus `VERIFY-2026-11-26-section6-realtime-and-search.md` contain tables, plans summary, commands, and AC evaluation. |
| AC-P1-5.2 | Met | `VERIFY-2026-11-01-global-search.md` residual risk updated. |
| AC-P1-5.3 | Met | `TODO.md` closed and `COMPLETED.md` updated. |

---

## 8. Residual risks

1. Very common terms can still approach the threshold; stress p95 breaches remain documented.
2. Benchmark artifacts live in `/tmp` and will disappear on reboot. Re-run the command to regenerate.
3. Index write amplification is accepted in exchange for selective-query performance; no production write benchmark was performed.

---

## 9. Conclusion

§6.16-fu1 is complete. The benchmark is reproducible, plans are captured, and the index decision is evidence-backed with one explicit deviation. No credentials are present in this document or the JSON artifacts.
