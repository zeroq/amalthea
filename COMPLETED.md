# COMPLETED — Amalthea

Finished work, with the evidence that it actually happened. Open items live in
[`TODO.md`](./TODO.md).
Last updated: 2026-10-03

Items are only listed here once independently verified. Where code exists but an acceptance criterion
is still unproven, the item says so explicitly and the verification gap is cross-linked in `TODO.md`.

---

## 1. Specification & decisions

- [x] **TheHive 5.8.0 ground-truth contract extracted**
  Fetched the official `openapi.yaml` from the TheHive-5 repo rather than relying on recall, and
  derived the wire contract from it: UUID internally / `_id` as a string on the wire; epoch-millisecond
  timestamps; `severity` 1–4, `tlp` 0–4, `pap` 0–3; task statuses
  `Waiting|InProgress|Completed|Cancel`; error envelope `{"type","message"}` with
  `BadRequest` / `AuthenticationError` / `AuthorizationError` / `NotFoundError` / `GenericError`.
  Also settled two things recall would have gotten wrong: observables are **global** entities with
  `dataType`/`data` (not case-owned `type`/`value` records), and statuses are **entities** with a
  derived `stage`, where unknown values are tolerated on ingest and recorded in `ingestion_warnings`.

- [x] **ADR-001 — backend framework** `docs/decisions/ADR-001-backend-framework.md` — *Accepted*
  Django 5.x + DRF + Channels over FastAPI: native ORM/admin/auth, one process serves HTML + API +
  WebSockets, mature Celery support. Accepted cost: async/sync boundary complexity; a real async
  client (Falcon/uvicorn workers) is left as an open question.

- [x] **ADR-002 — TheHive API compatibility** `docs/decisions/ADR-002-thehive-api-compatibility.md` — *Accepted*
  The defining scope decision. **Compatibility applies to the external integration endpoints and their
  accepted data formats — not to TheHive's UI behaviour.** Amalthea serves both an internal,
  Django-clean representation and a TheHive-shaped wire representation from the same resources; internal
  code never sees wire field names. Also fixes a config-breaking name collision (`source` on both
  `Alert` and `IngestionSource` would have shadowed `Alert.source` at class scope).

- [x] **Plan finalized and approved** `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` — *Approved*
  Goals, non-goals, stack rationale, data model, endpoint inventory, UI direction, Phases 0–10 with
  per-task acceptance criteria, security model, testing strategy, 12 risks, 12 deviations, 5 open
  questions.

- [x] **Superseded plan archived** `docs/planning/PLAN-2026-10-03-foundations-mvp.md` — marked *Superseded*

- [x] **Implementation brief written** `docs/planning/BRIEF-2026-10-03-phases-1-6.md`
  Wave breakdown, the one-way `compat/` dependency rule, and explicit stop-and-report gates per phase.

- [x] **Comparison against reference projects** — TheHive, DFIR-IRIS, Aurora-IR, Kanvas, FIR
  Positioning settled: case management, not ticketing; orchestration, not a separate SOAR; a schema-
  agnostic receiver; observables as standalone entities. Confirmed TheHive (Cortex) is action-based
  *and* alert-based, which drove the decision to commit to the alert-based path.

---

## 2. Environment (Phase 0)

- [x] **Virtualenv + full stack installed** — Python `3.14.6`, Django `5.2.17`
- [x] **Dependencies pinned and closure proven reproducible** — 95 packages, byte-identical closure
  (`scripts/lock.sh`); originally 95 with base+dev resolved from scratch
- [x] **`pyproject.toml`** — ruff, pytest, coverage, mypy strict, bandit, build metadata
- [x] **`docker-compose.yml`** — PostgreSQL 16 + Redis 7; `docker-compose config` exits `0`
- [x] **`.env.example`, `.gitignore`**
- [x] **`.venv` dependency closure re-verified after environment drift** — an abandoned
  `pip-compile` attempt left a third partial install; cleaned back to an exact 95-package closure

---

## 3. Code implemented

### Phase 1 — Django foundations *(code complete; two ACs unverified → TODO 5.8)*
- [x] Settings split `base` / `dev` / `test` / `prod`, `USE_TZ=True`, `TIME_ZONE="UTC"`
- [x] ASGI entrypoint and Celery app; DRF configured
- [x] `manage.py check` → **0 issues**
- [x] `manage.py migrate` → **runs on a fresh SQLite DB** (was broken: `H2`)
- [x] Health endpoints implemented
- [x] App skeletons: `core`, `identity`, `cases`, `alerts`, `observables`, `ingest`, `automation`, `realtime`, `services`, `compat`

