# Phase 3 Schema/Index Gate — Adversarial Re-Review (Round 3)

**Target:** `bda16e5` (round-2 remediation of `docs/reviews/REVIEW-2026-10-04-phase3-schema-gate-round2.md`)
**Remediation diff reviewed:** `616a017..bda16e5` — 27 files, +2569 / −151
**Environment:** SQLite only, Django 5.2.17 / Python 3.14.6, `.venv/bin/python`
**Date:** 2026-10-05

---

## 1. Verdict

# FAIL

Every one of the eight round-2 items in remediation scope (C-2, H-1, H-3, H-4, H-5, H-6, H-7, L-2)
is genuinely closed. The C-1 evidence defect did not recur: the 15 mutation guards in
`test_schema_mutation_guards.py` all land real DDL changes and all produce concrete failure text
(§5). The suite is materially stronger than round 2.

It still fails, on two findings this round executed to ground truth:

* **H3-1 (High)** — `ObservableType.is_case_sensitive` feeds `Observable.data_hash`, but **no code
  path observes a change to it**. Flipping the flag leaves every stored digest stale, and
  `uniq_obs_dtype_hash` then admits **two rows for one artifact** — the exact outcome the
  constraint exists to prevent. This is round-2 H-7/M-5's defect one level up, and it is reachable
  through an operation ADR-002 §D4 explicitly supports.
* **H3-2 (High)** — the two hand-adjusted migration dependency pins are **load-bearing only for the
  seed-rollback tests**, and the comments justifying them are **factually wrong** about both the
  provenance of the FK changes and the mechanism of the failure they claim to prevent. Proven by
  A/B experiment: the committed pin and the autodetector's pin unapply the *same* 13 migrations and
  drop `case_record` in *both* cases; only the test teardown differs.

Two round-2 Mediums outside the remediation scope are still open (M-1, M-6). One new Medium was
found in the R11 meta-enforcement itself.

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 2 |
| Medium | 3 |
| Low | 3 |
| **Total** | **8** |

Baseline gate reproduced: `make check` → **✓ check passed** — ruff clean, ruff format clean,
`mypy` `Success: no issues found in 73 source files`, Django check 0 issues,
`makemigrations --check` `No changes detected`, **286 passed, 1 skipped in 4.18s**. Working tree
clean before and after this review.

---

## 2. Round-2 item status

Every round-2 finding, re-tested. "Executed" means I ran the mutation myself and watched the
assertion fail; it does not mean I read the test and believed it.

