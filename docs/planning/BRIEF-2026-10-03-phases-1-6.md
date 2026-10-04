# Implementation Brief — Phases 1–6 (TheHive-Compatible Core + MVP Loop)

Date: 2026-10-03
Plan: `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` (**Approved**)
Decisions: `ADR-001-backend-framework.md`, `ADR-002-thehive-api-compatibility.md`
Environment: **already provisioned** — `.venv` on Python 3.14.6, Django 5.2.17. Do not reinstall the
world; use `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`.

---

## 0. Ground rules

1. **The plan is the contract.** Every task lists Acceptance Criteria. An AC is met only when a test
   or a command proves it.
2. **Deviations must be recorded** in plan §12/§13 *in the plan file* before the code lands. Do not
   silently diverge. If the plan is wrong, say so in your report and change the plan.
3. **Compatibility is the external integration surface only** (ADR-002 §D1/§D12). Do **not** build
   TheHive UI endpoints: no dashboards, reports, SSO config, org admin, patterns, Cortex passthrough,
   or Functions API. Do accept-and-ignore the legacy `?name=` query param.
4. **One-way dependency rule** (ADR-002 §D11). `serializers → services → ORM`. Services never import
   serializers; serializers never touch the ORM. TheHive field-name string literals
   (`_id`, `_createdAt`, `dataType`, …) live **only** under `compat/`. A lint check enforces this.
5. **Wire format is not negotiable.** Epoch-ms integer timestamps, `{"type","message"}` errors,
   severity 1–4, TLP 0–4, PAP 0–3, `customFields` in as object *or* array and out as array.
6. **Tolerate on input, strict on security** (ADR-002 §D11). Unknown status/`dataType`/custom field/
   assignee → auto-create or accept, and record an `ingestion_warnings` entry. Never 400 for these.
   Auth/authz/ownership/upload-path failures stay hard failures.
7. **Provisional decisions** (plan §14 open, do not block on them — implement so they are cheap to
   reverse, and flag in your report):
   - **Single-tenant for now.** Still create `identity.Organisation` and put a **nullable** FK on
     `Case`/`Alert` so multi-tenancy is additive later, not a rewrite.
   - **CommonMark**, sanitized, not TheHive-flavored Markdown. Recorded divergence.
   - **Default severity when a source declares none: `2` (Medium)** + a warning.
   - **Correlation default: 10 minutes**, per-source overridable.

---

## 1. Target layout

```
amalthea/                  # project package
  settings/{__init__,base,dev,prod,test}.py
  asgi.py  celery.py  routing.py  urls.py  wsgi.py
core/                      # UUIDModel, TimeStampedModel, health, events bus, pagination helpers
identity/                  # User(AbstractUser), ApiKey, Organisation
compat/                    # THE WIRE BOUNDARY — TheHive shapes live here and nowhere else
  errors.py  time.py  enums.py  auth.py  throttles.py  urls.py
  mappers/{base,case,alert,observable,task,custom_event,custom_field,user}.py
  serializers/…            # TheHive*Serializer (wire)  +  *Serializer (native, ISO-8601)
  views/…                  # DRF viewsets serving /api/v1/
  query/{engine,operators}.py
cases/                     # Case, CaseStatus, Task, TimelineEvent, Tag, CustomField, values, CaseObservable
alerts/                    # Alert, AlertStatus, AlertCustomFieldValue, AlertObservable
observables/               # Observable, ObservableType
ingest/                    # IngestionSource, mapping.py, pipeline.py, webhook view
automation/                # Playbook, AutomationRun, registry.py, dispatcher.py, executor.py
realtime/                  # Channels consumer + publisher
services/                  # per-context orchestration; imports models, never serializers
tests/{unit,integration,conformance,fixtures/thehive}/
```

App labels: `core identity compat cases alerts observables ingest automation realtime services tests`.
Do **not** create an app named `tasks` (collides conceptually with Celery); the model is `cases.Task`
with `Meta.db_table = "task"`.

---

## 2. Phase-by-phase tasks

### Phase 1 — Django Foundations
`django-admin startproject amalthea .` equivalent by hand (project package inside repo root).

- Settings split. `base.py` reads everything from env via `python-dotenv`, safe defaults, **no secrets**.
  `dev.py` → SQLite + in-memory channel layer. `prod.py` → Postgres via `DATABASE_URL`, Redis channel
  layer, secure cookie/SSL flags. `test.py` → fast, `CELERY_TASK_ALWAYS_EAGER`.
