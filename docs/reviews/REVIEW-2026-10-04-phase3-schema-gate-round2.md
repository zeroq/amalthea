# Phase 3 Schema/Index Gate — Adversarial Re-Review (Round 2)

**Target:** `ae4891e` (round-1 remediation of `docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md`)
**Environment:** SQLite only, Django 5.2.17 / Python 3.14.6, `.venv/bin/python`
**Date:** 2026-10-04

---

## 1. Verdict

# FAIL

The schema itself is materially better than round 1 and most individual fixes are real. But the
**evidence layer is not sound**: the H1 "vacuous test" fix contains a guard that is itself vacuous,
the FK `on_delete` audit is structurally blind to `CASCADE`, and 13 schema mutations the suite does
not catch survive a clean run.

| Severity | Count |
|---|---|
| Critical | 2 |
| High | 7 |
| Medium | 9 |
| Low | 4 |
| **Total** | **22** |

Baseline claims reproduced: `186 passed in 3.30s`, `ruff check` clean, `makemigrations --check` clean.
Baseline claims **not** reproduced: "mypy 0 errors" (93 errors), "coverage 73% as a green baseline"
(`pytest --cov` exits 1 against a configured `fail_under = 80`).

---

## 2. Claim status

| Claim | Verdict | Basis |
|---|---|---|
| C1 case numbering | **PARTIAL** | 7/7 tests pass; `Field.pre_save` alone does not survive a base-QuerySet `bulk_create` |
| C2 pending-run index | **PARTIAL** | index, predicate and plan confirmed; predicate is unguarded |
| C3 idempotent ingest | **CONFIRMED** | 7/7 executed; replay, reorder, warning, precedence all verified |
| C4 correlation index | **PARTIAL** | index + window plan confirmed; column never derived in production |
| H1 vacuous tests | **FAIL** | `test_mutation_5` is itself a no-op; 13 further mutations undetected |
| H2 fresh migrate | **CONFIRMED (SQLite)** | all 20 application migrations apply from empty |
| H3 data hash | **PARTIAL** | width + uniqueness confirmed; `update_fields` staleness, migration divergence |
| H4 hash case-folding | **CONFIRMED** | case-folding on, `file` case-sensitive, seed declares it |
| H5 CHECK constraints | **PARTIAL** | constraints work; 6 of them name-only |
| H6 ApiKey prefix | **CONFIRMED** | duplicate prefix rejected; `ApiKey.prefix` unique |
| M1 redundant indexes | **FAIL** | 2 needed indexes dropped (see H-5) |
| M2 db_index=False | **CONFIRMED** | every `db_index=False` is left-prefix-covered |
| M3 wire source vs FK | **CONFIRMED** | wire `source='okta'`, `ingestion_source.slug='splunk'` |
| M4 closed_date stamp | **CONFIRMED** | stamp fires on the Closed transition and is preserved |
| M5 reverse-direction indexes | **PARTIAL** | prefix coverage sound; reverse-order indexes exist |
| M6 `case_record` consistency | **CONFIRMED** | no bare `"case"` table references in code or migrations |
| M10 seed reversal | **CONFIRMED** | zero row loss across 10 reversal/re-apply steps |
| M12 `stage_from_alert_status` | **PARTIAL** | correct today; completely untested |
| M14 no orphan runs | **CONFIRMED** | `playbook_id` non-null, snapshot survives, PROTECT enforced |
| L4 `stamp_closed_date` | **PARTIAL** | correct today; untested |
| L5 `start_date` NOT NULL | **CONFIRMED** | `datetime NOT NULL` in DDL; queue ordering sound |

---

## 3. Findings

### CRITICAL

#### C-1 — The H1 fix contains a guard that mutates nothing

`tests/conformance/test_schema_mutation_guards.py` — `test_mutation_5`

Django's SQLite backend implements `remove_constraint()` as `self._remake_table(model)`, which rebuilds
the table **from the current model state**. The constraint being "removed" is still in
`model._meta.constraints`, so it is written straight back into the new table.

Executed probe (clean DB, live `schema_editor`):