### Phase 2 — Wire-first foundation *(partial → remainder in TODO 5.1)*
- [x] `compat/time.py` — epoch-ms ↔ datetime, both directions
- [x] `compat/enums.py` — task statuses, TLP, PAP, severity, `stage_from_alert_status`
- [x] `compat/errors.py` — the `{"type","message"}` envelope
- [x] `compat/auth.py` — API-key lookup
- [x] `compat/mappers/` — bidirectional mapper skeletons
- [x] Conformance fixtures from the real TheHive 5.8.0 spec (`tests/fixtures/thehive/case_example.json`)
- [ ] DRF serializers, the ingest DTOs, and full mapper coverage → **TODO 5.1**

### Phase 3 — Domain models & migrations
- [x] All 19 domain/supporting models and **20 migrations**
- [x] All 31 FKs carry explicit `on_delete` **and** `related_name`
- [x] Seeds: statuses, observable types, custom fields, tags
- [x] `makemigrations --check --dry-run` → **no changes**
- [x] Seeds idempotent via `get_or_create`; migrations contain no destructive operation; the
  `0001`/`0002` split is Django's standard circular-FK disambiguation

### Phase 3 gate remediation — 4 Criticals + the HIGH batch *(implemented; re-gate pending → TODO 1.11)*
All independently re-verified by the planner, because the implementing agent was cancelled and never
reported.
- [x] **`C1`** `cases/numbering.py` — case numbering fixed. **Exceeds the brief**: `pre_save()` on a
  custom `AllocatedNumberField` (the only hook `bulk_create()` also passes through), a batch allocator
  because Django renders a batch's full parameter list before INSERT, and `deconstruct()` pinning the
  field path so migrations survive the class moving. The `save()` guard was **kept**, with the mypy
  "unreachable" resolved by annotating `number: int | None` rather than deleting correct logic.
- [x] **`H2`** `manage.py` now defaults to `amalthea.settings.dev` — `migrate` works, so AC3.1 is
  verifiable for the first time
- [x] **`H1`** The four vacuous schema tests rewritten over all 19 tables, plus
  `test_mutation_probe.py`, which applies **real `ALTER TABLE` mutations** and re-runs the assertions
  to prove they can fail
- [x] **`C3`** `ingest/references.py` — `sha256:<digest>` synthetic `source_ref`, surfaced as
  `source_ref_synthesised` so it is recorded in `ingestion_warnings`
- [x] **`H3`** `observables/hashing.py` — `data_hash` + unique `(data_type, data_hash)`
- [x] **`H5`/`L1`** enum `choices` + CHECK constraints; **`M1`** redundant indexes dropped;
      **`M2`/`M3`** `source_id` → `ingestion_source`

---

## 4. Quality & verification

- [x] **`ruff check .`** — 1 remaining error (`W292`, trailing newline in the new mutation probe)
- [x] **`ruff format --check`** — 122 files formatted
- [x] **`pytest`** — **185 passed** (up from 18)
- [x] **`mypy amalthea`** — **0 errors** (was 4) via targeted per-module overrides, no weakening of
  global strictness
- [x] **Coverage 68% → 73%** — reported as a non-blocking signal per plan §13 #13
- [x] **Phase 3 schema review executed** by the `db-postgres` expert
  Report: [`docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md`](./docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md)
- [x] **Plan amended per the review** — the reviewer correctly identified several findings as planner
  edits rather than its own work: §6.1 (`correlation_key`, `source_ref` fallback, `ingestion_source`
  rename, `data_hash`), §6.3 (four missing indexes + a redundancy rule + the GIN deferral), §12 R10–R12,
  §13 #10–#12
- [x] **Coverage-gate decision recorded** — plan §13 #13: ≥80% enforced at the Phase 6 MVP gate, not
  per phase, because a per-phase gate would force filler tests that assert nothing

