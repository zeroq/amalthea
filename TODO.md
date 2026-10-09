# TODO — Amalthea

Open work only. Completed items live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-10-07 (post Phase 10 commit `e3a94d8`)

**Conventions** — every item carries a `Source` (plan AC, review finding ID, or decision ID) so it can
be traced, and `Blocks` when it gates other work. Review IDs (`C1`, `H2`, `M5`…) refer to
`docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md` (round 1) and
`REVIEW-2026-10-05-phase3-schema-gate-round3.md` (round 3).

**Status legend** — `[ ]` open · `[~]` in progress · `[!]` blocked

---

## 1. Phase 3 gate remediation

Status: **complete on SQLite and Postgres.** `491 passed, 3 skipped` on SQLite, `470 passed,
24 skipped` on Postgres (2026-10-07), `mypy` clean on 95 files (strict), `manage.py check` 0
issues, migrations in sync, `make check` green. Round-3 Highs H3-1 (observable re-hash), H3-3 (R11
AST standard), H3-4 (correlation_key derivation) are **FIXED** and hold on Postgres; the
`db-postgres` re-gate (§1.11) is **DONE**. H3-5 (coverage) is folded into §2.1. **Only H3-2
remains: the forward-only migration gap is documented but deliberately unfixed on SQLite** — its
verdict is recorded in §3.1 (Postgres re-verification gave it the same call: forward-only
discipline, no code change is safe on SQLite).

- [x] **1.1 — `Case.save()` number allocation** `C1` — **DONE, verified**
  New `cases/numbering.py`. Goes further than the brief: `pre_save()` on a custom
  `AllocatedNumberField` (the one hook `bulk_create()` also goes through, which `save()` is not), a
  batch allocator for `bulk_create()` (Django renders the whole parameter list before INSERT, so
  per-row `pre_save` would collapse a batch onto one number), and `deconstruct()` pinning the field's
  import path so migrations survive the class moving. Postgres → `nextval()`, SQLite → `MAX()+1`.
  `save()` keeps its explicit guard, annotated `int | None` — **the correct fix for the mypy
  "unreachable" trap; the guard was not deleted.** 10 numbering tests incl. bulk_create collisions.

- [x] **1.2 — `manage.py` default settings module** `H2` — **DONE, verified**
  Now `amalthea.settings.dev`; `manage.py migrate` runs on a fresh SQLite DB.

- [x] **1.3 — Replace the four vacuous schema tests** `H1`, `R11` — **DONE, verified**
  `test_phase3_schema.py` 39 assert/def lines (was a literal `pass`), `test_indexes.py` 15 (was
  `len(tables) > 0`), `test_fk_audit.py` 12. New `test_mutation_probe.py` applies **real schema
  mutations** — `ALTER TABLE alert RENAME COLUMN` — and calls the real assertions to prove they now
  fail.

- [x] **1.4 — `Alert.source_ref` digest fallback** `C3` — **DONE, verified**
  New `ingest/references.py`: `SYNTHETIC_PREFIX = "sha256:"` + `canonical_payload()` digest,
  flagged back to the caller as `source_ref_synthesised` so it lands in `ingestion_warnings`.

- [x] **1.5 — Enum `choices` + `CheckConstraint`s** `H5`, `L1` — **DONE, verified** (`test_enum_contracts.py`)
- [x] **1.6 — Drop the redundant indexes** `M1` — **DONE, verified**
      (`test_mutation_probe.py` proves the index test now catches a redundant index)
- [x] **1.7 — Rename `Alert.source_id` → `ingestion_source`** `M2`, `M3` — **DONE, verified**
      Column is now `ingestion_source_id`; docstring records why it must never equal wire `source`.
- [x] **1.8 — `Observable.data_hash`** `H3` — **DONE, verified**
      New `observables/hashing.py` (`DataHashField`, `canonical_value`); constraint is now
      `(data_type, data_hash)`.

