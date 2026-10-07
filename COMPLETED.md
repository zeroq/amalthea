# COMPLETED — Amalthea

Finished work, with the evidence that it actually happened. Open items live in
[`TODO.md`](./TODO.md).
Last updated: 2026-10-06

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

## 2026-10-05 — Phase 3 gate round 3 (independent review)

- [x] **Round-3 gate run against `bda16e5`.** Verdict **FAIL — 0 Critical, 5 High.** Report:
  `docs/reviews/REVIEW-2026-10-05-phase3-schema-gate-round3.md`. Critically, **all eight in-scope
  round-2 items were independently verified closed**, and the round-1 `C-1` evidence defect did not
  recur — all 15 mutation guards land real DDL changes and produce concrete failure text. The two
  prior rounds' Criticals are genuinely gone.
- [x] **Two reviewer claims independently re-tested rather than accepted.** My first attempt to
  reproduce `H3-1` used `data_hash(o.normalized_data)` instead of `data_hash(canonical_value(o))`,
  which showed no staleness and would have led me to wrongly reject a valid High; a second attempt
  inserted a value the constraint happened to still catch. Re-reading the code and testing the
  single-row case showed the reviewer right: with one row present, flipping `is_case_sensitive` and
  re-inserting the same artifact **is admitted as a duplicate**.
- [x] **`H3-2` verified by direct A/B experiment.** My dependency-pin comment claimed the pin
  prevented a `case_record` cascade. Both pins unapply the **same 13 migrations**; the pin only
  changes rollback *order*, turning a loud corruption into a silent one. Comments rewritten with the
  measured behaviour and the false safety claim explicitly retracted.
- [x] **Honest status:** the seed-rollback path is broken on SQLite under *any* dependency choice,
  because `observables/0003` + `alerts/0004` are not cleanly reversible. That is now tracked as its
  own finding rather than papered over by a comment that claimed a fix it never provided.

## 2026-10-05 — H3-3: the R11 standard can no longer be satisfied by mentioning a keyword

- [x] **`test_mutation_standard.py` was itself the loophole.** It enforced the standard with
  `any(m in src for m in VERIFIED_MECHANISMS)` — a substring search over the guard's own source. A
  `test_mutation_*` guard that mutated nothing and merely mentioned `_meta` passed. An enforcement
  mechanism weaker than the standard it enforces is the one failure mode R11 exists to prevent, and
  it survived two gate rounds undetected.
- [x] **Rewritten around AST analysis.** `analyse_guard()` requires a *located mutation site*
  (verified DDL context manager, self-verifying helper, raw `cursor.execute`, or an attribute
  assignment) **and** a *located verification site* (DDL read-back, self-verifying helper, or an
  `assert` reading the mutated attribute back). Matching on AST attribute nodes rather than substrings
  is what stops a guard faking its read-back from a string literal or a comment — both are now
  rejected cases.
- [x] **Negative self-tests make the standard non-vacuous in its own right.** Six deliberately-broken
  guards must be rejected (the exact round-3 bypass, a tautology, a bare local rebind, an unverified
  attribute edit, and read-backs faked from a string and from a comment); four sound guards must be
  accepted so the fix cannot degenerate into rejecting everything.
- [x] **Two structural tests block regression to the old technique:** no name in the module may be
  bound from `inspect.getsource`, and `analyse_guard` may not gain a text parameter.
- [x] **The standard immediately caught a real gap in the guards, not just in itself.** Guards 2 and
  4 mutated `remote_field.related_name` and `login.null`/`login.blank` and then asserted the
  consequence — without ever asserting the assignment took effect on the object the real assertion
  reads. Both now read the value back first. This is the first time the standard has flagged the
  guards rather than the code under test, which is the behaviour R11 was written to produce.
- [x] **`test_the_mutation_helper_still_verifies` was rewritten to be behavioural.** It previously
  read `inspect.getsource(schema_mutation)` and grepped for the sentinel string — the very technique
  this file forbids, used to protect the mechanism that forbids it, and it would have passed if the
  string survived only in a comment. It now *executes* `schema_mutation` over an unchanged table and
  requires the no-op to raise, with a positive control so the assertion cannot pass for the wrong
  reason.