| Item | Round-2 severity | Status | Basis |
|---|---|---|---|
| **C-2** FK `on_delete` audit one-sided | Critical | **CLOSED** | Executed mutations 7/8/9/10 — SET_NULL→CASCADE, CASCADE→SET_NULL, PROTECT→CASCADE, plus a behavioural row-count test. All four detected; concrete diffs printed (§5). |
| **H-1** `ar_pending_idx` predicate unguarded | High | **CLOSED** | Executed mutation 15: index re-created without `condition=`, `assert_ddl_delta(missing=["WHERE"])` fired, `test_the_pending_run_index_predicate_is_still_partial` raised on the real DDL. |
| **H-2** six CHECK constraints name-only | High | **CLOSED (as H-5)** | Re-audited as H-5 below. |
| **H-3** `idempotency_key` uniqueness untested | High | **CLOSED** | Executed mutation 13: `detach_field_unique` + `assert_ddl_delta(missing=["UNIQUE"])`, then `Failed: DID NOT RAISE IntegrityError`. |
| **H-4** `IngestionSource.slug` uniqueness untested | High | **CLOSED** | Executed mutation 14, same shape, detected. |
| **H-5** M1 over-pruning dropped needed indexes | High | **CLOSED** | Both restored FK indexes present in live DDL (`alert_custom_field_value_custom_field_id_e571a35f`, `case_custom_field_value_custom_field_id_1edb0ff9`). |
| **H-6** Postgres sequence not advanced | High | **CLOSED on SQLite; UNVERIFIABLE on Postgres** | Correctly gated `skipif`, with a precise reason. Cannot be settled here. See §6 — and a race in the fix SQL is listed there. |
| **H-7** backfill used a different hash function | High | **CLOSED** | `observables/migrations/0003_observable_data_hash.py:63` now calls `canonical_value()`/`data_hash()`. Mechanism pinned (`test_h7_the_backfill_calls_the_runtime_canonicalisation`) **and** behaviourally reproduced/repaired (`test_h7_a_backfilled_row_...`). |
| **L-2** redundant M2M single-column indexes | Low | **CLOSED** | `sqlite_master` index rows for `case_record_tags`, `alert_tags`, `observable_tags` contain no prefix-covered index — only the `*_tag_id` reverse-direction index the unique composite does not cover. |
| **M-1** `correlation_key` never derived | Medium | **STILL OPEN** | See H3-4. |
| **M-2** `stamp_closed_date` untested | Medium | **CLOSED** | `tests/conformance/test_case_numbering.py:298+`. |
| **M-3** `stage_from_alert_status` untested | Medium | **CLOSED** | `tests/conformance/test_time_enums.py:28+`, parametrised over every seeded status. |
| **M-4** contradictory numbering docstrings | Medium | **CLOSED** | `cases/numbering.py:28-33` now states `pre_save` is *not* sufficient and why. |
| **M-5** `DataHashField` skipped by `update_fields` | Medium | **CLOSED, NEW GAP ABOVE IT** | `observables/models.py:99-103` widens `update_fields`. Covered by 4 tests. But see **H3-1**. |
| **M-6** coverage gate red | Medium | **STILL OPEN (deferred by decision)** | See H3-5. |
| **M-7** seed test replans whole graph backwards | Medium | **STILL TRUE — and now quantified** | See **H3-2**: 13 migrations across 4 apps. |
| **M-8** dead docstring references | Medium | **CLOSED** | `test_enum_constraints` and `tests/unit/` no longer referenced anywhere. |
| **M-9** "mypy 0 errors" not reproducible | Medium | **CLOSED** | `Makefile:44` pins the invocation; `mypy` scope fixed at 73 files in `pyproject.toml`. |
| **L-1** `case_severity_idx` single-column on 4 values | Low | **STILL OPEN** | `cases/models.py:125`, live DDL `CREATE INDEX "case_severity_idx" ON "case_record" ("severity" DESC)`. |
| **L-3** prefix-coverage guard engine-dependent | Low | **STILL OPEN (latent)** | Unchanged; matters only on Postgres, which is unavailable. |
| **L-4** `lock.sh` bakes machine-specific Python | Low | **STILL OPEN** | `scripts/lock.sh:38` writes `# Python 3.14.6`; `pyproject.toml:5` says `requires-python = ">=3.12"`. |
| **L-5** webhook endpoint is Phase 4 scope | Low (scope) | **Acknowledged, disclosed** | Not a defect at this gate. |

---

## 3. Findings

### HIGH

#### H3-1 — `ObservableType.is_case_sensitive` is not in any digest-recompute path; flipping it defeats `uniq_obs_dtype_hash`

`observables/models.py:27` defines `is_case_sensitive`. `observables/hashing.py:47-63`
(`canonical_value`) reads it to decide whether to case-fold. `observables/hashing.py:83-85`
(`DataHashField.pre_save`) recomputes the digest — from the **instance**. `observables/models.py:99-103`
widens `update_fields` only when the **instance's** `normalized_data` or `data_type` is touched.

So the derivation of `Observable.data_hash` depends on a column of a **different table**, and nothing
observes that column. There is no `post_save` receiver on `ObservableType` (grep: zero receivers in
`observables/`, `cases/`, `automation/`, `alerts/`, `ingest/`, `identity/`), no trigger, and no data
migration.

ADR-002 §D4 makes this vocabulary **user-extensible** precisely so a SOC can add a type "without a
schema migration". Correcting a type definition is therefore a supported operation, and the schema
layer offers no way to keep the derived column consistent with it.

**Executed.** Seeded `file` type (`is_case_sensitive=True`), one observable `C:\Temp\A.txt`, then
`ObservableType.objects.filter(pk=...).update(is_case_sensitive=False)` — the ordinary ORM write:

```
[type=file True->False]
  stored  =71efc45866c9d2e1
  runtime =f1b5315a2cd7ea78   STALE=True
  two rows for one artifact ADMITTED -> YES
```

The stored digest still describes the *old* definition. Inserting the other spelling
`c:\temp\a.txt` hashes to `f1b5315a…` under the new definition, which does not collide with the
stale `71efc458…`, so **both rows are accepted** for one artifact.

The reverse direction (`url`, `False → True`) leaves the digest stale too but happens to still
reject the duplicate, because the stale value coincides with the incoming row's new digest. The
defect is direction-dependent, which is exactly why it would survive casual testing.