- [x] **1.12 — Mutation guards could not mutate UNIQUE/CHECK constraints** — **SUPERSEDED by 1.13**
  A `UNIQUE` constraint declared in a table definition is backed on SQLite by an auto-index named
  `sqlite_autoindex_<table>_N`, **not** by the constraint's own name, so `DROP INDEX IF EXISTS
  "<name>"` is a **silent no-op** against a `UniqueConstraint`. Fixed properly in 1.13.

- [x] **1.13 — Mutation 5 was a no-op; the guard lied about itself** `round2 C-1` — **FIXED**
  Django's SQLite `remove_constraint()` is `self._remake_table(model)`, which rebuilds the table **from
  current model state**, so the constraint was written straight back out. Planner-verified: the DDL
  contained `uniq_obs_dtype_hash` both before and after, yet the guard printed `duplicate insert
  accepted` and reported `5 passed`. **That output was false — a string I wrote, not observed behaviour**:
  the exact R11 failure the file exists to prevent, committed by the planner.
  New `tests/conformance/_mutation.py` makes the failure structurally impossible:
  `schema_mutation()` snapshots the DDL, and on exit **re-reads it and raises if it never moved**.
  `Mutation.detach_constraint()` supplies the technique SQLite actually requires (detach from `_meta`
  first, then remake). `Mutation.add_index()` verifies via `sqlite_master`, since an index does not
  alter the table's `CREATE` statement. Guard 5 now asserts the DDL delta **first**, then the behaviour.

- [x] **1.14 — FK `on_delete` audit is blind to `CASCADE`** `round2 C-2` · **CRITICAL** — **FIXED**
  `tests/conformance/test_fk_audit.py` now reads `field.remote_field.on_delete` (the only unambiguous
  source — `ForeignKey.deconstruct()` **omits** `on_delete` when it is `CASCADE`, which is why the old
  `"on_delete" in deconstruct()[3]` probe passed on exactly the value it needed to catch) and pins the
  **full** policy for every relation in `EXPECTED_ON_DELETE`, not a `PROTECT` subset.
  `test_no_cascade_is_reachable_from_a_longer_lived_row` enumerates the whole `CASCADE` surface (17
  entries) so a `PROTECT`-only check cannot hide a swap again. Inventory pinned at 35 FKs (29 + the 6
  `*TagLink` join FKs from L-2), counted against `EXPECTED_ON_DELETE` in both directions so neither the
  audit nor the table can be silently trimmed. **Verified by mutation:** `AlertTagLink.alert`
  `CASCADE`→`SET_NULL` fails `test_every_fk_matches_the_declared_on_delete_policy`.

- [x] **1.15 — `M1` over-pruned: two indexes are still needed** `round2 H-5` — **FIXED**
  Restored the implicit FK index on the **right**-hand column of each composite, in
  `cases/migrations/0006_restore_custom_field_fk_indexes.py` and
  `alerts/migrations/0005_restore_custom_field_fk_indexes.py`: `WHERE custom_field_id = ?` was a
  sequential scan because `custom_field` is the *right* column of `UNIQUE (case, custom_field)`.
  `test_every_unindexed_fk_is_a_left_prefix_of_a_composite_index` now re-derives the entire prune
  from live DDL instead of a hand-written column list, so an over-prune fails by construction.
  The prune itself was **correct** on the link tables and is preserved: `AlertObservable` now carries
  `db_index=False` on both FKs (covered by `UNIQUE (alert, observable)` and
  `alertobs_obs_alert_idx (observable, alert)` respectively), mirroring `CaseObservable`.

- [x] **1.16 — Postgres-only corruption: sequence not advanced for explicit numbers** `round2 H-6` — **FIXED (SQLite-blind, see below)**
  `CaseNumberField.pre_save` now calls `sync_case_number_sequence(current)` when a caller supplies an
  explicit number on INSERT, so `case_number_seq` is advanced past it via `setval` and the next
  auto-numbered Case cannot collide. Also wired through `allocate_case_numbers` for `bulk_create`
  (`test_h6_bulk_create_with_explicit_numbers_syncs_too`).
  **Verified by mutation** — removing the `pre_save` sync fails that test.
  **Honest limitation:** still **unverified on Postgres** (§3.1). SQLite allocates `MAX(number)+1`,
  which reads the row just written and self-heals, so the collision cannot be reproduced here; the
  test skips with that reason rather than passing vacuously. AC3.7 must run it on Postgres.

- [x] **1.17 — `data_hash` backfill uses a different hash function than the runtime** `round2 H-7` — **FIXED**
  `observables/migrations/0003_observable_data_hash.py` backfills with the same `canonical_value()`
  normalisation the runtime `save()` uses, so a migrated uppercase `hash` row carries a digest the
  runtime would have produced itself and the duplicate no longer slips past `uniq_obs_dtype_hash`.
  New tests assert a backfilled row satisfies the identical invariant as a runtime-created one, and
  that the digest is recomputed when `normalized_data` changes (`update_fields` must not become a
  blanket full-row write).

- [x] **1.18 — Pin a runnable static-analysis command; "mypy clean" was not one** `round2` — **FIXED**
  `pyproject.toml` pins `files` (11 app packages) and excludes `migrations/` and `tests/`, so bare
  **`mypy`** is the gate: **0 errors in 88 source files** today (strict). DRF/celery/channels get
  narrow, documented `ignore_missing_imports` / `disable_error_code` overrides — integration points
  only, global strictness never relaxed. `Makefile` owns the Definition of Done; coverage is a
  separate non-blocking `make coverage` target so `fail_under = 80` cannot make the baseline look red
  (or, worse, invite filler tests).

- [x] **1.19 — Smaller round-2 items** `round2 H-1`, `H-3`, `H-4`, `L-2` — **FIXED**
  `H-1` `ar_pending_idx` partial predicate is guarded: asserted present when `status='Pending'` and
  **absent** otherwise (`test_indexes.py`). `H-3` `AutomationRun.idempotency_key` uniqueness asserted
  against live unique keys plus a duplicate-insert `IntegrityError` (`test_integrity.py`).
  `H-4` `IngestionSource.slug` uniqueness likewise. `L-2` the three M2M through tables keep a redundant
  left-FK index: `CaseTagLink`/`AlertTagLink`/`ObservableTagLink` are now declared explicitly with
  `db_index=False` on the prefix-covered side, applied as state-only `SeparateDatabaseAndState` +
  `AlterField` (the autodetector cannot emit `through=`).

- [x] **1.20 — Round-3 Highs** `round3 H3-1..H3-5` — **H3-1 · H3-2 · H3-3 · H3-4 CLOSED; H3-5 closed via §2.1**
  Report: `docs/reviews/REVIEW-2026-10-05-phase3-schema-gate-round3.md`. All eight in-scope round-2
  items verified **closed**; the C-1 evidence defect did not recur (15/15 mutation guards land real
  DDL). **Status:** §1 as a whole closed 2026-10-07 with Phase 10 — H3-5 (coverage) is folded into
  §2.1 and **DONE at 83.10%**.
  - **`H3-1` (High) — a vocabulary change silently invalidates every stored digest.** **FIXED** (`7bea08a`).
    `ObservableType.save()` now re-hashes affected rows when `is_case_sensitive` flips, plus a
    backfill path for post-rec canonicalisation collisions. Mutation-verified.
  - **`H3-2` (High) — the `alerts/0007`+`0008` dependency pins are inert, and their comments were
    false.** **DOCUMENTED, NOT FIXED.** The comments were corrected in place and the false
    `case_record`-cascade safety claim retracted; the underlying gap is real: `observables/0003` +
    `alerts/0004` cannot be reversed cleanly on SQLite, so seed-rollback tests fail under either pin.
    That is a forward-only-migration problem (dev DB and prod migrations need forward-only discipline;
    the seed tests already assert it). No code change is safe on SQLite; revisit with Postgres (§3.1).
  - **`H3-3` — the R11 standard could be satisfied by mentioning a keyword.** **FIXED** (`1628dbd`).
    `analyse_guard()` judges guards from their **AST** — a located *mutation site* **and** a located
    *verification site*, folding module-level helpers one level deep. Six deliberately-vacuous guards
    must each be rejected; four sound guards must be accepted. Two structural tests block regression.
    Mutation-verified three ways.
  - **`H3-4` / `M-1` — `correlation_key` never derived.** **FIXED** (`ac6c8b0`). Derived in
    `ingest/mapping.py` via jsonpath-ng from `correlation_key`/`correlationKey`/`correlation`
    mapping keys; `AC4.1`-style round-trip tests pass.
  - **`H3-5` / `M-6` — coverage 76.36% vs `fail_under=80`.** **CLOSED via §2.1.** Now 83.10%
    (Phase 10a).
  - **Postgres-only, quarantined in report §6:** `sync_case_number_sequence` is a non-atomic
    read-modify-write whose `GREATEST` does **not** prevent backwards sequence movement under
    concurrent imports — correct but not concurrency-safe; treat as unproven until AC3.7 runs on
    Postgres.

- [x] **1.11 — Re-run the `db-postgres` gate** — **DONE (2026-10-06)**
  Round 2 confirmed `C3`, `H2`, `H4`, `H6`, `M2`, `M3`, `M4`, `M6`, `M10`, `M14` — **`M10` genuinely
  clean**: with analyst rows deliberately sharing seeded names (`Contained`, `Imported`, `hash`), all 10
  reversal/re-apply steps changed zero rows. Round 3 was executed after 1.13–1.18 (§3.1): full suite
  on PostgreSQL 16 → **408 passed, 24 skipped, 0 failures**; review §6(a)–(e) all CLOSED,
  including the observable re-hash (H3-1) and the H-6 sequence race on real `nextval()`. Phase 8's
  Postgres gate (2026-10-07, §5.5) re-confirms: **470 passed, 24 skipped**.

---

## 2. Quality gates not yet met

- [x] **2.1 — Coverage → ≥ 80%** Plan §11, `pyproject` `fail_under = 80` — **DONE (2026-10-07, Phase 10a)**
  **83.10%** at the Phase 10 gate; `fail_under=80` kept. The two largest gaps noted below are gone:
  `compat/mappers/` was **deleted** (9 empty stub files, zero importers — deviation P10-1 supersedes
  §13-13) and `query/engine.py` plus the new T1 serializers (`task_json`, `custom_event_json`,
  `custom_field_json`, `user_json`) are covered by the new contract surface:
  `test_t1_surface.py` (53), `test_unknown_fields.py` (33), `test_authz.py` matrix (131 tests),
  `test_ledger_keyset.py` (2), `test_thehive_fixtures.py` (13), `test_webhook_hardening.py` (37).
  Old items (operators, webhook hardening, automation executor, UI views) remain covered.

- [x] **2.2 — mypy strict clean** Plan DoD — **DONE** (95 source files, strict, 0 errors)
  The four residue items from the previous listing are resolved via targeted, documented overrides:
  celery (`ignore_missing_imports`), channels `URLRouter` arg-type (runtime-identical class),
  custom field generic bounds (`disallow_any_generics = false` on `cases.numbering`,
  `observables.hashing`). `make check` runs bare `mypy` and it is green (Phase 8 added `query/` to
  the pinned scope and it is clean there too).

- [ ] **2.3 — Enforce the one-way dependency rule with a lint check** Brief §0.4, `R3`
  Nothing currently prevents TheHive field-name literals (`_id`, `_createdAt`, `dataType`,
  `severityLabel`…) leaking outside `compat/`. This is the control that keeps "compatible on the wire,
  clean inside" honest.

- [x] **2.4 — Reverse-direction link-table indexes** `M5`, plan §6.3 — **DONE**
  `caseobs_obs_case_idx (observable, case)` and `alertobs_obs_alert_idx (observable, alert)` are
  declared in the models and asserted against live DDL in `test_indexes.py`. Module C's
  observable→cases fan-out no longer depends on an implicit FK index surviving a refactor.

- [~] **2.5 — Remaining Medium/Low cleanups** `M4`–`M14`, `L4`–`L6` — **most done; two remain**
  **DONE:** `M4` custom-field uniqueness (`uniq_case_custom_field`) · `M6` `db_table="case"` reserved
  word → `case_record` · `M8` `Organisation`/`ApiKey` `db_table` set (`identity_organisation`,
  `identity_apikey`) · `M9` timeline keyset (`timeline_case_date_idx (case, date, id)`) ·
  `M10` seed reverse no longer deletes by value (`test_seed_migrations.py`) · `M11` seed row-set
  assertion (`test_seed_migrations.py`) · `M12` `stage_from_alert_status` Imported branch legal
  (seeded status includes `Imported`, test present) · `M14` `AutomationRun.playbook` FK exists ·
  `L4` `Case.closed_date` set on transition (`stamp_closed_date`) · `L5` `start_date`/`date` have
  `default=timezone.now` (Postgres `DESC` no longer surfaces NULLs first).
  **REMAIN:** `M7` link-table `created_at` still shadows the abstract base field
  (`CaseObservable`/`AlertObservable` declare their own; harmless but redundant — either drop the
  base field from those models or remove the redeclaration) · `L6` alert unique-constraint headroom
  (`source(100)+type(100)+source_ref(255)` ≈ 1820 bytes worst case vs 2704-cap; acceptable, revisit
  only if a source needs longer refs).

---

## 3. Open decisions

- [x] **3.1 — Postgres verification** `R4`, `R10`, plan §5 · **DONE — verified 2026-10-06, re-confirmed 2026-10-07**
  **Decision 2026-10-03 (user):** proceed with the Phase 3 remediation wave now and verify on SQLite;
  do **not** block on Postgres. The `C1` sequence fix is vendor-guarded precisely so this is possible.
  Nine features still cannot be validated until `docker-compose up -d postgres` runs: GIN, covering
  indexes, `jsonb` operators, real `timestamptz`, `nextval()`, `COLLATE "C"`, the btree tuple cap,
  collation-aware comparison, and `DESC` in index DDL (**silently dropped on SQLite**, so any dev test
  relying on descending index order proves nothing). Also where H3-2's forward-only migration gap and
  the H-6 sequence race get their final verdict.
  **Must complete before:** Phase 10, AC3.7, and any production deployment — most urgently `H3`
  (`data_hash`), whose failure mode is prod-only. Verification SQL is in the review report.
  **VERIFIED 2026-10-06:** full suite on PostgreSQL 16 → **408 passed, 24 skipped**. Review
  §6(a)–(e) closed (round-3 report; summary: (b) advisory-lock fix + concurrency guard proven to
  bite, (e) new planner guard). AC1.3 (`celery inspect ping` → pong) and AC1.4 (live `/readyz` 200)
  verified against running Redis. Re-confirmed 2026-10-07 at the Phase 8 gate: **470 passed,
  24 skipped** and at the Phase 10 gate: **712 passed, 24 skipped** (SQLite `make check` 733/3).
  The two items that were still open at the Phase 8 gate — §5.7 (Phase 10) and coverage `H3-5`
  (§2.1) — are both **closed** as of 2026-10-07.

- [x] **3.2 — Multi-tenancy timing** Plan §14 Q4 — **DECIDED 2026-10-06: single-tenant + nullable `Organisation` FK**
  Matches the provisional in the plan and the implemented schema (`User.org` nullable, `SET_NULL`).
  Multi-org access control is out of scope for MVP; the nullable FK keeps the door open without a
  per-table migration day one. Recorded in plan §14.

- [x] **3.3 — Markdown dialect** Plan §14 Q5 — **DECIDED 2026-10-06: sanitized CommonMark**
  TheHive-flavored Markdown (mentions, attachments) is a superset; adopting it costs wire fidelity and
  sanitizer complexity. `markdown-it-py` (CommonMark) is already the pinned renderer. Recorded
  divergence in plan §14.

- [x] **3.4 — `Alert.type` vs `dataType` taxonomy** Plan §14 Q3 — **DECIDED 2026-10-06: independent, exposed as a tag**
  `Alert.type` stays Amalthea's own vocabulary (free-form CharField); the exact mapping onto TheHive's
  taxonomy is deferred to the Phase 8 query surface where `dataType` filtering is exercised.
  Recorded in plan §14.

- [x] **3.5 — Alert severity default when unmapped** Plan §14 Q1 — **DECIDED 2026-10-06: `2` (Medium) + warning**
  Implemented in `ingest/pipeline.py` (`DEFAULT_SEVERITY = 2`, `severity_defaulted` warning already
  appended) and `IngestionSource.default_severity`. Recorded in plan §14.

- [x] **3.6 — Multi-signal correlation default** Plan §14 Q2 — **DECIDED 2026-10-06: per-source `correlation_key`, configurable window; default 10 minutes**
  AGENTS.md's 10-minute window for identical destination IP is the default window on the
  `(correlation_key, date)` index; each `IngestionSource` can disable/enable correlation
  (`correlation_enabled`). Multi-signal correlation (same user + same IP) remains an operator-authored
  `correlation_key` expression — the engine correlates on the key, not on a fixed attribute pair.
  Recorded in plan §14.

---

## 4. Environment & repository

- [x] **4.1 — `README.md` missing but referenced by `pyproject.toml`** — **DONE (2026-10-06)**
  Real README added: what Amalthea is, quickstart (dev server + analyst login), the TheHive
  compatibility statement, and pointers to the plan and ADRs. Packaging/build steps no longer fail.

- [x] **4.2 — Local git repository** — **DONE**
  `git init -b main`, clean working tree, hooks installed. **Caveat to fix:** `user.name` was unset, so
  a repo-local identity was derived from your global email (`zero-q <zero-q@iname.com>`). Repo-local
  only — global config was not touched. To correct the author afterwards:
  `git commit --amend --reset-author --author="Your Name <you@example.com>"`.

- [x] **4.5 — Move to a real GitHub-hosted remote** — **CLOSED 2026-10-08**
  `origin` = `https://github.com/zeroq/amalthea` (public), `main` pushed and tracking `origin/main`.
  Visibility decided **public** (matches "open-source" in AGENTS.md). **Still open from the original
  plan:** (1) `LICENSE` file absent — AGENTS.md declares AGPL-3.0-or-later but no file exists; (2) no
  `.gitattributes`; (3) no `main` branch protection / PR workflow yet. Credentials are not persisted
  locally (per decision); pushes authenticate via Git Credential Manager.