- [x] **Mutation-verified, three ways.** Neutering `analyse_guard` fails all 6 rejection cases;
  reverting the read-back matcher to a raw substring test fails 6 tests; dropping the helper folding
  fails 4. Gate: **298 passed, 1 skipped** (was 286).

## 2026-10-06 — Phases 4–6 & 9: the MVP loop ships, end to end

- [x] **`H3-1` — vocabulary changes no longer silently invalidate stored digests** (commit `7bea08a`).
  `ObservableType.save()` re-hashes affected `Observable` rows when `is_case_sensitive` flips; a
  backfill path handles post-re-canonicalisation collisions. Mutation-verified.
- [x] **`H3-4`/`M-1` — `correlation_key` is derived** (commit `ac6c8b0`). `ingest/mapping.py`
  extracts it from `correlation_key`/`correlationKey`/`correlation` JSON-path rules via jsonpath-ng;
  the `(correlation_key, date)` index is now actually exercised.
- [x] **Phase 4 — Webhook ingestion hardened** (commit `b3643ed`). Hasher-backed secret check,
  non-object/depth refusal, per-request env limits (`WEBHOOK_MAX_BODY_SIZE`, `WEBHOOK_MAX_DEPTH`),
  per-source + per-IP cache throttling, idempotent replay (201 new / 200 replay). 36 hardening tests.
- [x] **Phase 5 — Escalation, observables, tasks, timeline.** Webhook → alert → case import/merge →
  typed observable extraction with global dedupe → cross-case graph; `{idOrName}` by UUID or case
  number. `test_mvp_loop.py` plus `test_case_numbering.py` cover the path.
- [x] **Phase 6 — Orchestration gateway closes the MVP loop** (commits `f2788a4`, `b8fa95b`).
  Domain events → Celery with `idempotency_key`, dispatched on `transaction.on_commit` (AC6.6
  rollback safety), SSRF-guarded executor with redirect hop cap and output size cap, results written
  to `AutomationRun.output_log` **and** the case timeline. **Real bug found by smoke test:** the
  idempotency key was scoped to the observable alone, so linking a shared observable into a second
  case reused case A's run — scoped per (observable, case) link instead. 22 executor tests + 6
  loop tests.
- [x] **Phase 9 — Minimal analyst UI** (commits `d150f01`, `dd2354d`). Server-rendered Django
  templates + HTMX + dark keyboard-first CSS: dashboard, alert triage (escalate/merge), case ledger
  (status, notes, tasks, artifacts, timeline), automation and sources pages. Session auth, POST-only
  mutations, CSRF, WCAG-aware severity rendering (text + colour). Fixed `LOGIN_URL` so anonymous
  visits land on `/login` instead of Django's stock 404. 25 UI-loop tests.
- [x] **The AGENTS.md §4 MVP loop is proven live, not just in tests.** Real HTTP against a running
  dev server: webhook 201 → UI login → escalate → `attacker@example.net` extracted → `enrich-mail`
  playbook Success → timeline entry. Dev settings run Celery **eager** so no Redis is needed locally.
- [x] Gate at this state: **429 passed, 1 skipped** (the skip is the Postgres-only H-6 sequence
  collision test, TODO §3.1).

## 2026-10-06 — Cheap closes

- [x] **2.4/`M5` — reverse-direction link-table indexes were verified already present**
  `caseobs_obs_case_idx (observable, case)` and `alertobs_obs_alert_idx (observable, alert)` exist in
  live DDL and are asserted in `test_indexes.py` — declared during the round-2 H-5 work. TODO item
  marked done.
- [x] **2.2 — mypy strict is clean** — 88 source files, 0 errors, `make check` green. Residue items
  from the earlier listing (celery stubs, channels arg-type, custom-field generic bounds) resolved
  via documented per-module overrides.
