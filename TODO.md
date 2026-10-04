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

- [ ] **1.12 — The mutation guards cannot mutate UNIQUE constraints on SQLite** (found by planner probe)
  A `UNIQUE` constraint declared in a table definition is backed on SQLite by an auto-index named
  `sqlite_autoindex_<table>_N`, **not** by the constraint's own name. So `DROP INDEX IF EXISTS
  "uniq_obs_dtype_hash"` — and the same trick for any `UniqueConstraint` — is a **silent no-op**, and the
  guard reports "caught" or "not caught" for a mutation that never happened.
  Consequence: `test_schema_mutation_guards.py` proves non-vacuity for **named** indexes, columns, and
  model metadata, but nothing currently proves the suite would notice a **dropped or weakened UNIQUE
  constraint** — including `(data_type, data_hash)`, which is the entire point of the `H3` fix.
  Fix: a guard that rebuilds the table (create-copy-drop-rename) or asserts the uniqueness *behaviourally*
  (attempt a duplicate insert, expect `IntegrityError`) rather than by name. Behavioural assertion is
  preferable — it is engine-independent and tests the contract rather than the implementation.
  Also: `scripts/lock.sh` reports `runtime=48`, which is the **declared-pin count**, not the
  `pip freeze` closure (96 = 48 base + 48 dev). Confirmed healthy via `pip check`; not a regression,
  but the two numbers are easy to misread as a dependency loss.

### Remaining before the re-gate
- [ ] **1.9 — Decide the fate of `test_mutation_probe.py`** — **RULED: keep, permanently** ✅ *done*
  Renamed `tests/conformance/test_schema_mutation_guards.py`; "TEMPORARY"/"deleted after" framing
  removed; docstring now cites R11 and explains that deleting the file would delete the evidence. The
  `ruff W292` is fixed. **See 1.12 for a gap in its coverage.**

- [ ] **1.10 — Confirm the "also fix" batch landed** — **ALL CONFIRMED** ✅ *done*
  `H4` (`CASE_SENSITIVE_TYPES = ('file',)`), `H6` (UUID PKs, `login` unique+non-blank,
  `prefix` unique), `M4` (`(case, custom_field)` / `(alert, custom_field)` uniqueness),
  `M6` (`case_record`, consistent across models/migrations), `M10` (all three seed migrations use
  `RunPython.noop` — **no data loss on rollback**), `M12` (`imported` → `Imported`),
  `M14` (`playbook` FK + `playbook_name` snapshot), `L4` (`closed_date` on transition),
  `L5` (`start_date`/`date` NOT NULL with `default=timezone.now`). Planner-verified by direct
  inspection, not by the agent's report.

- [ ] **1.11 — Re-run the `db-postgres` gate**
  Every Critical must be independently re-verified, including against a mutated schema. Blocked items
  `H3`/`C2`/`C4` can only be *partially* confirmed on SQLite (§3.1). Include **1.12** in its scope.

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

- [ ] **4.2 — Not a git repository**
  The whole workflow assumes version control (deviations recorded, gates reviewed, ACs verified).
  `git init`, commit the Phase 0–3 state as a baseline, add `docs/` and lock files.

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
