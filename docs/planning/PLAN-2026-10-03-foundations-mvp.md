# PLAN-2026-10-03: Foundations + MVP End-to-End

Status: **Superseded** by `PLAN-2026-10-03-thehive-compatible-mvp.md`
Owner: Planner
References: TheHive (primary), AGENTS.md, ADR-001-backend-framework

> Retained for audit trail. Its §5 data model was revised after verification against the official
> TheHive 5.8.0 OpenAPI spec — see ADR-002 §D4, §D5 and the deviation table in the successor plan.

## 1. Summary

Establish clean-slate Django 5.x + DRF + Channels (ASGI/Daphne) backend with Celery/Redis and Postgres. Implement MVP loop: webhook ingest (arbitrary JSON-path mapping, raw payload preserved) → alert escalation to case → observable extraction (email/IP/domain/hash/user/URL/file as needed) → automation trigger on observable creation → results appended to case timeline. Include realtime WebSockets for live ledger (comments, assignments, state changes).

## 2. Goals

- Consistently use Django stack per ADR-001
- Build end-to-end MVP per AGENTS.md §4 with acceptance criteria
- Establish planning→coding→verification workflow with docs as source of truth
- Clean slate, secure-by-default, DRY, performant (query/index conscious)

## 3. Non-goals

- UI polish beyond minimal functional flows for MVP
- Advanced playbook editor/UI
- Full threat intel integrations (stubs ok)

## 4. Architecture

- Backend: Django 5.x + DRF + Django Channels (ASGI). Server: Daphne (as requested). Auth/session/JWT as appropriate.
- Realtime: Channels consumers + Redis channel layer; events on comments, assignments, state transitions.
- Async: Celery 5 + Redis broker/result; workers handle correlation, automation, enrichment.
- DB: PostgreSQL 15+ with JSONB (models.JSONField for raw_payload, enrichment_data). UUID PKs, timestamptz.
- Frontend: Django templates + HTMX + Tailwind CSS with Channels WebSockets for realtime (minimal MVP UI).
- Config: 12-factor via env vars; secrets not committed.

## 5. Data Model (aligned to AGENTS.md §3)

Models (core):
- Alert: id (UUID), title, severity, source, raw_payload (JSONB/JSONField), status (New/Triaged/Dismissed), case (FK nullable, 1:0..1)
- Case: id (UUID), title, description (Markdown), severity, status (Open/Investigating/Containment/Closed), owner_id (FK User), created_at
- Observable: id (UUID), case (FK), type (IP/Domain/Hash/User/Email/URL/File or subset; align MVP needs), value (String), enrichment_data (JSONB), created_at; unique (case_id, type, value) or dedupe strategy
- Task: id (UUID), case (FK), title, status (Todo/InProgress/Done), assigned_to (FK User)
- AutomationRun: id (UUID), case (FK), playbook_name, status (Pending/Running/Success/Failed), output_log (Text), triggered_by_observable_id (FK nullable)

Notes:
- Use `models.JSONField` for JSONB (raw_payload, enrichment_data)
- related_name on FKs; db_index on frequently filtered (status, severity, source, created_at, case_id)
- UUID PKs (uuid4)
- timestamptz for created_at/updated_at

## 6. API Contracts

- POST `/api/v1/alerts/webhook/{source_id}/` (or trailing slash): accept arbitrary JSON. Apply arbitrary JSON-path mapping to extract normalized fields (src_ip, dest_ip, domain, url, user, target_user, hash, file_hash, email). Preserve raw_payload. Return alert ID/status.
- Alert management: GET/POST/PATCH/DELETE as needed
- Case escalation: endpoint to promote alert to case (or automatic on triage/correlation)
- Observables: CRUD under case; extraction service on case/alert
- Tasks, AutomationRuns: CRUD

## 7. Components & Services

- Mapping Engine (arbitrary JSON-path): configurable extractor supporting JSONPath expressions (jsonpath-ng or similar). Maps to normalized Alert fields; preserves full raw payload.
- Correlation: bundle alerts into case by correlating attributes (e.g., identical destination IP within 10-minute window) as specified.
- Observable Extraction: regex + mapping-derived; normalize values (lowercase hashes), dedupe per case.
- Automation: Celery tasks triggered on events (Observable created, Case status change, Alert ingested). Persist AutomationRun; append results to case timeline/ledger.
- Realtime: WebSocket consumers for case channels emitting events (comment added, assignment changed, status transitioned).

## 8. Phases & Tasks (with Acceptance Criteria)

### Phase 1: Foundations (project setup)
Tasks:
1. Initialize Django project (clean slate), apps: `alerts`, `cases`, `observables`, `tasks`, `automation`, `core`. Configure ASGI with Channels + Daphne.
2. Settings split: `settings/dev.py` (SQLite for Django dev server) and `settings/prod.py` (PostgreSQL for production). Redis (Celery + Channels), DRF, Celery app configured. Common settings in `settings/base.py`.
3. Dependencies: django, djangorestframework, channels, daphne, celery, redis, jsonpath-ng, psycopg[binary] (for prod). Add requirements/base/dev/prod; SQLite used in dev (no extra driver).
4. Docs structure exists (planning/decisions/spec) - verify.