```
ed.remove_constraint(Observable, uniq_obs_dtype_hash)
  -> CREATE TABLE "new__observable" (... CONSTRAINT "uniq_obs_dtype_hash" ...)
  -> INSERT INTO "new__observable" SELECT ... FROM "observable"
uniq_obs_dtype_hash STILL in DDL after remove_constraint: True
duplicate Observable insert: still rejected
```

Same for a `CheckConstraint`:

```
ed.remove_constraint(CaseStatus, case_status_stage_valid)
case_status_stage_valid STILL in DDL: True
CaseStatus(stage="inprogres") still rejected
```

`DROP INDEX IF EXISTS "uniq_obs_dtype_hash"` and `DROP INDEX IF EXISTS "case_status_stage_valid"`
also return without error while leaving the constraint fully intact — SQLite names these in
`PRAGMA index_list` but there is no such index to drop.

The permanent guard nonetheless prints `MUTATION-5 ASSERTION: duplicate insert accepted while
uniq_obs_dtype_hash was absent` and reports `5 passed`. That line is false.

**Impact:** H1's acceptance test demonstrates 4 of 5 mutations. The 5th is theatre.

**Required fix:** a mutation must change `model._meta.constraints` (or issue real DDL that SQLite
honours) before asserting. The same defect applies to the mutation-4 sibling described in the
mutation file's own docstring.

#### C-2 — The FK `on_delete` audit is one-sided and misses every data-loss swap

`tests/conformance/test_fk_audit.py:60-67` and `:107-129`

```python
def declared_on_delete_is_explicit(field):
    return "on_delete" in field.deconstruct()[3]  # line 67
```

Django **omits** `on_delete` from `deconstruct()` when the value equals `CASCADE`. So `CASCADE` is
indistinguishable from "explicit" and passes line 67. And `test_reference_rows_use_the_reviewed_on_delete_policy`
pins only `PROTECT` (lines 126-129) — nothing pins `SET_NULL` vs `CASCADE`.

Executed mutations (model + regenerated migration, 173 tests each):

| Mutation | Result |
|---|---|
| `Alert.case` `SET_NULL` → `CASCADE` | **NOT CAUGHT** |
| `Case.assignee` `SET_NULL` → `CASCADE` | **NOT CAUGHT** |
| `Task.case` `CASCADE` → `SET_NULL` | **NOT CAUGHT** |
| `Observable.data_type` `PROTECT` → `CASCADE` | caught by `test_fk_audit.py` |
| `Alert.status` `related_name` removed | caught by `test_fk_audit.py` |
| `Case.owner_org` index dropped | **NOT CAUGHT** |

**Impact:** deleting a `Case` can be changed to silently delete its `Alert`s with the whole suite
green. `Task.case` can be changed to orphan Tasks, violating the "Case 1 —— 0..* Task" cardinality in
AGENTS.md §3. Round-1 L3 called `Alert.case` `SET_NULL` "verified sound"; that verdict is not
supportable — the policy is unverifiable, not wrong.

**Required fix:** pin the full `{field: on_delete}` mapping, not just the `PROTECT` subset, and reject
`CASCADE` explicitly wherever an explicit non-cascade policy is intended.

### HIGH

#### H-1 — `ar_pending_idx` partial predicate is unguarded (C2)

`automation/models.py:79-83`. Removing `condition=models.Q(status="Pending")`, regenerating the
migration, and running the suite: **173 passed**. The entire value of C2 is that the index holds only
Pending rows; nothing tests that.

#### H-2 — Six CHECK constraints are name-only

`core/enums.py`. Replacing each with a vacuous same-named CHECK (`stage__isnull=False | stage=""`)
and regenerating the migration: **173 passed** for all six —
`case_status_stage_valid`, `alert_status_stage_valid`, `alert_tlp_range`, `alert_pap_range`,
`observable_pap_range`, `ingestion_source_severity_range`. A rename, or a `get_full_clean()` bypass in
a `bulk_create` path, would ship undetected.

#### H-3 — `AutomationRun.idempotency_key` uniqueness is untested

`automation/models.py:70`. `unique=True` removed + migration regenerated: **173 passed**. This is
Module D's idempotency guarantee against Celery task redelivery.

#### H-4 — `IngestionSource.slug` uniqueness is untested

`ingest/models.py`. Same result: **173 passed**. `slug` is the webhook route key.

#### H-5 — M1 over-pruning dropped two indexes that are still needed