### Verified correct by the gate review
- [x] All 19 domain tables use the UUID base model
- [x] `on_delete` semantics are sound: `PROTECT` on status/data_type (cases cannot be orphaned),
  `SET_NULL` on `Alert.case` (matches TheHive's unlink-on-delete), `CASCADE` on case-owned children
- [x] Both uniqueness constraints genuinely raise `IntegrityError`
- [x] All 10 `JSONField`s map to `jsonb` with a safe `default=dict`
- [x] `Task` lives in `db_table="task"` inside the `cases` app, per brief §1
- [x] **Four hot paths confirmed index-served** via `EXPLAIN QUERY PLAN`: alert queue, case lookup by
  number/UUID, timeline newest-first, and observable lookup **and** fan-out
- [x] **Module C's cross-case graph query** works and is index-served — the product's headline
  differentiator is genuinely in place

---

## 5. Process

- [x] **Reviewer refuted 2 of the planner's own 4 findings with executed evidence** — recorded rather
  than quietly dropped. `IntegerField` has `empty_strings_allowed = False`, so the `number is None`
  guard *does* fire; the mypy "unreachable" is a `django-stubs` artifact. The R4 SQLite/Postgres
  divergence exists but is **inverted** from the prediction: SQLite fails loudly, Postgres would fail
  silently under concurrency.
- [x] **Root cause of the gate failure identified** — four of six Phase 3 schema ACs were "verified" by
  tests that cannot fail, one with a literal `pass` body. This is why four Criticals reached a green
  suite. Recorded as risk R11 with the rule *a passing AC is only evidence once its failure mode has
  been demonstrated.*
- [x] **Tracking established** — `TODO.md` / `COMPLETED.md`, with every item cross-referenced to its
  source (AC number, review finding ID, or decision ID) and gated items marked with what they block
- [x] **Local git repository initialised** — `git init -b main`, 156 tracked files, commit `14d4fc2`
  capturing the verified baseline. Pre-commit checks confirmed `.venv/`, caches, `.env`, and
  `*.sqlite3` excluded, no oversized files, and no secrets in staged content. Promotion to a GitHub
  remote is deferred by decision and tracked in TODO §4.5 — including the two things that must be
  resolved first: **`AGENTS.md` calls the project open-source but no `LICENSE` file exists**, and the
  default repo visibility needs confirming against that intent.

## 2026-10-05 — Round-2 remediation wave (1.14–1.19)

- [x] **`C-2` / TODO 1.14 — `on_delete` audit made total.** The old probe
  (`"on_delete" in field.deconstruct()[3]`) passed on precisely the value it needed to catch, because
  `ForeignKey.deconstruct()` **omits** `on_delete` when it is `CASCADE`. Now reads
  `field.remote_field.on_delete` and pins the full policy for all 35 FKs. The `CASCADE` surface is
  enumerated explicitly (17 entries) since "can this child outlive its parent" is a domain judgement
  the schema cannot supply. **Mutation-verified:** `AlertTagLink.alert` `CASCADE`→`SET_NULL` fails.
- [x] **`H-5` / TODO 1.15 — the over-prune corrected, not reverted.** Restored the implicit index on
  the *right*-hand column of each `UNIQUE(case, custom_field)` composite. The guard now re-derives the
  whole prune from live DDL, so over-pruning fails by construction. Discovered mid-remediation that the
  prune was **correct** on the link tables — `AlertObservable` both FKs are prefix-covered — so that is
  preserved rather than blanketly restored.
- [x] **`H-6` / TODO 1.16 — explicit numbers advance `case_number_seq`**, in `pre_save` and through
  `bulk_create`. Mutation-verified. **Still Postgres-blind** (§3.1): SQLite's `MAX(number)+1` reads
  the row just written, so the collision cannot be reproduced locally; the test skips with that reason
  instead of passing vacuously.
- [x] **`H-7` / TODO 1.17 — backfill uses the runtime's `canonical_value()` normalisation**, and tests
  now assert a backfilled row satisfies the identical digest invariant as a runtime-created one.
- [x] **`H-1`/`H-3`/`H-4`/`L-2` / TODO 1.19 —** partial-index predicate guarded both ways,
  `idempotency_key` and `slug` uniqueness asserted against live keys plus duplicate-insert
  `IntegrityError`, and the three M2M through models declared explicitly with `db_index=False` on the
  covered side.
- [x] **Two independent agent dispatches were lost to provider rate limits** (`Rate limit exceeded`),
  each leaving an uncommitted, gate-red tree with no report. Recovered by treating the filesystem as
  the source of truth rather than any claim about it, and by finishing the interrupted work directly.
  Two defects introduced by the interrupted work were caught only because the gate was run:
  `AlertStatus.stage` was given flat string `choices` (**Django's own system check rejects this** — a
  `type: ignore` would have silenced mypy and left a broken app), and `correlation_key` had been
  silently narrowed 256→200 with `default=""` dropped, contradicting plan line 103. Both reverted.
- [x] **A recurring migration trap was diagnosed and prevented.** Two auto-generated migrations pinned
  a dependency on the *leaf* `observables/0004`, which made Django cascade-unapply `cases`/`alerts`
  during the seed-rollback tests and left teardown flushing against a renamed-away `case_record`
  table — 9 errors, same `no such table` signature `alerts/0005` had already documented and worked
  around. Both dependencies re-pinned to `observables/0002_seed`, the newest state actually required.
- [x] **Gate green: 286 passed, 1 skipped** (up from 214), ruff clean, `mypy` 0 errors across 73 files
  with a cold cache, migrations synchronized, Django system check clean.