- Apps per §1. `asgi.py` with `ProtocolTypeRouter` + `AuthMiddlewareStack` + `routing.py`.
- `celery.py` with `app.autodiscover_tasks()`, Redis broker, `beat_schedule` wired for
  `automation.dispatch_pending_runs`.
- DRF defaults: exception handler = `compat.errors.thehive_exception_handler`,
  auth classes = `[ApiKeyAuthentication, BasicAuthentication, SessionAuthentication]`,
  throttles on by default. Pagination off (listing is `POST /api/v1/query`, Phase 8).
- `/healthz` (no Redis) and `/readyz` (DB + Redis + unapplied migrations).

**ACs:** AC1.1 `manage.py check` clean in dev *and* prod · AC1.2 `import amalthea.asgi` OK ·
AC1.3 `celery -A amalthea inspect ping` reaches a worker · AC1.4 `/healthz` 200 without Redis,
`/readyz` 503 when Redis is down · AC1.5 no hardcoded secrets · AC1.6 `migrate` on empty DB, no prompts.

### Phase 2 — Wire-First Foundation
Build this **before** the models, so the contract shapes them rather than the reverse.

- `compat/time.py` — `to_epoch_ms(dt)`, `parse_timestamp(v)` accepting int-ms **and** ISO-8601
  **and** `None`. No inline arithmetic anywhere else.
- `compat/enums.py` — severity 1–4 ↔ labels, TLP/PAP labels, stage derivation, task status enum.
- `compat/errors.py` — `thehive_exception_handler`; status→type map (400 `BadRequest`,
  401 `AuthenticationError`, 403 `AuthorizationError`, 404 `NotFoundError`, 500 `GenericError`);
  400 carries a `fields` map. DRF defaults must appear nowhere.
- `compat/auth.py` — `ApiKeyAuthentication`: `Authorization: Bearer <key>`, HTTP Basic, session.
  Look up by indexed `prefix`, verify hash, enforce `scope` at the **permission** layer.
  No bearer tokens in query strings or logs.
- `compat/mappers/` — translator base + per-entity translators, unit-tested against
  `tests/fixtures/thehive/` seeded with the **v5.8.0 spec's own examples**
  (the `~98112`-style case example, alert example, observable example).
- `tests/conformance/` harness + marker wiring.

**ACs:** AC2.1 error bodies are exactly `{"type","message"}` · AC2.2 bad/missing key → **401**
(not 403) with `AuthenticationError` · AC2.3 read-scoped key on a write → 403 `AuthorizationError` ·
AC2.4 int-ms, ISO-8601 and `null` all parse; output always int-ms · AC2.5 severity/TLP/PAP round-trip,
out-of-range → 400 with `fields` · AC2.6 spec-example fixtures serialize without error.

### Phase 3 — Domain Models & Migrations
`db-postgres` reviews this phase before it is accepted.

- All models per plan §6.1/§6.2/§6.3 — that section is the field-level spec; follow it literally.
- UUID PKs on every domain entity, `timestamptz`, JSONB, explicit `on_delete`, `related_name` on
  every FK.
- Constraints: unique `(source, type, source_ref)` on Alert; unique `(data_type, normalized_data)`
  on Observable; unique link pairs; unique `Case.number` from a Postgres sequence.