- [x] **4.1 — `README.md` written.** Quickstart, TheHive compatibility statement, architecture
  summary, repo map, production notes, links to plan/ADRs/reviews. Unblocks `pyproject.toml`
  packaging.
- [x] **Decisions 3.2–3.6 recorded** in plan §14 (single-tenant + nullable org FK; sanitized
  CommonMark; independent `Alert.type` taxonomy; severity default 2 + warning; per-source
  `correlation_key` with 10-minute default window). Each matches the already-implemented code, so
  these are recordings, not retrofits.
- [x] **TODO.md rewritten** to reflect reality: Phases 4–6/9 marked done, round-3 highs resolved,
  most 2.5 items closed, remaining open work re-scoped (Phase 7 next, Postgres gate, coverage 78%→80%).

## 2026-10-06 — Postgres gate (TODO 3.1) executed

- [x] **§3.1 Postgres verification — DONE.** Full conformance suite on real PostgreSQL 16
  (`--ds=amalthea.settings.test_pg`): **408 passed, 24 skipped, 0 failures**. SQLite `make check`
  stays green: **429 passed, 3 skipped**.
- [x] **Review round-3 §6(a)–(e) closed** (`docs/reviews/REVIEW-2026-10-05-...round3.md`).
  - (a) H-6 collision test + (c) L-3 prefix guard + (d) btree tuple cap: settled green on Postgres.
  - (b) sequence race: **fixed** in `cases/numbering.py` — `setval`'s stale read is now serialized by
    `pg_advisory_xact_lock(hashtext('case_number_seq'))` (FOR UPDATE and LOCK TABLE are both rejected
    by Postgres for sequences). New `test_h6_concurrent_imports_cannot_walk_the_sequence_backwards`
    (Postgres-only, two-session barrier) fails against the pre-fix SQL (2/10 runs with the window
    widened) and passes 10/10 now; the lock token is also text-pinned in the statement test.
  - (e) `ar_pending_idx`: new Postgres-only `test_the_postgres_planner_picks_the_pending_run_index`
    (EXPLAIN with `enable_seqscan=off`: Pending sweep must be an index scan with no Sort; Success
    sweep must not touch the index). Bite-proven: recreating the index without the `WHERE` clause
    makes the Success sweep use it, failing the guard.
- [x] **AC1.3 live** — `celery -A amalthea inspect ping` against a real worker on the Redis broker:
  `pong, 1 node online`. (Broker URL required `CELERY_BROKER_URL=redis://...`; prod settings rely on
  env as documented.)
- [x] **AC1.4 live** — `/readyz` → HTTP 200 `{"status":"ok"}` with Postgres migrated + Redis up;
  503-when-Redis-down path remains covered by `test_readyz_fails_when_redis_is_unreachable`.
- [x] **pytest-django gotcha fixed while here:** content-type poisoning from
  `transaction=True`-without-`serialized_rollback` teardown flushes (m11 + H-6 + the new concurrency
  test now all use `serialized_rollback=True`); `test_numbers_increase_and_are_unique` asserts
  relative monotonicity instead of absolute numbers because Postgres sequences are non-transactional.

## 2026-10-06 — Phase 7: the realtime ledger closes the loop

- [x] **Ledger choke point.** All timeline writes flow through one service
  (`realtime/ledger.py::append_timeline_event`); `SCANNED_APPS` grew `alerts/` so the schema-mutation
  guard proves the choke point (re-introducing a direct `TimelineEvent.objects.create` in
  `alerts/escalation.py` fails `test_the_ledger_service_is_the_only_writer_of_timeline_rows`).
- [x] **WebSocket sync protocol** (`/ws/case/{id}/`): `ledger.append` events publish per-case,
  `ping`/`pong`, `sync` replays missed events by id. `test_ws_integration_client.py` exercises the
  real Channels in-memory layer: 4401/4000 "refused" events prove per-room fan-out, and the
  HTMX fallback re-reads the timeline when WS is unavailable.
