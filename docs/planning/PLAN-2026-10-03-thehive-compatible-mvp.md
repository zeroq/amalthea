# PLAN-2026-10-03: TheHive-Compatible Core + MVP Loop

Status: **Approved**
Owner: Planner
Supersedes: `PLAN-2026-10-03-foundations-mvp.md` (retained for audit trail)
References: AGENTS.md, ADR-001-backend-framework, **ADR-002-thehive-api-compatibility**,
TheHive **v5.8.0** OpenAPI 3.1 spec (retrieved 2026-10-03)

---

## 1. Summary

Build the Amalthea backend on Django 5.x + DRF + Channels (ASGI/Daphne), Celery/Redis and
PostgreSQL, and expose a **TheHive-compatible API** at `/api/v1/` so existing security tooling and
TheHive4py-based scripts work against Amalthea unchanged. Compatibility is scoped to the **external
integration surface** — endpoints and accepted data formats that third-party tools call — not to
TheHive's UI read models (ADR-002 §D1, §D12).

The MVP closes AGENTS.md §4's loop end-to-end: **ingest** arbitrary JSON via webhook → **escalate**
alert into a case → **extract** observables → **automate** via Celery → **write results back** into
the case timeline, with every mutation emitted over WebSockets.

Internally the backend is unconstrained by the compatibility target: UUID keys, real FKs, ISO-8601
on our native API. TheHive wire format is confined to a translation boundary (ADR-002 §D11).

## 2. Goals

- G1. Close the AGENTS.md §4 MVP loop with automated proof, end to end.
- G2. Ship T1 TheHive compatibility (ADR-002) as a hard, test-enforced contract.
- G3. Preserve AGENTS.md's differentiators: cross-case observable forensics, event-driven automation.
- G4. Secure by default; treat all webhook input as hostile.
- G5. Query/index conscious; no N+1 on list or timeline paths.
- G6. Reproducible environment and CI from day one.

## 3. Non-goals

- TheHive UI parity — dashboards, reports, SSO config, org administration (ADR-002 §D12).
- Cortex analyzer/responder passthrough — Amalthea's orchestration gateway replaces it (§D12).
- TheHive Functions API (Platinum) — we compete here rather than clone it.
- T2 endpoints before the MVP loop is proven.
- UI polish beyond functional flows.
- Multi-tenant org sharing/links (the access *model* is kept; admin UI is not).

## 4. Architecture

| Layer | Choice | Rationale |
|---|---|---|
| Framework | Django 5.2 LTS + DRF | ADR-001 |
| Realtime / ASGI | Django Channels on Daphne | ADR-001; WebSocket ledger |
| Async | Celery 5 + Redis broker | ADR-001; playbook execution |
| Store | PostgreSQL 15+ (`timestamptz`, JSONB, GIN) | ADR-001; JSONB for `raw_payload`/`enrichment_data` |
| Frontend | Django templates + HTMX + Tailwind, dark, keyboard-first | AGENTS.md §1 |
| API shape | Native REST **+** TheHive-compatible `/api/v1/` | ADR-002 §D11 |

Two API families share one service layer:

```
external tool ─▶ /api/v1/*  (TheHive shapes) ─┐
                                              ├─▶ services ─▶ ORM/Postgres
Amalthea UI  ─▶ /api/native/* (clean shapes)  ─┘
```

**Dependency direction is one-way.** Services never import serializers; serializers never touch the
ORM. This is what keeps "compatible on the wire, clean inside" honest rather than aspirational.

## 5. Environment & Tooling

Local machine constraints (verified 2026-10-03): Python **3.14.6** only (no pyenv/uv), **Docker daemon
not running**, no local Postgres or Redis, and only the standalone `docker-compose` binary (v5.4.0) —
the `docker compose` v2 plugin is **not** installed, so scripts and docs must call `docker-compose`.

- Interpreter: **Python 3.14.6** — supported by Django 5.2.8+ (3.14 support landed in 5.2.8).
- Env manager: stdlib `venv` at `.venv` (no system-wide tooling installed). All dependencies resolved
  3.14 wheels cleanly, so `uv` was **not** needed — this keeps the machine untouched.
- Django pinned to **5.2.17** (5.2 LTS; includes fixes for CVE-2026-25673 / CVE-2026-25674).
- Dependency split in `requirements/` is a true **dependency-closure computation**, not a name filter:
  `scripts/lock.sh` builds a throwaway venv with only the runtime top-level packages, freezes it, and
  treats the remainder as the dev closure. Name-based splitting misclassifies transitives
  (`CacheControl`, `thehive4py`, `mypy_extensions` all leaked into a naive `base.txt`).
- **Reproducibility verified**: a fresh venv built from `requirements/dev.txt` yields a
  byte-identical `pip freeze` to the working `.venv` (95 packages).
- **Dev default DB: SQLite** for zero-friction `manage.py` work. Accepted, recorded deviation —
  see §12 R4 and §13 #9. CI and all JSONB/index work run on Postgres.
- `docker-compose.yml` provides Postgres 16 + Redis 7; validated with `docker-compose config`
  (exit 0). The daemon must be started before full-stack runs.

## 6. Data Model

AGENTS.md §3 defines five entities. Verified against TheHive 5.8.0, four need revision and several
supporting tables are required. Every deviation is listed in §12.

### 6.1 Core entities

**Case** — `id` UUID PK · `number` int (incremental, unique; the human reference) · `title` ·
`description` (Markdown) · `severity` smallint (1–4) · `status` FK→CaseStatus · `assignee` FK→User
(null) · `tags` M2M→Tag · `flag` bool · `tlp` smallint (0–4) · `pap` smallint (0–3) · `summary`
(Markdown) · `start_date`/`end_date` timestamptz · `closed_date` timestamptz · `owner_org` FK ·
`created_at`/`updated_at` timestamptz · `ingestion_warnings` JSONB

