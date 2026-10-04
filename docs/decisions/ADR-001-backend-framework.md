# ADR-001: Backend Framework Choice (Django + DRF + Channels)

Date: 2026-10-03
Status: Accepted
Deciders: Project lead

## Context

The Amalthea specification allows "Python (FastAPI) or Go" for the backend (AGENTS.md line 9). We need to choose a backend stack that best supports:
- Rapid CRUD development for cases/alerts/observables/tasks/automation runs
- Event-driven async work (Celery/Redis) for playbook automation
- Real-time collaboration (WebSockets) for live ledger, comments, assignments, state transitions
- Postgres + JSONB for raw_payload/enrichment_data
- Clean, maintainable API surface with auth/permissions
- Security-first development with clear conventions

## Decision

Choose **Django 5.x + Django REST Framework (DRF) + Django Channels (ASGI)** as the backend framework. Use **Celery + Redis** for asynchronous workers. **PostgreSQL** for primary storage. **Redis** for Celery broker/result backend and for Channels channel layer.

Rationale:
- Django ORM + DRF accelerate CRUD-heavy case management with strong conventions and minimal boilerplate.
- Celery integrates first-class with Django; easier orchestration for automation runs/triggers.
- Django Channels provides cohesive WebSocket support (consumers, auth, Redis pub/sub) without splitting into a separate service.
- Django's `models.JSONField` maps cleanly to Postgres JSONB for `raw_payload` and `enrichment_data`.
- Built-in auth, permissions, signals, and admin speed development; security patterns well-established.
- Sync ORM is pragmatic here (workload is I/O-bound to Postgres/outbound APIs, not CPU-bound).

## Alternatives Considered

- **FastAPI**: Excellent async performance and Pydantic validation; however, for case-management CRUD with relations and migrations, Django's ORM/admin/conventions reduce glue. WebSocket/auth integration feasible but requires more assembly; Celery integration is fine but Django-native feel is weaker.
- **Go (fiber/gin + worker)**: High concurrency and small footprint; but team velocity, ORM/migrations, ecosystem for "case management UI/API" patterns favor Django in this context. More boilerplate for auth/admin.

## Consequences

Positive:
- Single coherent app stack (Django app + Channels ASGI + Celery workers).
- Rapid implementation of MVP loop with clear patterns.
- Stronger convention alignment reduces duplication.

Negative/Tradeoffs:
- ASGI required (Daphne recommended; uvicorn also works) adding runtime complexity vs WSGI-only.
- Django ORM is synchronous by default (mitigated: I/O-bound workload; can use sync_to_async only where needed).
- WebSocket scaling needs Redis channel layer and careful consumer design.

## Implementation Notes

- ASGI entrypoint with Django Channels (Daphne server). Redis for channel layer.
- Celery app configured in Django project; workers consume automation triggers.
- JSON-path mapping engine (arbitrary JSON-path support) for webhook ingestion to normalize into Alert fields while preserving raw_payload.
- Observables linkable across cases; events emitted on state changes (comments, assignments, status transitions) to drive WebSocket updates.
- Clean slate: no carryover from `security_cmdb`; follow Django/DRF best practices and project AGENTS.md.