- [x] **4.6 — Production secret hardening + repo-wide secret-hygiene guard** — **CLOSED 2026-10-08**
  Closes finding **F9**. Two parts: (a) `amalthea/settings/prod.py` now fails closed — refuses to
  import unless `DJANGO_SECRET_KEY` is ≥50 chars (missing/placeholder rejected) and
  `DJANGO_ALLOWED_HOSTS` is non-empty, and forces secure cookies/HSTS/SSL-redirect/frame-deny
  (`tests/conformance/test_prod_settings.py`, 9 tests); (b) `scripts/secret-scan.sh` (dependency-free
  scanner) wired into the pre-commit hook (staged, runs even on docs-only commits), the pre-push hook
  (tracked tree), and a new CI `secrets` job (full history + gitleaks pinned by SHA), plus `make
  secrets` and the `SECURITY.md` policy linked from README + AGENTS.md. **Hardened after an
  independent adversarial review**: `--staged`/`--tracked` scan git objects (index/HEAD) not the
  worktree, `--history` scans blobs + commit/tag messages, parsing fails closed, binaries scanned, CI
  triggers on every branch/tag, filename guard covers `*.env`/`.envrc`
  (`tests/unit/test_secret_scan.py`, 20 tests). Plan:
  `docs/planning/PLAN-2026-10-08-secret-hygiene.md`; record: master plan §13 Phase 13.

