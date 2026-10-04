# REVIEW-2026-10-03: Phase 3 Schema & Index Gate (`db-postgres`)

Date: 2026-10-03
Gate: Phase 3 → Phase 4 (per `BRIEF-2026-10-03-phases-1-6.md` §4)
Reviewer: `db-postgres`
Reviewed: `core/models.py`, `identity/models.py`, `cases/models.py`, `alerts/models.py`,
`observables/models.py`, `ingest/models.py`, `automation/models.py` + all migrations

## Verdict: **FAIL** — 4 Critical, 6 High, 14 Medium, 6 Low

Phase 4 must not start. Four blocking items, detailed below. Plan amendments were applied by the
planner on 2026-10-03 (see plan §6.1, §6.3, §12 R10–R12, §13 #10–#11).

---

## Meta-finding first: the schema AC tests could not fail (H1)

This is why C1–C4 shipped. Four of the Phase 3 acceptance criteria were "verified" by tests that
pass vacuously:

| AC | Test | Problem |
|---|---|---|
| AC3.2 | `test_introspection.py` | model-level, not schema-level; covers 2 of 19 entities. `assert f in fields or any(...)` is a tautology. |
| AC3.4 | `test_fk_audit.py` | **vacuous** — `rf.on_delete is not None` is always true (`on_delete` is a required FK arg), and `rf.get_accessor_name() is not None` falls back to the model name when `related_name` is unset. Setting `related_name=None` — the exact AC3.4 violation — still passes. |
| AC3.5 | `test_indexes.py` | **vacuous** — asserts `len(tables) > 0`. Passes with 32 tables and *zero* §6.3 indexes. |
| AC3.6 | `test_phase3_schema.py::test_all_pks_are_uuid` | body is literally `pass`. |

`test_integrity.py` is the **only** non-vacuous schema test in the suite.

**Lesson for the workflow:** a passing AC is only evidence if the test can fail. Every future AC needs
its failure mode demonstrated (mutation check) before we accept it.

---

## Validation of the planner's pre-reported findings

| # | Reported finding | Outcome |
|---|---|---|
| 1 | `IntegerField` initialises to `""`, so `if self.number is None` is never true | **REFUTED.** `IntegerField.empty_strings_allowed = False`, so `get_default()` returns `None`. The guard *does* fire. mypy's `unreachable` is a `django-stubs` artifact (stubs type `number` as `int`). |
| 2 | SQLite coerces the empty string; Postgres would reject | **REFUTED, and worse.** There is no empty string. Because the guard fires, `CREATE SEQUENCE` — which is not SQLite syntax — executes on every auto-allocated case and raises `OperationalError: near "SEQUENCE": syntax error`. The suite is green only because the single test that builds a `Case` hardcodes `number=1`; the auto-allocation path has **never been executed**. |
| 3 | `MAX(number)+1` is not concurrency-safe | **CONFIRMED.** Plan §6.3 requires a Postgres sequence. Not met. |
| 4 | `CREATE SEQUENCE ... case_number_seq` created but never used | **CONFIRMED.** Dead code on PG; a syntax error on SQLite. |

R4's SQLite/Postgres divergence is real but **inverted** from the prediction: SQLite fails *loudly*,
Postgres would fail *silently* under concurrency.

---

## CRITICAL

### C1 — `Case.save()` auto-allocation crashes; case creation is effectively broken
`cases/models.py:85-94`. `CREATE SEQUENCE IF NOT EXISTS case_number_seq` is not valid SQLite.

Evidence: `OperationalError: near "SEQUENCE": syntax error`. Grep finds exactly one `Case`
construction site in the repo, hardcoding `number=1`.

**Impact:** AGENTS.md §4.2 escalation and §4.4 automation both create cases. Phase 5's
`POST /alert/{id}/merge/{caseId}` and AC5.1 are unimplementable. The MVP loop cannot close.

**Fix:** vendor the numbering into `cases/numbering.py`; branch on `connection.vendor`, use
`SELECT nextval('case_number_seq')` on Postgres and `MAX()+1` on SQLite (single-connection in dev,
cannot race); create the sequence in a vendor-guarded `RunPython` migration; add
`editable=False` to `number`. Note `bulk_create()` bypasses `save()` — cover it with a test or set
the number in the service layer instead.

### C2 — `AutomationRun.status` unindexed: pending-run dispatch is a seq scan + sort
`EXPLAIN QUERY PLAN` → `SCAN automation_run` / `USE TEMP B-TREE FOR ORDER BY`. A Celery beat tick
every N seconds over a monotonically growing table is a guaranteed full scan in production.

**This is a plan gap, not just an implementation gap** — §6.3 omits it. Plan amended.

Fix: partial index `models.Index(fields=["created_at"], name="ar_pending_idx", condition=Q(status="Pending"))`
— supported on **both** SQLite and Postgres. Plus index `celery_task_id`, and
`Playbook(trigger_event, is_active)` for trigger resolution.

### C3 — `Alert.source_ref` NOT NULL with no default: an unconfigured source can ingest **exactly one alert, ever**
Second alert with an empty `sourceRef` → `IntegrityError: UNIQUE constraint failed: alert.source, alert.type, alert.source_ref`.

**Impact:** AGENTS.md Module A is the schema-agnostic receiver — it exists to accept unknown payload
shapes. AC4.3 ("unconfigured source ingests without error, never a 500") and AC4.4 ("replay is
idempotent") are both structurally impossible. The gossamer feed is the product's front door.

Fix: keep the unconditional unique constraint (correct shape) and make the pipeline always populate a
ref — synthesise `"sha256:<digest of canonical payload>"` when the mapping yields nothing, and record
the fallback in `ingestion_warnings`.

### C4 — The correlation hot path is unindexable: there is no column to index
AC5.2's query compiles to JSON extraction from `raw_payload` with no usable index:
`SCAN alert` + temp b-tree. Plan §6.1 gives `Alert` no correlation attribute at all.

**This is a plan gap.** Plan amended.

Fix: promote to a real, portable, indexable column — `correlation_key` (namespaced, e.g.
`dest_ip:10.0.0.5`) driven by an `IngestionSource.mapping_config` rule. Query becomes
`filter(correlation_key=key, date__range=(t-10min, t))`, index-served on both engines.

---

## HIGH

- **H1** — Vacuous schema AC tests (see meta-finding). Replace with four real introspecting tests:
  per-table column/nullability/type assertions driven off a declarative dict; FK audit without the
  `get_accessor_name()` escape hatch; index assertions on **column tuples**, not names; a real UUID
  PK loop over all 19 models.
- **H2** — `manage.py` defaults `DJANGO_SETTINGS_MODULE` to the empty `amalthea.settings` package, so
  `migrate`/`sqlmigrate` fail with `settings.DATABASES is improperly configured`. `check` and
  `makemigrations --check` pass only because neither touches the DB. **AC3.1 and AC1.6 are currently
  unverifiable.** Fix: default to `amalthea.settings.dev`.
- **H3** — `Observable.normalized_data` is an unbounded `TextField` inside a unique btree index. Two
  Postgres-only failure modes invisible on SQLite: (a) btree tuple cap ~2704 bytes, so a long URL or
  UNC path fails the INSERT on prod only; (b) on a non-`C` collation the index compare becomes
  collation-aware, so `C:\Temp\A.txt` and `c:\temp\a.txt` collide for case-sensitive types —
  silently contradicting AC5.4. Fix: add `data_hash` (sha256 hex) and make the unique constraint
  `(data_type, data_hash)`; tuple becomes 80 bytes and collation stops mattering.
- **H4** — `observables/migrations/0002_seed.py` marks `hash` as **case-sensitive**, which inverts
  AC5.4 ("case hashes normalize to lowercase; a case-sensitive type does not") and leaves no
  correctly-seeded case-sensitive type to test against. Fix: `is_case_sensitive = (t in ("file",))`.
- **H5** — No `choices` or `CheckConstraint` on any enum field: `severity`, `tlp`, `pap`,
  `Task.status`, `AutomationRun.status`, `CaseStatus.stage`, `AlertStatus.stage`. AC2.5's
  out-of-range → 400 has no implementation, because no DRF serializers exist yet. A typo'd
  `stage="inprogres"` is accepted at every layer. Fix: `choices` **and** `CheckConstraint` (both
  backends enforce CHECKs, so this is testable on SQLite).
- **H6** — `User.id`/`ApiKey.id` are `BigAutoField`, violating ADR-002 §D3 (UUID internally).
  `User.login` is `blank=True, unique=False` and never populated, yet TheHive keys `assignee` on
  `login` — assignee resolution is ambiguous by construction, and ADR-002 §D11 promises to auto-create
  unknown assignee logins. `ApiKey.prefix` is not unique, so the auth lookup can return two rows.

---

## MEDIUM (selected)

- **M1** — Seven redundant btree indexes. `ForeignKey.db_index=True` by default means **all 31 FK
  columns are already indexed**; four hand-written indexes duplicate an implicit or unique index, and
  three more are left-prefix-covered by composite indexes. Pure write amplification on hot tables.
- **M2** — `Alert.source_id` FK generates the column **`source_id_id`**. Every raw query in the
  correlation engine will trip on this. Rename to `ingestion_source` or set `db_column="source_id"`.
- **M3** — `Alert.source` (wire string) and `Alert.source_id` (FK) are two sources of truth with
  nothing keeping them consistent. Document the split; do **not** derive one from the other — the wire
  value is attacker-supplied and must be preserved verbatim.
- **M4** — Custom-field value tables have **no** `(parent, custom_field)` uniqueness, so a retried
  write silently produces duplicate rows.
- **M5** — Module C's reverse fan-out (`observable → cases`) works only via the implicit FK index.
  Declare it explicitly so AC3.5 has something to assert and a future `db_index=False` refactor cannot
  silently break AGENTS.md Module C.
- **M6** — `db_table = "case"` is a reserved SQL word. Django quotes it, but the correlation engine
  and the Phase 8 query engine will write raw SQL. Rename to `case_record`.
- **M7** — `CaseObservable.created_at`/`AlertObservable.created_at` shadow the abstract base field.
- **M8** — `Organisation` has no `db_table`; `ApiKey`'s is `identity_apikey` (only non-snake_case
  table name). Breaks the "grep `db_table`" reflex.
- **M9** — Timeline keyset pagination on `date` alone is non-deterministic under ties; automation
  events can share microsecond precision. Use `(case_id, -date, -id)`.
- **M10** — Seed reverse migrations delete by **value** (`.filter(value__in=[...])`). Since ADR-002
  §D4 makes custom statuses legal, a rollback would destroy an analyst's row named `Closed`. Filter on
  a seed marker, or declare the reverse a no-op.
- **M11** — `sqlmigrate` cannot render `RunPython` seeds; add a test asserting the seeded row set.
- **M12** — `compat/enums.py:stage_from_alert_status` has an unreachable branch: the schema seeds
  `AlertStatus("Imported", stage="Imported")` but the mapper can never emit `stage="Imported"`.
  Schema↔compat contract mismatch that surfaces in Phase 5 the moment `import/{caseId}` is called.
- **M13** — ADR-002 §D5 promised per-link `tags` and `is-IOC` on the link tables; only `added_by` was
  implemented. Recommend **not** adding per-link `is_ioc` (incoherent on a globally-deduped entity);
  per-link `tags` are worth adding. Recorded as plan §13 #11.
- **M14** — `AutomationRun.playbook_name` is free text with no FK; a run can reference a nonexistent
  playbook and the name drifts on rename.

## LOW (selected)

L1 no CHECK constraints even after H5 · L2 `ApiKey.prefix` not unique · L3 §6.3's GIN indexes on
`tags`/`raw_payload` do not exist and cannot be expressed portably (needs `django.contrib.postgres`);
plan amended to record the deferral · L4 `Case.closed_date` never set on transition to Closed ·
L5 `Case.start_date`/`Alert.date` have no defaults, and on Postgres `ORDER BY start_date DESC` returns
NULLs **first**, so undated cases would top the queue · L6 the alert unique constraint is ~1820 bytes
worst case, under the 2704-byte cap but with little headroom.

---

## What the schema got right

Verified correct and worth keeping: all 19 domain tables use `UUIDModel`; all 31 FKs have explicit
`on_delete` **and** `related_name`; `on_delete` semantics are sound (`PROTECT` on status/data_type so
cases cannot be orphaned, `SET_NULL` on `Alert.case` to match TheHive's unlink-on-delete, `CASCADE` on
case-owned children); `test_integrity.py` genuinely raises `IntegrityError` for both uniqueness
constraints; `USE_TZ=True` + `TIME_ZONE="UTC"` gives real `timestamptz`; all 10 `JSONField`s map to
`jsonb` with safe `default=dict`; `Task` in `db_table="task"` inside the `cases` app is correct per
brief §1; migrations contain no destructive operation and the `0001`/`0002` split is Django's standard
circular-FK disambiguation; seeds are idempotent via `get_or_create` and contain no raw SQL.

Hot paths confirmed index-served via `EXPLAIN QUERY PLAN`: alert queue, case by number/UUID, timeline
newest-first, and observable lookup **and** fan-out (Module C's graph query).

---

## Postgres-only features (why SQLite cannot validate this)

| Feature | SQLite | Postgres | Needed for |
|---|---|---|---|
| GIN index (`raw_payload`, tags) | ✗ | ✓ | §6.3, T2 hunting |
| `INCLUDE` covering index | ✗ | ✓ | large queues |
| `jsonb` + `@>` operators | ✗ (TEXT + `JSON_EXTRACT`) | ✓ | correlation, query engine |
| `timestamptz` | ✗ (naive ISO text) | ✓ | every timestamp |
| `SELECT nextval()` | ✗ | ✓ | `Case.number` (C1) |
| `COLLATE "C"` | n/a | ✓ | observable dedupe (H3) |
| btree 2704-byte tuple limit | none | **enforced** | H3 |
| collation-aware index compare | BINARY | locale | H3 |
| `DESC` in index DDL | **silently dropped** | ✓ | any dev test relying on descending index order proves nothing |

---

## Must be verified on real Postgres

Blocked locally: Docker daemon down, no local Postgres. Once `docker-compose up -d postgres`:

```sql
-- types are what the plan claims (expect 0 'json', 0 non-timestamptz, all ids uuid)
SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns
WHERE table_schema='public' AND (data_type IN ('jsonb','timestamp with time zone') OR column_name='id')
ORDER BY table_name, ordinal_position;

-- H3: reproduce the btree ceiling + collation trap (expect ERROR now)
INSERT INTO observable (...) SELECT gen_random_uuid(), now(), now(), repeat('A',5000), repeat('a',5000),
  (SELECT id FROM observable_type WHERE name='other'), false,false,false,'',2,2,'{}'::jsonb,false;

-- C1: sequence monotonic under concurrency (20 parallel inserts -> 20 distinct numbers, 0 IntegrityError)
SELECT nextval('case_number_seq');

-- C2 / C4: the two currently-unserved hot paths must switch to SEARCH after the fix
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM automation_run WHERE status='Pending' ORDER BY created_at LIMIT 100;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM alert WHERE correlation_key='dest_ip:10.0.0.5'
  AND date BETWEEN now() - interval '10 minutes' AND now();

-- index usage after seeding load
SELECT relname, indexrelname, idx_scan FROM pg_stat_user_indexes ORDER BY idx_scan;
```

AC3.7 control: `DJANGO_SETTINGS_MODULE=amalthea.settings.test pytest -q` green **on Postgres**.

---

## Fix order for `django-backend`

1. **C1** `cases/models.py:85-94` + vendor-guarded sequence migration + `editable=False` + regression
   test (including `bulk_create`). *Unblocks everything downstream.*
2. **H2** `manage.py` default settings module; then re-run AC3.1 honestly.
3. **H1** replace the four vacuous tests with real introspecting ones. *This is the control that let
   C1–C4 through — fix it before the next wave, not after.*
4. **C3** `source_ref` digest fallback + the AC4.3/AC4.4 tests. *Unblocks Phase 4.*
5. **H5** `choices` + CHECK constraints; **M1** drop 7 redundant indexes; **M2/M3** `source_id` naming
   and the source/ingestion_source docstring.
6. **C2, C4, H3, H4** index and key work gating Phases 5–6.
7. **M5–M14, L1–L6** cleanup.

H3 must land **before any production data exists**: a long observable can only ever fail on Postgres,
and would surface as an unexplained 500 on an analyst's paste.