- Idempotent seed migration: case statuses `New/InProgress/Contained/Closed`; alert statuses
  `New/Triaged/Dismissed/Imported`; observable types `ip domain fqdn url mail file hash user other`
  (from TheHive's own integration fixtures).
- `core/events.py` — typed domain-event dataclasses (`alert.ingested`, `case.status_changed`,
  `observable.created`, `task.completed`) dispatched on `transaction.on_commit`.
- Finish the Phase 2 mappers against the real models.

**ACs:** AC3.1 `makemigrations --check` clean, `migrate` on empty DB · AC3.2 introspection test
proves every §6 field exists · AC3.3 duplicate `sourceRef` and duplicate observable both raise
`IntegrityError` · AC3.4 every FK has `on_delete` + `related_name` · AC3.5 §6.3 indexes exist
(introspected) · AC3.6 all domain PKs are UUID · AC3.7 **CI runs the suite on Postgres**, SQLite-only
is not accepted.

### Phase 4 — Ingestion (AGENTS.md §4.1)
- `ingest/mapping.py` — jsonpath-ng rules from `IngestionSource.mapping_config`
  (`{field: jsonpath}`) → normalized alert fields.
- `ingest/pipeline.py` — validate → map → dedupe on `(source,type,sourceRef)` → persist →
  correlate → emit `alert.ingested`. Idempotent on replay.
- `POST /api/v1/alerts/webhook/{source_id}` — per-source secret, **size cap before parse**, strict
  JSON only, depth limits, per-source + per-IP throttle.
- T1 `alert` CRUD (`POST/GET/PATCH/DELETE /api/v1/alert/{alertId}`) + `GET /api/v1/alert/{id}/raw`.
- Auto-create unknown status/`dataType`/tags with `ingestion_warnings`.

**ACs:** AC4.1 mock phishing JSON → 201 + `_id`, `raw_payload` byte-identical to the request body ·
AC4.2 JSON-path rules extract title/severity/src_ip/user from **two structurally different** payload
shapes (nested, and flat-with-dotted-keys) · AC4.3 unconfigured source ingests without error, warning
recorded, no 500 · AC4.4 replaying a `sourceRef` is idempotent (one Alert, no duplicate event) ·
AC4.5 bad/absent secret → 401/403, oversized → 413 · AC4.6 malformed payload → 400 with `fields`,
never 500 · AC4.7 mapped severity lands int 1–4 with a correct `severityLabel`.

### Phase 5 — Escalation, Observables, Tasks, Timeline (AGENTS.md §4.2–4.3)
- `POST /api/v1/alert/{alertId}/merge/{caseId}` and `.../import/{caseId}`.
- `services/correlation.py` — bundle by correlating attribute within a window (default 10 min,
  per-source configurable).
- `services/observables.py` — typed extraction, normalization (lowercase unless the type is
  case-sensitive), **global** dedupe.
- `services/observables_graph.py` — cross-case neighbourhood query (Module C).
- T1 `case`, `task`, `observable`, `customEvent`, `timeline` endpoints + link tables.
- `{idOrName}` resolves by UUID **or** case `number`.

**ACs:** AC5.1 merge sets `alert.case`, leaves the alert otherwise intact, appends a
`status_change` TimelineEvent · AC5.2 two alerts sharing a destination IP within 10 min bundle into
one case; 30 min apart do not; **exactly 10 min is covered** · AC5.3 email extracted as
`dataType=mail`, re-extraction reuses the same row · AC5.4 hashes normalize lowercase,
case-sensitive types do not · AC5.5 one observable on 3 cases appears once in
`/observable/{id}` and is reachable from all 3 · AC5.6 `GET /case/{number}` works · AC5.7 timeline is
newest-first, paginated, **no N+1** (assert via `assertNumQueries`).

### Phase 6 — Orchestration Gateway (AGENTS.md §4.4) → **MVP LOOP COMPLETE**
- `automation/registry.py` — DB-backed `Playbook` with trigger bindings.
- `automation/dispatcher.py` — event → Celery task with `idempotency_key`, dispatched via
  `transaction.on_commit`.
- `automation/executor.py` — outbound-HTTP action + registered-Python action; capture
  `output_log`; **SSRF guard** (scheme allowlist, private-IP block, redirect limit, mandatory
  timeout). No `eval`/`pickle` of caller-supplied input.
- Feedback loop — result into `AutomationRun.output_log` **and** a `TimelineEvent`.
- Retries with exponential backoff; failure recorded, never raised into the request.

**ACs:** AC6.1 observable creation fires its bound playbook; run reaches `Success` with non-empty
`output_log` · AC6.2 the result is readable as a TimelineEvent on the case · AC6.3 the API call returns
**before** the playbook completes (prove by timing) · AC6.4 a failing action yields `Failed` + error
in `output_log` and does not 500 the originating call · AC6.5 replaying the trigger produces no second
run · AC6.6 a rolled-back transaction emits no events and no runs.

---

## 3. Definition of Done for this brief

- `ruff check .` and `ruff format --check .` clean.
- `mypy amalthea` clean under `strict = true`.
- `pytest` green, coverage ≥ 80, `bandit -r amalthea` no high/medium.
- One **end-to-end test** asserting the AGENTS.md §4 loop with no mocks:
  webhook POST → alert → merge into case → email observable extracted → playbook run → result visible
  on the case timeline. This single test is the MVP proof; AC4–AC6 all feed it.
- T1 endpoints covered by conformance tests keyed to recorded TheHive 5.8.0 examples.
- A short report listing: every AC with met/not-met, any deviation (recorded in the plan),
  and the §14 provisional decisions you had to make.

## 4. Sequencing

Dispatch in waves — do not run all six phases in one pass:

| Wave | Phases | Gate |
|---|---|---|
| 1 | 1 → 2 → 3 | `db-postgres` reviews the schema and indexes before Phase 4 |
| 2 | 4 → 5 → 6 | `qa-tester` reviews the E2E loop; `security-auditor` reviews webhook + authz |

Stop and report at each gate rather than pushing through a failed gate.