ACs (Acceptance Criteria):
- AC1.1: `manage.py check` passes with 0 issues
- AC1.2: ASGI imports without errors; Daphne can start (basic smoke)
- AC1.3: Celery app configured and importable
- AC1.4: No hardcoded secrets in settings
- AC1.5: Settings split (base/dev/prod). Dev uses SQLite (Django dev server), prod uses PostgreSQL. `manage.py check` passes with dev settings.

### Phase 2: Data Models & Migrations
Tasks:
1. Implement models per §5 with JSONField, indexes, related_name, constraints
2. Migrations generated and applied cleanly
3. Basic admin (optional) or model validation

ACs:
- AC2.1: Migrations run successfully; schema matches spec fields
- AC2.2: `manage.py check` passes
- AC2.3: Indexes present on filtered/joined columns (case_id, status, severity, source, created_at)
- AC2.4: FK `on_delete` set appropriately; related_name defined

### Phase 3: Ingestion + Mapping + Escalation + Extraction (MVP core)
Tasks:
1. Mapping Engine: arbitrary JSON-path extraction (jsonpath-ng). Configurable per source or generic; preserves raw_payload.
2. Webhook endpoint: POST `/api/v1/alerts/webhook/{source_id}/` creates Alert with mapped fields + raw_payload.
3. Correlation: auto-bundle alerts into Case by correlating attributes (e.g., identical dest IP within 10-min window). Manual escalation supported.
4. Observable Extraction: parse email/IP/domain/hash/user/URL/file from alert/case; normalize, dedupe per case, create links. Supports MVP "email address observable".
5. DRF serializers/viewsets/permissions for CRUD

ACs (MVP):
- AC3.1 (Ingest): POST mock raw JSON to webhook creates Alert with raw_payload preserved. Returns 201/200 with alert id.
- AC3.2 (Mapping): Arbitrary JSON-path expressions extract target fields (verified with test payloads).
- AC3.3 (Escalate): Promoting/creating case from alert works; Alert linked to Case (1:0..1). Correlation by dest IP within 10-min window functions.
- AC3.4 (Extract): Email observable parsed/extracted from case context; stored with type/value; deduped.
- AC3.5: `manage.py check` passes; basic API auth/permissions applied

### Phase 4: Automation + Realtime + Timeline (MVP complete)
Tasks:
1. Event hooks on state changes (Alert ingested, Observable created, Case status change). Emit internal signals.
2. Celery tasks: trigger on Observable creation (e.g., outbound API call stub or real configurable action); record AutomationRun (status, output_log, errors, triggered_by_observable_id).
3. Feedback loop: append automation results to case timeline/ledger as structured entries/notes.
4. Channels consumers: WebSocket rooms per case; emit events on comments, assignments, state transitions (real-time sync).
5. Error handling, retries, idempotency for tasks.

ACs:
- AC4.1 (Automate): On Observable creation, automation triggers; AutomationRun created with correct status; results appended to case timeline/notes.
- AC4.2: Event emission covers required state changes; failures logged in output_log.
- AC4.3: WebSocket consumer handles case-scoped events; auth/permissions considered.
- AC4.4: Tasks idempotent where applicable; retries configured.
- AC4.5: All previous ACs still met; no regressions.

### Phase 5: Minimal Frontend (optional for MVP verification)
Tasks:
1. Django templates structure, base layout with Tailwind CSS, HTMX included; Channels WS integration for realtime/timeline; dark theme baseline.
2. Minimal views: alerts list/create, case detail, observables, timeline (server-rendered + HTMX partials).

ACs:
- AC5.1: Templates render without errors; basic routes/views work
- AC5.2: Dark theme baseline established (Tailwind); HTMX + Channels WS integration functional; accessible

### Phase 6: Verification & Hardening
Tasks:
1. Unit/integration tests for MVP loop (ingest→escalate→extract→automate)
2. Security checks (secrets, auth, input validation, rate limiting on webhook)
3. Query optimization review (select_related/prefetch_related, index coverage)
4. Verify against all ACs with verifier agent

ACs:
- AC6.1: `python manage.py check` passes; tests pass
- AC6.2: All phase ACs verified by verifier (met)
- AC6.3: No hardcoded secrets; input validation on webhook; webhook treated as untrusted input

## 9. Dependencies & Risks

Dependencies: PostgreSQL, Redis running; jsonpath-ng; Daphne/Channels; Celery workers.
Risks: Channels/ASGI setup complexity (mitigate with minimal consumer, clear settings), JSON-path edge cases (validate, test with varied payloads), webhook abuse (rate limiting/validation).

## 10. Open Questions

- None blocking (answers provided: persistent verification reports, Daphne, arbitrary JSON-path). Will surface new questions as discovered.

## 11. Handoff Note

Approved plan → django-backend implements Phases 1-4 first (MVP), db-postgres reviews schema/indexes (Phase 2), django-frontend Phase 5 if desired, qa-tester + security-auditor + verifier validate Phase 6.