- [x] **4.3 — Hash-pin the requirements** `R9`, plan §5 — **CLOSED 2026-10-07 (Phase 11)**
  `scripts/lock.sh` rewritten to `pip-compile --generate-hashes` over the existing exact pins
  (all-platform hashes from the PyPI JSON API; `--no-strip-extras`; dev constrained by `-c base.txt`
  with the base-overlap filter keeping runtime/dev disjoint). base.txt: 49 pkgs / 710 hash lines;
  dev.txt: 54 pkgs / 878 hash lines; closure invariant machine-checked with a 6-entry bootstrap
  allowlist (`build`, `pyproject-hooks`, `pip-tools`, `wheel`, `pip`, `setuptools`); `make lock`
  verified byte-identical on repeat runs; `--check` mode adds the CI lock-currentness gate. Plan:
  `PLAN-2026-10-07-ci-hashed-requirements.md`; record: master plan §13 Phase 11.

- [x] **4.4 — `docs/spec/` populated with the implemented-system spec** (2026-10-07)
  Plan: `docs/planning/PLAN-2026-10-07-docs-spec.md`. Seven reference files derived from code (not
  the plan's intent), each cross-linked to its conformance/contract tests:
  `README.md` (index + drift policy), `data-model.md`, `api.md`, `realtime.md`, `automation.md`,
  `query-dsl.md`, `identity-auth.md`, `deviations.md` (register: M3/M6/M9/M12/M14, P7-*, P8-*,
  P10-*, P11-*, deferred F2/F9, and the new **P12-1**: `task.completed` is a registered trigger that
  nothing emits). Source: `tests/conformance/` + `core/serializers.py` + `compat/`. See COMPLETED.md.

---

## 5. Remaining phases

Phases 4–10 are complete and pass their gates on SQLite and Postgres; Phase 10 shipped in
commit `e3a94d8` (2026-10-07, §5.7). This section is now fully historical.
Full task/AC detail in `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §9.

- [x] **5.1 — Phase 4: Ingestion** (AGENTS.md §4.1) — **DONE**
  jsonpath-ng mapping engine, `POST /api/v1/alerts/webhook/{source_id}`, size cap **before** parse,
  per-source secret (hasher-backed), per-source + per-IP throttle, idempotent replay, correlation_key
  derivation. `test_webhook_hardening.py` (37) + `test_mvp_loop.py`.

- [x] **5.2 — Phase 5: Escalation, Observables, Tasks, Timeline** (AGENTS.md §4.2–4.3) — **DONE**
  `merge/{caseId}` + `import/{caseId}`, correlation engine on `correlation_key`, typed observable
  extraction + global dedupe, cross-case graph, `{idOrName}` by UUID **or** case `number`.

- [x] **5.3 — Phase 6: Orchestration Gateway** (AGENTS.md §4.4) — **DONE — closes the MVP loop**
  Domain events → Celery with `idempotency_key` (scoped per observable/case link), dispatched on
  `transaction.on_commit`; playbook executor with SSRF guard; results written to
  `AutomationRun.output_log` **and** the case timeline. `test_automation_executor.py` (22) +
  `test_mvp_loop_automation.py` (6) + live smoke-tested end-to-end.

- [x] **5.4 — Phase 7: Realtime Ledger** (AGENTS.md §2 Module B) — **DONE (2026-10-06)**
  `cases/ledger.py` is the only writer of a `TimelineEvent`: it creates the row and publishes it to
  `case_{uuid}` on commit, and `events_after` is the same `(date, id)` keyset the client resyncs
  from. All five briefed call sites migrated (+ an `assigned` event on assignment);
  `CaseConsumer` refuses an unauthenticated handshake with 4401, serves
  `{"type":"sync","after":…}` → `{"type":"timeline","events":[…]}`, and closes 4000 on anything
  else. `_timeline_entry.html` + `hx-*` attributes on the comment form + `ui/static/ui/live.js`
  (dedupe by `data-event-id`, 1s→15s backoff, re-sync on every open). `test_realtime.py` (10)
  covers AC7.1–AC7.4 plus the mutation-guard scan; SQLite 440/3, Postgres 419/24.
  Two gaps were recorded in plan §13. P7-1 (`alerts/escalation.py` writing `alert-imported` /
  `alert-merged` outside the choke point) is **CLOSED** — both sites migrated and `SCANNED_APPS`
  now includes `alerts/`. The other stands: the repo ships no htmx, so the new `hx-*` attributes
  are inert until one is added.

- [x] **5.5 — Phase 8: Query API** — **DONE (2026-10-06)**
  `POST /api/v1/query` DSL, `X-Total`, bare-array responses. Unimplemented operators must 400, never
  silently return a wrong answer. Includes **AC8.4**: TheHive4py unmodified against a live server.
  Gate: SQLite `make check` 491 passed / 3 skipped; Postgres `test_pg` 470 passed / 24 skipped;
  query suite 50 tests. AC8.4 live gate PASS (thehive4py 2.1.0: find → create → merge → timeline).
  Deviations P8-1..P8-7 in plan §13 (timeline envelope P8-1; inline 400s P8-2; `excludeFields: []`
  P8-3; getCase-miss → `[]` P8-4; live-gate gaps P8-5 POST /api/v1/alert, P8-6 merge → OutputCase,
  P8-7 channels-redis pin).

- [x] **5.6 — Phase 9: Minimal UI** — **DONE (2026-10-06)**
  Dark, keyboard-first, TheHive-aligned, WCAG-aware severity rendering (text+colour, §a11y tests).
  Dashboard, alert triage (escalate/merge), case ledger (status, notes, tasks, artifacts), automation
  + sources pages. Session auth, POST-only mutations, CSRF. `test_ui_loop.py` (25).

- [x] **5.7 — Phase 10: Conformance, Security & Performance** — **DONE (2026-10-07)**
  Executed as **Phase 10a (T1 surface closure, Option 1 — user-approved)** + this phase per
  `docs/planning/PLAN-2026-10-07-phase10-t1-closure.md` / `BRIEF-2026-10-07-phase10-t1-closure.md`.
  Twelve missing T1 endpoint groups implemented (`login`, `logout`, alert/case/observable DELETE,
  `POST /alert/{id}/observable`, `POST /case/{id}/customEvent`, task detail GET/PATCH/DELETE,
  customEvent PATCH/DELETE, `GET /customField`, case→alert unlink), `ScopePermission` wired into
  `DEFAULT_PERMISSION_CLASSES` (read-only keys can no longer mutate), `compat/mappers/` deleted.
  **Gate:** SQLite `make check` **733 passed / 3 skipped**; Postgres `test_pg` **712 passed /
  24 skipped**; coverage **83.10%** (≥80 gate); ruff + mypy clean; pip-audit 0 CVEs; bandit no new
  findings. Security-auditor pass (Wave C) + fix burst: findings F1·F3·F5·F6·F7·F8 fixed; F2/F9
  (tenant isolation) and F3-observable recorded deferred. EXPLAIN review (Wave D) → AC10.5 MET;
  `events_after` keyset rewritten (F1); transcripts committed under `docs/perf/`. Verifier pass →
  VERIFY report; its V1 (slashless `POST /case/{id}/observable` 405 — thehive4py's spelling) and
  V3–V5 (4 test-pin gaps) **closed** — `docs/planning/VERIFY-2026-10-07-phase10.md`.
  Deviations P10-1..P10-13 recorded in plan §13.

- [x] **5.8 — AC1.3 / AC1.4 verification** — **DONE (2026-10-06, via §3.1)**
  `celery -A amalthea inspect ping` → `pong, 1 node online` and live `/readyz` → 200
  (`{"status": "ok"}`) with Redis up; the 503-when-Redis-down path stays covered by
  `test_readyz_fails_when_redis_is_unreachable`.

- [x] **5.9 — §3.1 Postgres gate executed (2026-10-06)** — see §3.1
  Full suite on real PostgreSQL 16: **408 passed, 24 skipped, 0 failures**. Review §6 (a)–(e) all
  CLOSED: (b) sequence race fixed with `pg_advisory_xact_lock` + mutation-bitten concurrency test;
  (e) new Postgres planner guard for `ar_pending_idx`. **5.8 also closed here**: AC1.3
  `celery -A amalthea inspect ping` → `pong, 1 node online` against a live worker on the Redis broker;
  AC1.4 live `/readyz` → HTTP 200 (`{"status": "ok"}`) with Redis up; the 503-when-Redis-down path
  stays covered by `test_readyz_fails_when_redis_is_unreachable` (`test_core_views.py:8`).

---

## 6. Deferred scope (committed, not yet scheduled)

- [ ] **6.1 — T2 endpoints** Plan §7.3
  Attachments, `page`, `comment`, `tag`, `shares`, `observable/type` CRUD, case/alert status CRUD,
  `user`, bulk endpoints, `describe`, `export`, `flow`, case templates, taxonomy, procedures/TTP.

- [ ] **6.2 — Postgres-only indexes** `L3`, plan §6.3
  GIN on `raw_payload` and `Observable.tags`; `INCLUDE` covering indexes. `GinIndex` needs
  `django.contrib.postgres` in `INSTALLED_APPS` (a prod-only dependency).

- [ ] **6.3 — Per-link tags on observable link tables** Plan §13 #11
  Per-link `tags` agreed; per-link `is_ioc` **rejected** as incoherent on a globally-deduped entity.

- [ ] **6.4 — Tenant isolation** Phase 10 SEC-AUDIT F2, plan §13 deferred (a)
  `User.org` / `Organisation` exist and are nullable (decision §3.2), but **no endpoints scope by
  organisation yet**; `owner_org` is not populated and there is no tenant predicate in any query.
  Requires a decision on how isolation is enforced (middleware org-scope vs per-view filters) and on
  superuser/analyst roles before implementation.

- [ ] **6.5 — Observable PATCH/DELETE cross-case blast radius** Phase 10 SEC-AUDIT F3, plan §13 deferred (b)
  An observable is globally deduped across cases; free PATCH/DELETE would let one case mutate or
  delete an artifact other cases depend on. Decide policy (allow with warning vs. require
  `force=true` vs. per-link shadow copy) before opening those verbs wider.

- [ ] **6.6 — Production cookie/secret hardening** Phase 10 SEC-AUDIT F9, plan §13 deferred (c)
  Dev settings pin `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECRET_KEY` handling for local use
  only. Prod-grade: env-driven secrets, `SecurityMiddleware` headers (HSTS, X-Content-Type-Options,
  Referrer-Policy), `SESSION_COOKIE_HTTPONLY` audit, rate-limited auth/login.

- [ ] **6.7 — Paged case-detail timeline** Phase 10 PERF F2, plan §13 deferred (d)
  `case_json(detail=True)` embeds the **full** timeline in one payload; a very large ledger (10k+
  events) makes the case page heavy. Follow the `events_after` keyset pattern: paginate the embedded
  timeline (e.g. `?includeTimeline=true&timelinePage=1`) or move it to a separate endpoint.

---

## 7. Process improvements identified

- [ ] **7.1 — Demonstrate failure modes for every AC** `R11`
  Adopted as a rule; make mutation checks part of the Definition of Done, not a one-off. (In use
  across the conformance suite; keep extending to new ACs as phases land.)

- [ ] **7.2 — Keep the domain-expert gate on every phase, not just Phase 3**
  The `db-postgres` review found 4 Criticals and refuted 2 of the planner's own 4 findings. The
  division of labour (planner proposes, expert verifies with evidence, planner amends the plan) is
  what caught these. Phases 7–9 shipped without a full expert gate (Phase 8 had the live AC8.4
  thehive4py gate, which did catch three real gaps — P8-5/P8-6/P8-7); the process intent stands for
  Phase 10 and beyond.

- [ ] **7.3 — Require subagents to deliver the report they promise**
  Twice an agent stated a detailed report "follows in the final response" and never produced it, and
  once stopped a whole wave early claiming completion. Add an explicit "report must be in your final
  message" instruction and verify claims independently against the filesystem.

---

## 8. Standards (added 2026-10-03)

Established after two consecutive gate rounds passed green tests while real defects shipped. Both
failures were in the **evidence layer**, not the code under test.

- [x] **8.1 — Every mutation guard must prove its own mutation landed**
  `tests/conformance/_mutation.py`. `schema_mutation()` snapshots the live DDL from `sqlite_master` and
  on exit re-reads it and raises if it never moved. Rule: **assert the mutation changed something
  before asserting what it changed.** 6 guards, including the two that close round-2 `C-1`/`H-2`.

- [x] **8.2 — The standard is enforced mechanically, for all tests, not by convention**
  `tests/conformance/test_mutation_standard.py` (27 checks). Every `test_mutation_*` must use a verified
  mechanism; **no** test anywhere may call `schema_editor` / `remove_constraint` / `add_constraint` /
  `alter_db_table` / `add_field` / `remove_field` unverified; the helper's own check must not be deleted;
  the guards' "permanent" framing must not be removed. The rule caught two inconsistencies in itself on
  first run (raw `cursor.execute` is legitimate; model-metadata mutation is self-verifying).

- [x] **8.3 — `make check` is the single Definition of Done**
  ruff · ruff-format · mypy (scope pinned, strict, 88 files) · `manage.py check` · migration-sync ·
  pytest. `make check-fast` is the fast subset the pre-commit hook runs on every save.

- [x] **8.4 — Pre-commit and pre-push hooks installed** (`make hooks`)
  Pre-commit blocks bad lint and failing tests, and skips docs-only commits. **Verified by probe**, not
  assumed: a lint violation and a failing test were both blocked; a docs-only commit passed through.
  Pre-push additionally requires `make check` **and** a green mutation-guard run, because the gate is
  the project's main quality control.

- [ ] **8.5 — Extend mutation coverage to the remaining contract assertions**
  Guards now cover column rename, FK `related_name`, redundant index, nullable column, UNIQUE, CHECK,
  `on_delete` policy per relation (`C-2`), digest-recompute on vocabulary change (`H3-1`). Still open
  for future phases: `idempotency_key` uniqueness and `slug` uniqueness are now behaviour-tested
  (`test_integrity.py`) but lack a mutation guard; `severity`/`pap` ranges and the `ar_pending_idx`
  partial predicate are asserted against DDL/behaviour. Judge on a per-AC basis as Phases 7–8 land.

- [x] **8.6 — Guard the CI invocation itself** — **CLOSED 2026-10-07 (Phase 11)**
  `.github/workflows/ci.yml` runs the full DoD on `ubuntu-latest` (Python 3.14, Postgres 16-alpine
  service container, `permissions: contents: read`): pip install with `--require-hashes`, `make
  check`, the Postgres suite, the coverage gate, `scripts/lock.sh --check`, and `pip-audit`. Every
  step is a locally-verified command; runner-validated on first push (§4.5). Plan:
  `PLAN-2026-10-07-ci-hashed-requirements.md`; record: master plan §13 Phase 11.

- [ ] **8.7 — Timestamp-unit wording in plan §7.1** Verifier (2026-10-07), POST-closure note
  The plan's T1 timestamp row still reads "ms" while the implemented (and verifier-confirmed)
  contract is: entity JSON carries ISO-8601 strings, the timeline envelope carries ms integers
  (deviation P10-2). Reword the plan row so the two surfaces are stated separately.

---

## 9. Scratch/probe hygiene

- [x] **9.1 — Stale `__pycache__` from deleted scratch tests** Verifier recommendation 7 — **DONE (2026-10-07)**
  Removed all `tests/**/__pycache__` dirs; none of the pycs were tracked (`.gitignore` covers
  `__pycache__/`), so no commit contained them.