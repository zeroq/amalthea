# TODO — Amalthea

Open work only. Completed items live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-10-03

**Conventions** — every item carries a `Source` (plan AC, review finding ID, or decision ID) so it can
be traced, and `Blocks` when it gates other work. Review IDs (`C1`, `H2`, `M5`…) refer to
`docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md`.

**Status legend** — `[ ]` open · `[~]` in progress · `[!]` blocked

---

## 1. Phase 3 gate remediation

Status: **the `django-backend` wave was CANCELLED mid-flight but had already landed most of §1.1–1.8.**
All items below were re-verified independently by the planner (not taken on the agent's word — the
agent never reported). `185 passed`, `mypy` **0 errors**, `manage.py check` 0 issues, migrations in sync.
**Still required: a `db-postgres` re-gate to confirm the fixes hold.**

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
  fail. See §1.9 on whether to keep it.

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

- [ ] **1.12 — The mutation guards cannot mutate UNIQUE/CHECK constraints** (found by planner probe,
  **fix attempted and FAILED — superseded by 1.13**)
  A `UNIQUE` constraint declared in a table definition is backed on SQLite by an auto-index named
  `sqlite_autoindex_<table>_N`, **not** by the constraint's own name, so `DROP INDEX IF EXISTS
  "<name>"` is a **silent no-op** against a `UniqueConstraint`.
  Attempted fix: mutation 5 using `schema_editor.remove_constraint`. **This did not work** — see 1.13.

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
  I reported `mypy amalthea` → 0 errors. That scopes to **10 files**. `mypy .` gave 93. A gate whose
  result depends on which paths you name is not a gate.
  `pyproject.toml` now pins `files` (11 app packages) and excludes `migrations/` and `tests/`, so bare
  **`mypy`** is the gate: **0 errors in 73 source files**. Honest disclosure: an *earlier* measurement
  of mine was wrong because mypy's incremental cache deduped errors across sequential runs — error
  counts must be taken with a cold cache.
  DRF ships no stubs, so `rest_framework.*` gets `ignore_missing_imports`; `compat/auth.py` and
  `identity/admin.py` need two narrow, documented ignores (untyped base class; `UserAdmin` is generic
  in the stubs but **not subscriptable at runtime** — `DjangoUserAdmin[User]` raises `TypeError`, which
  broke app import until fixed).
  `Makefile` now owns the Definition of Done, and coverage is a separate non-blocking `make coverage`
  target so `fail_under = 80` cannot make the baseline look red (or, worse, invite filler tests).

- [x] **1.19 — Smaller round-2 items** `round2 H-1`, `H-3`, `H-4`, `L-2` — **FIXED**
  `H-1` `ar_pending_idx` partial predicate is guarded: asserted present when `status='Pending'` and
  **absent** otherwise (`test_indexes.py`). `H-3` `AutomationRun.idempotency_key` uniqueness asserted
  against live unique keys plus a duplicate-insert `IntegrityError` (`test_integrity.py`).
  `H-4` `IngestionSource.slug` uniqueness likewise. `L-2` the three M2M through tables keep a redundant
  left-FK index: `CaseTagLink`/`AlertTagLink`/`ObservableTagLink` are now declared explicitly with
  `db_index=False` on the prefix-covered side, applied as state-only `SeparateDatabaseAndState` +
  `AlterField` (the autodetector cannot emit `through=`). **The 9 Medium / 4 Low of report §3 remain
  open** and are folded into the round-3 review agenda.

- [ ] **1.20 — Round-3 Highs** `round3 H3-1`, `H3-2`, `H3-3`, `H3-4`, `H3-5` — **round 3 FAILED**
  Report: `docs/reviews/REVIEW-2026-10-05-phase3-schema-gate-round3.md`. All eight in-scope round-2
  items verified **closed**; the C-1 evidence defect did not recur (15/15 mutation guards land real
  DDL). Five findings remain:

  - **`H3-1` (High) — a vocabulary change silently invalidates every stored digest.**
    `ObservableType.is_case_sensitive` feeds `canonical_value()` → `data_hash`, but nothing observes
    a change to it: `DataHashField.pre_save` recomputes only for rows being written, and ADR-002 §D4
    makes the vocabulary user-extensible. **Reproduced:** create `/Tmp/A.bin` under the case-sensitive
    `file` type, flip the flag to case-insensitive (permitted by §D4), then insert `/tmp/a.bin` —
    the unique constraint **admits the duplicate**; two rows now denote one artifact because the
    first row's on-disk digest is stale. Same stale-derived-state class as `H-7`, one level out.
    Needs a signal on `ObservableType.save()` that re-hashes affected rows, plus a backfill path when
    two rows collide post-rec canonicalisation.
  - **`H3-2` (High) — the `alerts/0007`+`0008` dependency pins are inert, and their comments were
    false.** Round-3 A/B: committed pin and autodetector pin unapply the **same 13 migrations** and
    drop `case_record` in **both**. What the pin actually changes is rollback *order* — with it the
    corruption is **loud** (9 test errors), without it **silent**. It converts a loud failure into a
    quiet one. The comments also credited `observables/0004` with altering `AlertObservable` FKs; it
    touches `ObservableTagLink` only (0 matches). Comments corrected in place and the false
    `case_record`-cascade safety claim retracted. **The underlying gap is real:** `observables/0003` +
    `alerts/0004` cannot be reversed cleanly on SQLite, so the seed-rollback tests fail under either
    pin. That is a forward-only-migration problem, not a dependency-ordering one.
  - **`H3-3` — `test_mutation_standard.py` enforces R11 by substring match.** A `test_mutation_*`
    guard that mutates nothing passes by merely mentioning `_meta`. The standard's own enforcement is
    weaker than the standard.
  - **`H3-4` / `H3-5`** — round-2 `M-1` (`correlation_key` never derived) and `M-6`
    (coverage 76.36% vs `fail_under=80`) still open.
  - Also: `TODO.md:183` claims SQLite drops `DESC`, which is false; `L-1`, `L-4` unchanged.
  - **Postgres-only, quarantined in report §6.** Notably `sync_case_number_sequence` (my 1.16 fix) is
    a non-atomic read-modify-write whose `GREATEST` does **not** prevent backwards sequence movement
    under concurrent imports. My 1.16 fix is therefore correct but not concurrency-safe; treat as
    unproven until AC3.7 runs on Postgres.

- [ ] **1.11 — Re-run the `db-postgres` gate** *(round 2 FAIL 2C/7H/9M/4L; **round 3 FAIL — 0 Critical, 5 High**, all 8 round-2 items closed)*
  Round 2 confirmed `C3`, `H2`, `H4`, `H6`, `M2`, `M3`, `M4`, `M6`, `M10`, `M14` — **`M10` genuinely
  clean**: with analyst rows deliberately sharing seeded names (`Contained`, `Imported`, `hash`), all 10
  reversal/re-apply steps changed zero rows. Round 3 required after 1.13–1.18.

---

## 2. Quality gates not yet met

- [ ] **2.1 — Coverage 68% → ≥ 80%** Plan §11, `pyproject` `fail_under = 80` · **non-blocking until Phase 6**
  **Decision 2026-10-03:** enforce as a **Phase 6 MVP gate only**, not per phase (plan §13 #13). The
  largest gap is `compat/mappers/`, deliberately stubbed until Phases 4–5 supply the real logic; a
  per-phase gate would force filler tests that assert nothing. Phases 0/1/3 report it as a signal only.

- [ ] **2.2 — mypy strict clean** Plan DoD · **4 errors remain**
  - `amalthea/celery.py:7` — celery has no official stubs → targeted `ignore_missing_imports` override
  - `cases/models.py:87` unreachable — **do not "fix" by deleting the guard**; it is a django-stubs
    artifact and the guard is correct (`IntegerField.empty_strings_allowed = False`)
  - `realtime/routing.py:6`, `amalthea/asgi.py:20` — channels stub friction → per-module override
  (`types-channels` was already added and pinned; these are the residue)

- [ ] **2.3 — Enforce the one-way dependency rule with a lint check** Brief §0.4, `R3`
  Nothing currently prevents TheHive field-name literals (`_id`, `_createdAt`, `dataType`,
  `severityLabel`…) leaking outside `compat/`. This is the control that keeps "compatible on the wire,
  clean inside" honest.

- [ ] **2.4 — Reverse-direction link-table indexes** `M5`, plan §6.3
  Module C's fan-out (`observable → cases`) currently relies on Django's implicit FK index. Declare it
  so a future `db_index=False` refactor cannot silently break the product's headline feature.

- [ ] **2.5 — Remaining Medium/Low cleanups** `M4`–`M14`, `L4`–`L6`
  Custom-field value uniqueness (`M4`) · `db_table="case"` reserved word (`M6`) · shadowed
  `created_at` on link tables (`M7`) · `Organisation`/`ApiKey` `db_table` (`M8`) · timeline keyset
  determinism (`M9`) · **seed reverse migrations delete by value, destroying analyst-created rows with
  colliding names** (`M10`) · seed row-set assertion test (`M11`) · `stage_from_alert_status`
  unreachable `Imported` branch (`M12`) · `AutomationRun.playbook` FK (`M14`) · `Case.closed_date`
  never set on transition (`L4`) · nullable `start_date`/`date` defaults, since Postgres
  `ORDER BY … DESC` returns NULLs **first** (`L5`) · alert unique-constraint headroom (`L6`)

---

## 3. Open decisions

- [ ] **3.1 — Postgres verification** `R4`, `R10`, plan §5 · **Blocks AC3.7 and production; does NOT block Phases 4–5**
  **Decision 2026-10-03 (user):** proceed with the Phase 3 remediation wave now and verify on SQLite;
  do **not** block on Postgres. The `C1` sequence fix is vendor-guarded precisely so this is possible.
  Nine features still cannot be validated until `docker-compose up -d postgres` runs: GIN, covering
  indexes, `jsonb` operators, real `timestamptz`, `nextval()`, `COLLATE "C"`, the btree tuple cap,
  collation-aware comparison, and `DESC` in index DDL (**silently dropped on SQLite**, so any dev test
  relying on descending index order proves nothing).
  **Must complete before:** Phase 10, AC3.7, and any production deployment — most urgently `H3`
  (`data_hash`), whose failure mode is prod-only. Verification SQL is in the review report.

- [ ] **3.2 — Multi-tenancy timing** Plan §14 Q4 · provisional: single-tenant + nullable `Organisation` FK
  Confirm this is acceptable. Deferring risks a migration on every table later.

- [ ] **3.3 — Markdown dialect** Plan §14 Q5 · provisional: sanitized CommonMark
  TheHive-flavored Markdown is a superset (mentions, attachments). Adopting it costs wire fidelity;
  staying on CommonMark is a recorded divergence. Confirm.

- [ ] **3.4 — `Alert.type` vs `dataType` taxonomy** Plan §14 Q3 · provisional: independent, exposed as a tag
  Should Amalthea's `IngestionSource` map onto TheHive's `alert.type` taxonomy?

- [ ] **3.5 — Alert severity default when unmapped** Plan §14 Q1 · provisional: `2` (Medium) + warning
  Currently only matters once 1.4 lands.

- [ ] **3.6 — Multi-signal correlation default** Plan §14 Q2
  AGENTS.md specifies 10 minutes for identical destination IP. What is the default for correlating
  *multiple* signals (e.g. same user + same IP)?

---

## 4. Environment & repository

- [ ] **4.1 — `README.md` is missing but referenced by `pyproject.toml`** (`readme = "README.md"`)
  Any packaging/build step fails. Needs a real README: what Amalthea is, quickstart, the TheHive
  compatibility statement, and pointers to the plan and ADRs.

- [x] **4.2 — Local git repository** — **DONE**
  `git init -b main`, 156 tracked files, 804K, clean working tree. Commit `14d4fc2` captures the
  planner-verified state (185 tests pass, mypy clean, ruff clean, migrations in sync), so every
  subsequent gate is now auditable against a known-good baseline and recoverable after an interrupted
  agent. Verified before committing: `.venv/`, `__pycache__`, `.env`, `*.sqlite3`, and all tool caches
  excluded; no file over 200KB; no secrets in staged content (`.env.example` holds only placeholders).
  **Caveat to fix:** `user.name` was unset, so a repo-local identity was derived from your global
  email (`zero-q <zero-q@iname.com>`). Repo-local only — global config was not touched. To correct
  the author afterwards: `git commit --amend --reset-author --author="Your Name <you@example.com>"`.

- [ ] **4.5 — Move to a real GitHub-hosted remote** · **deferred by decision 2026-10-03**
  The repo is intentionally local for now; promoting it to GitHub is a later, separate task. When done:
  1. Confirm the target org/repo name and visibility (**public** is the likely intent — AGENTS.md says
     "open-source, developer-friendly"; a private repo contradicts that, and the commit history contains
     `docs/reviews/` and `TODO.md`, which are candid about open risks and unfinished work).
  2. Add `LICENSE` — **absent today.** AGENTS.md declares the project open-source but no licence file
     exists, which is a real blocker for outside contributions.
  3. Add a `.gitattributes` and confirm no CRLF/filemode churn.
  4. `git remote add origin <url>` then push `main`.
  5. Consider branch protection on `main` and a PR workflow, since gates are the project's main quality
     control — a gate is far less meaningful if anyone can push straight to `main`.
  6. Decide whether `TODO.md` / `COMPLETED.md` / `docs/reviews/` stay public. They are honest and
     useful, but they narrate internal process; keeping them is defensible, hiding them is not.

- [ ] **4.3 — Hash-pin the requirements** `R9`, plan §5
  Versions are pinned and the closure is verified byte-reproducible (95 packages), but
  `requirements/*.txt` carry no hashes. Run `pip-compile --generate-hashes` with a **larger timeout**
  than the 120s default — the first attempt timed out resolving hashes for 97 packages.

- [ ] **4.4 — `docs/spec/` is empty**
  Intended home for the data-model/API spec once it stabilises past the plan.

---

## 5. Remaining phases

Phases 1–3 have code in place; Phases 4–10 are unstarted. Full task/AC detail in
`docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §9.

- [ ] **5.1 — Phase 4: Ingestion** (AGENTS.md §4.1) — gated by §1.1, §1.4
  jsonpath-ng mapping engine, `POST /api/v1/alerts/webhook/{source_id}`, size cap **before** parse,
  per-source secret, per-source + per-IP throttle, idempotent replay, T1 `alert` CRUD + `/raw`.

- [ ] **5.2 — Phase 5: Escalation, Observables, Tasks, Timeline** (AGENTS.md §4.2–4.3)
  `merge/{caseId}` + `import/{caseId}`, correlation engine on `correlation_key`, typed observable
  extraction + global dedupe, cross-case graph, `{idOrName}` by UUID **or** case `number`.
  Gated by §1.1, §1.8.

- [ ] **5.3 — Phase 6: Orchestration Gateway** (AGENTS.md §4.4) — **closes the MVP loop**
  Domain events → Celery with `idempotency_key`, dispatched on `transaction.on_commit`; playbook
  executor with SSRF guard; results written to `AutomationRun.output_log` **and** the case timeline.
  Gated by §1.6 (pending-run index).

- [ ] **5.4 — Phase 7: Realtime Ledger** (AGENTS.md §2 Module B)
  Case-scoped WebSocket rooms, session-authenticated handshake, single publisher choke point,
  HTMX fallback for non-WebSocket clients.

- [ ] **5.5 — Phase 8: Query API** (T2)
  `POST /api/v1/query` DSL, `X-Total`, bare-array responses. Unimplemented operators must 400, never
  silently return a wrong answer. Includes **AC8.4**: TheHive4py unmodified against a live server.

- [ ] **5.6 — Phase 9: Minimal UI**
  Dark, keyboard-first, TheHive-aligned. Accessible per WCAG AA.

- [ ] **5.7 — Phase 10: Conformance, Security & Performance**
  Contract tests per T1 endpoint, `security-auditor` pass, `EXPLAIN` review of the five hot paths,
  `verifier` pass over every AC.

- [ ] **5.8 — AC1.3 / AC1.4 verification**
  `celery inspect ping` and `/readyz` 503-when-Redis-down were never exercised — no Redis is running.
  Verify as part of §3.1.

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

---

## 7. Process improvements identified

- [ ] **7.1 — Demonstrate failure modes for every AC** `R11`
  Adopted as a rule; make mutation checks part of the Definition of Done, not a one-off.

- [ ] **7.2 — Keep the domain-expert gate on every phase, not just Phase 3**
  The `db-postgres` review found 4 Criticals and refuted 2 of the planner's own 4 findings. The
  division of labour (planner proposes, expert verifies with evidence, planner amends the plan) is
  what caught these.

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
  ruff · ruff-format · mypy (scope pinned, 73 files) · `manage.py check` · migration-sync · pytest.
  `make check-fast` is the 3.9s subset the pre-commit hook runs on every save.

- [x] **8.4 — Pre-commit and pre-push hooks installed** (`make hooks`)
  Pre-commit blocks bad lint and failing tests, and skips docs-only commits. **Verified by probe**, not
  assumed: a lint violation and a failing test were both blocked; a docs-only commit passed through.
  Pre-push additionally requires `make check` **and** a green mutation-guard run, because the gate is
  the project's main quality control.

- [ ] **8.5 — Extend mutation coverage to the remaining contract assertions**
  Round 2 found the FK `on_delete` audit blind to `CASCADE` (C-2). Guards now cover column rename, FK
  `related_name`, redundant index, nullable column, UNIQUE, and CHECK. Still unguarded: `on_delete`
  policy per relation, `severity`/`pap` ranges, `idempotency_key` uniqueness (`H-3`), `slug` uniqueness
  (`H-4`), and the `ar_pending_idx` partial predicate (`H-1`).

- [ ] **8.6 — Guard the CI invocation itself**
  The hooks pin the commands, but nothing pins them on a remote runner. A single workflow file running
  `make check` + `make coverage` is needed when this moves to GitHub (§4.5).