`cases/models.py:214-222` (and the `Alert` twin):

```python
# db_index=False on both FKs: the unique constraint below is a left prefix for each.
case       = ... db_index=False
custom_field = ... db_index=False
```

`custom_field` is the **right-hand** column of `uniq_case_custom_field(case, custom_field)`. A left
prefix is `case`, not `custom_field`. Live plan:

```
SELECT ... FROM case_custom_field_value WHERE case_id = ?            -> SEARCH ... USING sqlite_autoindex
SELECT ... FROM case_custom_field_value WHERE custom_field_id = ?    -> SCAN case_custom_field_value
SELECT ... WHERE case_id = ? AND custom_field_id = ?                 -> SEARCH ... USING sqlite_autoindex
```

Live introspection across Amalthea's own models: 29 FK columns, 16 implicitly indexed,
13 `db_index=False`. Of the 13, 11 are genuinely left-prefix-covered. The 2 exceptions are
`case_custom_field_value.custom_field_id` and `alert_custom_field_value.custom_field_id`.
Note also the review's own count of "31 FK columns" does not reconcile with 29.

**Required fix:** restore `db_index=True` on `custom_field` in both models.

#### H-6 — Postgres-only data corruption: the sequence is not advanced for explicit numbers

`cases/numbering.py:48-64` allocates `nextval('case_number_seq')` on Postgres; SQLite uses
`MAX(number) + 1`. `cases/numbering.py:25-27` and `tests/conformance/test_case_numbering.py` both
explicitly support creating a `Case` with an explicit `number` (the TheHive-migration path).

If any Case is created with an explicit number — or a table is imported — the sequence is never
advanced. On Postgres the next auto-numbered Case gets a colliding value:

```
BEGIN;
INSERT INTO case_record (id, number, ...) VALUES (gen_random_uuid(), nextval('case_number_seq'), ...);
ERROR:  duplicate key value violates unique constraint "case_record_number_uniq"
```

`MAX(number)+1` self-heals on SQLite, so all 186 tests are structurally blind to this. On a fresh
database the sequence starts at 1 and the first migrated Case breaks ingestion.

**Required fix:** in the allocation path, when `number` is explicitly supplied on Postgres, call
`setval('case_number_seq', GREATEST(last_value, number))`.

#### H-7 — `data_hash` backfill uses a different hash function than the runtime

- `observables/migrations/0003_observable_data_hash.py:33` backfills with
  `hashlib.sha256(normalized_data)`.
- `observables/hashing.py:83-84` saves `data_hash(canonical_value(instance))`, and
  `canonical_value()` (`:47`) case-folds values for non-case-sensitive types.

Executed: a `hash`-type Observable stored as uppercase is backfilled to `b864a0...`, while the runtime
would compute `b1c6d2...`. Since `uniq_obs_dtype_hash(data_type, data_hash)` is the dedup key, the
backfilled row and a later runtime-created row for the same artifact hash differently and both are
admitted — a duplicate slips past the only constraint meant to prevent it.

### MEDIUM

- **M-1 — `correlation_key` is never derived.** `ingest/models.py:12-13` states `mapping_config` holds
  "the rule that produces each alert's `correlation_key` (REVIEW C4)". Repo-wide, `mapping_config` is
  referenced only by its own field definition and its migration — **zero readers**. There is no
  mapping engine and no JSON-path extractor anywhere in the tree. `store_ingested_alert` accepts
  `correlation_key: str = ""` (`ingest/pipeline.py:71`) and copies it verbatim (`:104`). Setting it to
  `""` unconditionally: **186 passed**. The index is correct and used; nothing populates the column.
- **M-2 — `Case.stamp_closed_date()` is untested.** `cases/models.py:142,145-153`. Deleting the call:
  **186 passed**. L4 is unverifiable.
- **M-3 — `stage_from_alert_status` is untested.** `compat/enums.py`. `"Imported"` → `"Closed"`:
  **186 passed**. M12 is unverifiable. (Verified correct today: `"Imported"` and `"imported"` both
  return `"Imported"`.)