**Why this matters more than round-2 M-5.** M-5 was `update_fields` skipping a recomputed value on
the row itself; it is fixed and well covered. H3-1 is the same class of stale-derived-state defect,
one level out, on the one field whose semantics ADR-002 declares user-editable. Round-2 H-7 was
raised for precisely this divergence between what a row is *keyed on* and what it *means*; H3-1
re-opens it through a path H-7's fix does not cover.

**Test that would close it** (behavioural, not a schema mutation):

```python
@pytest.mark.django_db
def test_changing_a_type_case_sensitivity_rehashes_its_observables():
    t = ObservableType.objects.get(name="file")
    Observable.objects.create(
        data_type=t, data="C:\\Temp\\A.txt", normalized_data="C:\\Temp\\A.txt"
    )
    t.is_case_sensitive = False
    t.save()
    assert not _duplicate_admitted(t, "c:\\temp\\a.txt"), (
        "flipping ObservableType.is_case_sensitive left every data_hash stale, so "
        "uniq_obs_dtype_hash admitted two rows for one artifact"
    )
```

Either a recompute on the flag's change or an explicit documented refusal to allow the flag to
change while rows exist would close it.

---

#### H3-2 — Two migration dependency pins exist to keep a test green, and their justifying comments are factually wrong

Three claims in the comments are wrong. All three were tested.

**(a) Provenance is misattributed.** `alerts/migrations/0008_alter_alertobservable_alert_and_more.py:7-8`:

> The two `AlertObservable` FKs were altered by REVIEW-2026-10-04 **L-2** in `observables/0004`

`observables/migrations/0004_observabletaglink_alter_observable_tags.py` operates on
`ObservableTagLink` and `Observable.tags` only (`model_name='observabletaglink'` at lines 32/82/87,
`model_name='observable'`, `name='tags'` at 66/70). It does not touch `AlertObservable`.

**(b) The cascade-prevention claim is disproved.** `alerts/0008:11-17`:

> It deliberately does **not** depend on `observables/0004`. […] the extra unapply cascaded into
> `cases`/`alerts`, leaving `alert` pointing at a `case_record` table the rollback had already
> renamed away

**Executed A/B.** Same commit, only `alerts/0008`'s `observables` dependency differs
(`0002_seed` as committed vs `0004_…` as the autodetector emits). `migrate observables
0001_initial` on a fresh fully-migrated database:

| | committed pin (`0002_seed`) | autodetector pin (`0004`) |
|---|---|---|
| forward `migrate` | ok | ok |
| `makemigrations --check` | `No changes detected` | `No changes detected` |
| migrations unapplied | **13** | **13** |
| which 13 | *identical set* | *identical set* |
| `case_record` after cascade | **dropped** | **dropped** |
| `case_record_tags` after cascade | **dropped** | **dropped** |
| `alert.case_id` REFERENCES | `"case"` | `"case_record"` |
| `DELETE FROM "alert"` with `PRAGMA foreign_keys=ON` | **ok** | **`sqlite3.OperationalError: no such table: main.case_record`** |
| `pytest -q` | 286 passed | **284 passed, 9 errors** |

The unapplied set is identical. `case_record` is destroyed either way. The comment describes a
cascade that the pin does not prevent.

**(c) What the pin actually does.** It changes the *order* in which `alert` is remade during the
rollback. With the `observables/0004` pin, `alert` is not remade after `case_record` is renamed
away, so its FK clause keeps pointing at a dropped table and the corruption is **loud**. Without it,
`alert` is remade and happens to point at `case`, so the corruption is **silent**. The pin converts
a loud failure into a silent one — which is why the suite is green.

Confirmed for the sibling pin at `alerts/migrations/0005_restore_custom_field_fk_indexes.py:17-25`,
which admits the underlying problem outright:

> a path where `observables/0003` and `alerts/0004` already cannot be reversed cleanly

Each pin alone reproduces the 9 errors; both together also produce 9.

**Impact.** Three consequences, in increasing order of seriousness.

1. The comments are the only record of why these migrations are hand-edited. A future migration
   author reading `alerts/0008:5-9` will believe `observables/0004` altered `AlertObservable` FKs,
   and will not know that re-running the autodetector silently reverts a load-bearing edit.