- [x] Gate: **440 passed / 3 skipped** on SQLite (`make check`); Postgres `test_pg` suite stays green.

## 2026-10-06 — Phase 8: Query API (T2)

- [x] **`POST /api/v1/query`** drives TheHive-shaped reads without leaking the internal ORM: steps
  `listCase/listAlert/listObservable/listAny/getCase/filter/sort/page/count`, operators
  `_eq/_ne/_gt/_gte/_lt/_lte/_between/_in/_like/_has/_and/_or/_not`, bare-array responses with
  `X-Total` when requested, epoch-ms timestamps, `count` answering a bare int. Unimplemented ops
  400 naming the operator. Bodies are built with the real thehive4py builders in the suite.
  (Brief: `docs/planning/BRIEF-2026-10-06-query-api.md`; deviations P8-1..P8-7 in plan §13.)
- [x] **AC8.4 verified live, not just in tests.** thehive4py 2.1.0 unmodified against a running
  prod-settings server (seeded local Postgres container): `case.find` → 1 case, `alert.create` →
  201, `alert.merge_into_case` → `OutputCase`, `case.get_timeline` → `{"events": [...]}` with
  `alert.occurred`. **The live gate surfaced three gaps closed in this phase:**
  - `POST /api/v1/alert` (T1 `InputCreateAlert`) existed only as a webhook path — `alert.create`
    returned 405. Added the create endpoint (P8-5).
  - `merge/{caseId}` returned the **alert**, but 5.8.0's contract and thehive4py say the **case**
    (P8-6).
  - `channels-redis` was configured by prod settings but never a pinned dependency; the merge's
    ledger publish hit `ModuleNotFoundError` (P8-7). Pinned `channels-redis==4.3.0`.
  - Also: `GET /api/v1/case/{id}/timeline` now returns `{"events": [...]}` (5.8.0 `OutputTimeline`,
    P8-1) — the internal Phase 7 ledger shape is untouched.
- [x] Final gate: SQLite `make check` **491 passed / 3 skipped**; Postgres `test_pg`
  **470 passed / 24 skipped** (query suite is 50 tests). mypy/ruff clean; `makemigrations
  --check` clean (query app adds no models).

## 2026-10-07 — Phase 10a: T1 surface closure + Phase 10: Conformance, Security & Performance

Plans: `docs/planning/PLAN-2026-10-07-phase10-t1-closure.md` /
`BRIEF-2026-10-07-phase10-t1-closure.md`. User-approved **Option 1**: implement the missing T1
endpoints rather than shrink the contract (AC10.1/AC10.3 required them to exist).