- **M-4 — `AllocatedNumberField` uniqueness is guaranteed at one of two layers, and the two docstrings
  contradict.** `cases/numbering.py:25-27` asserts `Field.pre_save` is sufficient because it covers
  both `save()` and `bulk_create()`; `cases/numbering.py:69-71` asserts the opposite. The second is
  correct. Executed: `models.QuerySet.bulk_create(Case.objects.all(), objs)` →
  `IntegrityError: UNIQUE constraint failed: case_record.number`, because `pre_save` reads the same
  `MAX(number)` for every row in the batch.
- **M-5 — `DataHashField` is skipped by `update_fields`.** `observables/hashing.py:83-84`.
  Executed: `o.normalized_data = "bbb"; o.save(update_fields=["normalized_data"])` leaves the stored
  `data_hash` at `983487...` instead of `3e744...`. `pre_save` updates the Python attribute but the
  column is not in `update_fields`, so the unique key tracks stale data. `QuerySet.update()` bypasses it
  entirely.
- **M-6 — The coverage gate is red.** `pyproject.toml` sets `fail_under = 80`; measured **73.45%**, so
  `pytest --cov` exits **1** while `pytest` exits 0. The claimed baseline is green only because coverage
  is not run. `core/events.py` (the Module D event surface), all of `realtime/` (`consumers.py`,
  `publisher.py`, `routing.py`) and all five `compat/mappers/*` sit at **0%** — and C2 and C4 both
  depend on that event layer.
- **M-7 — `test_seed_migrations.py` replans the whole graph backwards.** It produced 9 errors under
  mutation-generated migrations. Any migration added outside the exact dependency set breaks it.
- **M-8 — Dead docstring references.** `core/enums.py:20` cites
  `tests/conformance/test_enum_constraints.py`; the file is `test_enum_contracts.py`.
  `tests/conformance/test_seed_migrations.py:21` cites `tests/unit/`, which does not exist.
- **M-9 — "mypy 0 errors" is not reproducible.** `mypy .` → **93 errors in 28 files**. Scoped to source
  packages plus `manage.py` → **26 errors**, dominated by missing `djangorestframework-stubs` and untyped
  `admin.py` / `migrations/` code. No CI config pins an invocation, so "mypy clean" names no runnable
  command.

### LOW

- **L-1** — `case_severity_idx` is a single-column btree on a 4-value enum. Postgres will not use it to
  filter a queue; it exists to satisfy the index-list assertion.
- **L-2** — Three M2M through tables keep a redundant single-column index already left-prefix-covered by
  their own unique composite.
- **L-3** — `test_no_index_is_left_prefix_covered_by_a_wider_one` is engine-dependent. Django's SQLite
  `indexes()` omits unique constraints, so on SQLite the guard iterates an **empty** name set for
  `*_custom_field_value` and asserts nothing. Meaningful on Postgres only — which is where H-5 bites.
- **L-4** — `scripts/lock.sh` bakes a machine-specific `Python 3.14.6` header into committed requirement
  files, while `pyproject.toml` requires `>=3.12` and mypy is pinned to 3.12.
- **L-5 (scope, not a defect)** — `compat/urls.py` mounts a `re_path(r"^.*$", views.not_found)`
  catch-all at `/api/v1/`, and `ingest/` has no `views.py` or `urls.py`, so
  `/api/v1/alerts/webhook/{source_id}` returns 404 and `store_ingested_alert` has zero production
  callers. This is disclosed and accepted at `tests/conformance/test_ingest_pipeline.py:17-18`
  ("Phase 4 work and deliberately does not exist yet"). Recorded so the C3/C4 verdicts are read as
  statements about the function, not about a reachable endpoint.

---

## 4. What was verified clean

- **C1** — save order `[1..5]`; `bulk_create` `[1..7]` in-memory and DB; mixed explicit+auto
  `[1,2,3,101]`; explicit values preserved; updates do not reallocate; `number` initialises to `None`.
- **C2/C4 plans** — `ar_pending_idx` DDL is
  `CREATE INDEX ... ("created_at") WHERE "status" = 'Pending'`;
  `WHERE status='Pending' ORDER BY created_at LIMIT 100` → `SCAN automation_run USING INDEX ar_pending_idx`;
  the Success variant falls back to `SCAN automation_run` + temp B-tree. `celery_task_id`, `case`+
  `started_at`, and `triggered_by_observable_id` all use their indexes. C4 window query →
  `SEARCH alert USING INDEX alert_corrkey_date_idx (correlation_key=? AND date>? AND date<?)`.