2. `test_seed_migrations.py::test_m10_rolling_back_a_seed_migration_deletes_no_rows[observables-0002_seed]`
   is titled as evidence that rolling back **one seed migration** deletes no rows. In fact it
   unapplies 13 migrations across `alerts`, `automation`, `cases` and `observables`, and leaves the
   database in a mid-history schema state (`case` present, `case_record` gone). Round-2 **M-7**
   flagged this test's fragility; this round quantifies what it actually does. The assertion still
   holds — the rows do survive — but it is evidence about a much larger operation than its name
   suggests.
3. The real defect, `observables/0003` and `alerts/0004` not being cleanly reversible, is
   documented as a known-but-unfixed problem and worked around in the dependency graph rather than
   fixed. `makemigrations --check` cannot detect the drift, because the autodetector only proposes
   *new* migrations.

**What would close it.** Make the reversals clean so no pin is needed — the SQLite table remakes in
`observables/0003` (drop-column on `observable`) and `alerts/0004` are the ones that strand
`case_record` — or rename the tests to state the true blast radius and correct all three comments.
As committed, the comments assert a mechanism that does not exist.

---

### MEDIUM

#### H3-3 — `test_mutation_standard.py` enforces R11 by substring match on the guard's source

`tests/conformance/test_mutation_standard.py:59`:

```python
VERIFIED_MECHANISMS = ("schema_mutation(", "cursor.execute(", "_meta", "index_names(")
```

Line 75 then asks only `any(m in src for m in VERIFIED_MECHANISMS)` — whether the guard's **source
text** contains one of those substrings. `_meta` is in that tuple, and `_meta` appears in almost any
guard that reads a field.

**Executed.** I appended a `test_mutation_97_probe_...` to `test_schema_mutation_guards.py` that
performs **no schema edit at all** — no `schema_mutation`, no `cursor.execute`, no `add_index`, no
`detach_constraint` — and merely reads `AutomationRun._meta.get_field("idempotency_key").unique`:

```
$ pytest "tests/conformance/test_mutation_standard.py::test_every_mutation_guard_verifies_its_own_mutation[test_mutation_97_probe_...]"
1 passed
```

The standard accepted a guard that mutates nothing, because its source mentions `_meta`.

This is round-2 C-1 one level up. C-1 was a guard that mutated nothing while claiming a verified
failure mode; the mechanism built to prevent a recurrence accepts a guard that verifies nothing.
`_meta` is legitimate for genuine metadata mutations (mutations 2 and 4) but is indistinguishable
from an incidental read.

Two further weaknesses in the same file, stated as observations rather than demonstrated defects:

* `SILENT_NOOP_CALLS` (lines 48-55) omits `add_index`, `remove_index`, `alter_field`, `create_model`
  and `delete_model`. `Mutation.add_index`/`replace_index` in `_mutation.py` use precisely
  `editor.add_index`/`remove_index`. Today the parent `connection.schema_editor()` call is caught, so
  the omission is not exploitable inside this tree — but the coverage is accidental (the container is
  flagged, not the operation), so any file that receives a `schema_editor` from elsewhere is
  unchecked.
* `_all_test_files()` (line 63) globs `TESTS_DIR` = `tests/conformance` only. `tests/unit/` and
  `tests/integration/` exist. They currently contain no `test_*.py` files — every test in the repo
  lives in `tests/conformance` — so this is latent, not exploitable.

**Test that would close it.** Require the mechanism to be *invoked*, not mentioned: parse the AST
for a call whose callee resolves to `schema_mutation`, or which is an `ast.Call` on a name in a
`_verified_mutation` decorator registry, and assert that at least one such call exists in the body.

---

#### H3-4 — Round-2 M-1 is still open: `correlation_key` is never derived, so C4's index can never be used

`ingest/models.py:12-18` documents `mapping_config` as holding "the JSON-path rules the Phase 4
mapping engine reads" and, at line 13, "the rule that produces each alert's `correlation_key`
(REVIEW C4)". Repo-wide grep for `mapping_config` outside its own field definition and its migration
returns **3 hits total: 2 in `ingest/models.py` (docstring + field), 1 in its migration**. There is
no reader and no mapping engine.

`ingest/pipeline.py:71` takes `correlation_key: str = ""` and line 104 copies it verbatim.
`alerts/models.py:96` declares `correlation_key = CharField(max_length=256, blank=True, default="")`,
and line 125 declares the `("correlation_key", "date")` index that round 2 confirmed is used correctly
for AC5.2's window query.

The column is therefore always `""` in production. The index is correct; nothing populates the key.
This was **not** in the round-3 remediation scope, so it is not a scope failure — but it is the reason
the C4 verdict must still read as "the index is right, the feature is absent".