- [x] **The twelve missing T1 groups now exist.** `POST /api/v1/login`; `GET/POST /logout`;
  `DELETE /alert/{id}`; `POST /alert/{id}/observable` (links `AlertObservable`, no automation
  dispatch at link time — the case path owns that); `DELETE /case/{id}` (alerts survive via
  `SET_NULL`, tasks/links/timeline/customFieldValues cascade); `DELETE /case/{id}/alert/{alertId}`
  (unlink + `alert-removed` ledger entry); `POST /case/{caseId}/customEvent`;
  `GET/PATCH/DELETE /task/{taskId}` (task statuses are the model's `Waiting/InProgress/Done`);
  `PATCH/DELETE /customEvent/{eventId}` (system kinds → 400); `GET /customField`;
  `PATCH/DELETE /observable/{id}`. New serializers `task_json`, `custom_event_json`,
  `custom_field_json`, `user_json` in `core/serializers.py`. All mutations scoped via
  `ScopePermission` wired into `DEFAULT_PERMISSION_CLASSES` (P10-z); read-only keys now 403 on every
  mutable verb including `POST /api/v1/query`.
- [x] **`compat/mappers/` deleted** (P10-a): nine empty stub files, zero importers — dead weight
  that blocked the coverage gate (supersedes §13-13). Wire rendering lives only in
  `core/serializers.py`.
- [x] **Conformance surface added.** `test_t1_surface.py` (42 contract tests), `test_unknown_fields.py`
  (33, unknown-field policy), `test_authz.py` rewritten as a 31-route × 4-actor matrix (125 rows),
  `test_thehive_fixtures.py` rewritten with pinned keys (13), new golden fixtures
  (task/custom_event/custom_field/user/alert_observable). Two latent bugs found and fixed:
  `case_task_create` defaulted `"Todo"` (violates the PG CHECK constraint; now `"Waiting"`) and a
  `@api_view` decorator misuse that 500'd every task creation.
- [x] **Security-auditor pass (Wave C) + fix burst.** F1 (login type-hostility + exception-text
  leak), F3 (unbounded customEvent title), F5 (`parse_timestamp` inf/nan 500), F6 (envelope echoes
  `str(exc)`), F7/F8 test/doc pins — **fixed**. F2 (org-blind case/alert resolution) and F9 (prod
  cookie/secret hardening) **recorded, deferred to the tenant-isolation follow-up** (nothing sets
  `owner_org` today), alongside the F3-observable blast-radius belt suggestion. pip-audit 0 CVEs;
  bandit no new findings.
- [x] **Postgres EXPLAIN review (Wave D) → AC10.5 MET.** All five hot paths index-driven (<2ms at
  6k alerts / 29k timeline / 5.8k automation-run seed on scratch DB `amalthea_perf`).
  `events_after` keyset rewritten (range predicate + negated tie-break) — D-F1 was filtering
  12,501 rows for a 50-row page on PG, invisible on SQLite. `test_ledger_keyset.py` pins the
  semantics. Borderline D-F2 (one case owning ~86% of the timeline) recorded; mitigation = paging.
- [x] **Final gate (2026-10-07):** SQLite `make check` **724 passed / 3 skipped** (baseline 491/3);
  Postgres `test_pg` **703 passed / 24 skipped** (baseline 470/24); coverage **83.07%** (gate
  ≥80 — TODO §2.1 DONE); ruff check + format clean (164 files); mypy clean (86 files);
  `makemigrations --check` clean (no new migrations). Deviations P10-1..P10-11 recorded in plan §13;
  verifier report: `docs/planning/VERIFY-2026-10-07-phase10.md`.
- [x] **Verifier closure (2026-10-07).** The read-only verify pass (23 Met / 2 Deviated / 0 Not Met)
  surfaced one real interop defect and four test gaps, all now closed:
  - **V1 — slashless `POST /api/v1/case/{id}/observable` answered 405** — the exact spelling
    thehive4py 2.1.0 posts (`endpoints/observable.py:38`). The pre-wave route split served GET on
    the slashless path and POST on the slashed one; both spellings now resolve to one GET+POST
    dispatcher (`case_observable_list` / undecorated `case_observable_add` helper, the
    `case_task_list` pattern). DRF `MethodNotAllowed` also now maps to a fixed `BadRequest`
    envelope instead of stringifying `ErrorDetail` into `GenericError` (`compat/errors.py`).
    Pinned by `test_case_observable_post_slashless_is_the_spelling_thehive4py_uses`.
  - **V3/V4 — three untested §7.2 rows pinned:** `POST /logout` (parametrised over GET+POST),
    `POST /alert/{id}/import/{caseId}`, the array-`data` branch of `POST /alert/{id}/observable`.
  - **V5 — two matrix gaps pinned:** `alert-import-into` added to the authz matrix; the
    per-IP webhook counter got `test_throttling_is_per_ip_not_global`; the
    `case-observable-create` authz + unknown-field rows now post **slashless** (the real spelling).
  - **Durability:** the five `EXPLAIN` transcripts moved from ephemeral `/tmp` to committed
    `docs/perf/`. Deviations P10-12/P10-13 record all of it.
  **Post-closure gate:** SQLite `make check` **733 passed / 3 skipped**; Postgres `test_pg`
  **712 passed / 24 skipped**; coverage **83.10%**; ruff + mypy clean.