- **C3** — two ref-less payloads → two alerts, refs `sha256:fbf664...` and `sha256:10ed887...`; replay
  → `created=False`, same pk; reordered replay → idempotent; `source_ref_missing` warning persisted;
  mapped ref wins and is stored verbatim; two wire sources stay distinct.
- **H2** — fresh SQLite migration applies all 20 application migrations, including
  `cases.0005_case_number_sequence`. Its `create/drop` functions were called directly with a SQLite
  editor: clean no-op, no sequence object created (correct vendor guard).
- **H3/H4** — two 5000-char values coexist; `max_length=64`, DDL `varchar(64) NOT NULL`;
  `(data_type_id, data_hash)` unique; hash case-folding on; `file` case-sensitive;
  `CASE_SENSITIVE_TYPES = ('file',)`.
- **M2** — every `db_index=False` is left-prefix-covered except the two in H-5.
- **M5** — reverse-direction indexes present: `case_status_start_idx(status, -start_date)`,
  `ar_case_started_idx(case, -started_at)`, `alert_corrkey_date_idx(correlation_key, date)`.
- **M10** — with analyst rows present, including rows deliberately **sharing a seeded name**
  (`Contained`, `Imported`, `hash`), all 10 reversal/re-apply steps produced **zero row change**. Final
  state byte-identical to baseline. No data loss.
- **M14** — `playbook_id` non-null, `playbook_name` snapshot survives, deleting a referenced `Playbook`
  raises `ProtectedError`.
- **M6** — no bare `"case"` table references in code or migrations; the table is `case_record`
  throughout.
- `ruff check .` clean; `ruff format --check .` → 122 files formatted; `manage.py check` clean;
  `makemigrations --check --dry-run` clean; `pip check` clean (48 runtime pins vs 96 installed —
  healthy closure).

---

## 5. SQLite limitations — and the PostgreSQL SQL that closes them

Everything above is SQLite. These claims are **not** verified and must be run on Postgres:

| SQLite could not test | Why |
|---|---|
| Sequence allocation under concurrency | SQLite has no `CREATE SEQUENCE`; `MAX()+1` path taken instead |
| Partial-index predicate under real planner cost | SQLite accepted the plan, but that is not a Postgres cost model |
| Btree 2704-byte tuple limit | SQLite has no per-tuple size limit; 5000-char values are free here |
| `INCLUDE` covering indexes | Not emitted on SQLite |
| JSONB operator classes / GIN | `JSONField` is TEXT on SQLite |
| `timestamptz`, interval semantics, `NULLS FIRST` on `DESC` | SQLite `datetime` is naive text |
| Collations / locale-aware `ILIKE` | SQLite default binary collation |
| `EXPLAIN (ANALYZE, BUFFERS)` real I/O | SQLite reports pages, not shared-buffer hits |
| Unique-constraint-as-index introspection | Django's SQLite `indexes()` omits unique constraints |

Run against Postgres:

```sql
-- H-6: sequence not advanced by explicit-number inserts. Expect failure.
BEGIN;
INSERT INTO case_record (id, number, title, status_id, severity, start_date, created_at, updated_at)
VALUES (gen_random_uuid(), nextval('case_number_seq'), 'auto', <closed_status_id>, 3, now(), now(), now());
-- Then, on a migrated DB with explicit numbers already present:
SELECT number FROM case_record ORDER BY number DESC LIMIT 1;
SELECT last_value, is_called FROM case_number_seq;
-- last_value < max(number)  =>  the bug is live. Fix:
SELECT setval('case_number_seq', GREATEST((SELECT last_value FROM case_number_seq),
                                         (SELECT COALESCE(MAX(number),0) FROM case_record)));

-- C2: partial index exists and the planner uses it.
SELECT indexname, indexdef FROM pg_indexes
 WHERE tablename = 'automation_run' AND indexname = 'ar_pending_idx';
EXPLAIN (ANALYZE, BUFFERS, COSTS)
SELECT * FROM automation_run
 WHERE status = 'Pending' ORDER BY created_at LIMIT 100;
-- want: Index Scan using ar_pending_idx, zero heap fetches, no Sort node.

-- C2: prove the predicate. Run this, expect an ERROR (index has no such entry).
EXPLAIN (ANALYZE) SELECT * FROM automation_run WHERE created_at > now() - interval '1 day';

-- C4: composite index serves the correlation window.
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM alert
 WHERE correlation_key = 'dest_ip:1.2.3.4'
   AND date >  now() - interval '10 minutes'
   AND date <= now();
-- want: Index Scan on alert_corrkey_date_idx, correlation_key=? AND date>? AND date<?.

-- H-3: the btree tuple limit SQLite cannot express.
INSERT INTO observable (id, data_type_id, data, normalized_data, data_hash, created_at, updated_at)
SELECT gen_random_uuid(), <hash_type_id>, repeat('A', 5000), repeat('a', 5000),
       repeat('d', 64), now(), now()
 WHERE NOT EXISTS (SELECT 1 FROM observable WHERE data_type_id = <hash_type_id>);
-- ERROR: index row size 5216 exceeds btree version 4 maximum 2704 for index
--   "uniq_obs_dtype_hash". Confirm and size the real index:
SELECT pg_size_pretty(pg_relation_size('uniq_obs_dtype_hash'));
SELECT amname, amoptions FROM pg_class c JOIN pg_am a ON a.oid = c.relam
 WHERE c.relname = 'uniq_obs_dtype_hash';

-- H-2: confirm the six CHECKs are real constraints, not merely named.
SELECT conrelid::regclass AS tbl, conname, pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE contype = 'c'
   AND conname IN ('case_status_stage_valid','alert_status_stage_valid','alert_tlp_range',
                   'alert_pap_range','observable_pap_range','ingestion_source_severity_range')
 ORDER BY 1,2;
-- Each definition must enumerate the full allowed-value set, not `stage <> ''`.

-- H-5: the dropped index. Expect seq_scan on custom_field_id alone.
EXPLAIN (ANALYZE, BUFFERS)
SELECT case_id, value FROM case_custom_field_value WHERE custom_field_id = <uuid>;
EXPLAIN (ANALYZE, BUFFERS)
SELECT case_id, value FROM alert_custom_field_value WHERE custom_field_id = <uuid>;
-- Fix: ALTER TABLE case_custom_field_value ADD COLUMN ...;
--   CREATE INDEX cfv_custom_field_idx ON case_custom_field_value (custom_field_id);
--   CREATE INDEX acfv_custom_field_idx ON alert_custom_field_value (custom_field_id);

-- C-2: L1: show that the severity-only index is never chosen.
EXPLAIN (ANALYZE) SELECT * FROM case_record WHERE severity = 3 ORDER BY start_date DESC LIMIT 50;

-- L-3: on Postgres the prefix guard becomes meaningful; list real indexes per table.
SELECT tablename, indexname, indexdef FROM pg_indexes
 WHERE schemaname='public'
   AND tablename IN ('case_custom_field_value','alert_custom_field_value','automation_run','case_record')
 ORDER BY tablename, indexname;

-- Concurrency proof for C1 on Postgres (run with N parallel sessions).
-- All N must succeed with distinct numbers; any duplicate key error is a failure.
SELECT number FROM case_record ORDER BY number;
```

---

## 6. Required before re-gate

1. Fix C-1: make the mutation-guard mutate real DDL, or mutate `_meta.constraints` before asserting.
   Add a guard for `CheckConstraint` removal.
2. Fix C-2: pin the complete `{field: on_delete}` mapping and explicitly reject `CASCADE`.
3. Fix H-1, H-2, H-3, H-4: add behavioural tests that fail when the predicate / CHECK / unique key is
   removed or neutered.
4. Fix H-5: restore `db_index=True` on both `custom_field` FKs.
5. Fix H-6: `setval` the sequence when an explicit number is supplied; add the concurrency test.
6. Fix H-7: make the `0003` backfill call `canonical_value()`, or make the runtime hash raw.
7. Add tests for M-1 (`correlation_key` derivation), M-2, M-3, M-4, M-5.
8. Reconcile M-6: either reach 80% or lower `fail_under` to the measured value so the gate is honest.
9. Reconcile M-9: pin a mypy invocation in CI and either fix or explicitly waive the 93 errors.