---

#### H3-5 — Round-2 M-6 is deferred by decision rather than fixed; the gate is still red

`pyproject.toml:67` sets `fail_under = 80`. Measured:

```
$ pytest -q --cov --cov-report=
Total coverage: 76.36%
FAIL Required test coverage of 80.0% not reached.
exit code 1
```

Up from round-2's 73.45%, but still red, and `pytest --cov` still exits 1. `Makefile:63-66` now makes
this explicit and non-blocking (`|| true`, with the note "fail_under is 80 but coverage is
informational until Phase 6 by decision") — a real improvement over round 2, where the green baseline
was obtained by simply not running coverage.

Still at 0%: `core/events.py` (22 statements — the Module D event surface), `realtime/consumers.py`,
`realtime/publisher.py`, `realtime/routing.py`, and all five `compat/mappers/*`. Round 2 noted that
C2 and C4 both depend on that event layer, and that remains true. Recorded so the gate's green is not
read as covering the orchestration surface.

---

### LOW

#### H3-6 — `TODO.md:183` states `DESC` index DDL is "silently dropped on SQLite"; it is not

`TODO.md:183`:

> collation-aware comparison, and `DESC` in index DDL (**silently dropped on SQLite**, so any dev test …)

Live `sqlite_master` DDL from a fresh `migrate`:

```
alert_created_at_idx    CREATE INDEX ... ON "alert" ("created_at" DESC)
case_created_at_idx     CREATE INDEX ... ON "case_record" ("created_at" DESC)
case_severity_idx       CREATE INDEX ... ON "case_record" ("severity" DESC)
case_status_start_idx   CREATE INDEX ... ON "case_record" ("status_id", "start_date" DESC)
timeline_case_date_idx  CREATE INDEX ... ON "timeline_event" ("case_id", "date" DESC, "id" DESC)
ar_case_started_idx     CREATE INDEX ... ON "automation_run" ("case_id", "started_at" DESC)
alert_status_date_idx   CREATE INDEX ... ON "alert" ("status_id", "date" DESC)
obs_created_at_idx      CREATE INDEX ... ON "observable" ("created_at" DESC)
task_created_at_idx     CREATE INDEX ... ON "task" ("created_at" DESC)
```

Django's SQLite backend emits `DESC` normally. The stale claim is a live instruction to future
readers that any dev-only `DESC` assertion is vacuous when it is not. Round-2 L-3 is the sibling:
`test_no_index_is_left_prefix_covered_by_a_wider_one` is engine-dependent for a *different* reason
(Django's SQLite `indexes()` omits unique constraints), and that one still holds.

#### H3-7 — L-1 stands: `case_severity_idx` indexes a 4-value column to satisfy an index-list assertion

`cases/models.py:125` declares `models.Index(fields=["-severity"], name="case_severity_idx")`, and
`tests/conformance/test_indexes.py:51` requires it. A single-column btree over a 4-value enum will
not be chosen by a Postgres planner to filter a case queue; it costs write amplification on every
`severity` update for a list assertion. Unchanged from round 2 and not in remediation scope.

#### H3-8 — L-4 stands: `scripts/lock.sh` pins a machine-specific interpreter into committed files

`scripts/lock.sh:38` writes the literal header `# Python 3.14.6 | Django 5.2 LTS` into
`requirements/base.txt`, while `pyproject.toml:5` declares `requires-python = ">=3.12"`. The
generated lock files therefore advertise an interpreter narrower than the project supports, and
narrower than the one mypy is pinned to. Unchanged from round 2.

---

## 4. Migration graph — executed

Forward, from an empty database, test settings:

```
$ migrate                                  # 20 application migrations
forward ok
$ makemigrations --check
No changes detected
```

Reverse, one app at a time. `migrate observables 0001_initial` on a fully-migrated database:

```
  cascade unapplied by `migrate observables 0001_initial`: 13
     - alerts       0004_alert_hardening
     - alerts       0005_restore_custom_field_fk_indexes
     - alerts       0006_alerttaglink_alter_alert_tags
     - alerts       0007_alter_alertcustomfieldvalue_alert_and_more
     - alerts       0008_alter_alertobservable_alert_and_more
     - automation   0003_automation_hardening
     - cases        0004_case_hardening
     - cases        0005_case_number_sequence
     - cases        0006_restore_custom_field_fk_indexes
     - cases        0007_casetaglink_alter_case_tags
     - observables  0002_seed
     - observables  0003_observable_data_hash
     - observables  0004_observabletaglink_alter_observable_tags
```

`cases/0002_initial` and `cases/0003_seed` are already unapplied by that point, which is why
`case_record` does not survive: the rollback crosses into `cases/0001_initial`'s era, where the
table was named `case`. This is the blast radius behind **H3-2**.

Two negative results worth recording so they are not re-run:

* `manage.py migrate zero` → `LookupError: No installed app with label 'zero'.` Django's `zero`
  target is only valid as `migrate <app> zero`.
* `MigrationExecutor.migrate(<every app's first migration>)` → `InvalidMigrationPlan: Migration plans
  with both forwards and backwards migrations are not supported.` The targets include third-party
  apps already past their first migration, so the planner mixes directions. Reverse one app at a time
  via `call_command("migrate", app, target)`.

## 5. Mutation evidence

All 15 guards in `tests/conformance/test_schema_mutation_guards.py`, executed with `-s`. Each line
below is the guard's own output — the failure text it required the real assertion to produce.

```
MUTATION-1  ASSERTION: alert is missing required column(s): ['ingestion_source_id']
MUTATION-2  ASSERTION: FK(s) without an explicit related_name … ['automation.models.AutomationRun.playbook']
MUTATION-3  ASSERTION: redundant (prefix-covered) indexes: case_record.case_status_idx_mutated('status_id',)
                        covered by case_status_start_idx('status_id', 'start_date')
MUTATION-4  ASSERTION: `login` must be populated, never nullable
MUTATION-5  : uniq_obs_dtype_hash absent from DDL, duplicate accepted, then restored
MUTATION-6  : observable_tlp_range absent from DDL, tlp=99 accepted, then restored
MUTATION-7  ASSERTION: AssertionError: on_delete policy changed (actual, expected) … {('alerts','Alert','case'): ('CASCADE','SET_NULL')}
MUTATION-8  ASSERTION: AssertionError: on_delete policy changed (actual, expected) … {('cases','Task','case'): ('SET_NULL','CASCADE')}
MUTATION-9  ASSERTION: AssertionError: on_delete policy changed (actual, expected) … {('observables','Observable','data_type'): ('CASCADE','PROTECT')}
MUTATION-10 ASSERTION: DoesNotExist: Alert matching query does not exist.
MUTATION-11 ASSERTION: Failed: DID NOT RAISE IntegrityError      # case_severity_range
MUTATION-12 ASSERTION: Failed: DID NOT RAISE IntegrityError      # alert_pap_range
MUTATION-13 ASSERTION: Failed: DID NOT RAISE IntegrityError      # idempotency_key UNIQUE
MUTATION-14 ASSERTION: Failed: DID NOT RAISE IntegrityError      # slug UNIQUE
MUTATION-15 ASSERTION: AssertionError: ar_pending_idx has no WHERE clause in the database —
                        DDL: 'CREATE INDEX "ar_pending_idx" ON "automation_run" ("created_at")'
15 passed in 1.24s
```

The C-1 defect did not recur. Mutation 5 is the specific case round 2 found vacuous: it uses
`detach_constraint`, which removes the constraint from `_meta` before the schema editor runs, so
`_remake_table` cannot write it back — and then asserts `uniq_obs_dtype_hash` is absent from the DDL
*before* asserting the consequence.

Two additional mutations I ran myself, neither of which the suite provides a guard for:

| Mutation | Verdict |
|---|---|
| Neuter `case_tlp_range` — a CHECK with **no** dedicated guard (only `case_severity_range` and `alert_pap_range` do) | Detected by the parametrised sweep `GRADED_DOMAIN`: `Failed: DID NOT RAISE IntegrityError`. H-5 covers all 9 constraints behaviourally, not just the two with dedicated guards. |
| `test_mutation_standard.py`'s own standard, against a guard that mutates nothing | **Accepted the guard** — see **H3-3**. |

Note on `_detected()` (`test_schema_mutation_guards.py:212-237`): it catches `BaseException` so that
contracts enforced with `pytest.raises(IntegrityError)` register as *detected* rather than being
swallowed as failures, and it re-raises on `Skipped` so a skipped assertion cannot be mistaken for a
caught mutation. Both are the right call and are exercised by mutations 11-14.

## 6. PostgreSQL-only — unverifiable in this environment

PostgreSQL is not available here. Nothing below is claimed as verified. For each item, the test or
SQL that would settle it is named.

> **Closure (2026-10-06, TODO 3.1 gate):** §6 (a)–(e) were all executed against a live
> PostgreSQL 16 (`amalthea/amalthea@127.0.0.1:5432`, `--ds=amalthea.settings.test_pg`) and are
> now **CLOSED** — full suite **408 passed, 24 skipped, 0 failures**; SQLite `make check`
> **429 passed, 3 skipped**. (a)–(d) settled by the passing suite; (b) additionally got a
> mutation-proven race fix; (e) got a new Postgres planner guard. Details per item below.

**(a) H-6's end-to-end collision test is skipped, correctly.**
`tests/conformance/test_case_numbering.py:272` is gated on `connection.vendor != "postgresql"` with a
precise reason: SQLite allocates `MAX(number) + 1`, which reads the row just written and therefore
cannot reproduce the collision. Settled by: running the suite on Postgres (TODO 3.1). The skip reason
names the test to delete as the mutation, which is the right standard.

**(b) `sync_case_number_sequence` is a non-atomic read-modify-write.** `cases/numbering.py:64-67`:

```sql
SELECT setval('case_number_seq',
              GREATEST((SELECT last_value FROM case_number_seq), %(number)s), true)
```

The inner `SELECT last_value` takes only `ACCESS SHARE`; `setval` takes the sequence's row lock
later. Two concurrent imports can therefore walk the sequence **backwards**, which the comment at
lines 59-63 explicitly claims cannot happen:

```
T1: SELECT last_value                      -> 100        (for caller-supplied number=500)
T2: SELECT last_value                      -> 100        (for caller-supplied number=300)
T1: setval('case_number_seq', GREATEST(100,500), true)  -> 500
T2: setval('case_number_seq', GREATEST(100,300), true)  -> 300   -- overwrites 500
```

Afterwards `nextval` hands out 301, 302, … against a table that already contains 500, and the
sequence can re-issue numbers taken by rows written in between. `GREATEST` only protects against
backwards movement relative to the value *it read*, which under concurrency may be stale by an
arbitrary amount. A single-statement fix exists:
`setval('case_number_seq', GREATEST(<subquery in the same statement>, %(number)s), true)` does not
help; the correct form takes the lock before reading, e.g.
`SELECT setval('case_number_seq', GREATEST((SELECT last_value FROM case_number_seq FOR UPDATE), %(number)s), true)`
or `pg_advisory_xact_lock` before the read, or `setval` inside a `LOCK TABLE`-equivalent on the
sequence.

**Not executed — no PostgreSQL in this environment.** Settled by:

```sql
-- session A                          -- session B
BEGIN;                               BEGIN;
SELECT setval('case_number_seq',     SELECT setval('case_number_seq',
  GREATEST((SELECT last_value FROM      GREATEST((SELECT last_value FROM
    case_number_seq), 500), true);       case_number_seq), 300), true);
SELECT last_value FROM case_number_seq;  -- expect 300, not >= 500
ROLLBACK;
```

> **CLOSED (2026-10-06).** Empirically on PostgreSQL 16: `FOR UPDATE` inside the subquery raises
> `ProgrammingError: cannot lock rows in sequence "case_number_seq"`, and `LOCK TABLE
> case_number_seq IN ACCESS EXCLUSIVE MODE` raises `cannot lock relation "case_number_seq"` /
> *not supported for sequences* — so the review's first two suggested forms are impossible and the
> third is the one live. `cases/numbering.py` now wraps the read in
> `pg_advisory_xact_lock(hashtext('case_number_seq'))` inside the same statement. Proof:
> - `test_h6_concurrent_imports_cannot_walk_the_sequence_backwards` (Postgres-only, two
>   transactions racing 500 vs 300; the final position must be 500) fails against the old
>   non-atomic SQL — the race bit in 2/10 runs once a start barrier widened the window, and
>   passes 10/10 with the fix.
> - The lock token is pinned in `test_h6_the_sync_statement_advances_the_sequence_monotonically`
>   (`pg_advisory_xact_lock` must be inside the GREATEST argument subquery), so removing the fix
>   is a deterministic text-level failure in addition to the behavioural race test.

**(c) L-3's prefix-coverage guard is engine-dependent.**
`test_no_index_is_left_prefix_covered_by_a_wider_one` relies on Django's `indexes()`, which omits
unique constraints on SQLite — the exact engine where H-5's dropped indexes did not bite. Settled by
running the suite on Postgres. Unchanged from round 2.

> **CLOSED (2026-10-06):** ran green on PostgreSQL in the full suite (408 pass). The redundant-index
> check is live-engine, so any H-5-style regression is caught on the production engine.

**(d) Unbounded-index-tuple behaviour (H3's original motivation) cannot be exercised.** SQLite has no
btree tuple cap, so `test_two_long_observables_coexist` proves the *bound* (64 hex chars regardless of
input length) and not the original Postgres symptom. Settled by inserting a >2704-byte
`normalized_data` on Postgres.

> **CLOSED (2026-10-06):** `test_two_long_observables_coexist` ran green on PostgreSQL — a 4000+ byte
> `normalized_data` inserts and indexes fine because `uniq_obs_dtype_hash` indexes `data_hash`
> (64 hex chars), never `normalized_data`; the btree cap is structurally avoided, not stretched.

**(e) Partial-index predicate selection is confirmed by DDL only.** `ar_pending_idx`'s DDL is
`CREATE INDEX … ("created_at") WHERE "status" = 'Pending'`, which is correct, but I cannot confirm a
Postgres planner chooses it for the dispatch query. Round 2 confirmed the SQLite plan
(`SCAN automation_run USING INDEX ar_pending_idx`); that is carried forward, not re-verified.

> **CLOSED (2026-10-06).** New Postgres-only guard
> `test_the_postgres_planner_picks_the_pending_run_index` runs
> `EXPLAIN` with `enable_seqscan=off` (the table is empty under test, so the planner must be forced
> onto index paths): the Pending sweep must be `Index Scan using ar_pending_idx` with **no Sort**
> node, and the Success sweep must **not** mention `ar_pending_idx`. The predicate is also catalog-
> read live on both engines by `test_the_pending_run_index_predicate_is_still_partial`. The guard
> bites: a probe that recreated the index without `WHERE status='Pending'` produced
> `Index Scan using ar_pending_idx on automation_run ... Filter: ((status)::text = 'Success'::text)`
> for the Success sweep — exactly the plan the new test fails on.

## 7. Required before re-gate

1. **H3-1** — recompute `Observable.data_hash` when `ObservableType.is_case_sensitive` changes, or
   forbid the change while rows exist. Land the test in §3 alongside it. Without this the C4/dedup
   guarantee holds only for types whose flag is never corrected after rows exist.
2. **H3-2** — correct all three false claims in `alerts/0008` and the one in `alerts/0005`, and either
   make `observables/0003`/`alerts/0004` reversibly clean so the pins can be deleted, or rename
   `test_m10_rolling_back_a_seed_migration_deletes_no_rows` to state that it unapplies 13 migrations
   across 4 apps. Also add a guard that pins the two hand-edited dependency lists, so the next
   autodetector run cannot revert them silently — `makemigrations --check` cannot detect that.
3. **H3-3** — replace the substring standard with an AST check that a verified mechanism is
   *invoked*, and add the missing `SILENT_NOOP_CALLS` entries.
4. **H3-4 / H3-5** — out of remediation scope but open: either ship the mapping engine that derives
   `correlation_key`, or record C4 as "index verified, feature absent" in the acceptance criteria.
   For M-6, either raise coverage past 80 or lower `fail_under` so the configured gate and the
   decision note agree.
5. **H3-6 / H3-7 / H3-8** — fix the stale `DESC` claim in `TODO.md:183`; drop or justify
   `case_severity_idx`; make `scripts/lock.sh` derive the interpreter header instead of hardcoding
   `3.14.6`.
6. **Postgres pass** — §6 (a), (b), (c), (d), (e) all require a PostgreSQL instance. §6 (b) is the
   one I would not ship without.

## 8. Method

* Baseline `make check` run first and again last; both green; tree clean before and after.
* Every round-2 item in scope re-tested by executing its recorded mutation and requiring the real
  assertion to fail. Mutation 1-15 output in §5 is the guards' own, not mine.
* Two A/B experiments on migration dependencies (§4, H3-2) — same commit, one dependency changed —
  to separate what a hand-edit buys from what its comment claims.
* Two probe mutations authored for this review (unguarded CHECK constraint; meta-standard bypass),
  both removed. Nothing in the tree was left modified.
* Findings are limited to what I executed. §6 is explicitly separated for that reason; H3-1, H3-2,
  H3-3, H3-4, H3-5, H3-6 each cite the command output that produced them.