**Alert** — `id` UUID PK · `type` · `source` · `source_ref` · `external_link` · `title` ·
`description` · `severity` smallint · `status` FK→AlertStatus · `date` timestamptz (default now) ·
`tags` M2M · `flag` · `tlp` · `pap` · `summary` · `assignee` FK · **`raw_payload` JSONB** (AGENTS.md §3,
exposed via the `/raw` extension, D9) · **`correlation_key`** CharField(256, blank, default `""`,
namespaced e.g. `dest_ip:10.0.0.5` — *added 2026-10-03, see REVIEW C4*) ·
**`ingestion_source`** FK→IngestionSource (*renamed from `source_id` 2026-10-03: the FK field named
`source_id` generated the column `source_id_id`, REVIEW M2*) · `case` FK **null** (AGENTS.md's 1:0..1) ·
`follow` bool · `ingestion_warnings` JSONB

Unique constraint: `(source, type, source_ref)` — TheHive's documented de-duplication key.
**`source_ref` fallback semantics (added 2026-10-03, REVIEW C3):** the constraint is unconditional, so
the ingestion pipeline must *always* populate `source_ref`. When the mapping yields none, synthesise
`"sha256:<digest of the canonicalised payload>"` and record the substitution in
`ingestion_warnings`. Without this, a schema-agnostic source whose payloads carry no `sourceRef` can
ingest exactly one alert before hitting `IntegrityError` — which breaks Module A's entire premise.

`Alert.source` (string, verbatim from the wire) and `Alert.ingestion_source` (FK, the Amalthea source
that accepted it) are **intentionally two separate fields** (REVIEW M3). They are not derived from one
another: the wire value is attacker-supplied and must be preserved exactly as received.

**Observable** — **global**, not case-owned (ADR-002 §D5) · `id` UUID PK · `data_type` FK→
ObservableType · `data` text · **`data_hash`** CharField(64) sha256 of `normalized_data` (*added
2026-10-03, REVIEW H3*) · `tags` M2M · `ioc` bool · `sighted`/`sighted_at` ·
`ignore_similarity` bool · `message` · `tlp` · `pap` · **`enrichment_data` JSONB** → serialized as
TheHive's `reports` map · `external` bool · `created_at`/`updated_at`
Unique: `(data_type, data_hash)`, **not** `(data_type, normalized_data)`. `normalized_data` is an
unbounded `TextField`, and on Postgres it carries two failure modes invisible on SQLite: a btree tuple
cap of ~2704 bytes (a long URL or UNC path fails the INSERT on prod only), and locale-collation-aware
index comparison (on a non-`C` collation `C:\Temp\A.txt` and `c:\temp\a.txt` collide for a
case-sensitive type, silently contradicting the normalization rule below). Hashing fixes both and
shrinks the index tuple to ~80 bytes. Normalization itself is unchanged: lowercase unless the type is
case-sensitive (mirrors TheHive's `isCaseSensitive`).

**Task** — `id` UUID PK · `case` FK (related_name `tasks`) · `title` · `description` · `group` ·
`status` (`Waiting`/`InProgress`/`Completed`/`Cancel`) · `flag` · `assignee` FK · `order` int ·
`due_date` · `started_at`/`ended_at` timestamptz · `mandatory` bool

**AutomationRun** (Amalthea extension, D9) — `id` UUID PK · `case` FK (null) · `alert` FK (null) ·
`playbook_name` · `trigger_event` · `status` (`Pending`/`Running`/`Success`/`Failed`) · `output_log`
text · `error` text · `triggered_by_observable` FK **null** (AGENTS.md §3) · `celery_task_id` ·
`started_at`/`finished_at` · `idempotency_key` unique

### 6.2 Supporting entities (required by the compatibility contract)

**CaseStatus** / **AlertStatus** — `value` (unique, 1–64 chars, immutable) · `stage`
(`New`/`InProgress`/`Closed`; alerts add `Imported`) · `order` · `description` · `hidden`.
*Seeded:* case → `New`, `InProgress`, `Contained`, `Closed`; alert → `New`, `Triaged`, `Dismissed`,
`Imported`. This reconciles AGENTS.md §3's fixed sets with TheHive's configurable names (D4).

**ObservableType** — `name` (unique) · `is_attachment` · `is_case_sensitive`.
*Seeded* from TheHive's own integration suite: `ip`, `domain`, `fqdn`, `url`, `mail`, `file`,
`hash`, `user`, `other`.

**CustomField** — `name` · `group` · `type` (`string`/`integer`/`float`/`boolean`/`date`/`url`) ·
`options` JSONB. Values live in `CaseCustomFieldValue` / `AlertCustomFieldValue`
(`order`, `value` JSONB) so one definition serves both.

**Tag** — `name` unique, `colour`, `description`. Auto-created on first use (TheHive behaviour).

**CaseObservable** / **AlertObservable** — link tables carrying per-link context
(`observable`, `case`/`alert`, `added_by`, `created_at`). *This is what makes AGENTS.md Module C's
"same observable across 4 cases" possible while keeping observabiles globally deduplicated.*

**TimelineEvent** (TheHive `CustomEvent`) — `case` FK · `date`/`end_date` · `title` · `description` ·
`kind` (`comment`/`automation`/`status_change`/`system`, internal discriminator) · `actor` FK ·
`metadata` JSONB. Automation results land here (Module D feedback loop).

**IngestionSource** — `slug` (the `{source_id}` path segment) · `name` · `mapping_config` JSONB
(JSON-path rules) · `default_severity` · `correlation_enabled` · `webhook_secret_hash`.

**ApiKey** — `user` FK · `name` · `prefix` (indexed lookup) · `key_hash` · `scope`
(`read`/`readwrite`) · `revoked_at`/`last_used_at`. Hashed at rest; plaintext shown once.

**User** — Django `AbstractUser` + `login` (TheHive uses login as the assignee key), `role`, `org`.

### 6.3 Indexing

Cases: `(status, -start_date)`, `(-severity)`, `assignee`, `number` (unique), `(-created_at)`.
Alerts: `(status, -date)`, `(case)`, `(-created_at)`, unique `(source, type, source_ref)`,
**`(correlation_key, date)`** (*added 2026-10-03, REVIEW C4* — this is what makes the AGENTS.md §4
10-minute-window correlation an index seek instead of a JSON scan of `raw_payload`).
Observables: unique `(data_type, data_hash)`.
Link tables: unique `(case, observable)` / `(alert, observable)` — doubles as the dedupe guard, **plus
the reverse direction** `(observable, case)` and `(observable, alert)`. The reverse direction is what
serves AGENTS.md Module C's fan-out; Django's implicit `ForeignKey` index happens to cover it today, but
relying on that is exactly the refactor that would silently break the product's headline feature
(*added 2026-10-03, REVIEW M5*).
Timeline: `(case, -date, -id)` — the trailing `-id` makes keyset pagination deterministic when
automation events share microsecond precision (REVIEW M9).
AutomationRun: `(case, -started_at)`, unique `idempotency_key`, `celery_task_id`, and a **partial**
index `(created_at) WHERE status='Pending'` for the dispatch beat tick (*added 2026-10-03, REVIEW C2* —
without it, pending-run dispatch is a guaranteed sequential scan of a monotonically growing table on
every tick. Partial indexes are supported by **both** SQLite and Postgres, so this one is portable).
Playbook: `(trigger_event, is_active)`, resolved on every domain event.
Custom-field values: unique `(case, custom_field)` / `(alert, custom_field)` so a retried write cannot
silently duplicate a row (REVIEW M4).

**Redundancy rule.** `ForeignKey` sets `db_index=True` by default, so all FK columns are already
indexed. Do **not** hand-write an index that duplicates an implicit FK index or a unique constraint's
index, and set `db_index=False` where a composite index already covers the FK as a left prefix
(REVIEW M1). Seven such redundant indexes were found in the first review pass.

**Deferred to Postgres-only (T2).** GIN on `raw_payload` and on `Observable.tags`, and `INCLUDE`
covering indexes for large queues. These cannot be expressed portably — `GinIndex` requires
`django.contrib.postgres` in `INSTALLED_APPS`, which is a prod-only dependency (REVIEW L3).

## 7. API Contract

### 7.1 Universal wire rules (T1)

| Rule | Value |
|---|---|
| Base path | `/api/v1/` |
| Auth | `Authorization: Bearer <api_key>`, HTTP Basic, or session cookie |
| Entity envelope | `_id` (string UUID), `_type`, `_createdBy`, `_createdAt`, `_updatedBy`, `_updatedAt` |
| Timestamps | **Unix epoch milliseconds (integer)** on input *and* output |
| Severity | integer `1..4` → Low/Medium/High/Critical, plus `severityLabel` |
| TLP / PAP | `tlp` `0..4`, `pap` `0..3`, plus `tlpLabel`/`papLabel` |
| Case status | string name of an existing `CaseStatus`; response adds derived `stage` |
| Alert status | string name of an existing `AlertStatus`; response adds derived `stage` |
| Task status | `Waiting` \| `InProgress` \| `Completed` \| `Cancel` |
| Observable | `dataType` (type name) + `data` (string or array of strings) |
| customFields | in: object *or* `[{name, value, order}]`; out: `[{_id, name, type, value, order}]` |
| Errors | `{"type": ..., "message": ...}` — 400 `BadRequest`, 401 `AuthenticationError`, 403 `AuthorizationError`, 404 `NotFoundError`, 500 `GenericError` |
| Case lookup | `{idOrName}` accepts UUID **or** case `number` |
| Unknown fields | ignored, not rejected (ADR-002 §D11) |

### 7.2 T1 endpoints

```
POST   /api/v1/login                                   (public)  → 200 OutputUser + session cookie
GET    /api/v1/logout                                  (public)
POST   /api/v1/logout                                  (public)

POST   /api/v1/alert                                                  req: InputCreateAlert
GET    /api/v1/alert/{alertId}                                       → OutputAlert
PATCH  /api/v1/alert/{alertId}                                       req: InputUpdateAlert
DELETE /api/v1/alert/{alertId}                                       → 204
POST   /api/v1/alert/{alertId}/merge/{caseId}                        → escalate alert into case
POST   /api/v1/alert/{alertId}/import/{caseId}                       → mark Imported + link
POST   /api/v1/alert/{alertId}/observable                            req: InputCreateObservable

POST   /api/v1/case                                                  req: InputCreateCase
GET    /api/v1/case/{idOrName}                                       → OutputCase
PATCH  /api/v1/case/{idOrName}                                       req: InputUpdateCase
DELETE /api/v1/case/{idOrName}                                       → 204
POST   /api/v1/case/{caseId}/task                                    req: InputCreateTask
POST   /api/v1/case/{caseId}/observable                              req: InputCreateObservable
POST   /api/v1/case/{caseId}/customEvent                             req: InputCustomEvent → 201
GET    /api/v1/case/{caseId}/timeline                                → ordered TimelineEvent[]
DELETE /api/v1/case/{caseId}/alert/{alertId}                         → unlink

GET    /api/v1/observable/{observableId}                             → OutputObservable
PATCH  /api/v1/observable/{observableId}
DELETE /api/v1/observable/{observableId}                              → 204
GET    /api/v1/task/{taskId} · PATCH · DELETE                         → 204
PATCH  /api/v1/customEvent/{eventId} · DELETE                        → 204
GET    /api/v1/customField
```

### 7.3 T2 endpoints (committed, post-MVP)

`POST /api/v1/query` (+ `_bulk` patches, `POST /case/_merge/{ids}`), attachments
(`POST /case/{caseId}/attachments`, download), `page`, `comment`, `tag`, `shares`,
`observable/type` CRUD, case/alert status CRUD, `user`/`user/current`, `describe`, `export`,
`flow`, case templates, taxonomy, procedures/TTP, `organisation`.

### 7.4 Amalthea extensions (outside TheHive's namespace, D9)

```
POST /api/v1/alerts/webhook/{source_id}     gossamer ingest, arbitrary JSON  ← AGENTS.md §4.1
GET  /api/v1/alert/{alertId}/raw            original payload (TheHive has no accessor)
GET  /api/v1/ingestion/sources              manage webhook sources + mapping config
     /api/v1/automation/playbooks|defs      playbook definitions
     /api/v1/automation/runs                AutomationRun records + output_log
GET  /api/v1/case/{id}/observable/_graph    cross-case observable neighbourhood (Module C)
     /api/native/...                        clean API for our own UI (ISO-8601, real FK shapes)
```

## 8. Components & Services

| Component | Responsibility | Notes |
|---|---|---|
| `compat.mappers` | internal ↔ TheHive field translation | **Only** place TheHive field-name literals live |
| `compat.errors` | `{"type","message"}` envelope + 400 `fields` map | Replaces DRF's default handler |
| `compat.auth` | Bearer / Basic / session resolution | `ApiKey` lookup by prefix, verify hash |
| `ingest.mapping` | JSON-path extraction (jsonpath-ng) per `IngestionSource` | Arbitrary payloads; never loses `raw_payload` |
| `ingest.pipeline` | validate → map → dedupe → persist → correlate → emit events | Idempotent on `sourceRef` |
| `correlation.engine` | bundle alerts by attribute within a time window | e.g. same dest IP in 10 min; opt-out per source |
| `observables.extractor` | typed extraction + normalization + dedupe | Reuses `compat.mappers` for `dataType` |
| `observables.graph` | cross-case similarity queries | Powers Module C |
| `automation.registry` | playbook definitions and trigger bindings | Declarative, DB-backed |
| `automation.dispatcher` | event → Celery task, idempotency key | `transaction.on_commit` |
| `automation.executor` | run playbook, capture `output_log`, write TimelineEvent | Retries with backoff |
| `realtime.publisher` | emit domain events to Channels group | Single choke point for fan-out |
| `query.engine` | `POST /api/v1/query` DSL → QuerySet | T2; operator set per ADR-002 §D6 |

## 9. Phases, Tasks & Acceptance Criteria

### Phase 0 — Environment & Tooling
**Tasks**
1. Create `.venv` on Python 3.14.6; install pinned `requirements/base.txt` (+ `dev`, `prod`).
2. `pyproject.toml` with project metadata, `ruff`, `pytest`, `pytest-django`, `coverage`.
3. `.env.example`, `.gitignore`, `.editorconfig`; pre-commit with ruff.
4. `docker-compose.yml` for Postgres 16 + Redis 7; document that the daemon must be running.

**ACs**
- **AC0.1** `python -c "import django, rest_framework, channels, celery, jsonpath_ng"` succeeds in `.venv`.
- **AC0.2** Django version is 5.2.x; `python -c "import django;print(django.VERSION)"` matches.
- **AC0.3** `ruff check .` and `ruff format --check .` pass on a clean tree.
- **AC0.4** No secrets in the repo; `.env.example` contains only placeholder values.
- **AC0.5** `docker compose config` validates (daemon need not be running).
- **AC0.6** A pinned, hashable requirements set exists; installing it twice yields the same versions.

### Phase 1 — Django Foundations
**Tasks**
1. Project `amalthea` with apps `core`, `compat`, `ingest`, `cases`, `alerts`, `observables`,
   `automation`, `realtime`. Settings split `base` / `dev` / `prod` / `test`.
2. ASGI entrypoint wiring Channels + Daphne; Redis channel layer in prod/test, in-memory in dev.
3. Celery app with Redis broker, `beat` schedule, task autodiscovery.
4. DRF configured with the custom exception handler and auth classes (registered now, used from P2).
5. Health endpoint `/healthz` (liveness) and `/readyz` (DB + Redis + migrations).

**ACs**
- **AC1.1** `manage.py check` passes with 0 issues under dev and prod settings.
- **AC1.2** `python -c "import amalthea.asgi"` succeeds; `daphne --version` present.
- **AC1.3** `celery -A amalthea inspect ping` reaches a running worker.
- **AC1.4** `/healthz` returns 200 without touching Redis; `/readyz` returns 503 when Redis is down.
- **AC1.5** No hardcoded secrets; all settings read from env with safe defaults.
- **AC1.6** `manage.py migrate` on an empty DB completes with no prompts.

### Phase 2 — Wire-First Foundation (compat layer)
> Deliberately **before** the models. Building the contract first stops us designing a clean internal
> model and then discovering it cannot be expressed on the wire.

**Tasks**
1. `compat/errors.py`: `{"type","message"}` envelope, `fields` map on 400, status↔type mapping (D7).
2. `compat/time.py`: epoch-ms ↔ datetime, dual acceptance (D11).
3. `compat/enums.py`: severity/TLP/PAP labels, stage derivation, task status enum.
4. `compat/auth.py`: `ApiKeyAuthentication` (Bearer + Basic), DRF defaults, scope enforcement.
5. `compat/mappers/`: skeleton translators per entity with unit tests against fixtures.
6. `conftest.py` + `tests/fixtures/thehive/` golden corpus seeded from the v5.8.0 spec examples.

**ACs**
- **AC2.1** Every error response body is exactly `{"type","message"}`; DRF defaults appear nowhere.
- **AC2.2** Unauthenticated request → 401 `{"type":"AuthenticationError",...}`; bad key → 401, not 403.
- **AC2.3** `read`-scoped key cannot perform a write → 403 `AuthorizationError`.
- **AC2.4** Epoch-ms int, ISO-8601 string and `null` all parse on input; output is always epoch-ms int.
- **AC2.5** Severity 1–4, TLP 0–4, PAP 0–3 round-trip; out-of-range → 400 with `fields`.
- **AC2.6** Fixtures from the spec's own examples serialize without error.

### Phase 3 — Domain Models & Migrations
**Tasks**
1. All models per §6, UUID PKs, `timestamptz`, JSONB, `related_name` on every FK.
2. Constraints: unique `(source,type,source_ref)`; unique `(data_type, normalized_data)`;
   unique link pairs; unique case `number` (allocated by a Postgres sequence).
3. Status/type/custom-field/tag seed data migration (idempotent).
4. `select_related`/`prefetch_related` on all list and timeline querysets.
5. Django admin registration for triage convenience.

**ACs**
- **AC3.1** `makemigrations --check --dry-run` is clean; `migrate` succeeds on empty DB.
- **AC3.2** Schema contains every field in §6; verified by an introspection test.
- **AC3.3** Duplicate `sourceRef` and duplicate observable both raise `IntegrityError`.
- **AC3.4** Every FK has an explicit `on_delete` and a `related_name`.
- **AC3.5** Indexes from §6.3 exist (introspected via `connection.introspection`).
- **AC3.6** All PKs are UUID; no integer PKs on domain entities.
- **AC3.7** CI runs the suite on **Postgres**; SQLite-only passes are not accepted.

### Phase 4 — Ingestion (AGENTS.md §4.1)
**Tasks**
1. `IngestionSource` CRUD (extension API) + secret-based webhook auth.
2. `ingest/mapping.py`: jsonpath-ng expressions per source → normalized alert fields.
3. `POST /api/v1/alerts/webhook/{source_id}`: validate → map → dedupe → persist → correlate → events.
4. Auto-create unknown statuses/types/tags with recorded `ingestion_warnings` (D11).
5. T1 `alert` CRUD + `GET /raw`.

**ACs**
- **AC4.1** Posting a mock phishing JSON creates an Alert, returns 201 + `_id`, and `raw_payload`
  is byte-identical to the request body.
- **AC4.2** Arbitrary JSON-path rules extract title/severity/src_ip/user from two structurally
  different payload shapes (nested, and flat-with-dotted-keys).
- **AC4.3** An unconfigured source ingests without error — fields fall back to defaults and a
  warning is recorded, never a 500.
- **AC4.4** Replaying the same `sourceRef` is idempotent: one Alert, no duplicate event, no error.
- **AC4.5** A webhook with a wrong/absent secret → 401/403; payload > configured limit → 413.
- **AC4.6** Missing/!JSON/malformed payload → 400 `BadRequest` with a `fields` map, never a 500.
- **AC4.7** Mapped `severity` lands as an int in 1–4 and `severityLabel` reads back correctly.

### Phase 5 — Escalation, Observables, Tasks, Timeline (AGENTS.md §4.2–4.3)
**Tasks**
1. `POST /api/v1/alert/{alertId}/merge/{caseId}` and `import/{caseId}`; case creation from alert.
2. `correlation.engine`: bundle by correlating attribute within a time window.
3. `observables.extractor`: typed extraction, normalization, global dedupe.
4. T1 `case`, `task`, `observable`, `customEvent`, `timeline` endpoints; link tables.
5. `observables.graph`: cross-case neighbourhood query.

**ACs**
- **AC5.1** Merging an alert into a case sets `alert.case`, leaves the alert otherwise intact, and
  appends a `status_change` TimelineEvent.
- **AC5.2** Two alerts sharing a destination IP within 10 minutes bundle into one case; the same IP
  30 minutes apart does not. Boundary at exactly 10 minutes is covered by a test.
- **AC5.3** An email address is extracted as `dataType=mail`; re-extraction reuses the same
  Observable row rather than creating a duplicate.
- **AC5.4** Case hashes normalize to lowercase; a case-sensitive type does not.
- **AC5.5** The same observable attached to 3 cases appears once in `/observable/{id}` and is
  reachable from all 3 — the cross-case link that AGENTS.md Module C requires.
- **AC5.6** `GET /case/{number}` resolves by human number as well as by UUID.
- **AC5.7** Timeline is returned newest-first, paginated, and contains no N+1 queries
  (asserted via `assertNumQueries`).

### Phase 6 — Orchestration Gateway (AGENTS.md §4.4) → **MVP COMPLETE**
**Tasks**
1. Domain events: `alert.ingested`, `case.status_changed`, `observable.created`, `task.completed`.
2. `automation.registry`: DB-backed playbook definitions with trigger bindings.
3. `automation.dispatcher`: event → Celery task with `idempotency_key`; dispatched via
   `transaction.on_commit` so a rolled-back transaction fires nothing.
4. `automation.executor`: outbound HTTP action + Python-script action, capture `output_log`.
5. Feedback loop: result written to `AutomationRun.output_log` **and** as a TimelineEvent.
6. Retries with exponential backoff; failure recorded, never raised into the request.

**ACs**
- **AC6.1** Creating an observable fires its bound playbook; an `AutomationRun` reaches `Success`
  with non-empty `output_log`.
- **AC6.2** The run's result is readable as a TimelineEvent on the case — the Module D feedback loop.
- **AC6.3** The webhook request returns **before** the playbook completes (async proven by timing).
- **AC6.4** A failing outbound action yields `Failed` + error text in `output_log`, and does not
  roll back or 500 the originating API call.
- **AC6.5** Replaying the trigger event produces no second run (`idempotency_key` holds).
- **AC6.6** A transaction that rolls back emits no events and no runs.

### Phase 7 — Realtime Ledger (AGENTS.md §2 Module B)
**Tasks**
1. Channels consumer for case rooms, session-authenticated at handshake (D8).
2. `realtime.publisher`: one choke point emitting comment/assignment/status/automation events.
3. HTMX partial swap for timeline append so non-WebSocket clients still update.

**ACs**
- **AC7.1** Two concurrent clients see a new comment within 1s without reloading.
- **AC7.2** An unauthenticated WebSocket handshake is refused with 4401.
- **AC7.3** Case-scoped events never leak to another case's room (cross-tenant isolation test).
- **AC7.4** A dropped connection reconnects and re-syncs from the timeline without duplicates.

### Phase 8 — Query API (T2)
**Tasks**
1. `query.engine`: `_name` dispatch, `_eq/_ne/_gt/_gte/_lt/_lte/_between/_in/_like/_has`,
   `_and/_or/_not`, `_sort`, `_page`, `extraData`, `excludeFields`.
2. `listCase`, `listAlert`, `listObservable`, `listAny`, `getCase` (+ `count`).
3. `X-Total` header and bare-array response.
4. Accept-and-ignore the legacy `?name=` parameter.

**ACs**
- **AC8.1** The documented example query returns a bare array with `X-Total` set correctly.
- **AC8.2** `from`/`to` paging returns disjoint, complete, ordered result sets.
- **AC8.3** Every implemented operator has a test; unimplemented operators return 400
  `BadRequest`, never a silent wrong answer.
- **AC8.4** **TheHive4py** runs unmodified against Amalthea: list cases, create an alert, merge
  it into a case, read the timeline.

### Phase 9 — Minimal UI (TheHive-aligned, dark, keyboard-first)
**Tasks**
1. Base layout, Tailwind dark theme, WCAG AA contrast, focus-visible rings, command palette (`⌘K`).
2. Views: alert queue, case detail (summary/tasks/observables/timeline), observable graph.
3. HTMX partials + WebSocket live timeline.

**ACs**
- **AC9.1** The MVP loop is demonstrable end-to-end through the UI alone.
- **AC9.2** Full keyboard operation; visible focus at all times; no color-only status signalling.
- **AC9.3** Axe scan reports zero critical/serious violations.
- **AC9.4** Timeline updates live in two tabs.

### Phase 10 — Conformance, Security & Performance
**Tasks**
1. Contract tests per T1 endpoint against the golden corpus.
2. `security-auditor` pass: authn/authz, webhook trust boundary, upload handling, injection,
   rate limiting, secrets, dependency audit.
3. Query review with `EXPLAIN` on Postgres for the five hot paths.
4. `verifier` pass: every AC in this document evaluated and reported.

**ACs**
- **AC10.1** Every T1 endpoint has ≥1 conformance test keyed to a recorded TheHive example.
- **AC10.2** No `400`-on-unknown-field behaviour anywhere in the compat layer.
- **AC10.3** Unauthenticated/under-scoped access to every T1 endpoint is proven to fail closed.
- **AC10.4** Webhook is rate-limited per source and per IP; oversized payloads rejected before parse.
- **AC10.5** Hot-path queries (alert queue, case detail, timeline, observable graph, automation
  dispatch) show index usage under `EXPLAIN` on Postgres; no unbounded table scans.
- **AC10.6** `verifier` report lists every AC as met, or deviations are recorded in §12.

## 10. Security

- **Webhook trust boundary.** Arbitrary JSON from the internet: size cap before parse, strict JSON
  only, depth/size limits, no deserialization of caller-supplied types, per-source secret, per-source
  and per-IP rate limits. Every mapped value is length-capped and type-coerced.
- **Path safety.** `raw_payload` and `customFields` values are never interpolated into templates
  unescaped; file names from uploads are sanitized and stored outside the web root.
- **AuthN.** API keys hashed (Argon2 or PBKDF2) with an indexed prefix for lookup; rotation and
  revocation supported. Bearer tokens never accepted in query strings or logged.
- **AuthZ.** Object-level permission checks on every case/alert access, including nested sub-resources;
  the compatibility layer is not a bypass. Read-only keys are enforced at the permission layer, not
  the view.
- **Rate limiting.** Webhooks and auth endpoints are throttled (DRF throttling).
- **Markdown.** Case descriptions and timeline entries are rendered with sanitization; no raw HTML,
  no script execution, CSP enforced.
- **Celery.** No `eval`/`pickle` of playbook input; playbooks are registered Python, not uploaded
  code. Outbound HTTP is SSRF-guarded (scheme allowlist, private-IP blocking, redirect limits).
- **Secrets.** Env/secret store only; `.env` gitignored; CI scans for committed secrets.

## 11. Testing Strategy

| Layer | Tool | Focus |
|---|---|---|
| Unit | pytest | mappers, mapping engine, correlation windows, normalization, query operators |
| Integration | pytest + Django test client | every T1 endpoint, permissions, error envelope |
| Conformance | pytest + golden corpus | TheHive 5.8.0 request/response pairs, per ADR-002 |
| Interop | TheHive4py | unmodified third-party client against a live server (AC8.4) |
| E2E | Playwright | MVP loop through the UI |
| Static | ruff, mypy, bandit, pip-audit | style, types, security, CVEs |
| Perf | `EXPLAIN` + load script | five hot paths |

DB matrix: SQLite for speed, **Postgres required in CI** (JSONB operators, `timestamptz`, partial
indexes behave differently). Celery runs eagerly in unit tests; at least one test runs against a real
worker + Redis to prove the async path.

## 12. Risks

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Scope: TheHive compat + MVP + orchestration is a lot | Slippage | T1/T2/T3 tiering; MVP loop gated at Phase 6; T2 strictly after |
| R2 | Query DSL is a real subsystem (D6) | Schedule | T2, ship a documented operator subset, 400 on the rest — never a wrong answer |
| R3 | Compat layer leaks into the domain model | Long-term cost | One-way dependency rule + lint check banning TheHive literals outside `compat/` |
| R4 | **SQLite in dev diverges from Postgres** | Subtle bugs | CI is Postgres-only for the suite (AC3.7); JSONB/index features tested only on PG; `docker-compose.yml` provided |
| R5 | No Docker daemon / no local PG or Redis on this machine | Blocks full-stack runs | Documented; SQLite + in-memory channel layer keep `manage.py` usable; escalate to `uv`/Colima if a 3.14 wheel is missing |
| R6 | Epoch-ms timestamps cause off-by-1000 bugs | Data corruption | Single conversion helper, no inline arithmetic, unit tests at boundaries (D11) |
| R7 | Auto-created statuses/types drift from TheHive's set | Subtle incompat | Seeded from TheHive's own fixtures; recorded in `ingestion_warnings` |
| R8 | Global observable dedupe surprises users | Duplicate-looking data | `ignoreSimilarity` escape hatch + explicit link rows |
| R9 | Requirements are version-pinned but **not hash-pinned** | Supply-chain substitution | Versions are locked and the closure is verified reproducible (AC0.6). Hashes via `pip-compile --generate-hashes` are a tracked follow-up; that step exceeded the 120s command budget on first attempt and must be run with a longer timeout. |
| R10 | **SQLite cannot validate Postgres semantics** (REVIEW 2026-10-03) | Prod-only failures | Nine features differ or are missing in SQLite: GIN, covering indexes, `jsonb` operators, real `timestamptz`, `nextval()`, `COLLATE "C"`, the btree 2704-byte tuple cap, collation-aware index comparison, and `DESC` in index DDL (**silently dropped**, so any dev test relying on descending index order proves nothing). Mitigation is structural, not incidental: AC3.7 requires Postgres in CI, and every Postgres-only check is written down in the review's verification SQL to run once the daemon is available. |
| R11 | **AC tests can pass vacuously** (REVIEW H1) | False confidence — the single worst failure mode found to date | Four of six Phase 3 schema ACs were "met" by tests that could not fail (one had a literal `pass` body). Rule adopted: **a passing AC is only evidence once its failure mode has been demonstrated.** New conformance tests must include a mutation check proving they fail on a deliberately broken schema. |
| R12 | Long observable values | Unexplained 500 on prod only | Mitigated by `data_hash` (R-H3): bounds the index tuple at ~80 bytes and removes collation dependence. Must land before any production data exists. |

## 13. Deviations from AGENTS.md §3 (require acknowledgement)

| # | AGENTS.md says | We do | Why |
|---|---|---|---|
| 1 | `Observable.case_id` FK | Global `Observable` + `CaseObservable`/`AlertObservable` links | Required to satisfy Module C's cross-case linking; matches TheHive |
| 2 | `Observable.type` ∈ {IP,Domain,Hash,User}, `.value` | `data_type` FK → `ObservableType`, `.data` | TheHive contract + user-definable types |
| 3 | `Task.status` ∈ {Todo,InProgress,Done} | `Waiting`/`InProgress`/`Completed`/`Cancel` | TheHive contract; semantics preserved |
| 4 | `Case.status`/`Alert.status` fixed sets | Status **entities** + derived `stage`, seeded with AGENTS.md's names | TheHive contract; no migration needed to add a status |
| 5 | Severity unspecified | Integer `1..4` | TheHive contract |
| 6 | 5 entities | + CaseStatus, AlertStatus, ObservableType, CustomField(+values), Tag, link tables, TimelineEvent, IngestionSource, ApiKey, Organisation, Attachment | Required by the compatibility contract |
| 7 | Timestamps unspecified | `timestamptz` in Postgres; epoch-ms on the TheHive wire | TheHive contract |
| 8 | Webhook at `/api/v1/alerts/webhook/{source_id}` | Same path **plus** full TheHive `alert` surface | Keeps the AGENTS.md path intact; TheHive tools get their own endpoints |
| 9 | Postgres primary | SQLite in **dev**, Postgres in CI/prod | No local PG or Docker daemon on this machine (R5) |
| 10 | `Observable` unique on `(data_type, normalized_data)` | unique on `(data_type, data_hash)` | Unbounded `TextField` in a btree index hits Postgres's ~2704-byte tuple cap, and locale collation breaks case-sensitive dedupe. Both invisible on SQLite. REVIEW H3 |
| 11 | ADR-002 §D5: link tables carry "tags" and "is-IOC" | link tables carry `added_by` only; per-link `tags` added, per-link `is_ioc` **rejected** | Per-link `is_ioc` on a globally-deduplicated observable is semantically incoherent — two links for the same IP disagreeing about IOC status is a bug magnet. REVIEW M13 |
| 12 | `Alert.source_id` FK | `Alert.ingestion_source` FK | The field named `source_id` produced the column `source_id_id`. REVIEW M2 |
| 13 | Coverage ≥ 80% per phase | Coverage ≥ 80% enforced at the **Phase 6 MVP gate** only | The largest uncovered block is `compat/mappers/`, which is intentionally stubbed until Phases 4–5 supply the real logic. A per-phase gate would force filler tests that assert nothing — the exact failure mode R11 warns about. Phase 0/1/3 still report coverage as a non-blocking signal. |

### Phase 7 record — BRIEF-2026-10-06 (Realtime Ledger), 2026-10-06

Filed here rather than in §12: §12 is the risk register and §13 is the register the plan itself
points at (§6 and AC10.6 both say "§12"; the brief's cross-reference inherits that drift). Gate
figures at the end.

| # | Brief / plan said | We did | Why |
|---|---|---|---|
| P7-1 | "`TimelineEvent` is created at exactly these sites" — five, in `cases/ ui/ automation/` | **CLOSED 2026-10-06 (approved follow-up).** There were seven: `alerts/escalation.py` wrote two directly (`alert-imported`, `alert-merged`). Both now go through `append_timeline_event`, and the scan's `SCANNED_APPS` includes `alerts/`, so the audit covers every app that writes ledger rows | Left open initially because widening the guard would have changed an approved AC; the planner approved closing it — an analyst watching a case should see an alert imported or merged into it live, and a single publisher choke point is a task requirement. Titles, descriptions, kinds, actor and metadata are unchanged; only the construction moved, and `merge_alert_into_case` still returns its `TimelineEvent` |
| P7-2 | Phase 9 / README: "HTMX for interactivity" | `hx-post`/`hx-target`/`hx-swap`/`hx-disabled-elt` added to the comment form as specified, but the repo ships **no htmx** — no script tag, no vendored file | T4's AC only requires the attributes. Without a library they are inert and the form is the plain POST it always was (the fallback the design depends on). Vendoring was not in the deliverables, so it is reported rather than smuggled in |
| P7-3 | `sync` `after` is "the last event id the client has rendered" | A missing, stale or foreign `after` returns the **whole** case ledger; only a well-formed id belonging to the case applies the keyset. Malformed id → close `4000` | A gap in a timeline is unrecoverable by the client; a replay is not — the client dedupes on `data-event-id`. Keyset semantics for a valid anchor are unchanged |
| P7-4 | Not specified: `HX-Request` POST with an empty body | HTTP `400`, plain text | HTMX swaps only 2xx, so a flash message would land nowhere and a redirect would inject a whole page into `<ol id="timeline-events">` |
| P7-5 | Not specified: `case_id` in the WS URL | A non-UUID room id closes `4000` instead of joining | The publisher only ever names `case_<uuid>`; a group nobody writes to would leave the socket silently dead |

**Test-harness note (not a product deviation):** `tests/conformance/test_realtime.py` patches
`channels.db.close_old_connections` to a no-op for the module. It runs around every consumer
dispatch and, inside pytest-django's never-committing transaction, closes the test's connection
mid-run on Postgres — SQLite's in-memory `close` is a no-op, which is why the suite looked green
without it. Channels' own `ApplicationCommunicator` patches the same function for the same reason;
no product code is affected.

**Gate (2026-10-06):** SQLite `make check` — **440 passed, 3 skipped**; Postgres
`DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg pytest -q` — **419 passed, 24 skipped**;
`ruff check`, `ruff format --check`, `mypy` all clean. Both baselines in the brief (429 / 408) read
one low; `test_realtime.py` contributes 10 tests and no other file's test count changed.

**P7-1 follow-up gate (2026-10-06):** SQLite `make check` — **440 passed, 3 skipped**; Postgres
`test_pg` — **419 passed, 24 skipped**; `ruff check` + `ruff format` clean on both edited files.
No count moved: the two call sites were migrated in place and the scan test widened rather than
duplicated. Failure mode demonstrated, per R11 — restoring a direct create in
`alerts/escalation.py` fails `test_the_ledger_service_is_the_only_writer_of_timeline_rows` with
`[PosixPath('alerts/escalation.py')]` in the diff.

### Phase 8 record — BRIEF-2026-10-06 (Query API), 2026-10-06

| # | Brief / plan said | We did | Why |
|---|---|---|---|
| P8-1 | `GET /api/v1/case/{caseId}/timeline` returns "→ ordered TimelineEvent[]" (plan §7.2) | The response is now `{"events": [...]}` — 5.8.0 `OutputTimeline`: ms-epoch `date`, mapped `kind` (`case.created`/`log.created`/`alert.occurred`/`custom`), `entity`/`entityId` (`Case`/`Alert` + `~<id>`), `details` from event metadata, `endDate: null` | The recorded 5.8.0 OpenAPI and thehive4py `case.get_timeline()` consume the envelope; AC8.4 requires reading the timeline through thehive4py unmodified. Internal `timeline_event_json` (Phase 7 WS `sync`/UI) is untouched — only the API serialization changed, via `_timeline_event_wire` |
| P8-2 | Errors go through `compat.errors.thehive_exception_handler` (400 `BadRequest`) | The query view builds the 400 envelope inline (`_bad_request`), exactly as the existing T1 `_not_found`/`_create_case` do | `compat.errors` flattens every 400's `message` to the literal "Bad request" and parks the detail in `fields`; AC8.3 demands the response *name* the offending operator/field. The envelope shape (`type`/`message`) is unchanged — only the message survives |
| P8-3 | `excludeFields` is an optional array; `None` presumably means "no exclusions" | `excludeFields: []` is treated as **exclude nothing** (`[]` is not `None`) | thehive4py `query.run(query, exclude_fields=[])` always sends `excludeFields: []`; `[]` must not mean "exclude everything". Pinned by `test_thehive4py_query_run_with_explicit_empty_exclusions` |
| P8-4 | `getCase` resolves one case (reuse `_resolve_case`); miss unspecified | A miss threading `Case.objects.none()` → HTTP 200 with a bare `[]`, and every later step (`count` included) answers over the empty set | `_resolve_case` returns `None` (→ 404), but the query engine resolves via `alerts/escalation.link_case_from_identifier` which raises `DoesNotExist`; the "empty result set" reading matches how every other step threads "no rows", so `listCase → getCase ~missing → count` answers 0 rather than erroring mid-chain |

**Read-the-docstring notes (not wire deviations):** `listAny` is three branches, not one UNION —
a field a kind lacks is "cannot match" (`_NO_MATCH`), so under `_eq` the kind drops out of the
result and under `_not` it drops *in*; only a field on **no** active whitelist 400s. Every sort
appends an ascending `_id` tie-break (matching `_sort_rows`), so paging is deterministic whether
or not the set materialised. `_between` is `_from` inclusive / `_to` exclusive on every field
type, which is what makes adjacent pages disjoint. None of these change the wire shape recorded
in the brief.

**Test-harness note:** `tests/conformance/test_query_api.py` imports `thehive4py` (a pinned dev
dependency) to build AC8.4's request bodies with the client's own query builders, then POSTs them
through the DRF `APIClient` — the live HTTP hop stays the planner's AC8.4 verification, exactly
like AC1.3/AC1.4. The file also carries an autouse `cache.clear()` fixture: DRF's UserRateThrottle
(1000/min) keys into the process-wide LocMemCache and a long suite run would otherwise trip 429s
unrelated to the assertion at hand (the webhook-hardening tests grew the same pattern for the
anonymous throttle).

**Gate (2026-10-06):** SQLite `make check` — **488 passed, 3 skipped**; Postgres `test_pg` —
**467 passed, 24 skipped**; `ruff check` + `ruff format --check` clean on `query/` and the two
edited files; mypy "Success: no issues found in 95 source files"; `manage.py check` clean;
`makemigrations --check --dry-run` clean (the `query` app adds no models, as the brief requires).
The query suite contributes exactly 47 tests (488 = 441 pre-existing + 47; the pre-existing total
moved 440 → 441 by a single test landed between the P7 gate and HEAD, outside Phase 8 — the
P7-era 419 Postgres figure moves the same way).

**Post-gate record (the AC8.4 live check ran against a seeded prod-settings DB through thehive4py
unmodified; it surfaced three gaps, all closed and committed with Phase 8):**

| # | Live gate finding | Fix | Why |
|---|---|---|---|
| P8-5 | `POST /api/v1/alert` (T1 `InputCreateAlert`, plan §7.2) did not exist — the MVP loop only ever created alerts via the webhook, so `client.alert.create` from AC8.4 returned 405 | Added the endpoint: `alert_list` accepts `GET`/`POST`, new `_create_alert` validates `title`/`type`/`source`/`sourceRef` (400 `BadRequest` with `fields`), auto-creates unknown alert statuses (ADR D11 `resolve_alert_status`), converts the ms-epoch wire `date` to a tz-aware `DateTimeField`, and records an `ingestion_warnings` entry for any unmapped fields; returns `alert_json` 201 | AC8.4 drives `alert.create` first; without the T1 create endpoint the gate cannot run its merge/timeline half |
| P8-6 | `POST /api/v1/alert/{alertId}/merge/{caseId}` returned `alert_json` (the alert), but 5.8.0's `OutputCase`/thehive4py `merge_into_case()` contract says the **case** | The view now returns `case_json(case, detail=True)` | AC8.4's `merged["_id"] == case_id` assertion failed on the alert uuid; the OpenAPI records `$ref: OutputCase` for the 200 of that route |
| P8-7 | prod settings configure `channels_redis.core.RedisChannelLayer` but `channels-redis` was never a pinned dependency — the merge path publishes a ledger event through the layer, so the first live merge raised `ModuleNotFoundError` | Added `channels-redis==4.3.0` to `requirements/base.txt` and installed it | The AC1.3/AC1.4 live gate never exercised an event publishing path; this one does. The dependency was always required by `settings/prod.py`, Phase 8 merely made it reachable |

**Final gate after the P8-5..P8-7 fixes (this commit):** SQLite `make check` — **491 passed, 3
skipped**; Postgres `test_pg` — **470 passed, 24 skipped** (the +3 are `test_alert_create_roundtrip
_through_thehive4py_shape`, `test_alert_create_requires_source_ref_and_titles`, `test_merge_returns
_the_case_not_the_alert` in the query suite, now 50 tests). AC8.4 live gate
(`live_ac84_seed.py` + `live_ac84.py`, thehive4py 2.1.0 against the seeded prod DB): **PASS** —
`case.find` → 1 case; `alert.create` → 201; `alert.merge_into_case` → `OutputCase` with the case
id; `case.get_timeline` → `{"events": [...]}` with `alert.occurred` events. The gate scripts are
documented here, not committed (same as AC1.3/AC1.4 — the seed writes a throwaway key and user on
the local prod-DB container).

## 14. Open Questions

Resolved 2026-10-06 per TODO §3; each decision is recorded with the rationale that settled it. The
provisional answers proposed here held in every case — the code now implements them, so these are
recorded decisions, not open questions.

1. **Alert `severity` default** when a source declares none — `2` (Medium) or require explicit
   mapping? **DECIDED 2026-10-06: `2`, with a warning.** Implemented in `ingest/pipeline.py`
   (`DEFAULT_SEVERITY = 2`, `severity_defaulted` warning appended to `ingestion_warnings`) and
   `IngestionSource.default_severity`. A source that wants a different fallback configures it.
2. **Correlation window** — AGENTS.md says 10 minutes for identical destination IP. Confirm this
   should be per-source configurable, and what the default is for multi-signal correlation.
   **DECIDED 2026-10-06: per-source `correlation_key` with a configurable window; default
   10 minutes.** The correlation engine correlates on the operator-authored `correlation_key`
   (derived via JSON-path mapping, not a fixed attribute pair), over the `(correlation_key, date)`
   index. Each `IngestionSource` can disable correlation (`correlation_enabled`). Multi-signal
   correlation (same user + IP) is expressed by giving those signals the same `correlation_key` rule.
3. **`Alert.type` vs `dataType`** — should Amalthea's `IngestionSource` map to TheHive's `alert.type`
   taxonomy, or stay independent? **DECIDED 2026-10-06: independent, exposed as a tag.**
   `Alert.type` is Amalthea's own vocabulary; exact mapping onto TheHive's taxonomy is deferred to
   the Phase 8 query surface where `dataType` filtering is exercised.
4. **Organisation tenancy** — single-tenant for MVP, or multi-org from the start? TheHive's access
   model assumes orgs; deferring risks a later migration on every table.
   **DECIDED 2026-10-06: single-tenant + nullable `Organisation` FK.** The nullable FK (already
   implemented) keeps multi-org open without a per-table migration on day one; multi-org access
   control is out of MVP scope.
5. **Markdown dialect** — TheHive-flavored Markdown is a superset of CommonMark with mentions and
   attachments. Adopt it for wire compat, or CommonMark and accept divergence?
   **DECIDED 2026-10-06: sanitized CommonMark.** Adopting the superset costs wire fidelity and
   sanitizer complexity; `markdown-it-py` (CommonMark) is the pinned renderer. Recorded divergence
   from TheHive's note dialect.

## 15. Handoff

Phase 0 → environment, then Phases 1–6 to `django-backend`, with `db-postgres` reviewing §6/§6.3 at
Phase 3 and `security-auditor` at Phase 2 and Phase 10. `qa-tester` owns §11 and the golden corpus.
`verifier` evaluates every AC after Phase 6 and again after Phase 10, writing a persistent report.
Any deviation from this plan must be recorded in §12 before the code lands.
