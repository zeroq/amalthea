# Amalthea

Open-source, developer-friendly **Security Case Management and Orchestration** platform. Amalthea
bridges the gap between pure documentation/case-logging tools (like [TheHive](https://github.com/TheHive-Project/TheHive))
and heavy automation workflows (like Tines) by treating **Alerts, Cases, Observables, and Automated
Playbook Triggers as deeply interconnected, first-class objects**.

- **Schema-agnostic ingestion** — receive raw JSON from any security tool (SIEM, EDR, CloudTrail,
  phishing boxes) at `POST /api/v1/alerts/webhook/{source_id}`, with a JSON-path mapping layer that
  normalises telemetry into a standard `Alert` without losing the raw context.
- **Incident lifecycle** — triage alerts, escalate or merge them into `Case`s, correlate on
  `correlation_key`, collaborate on a live Markdown ledger with tasks and status transitions.
- **Forensic observables** — every artifact (IP, domain, hash, mail, user) is a standalone, globally
  deduplicated entity. When the same observable appears across several cases, Amalthea links those
  cases to surface advanced persistent patterns.
- **Orchestration gateway** — domain events (`observable.created`, `case.status_changed`, …) fire
  playbooks asynchronously via Celery; results are appended back to the case timeline as structured
  entries.

## Quickstart

Requires Python 3.12+ and a virtualenv. SQLite + in-process Celery are the zero-infrastructure dev
path; Postgres 15+ and Redis are used in production (see below).

```bash
make setup            # create .venv, install pinned requirements, apply migrations
make dev              # run the dev server (Django runserver)
```

Alternatively, by hand:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env        # dev settings default to SQLite + eager Celery; edit if needed
python manage.py migrate    # defaults to amalthea.settings.dev
python manage.py runserver  # http://127.0.0.1:8000/
```

Create an analyst account and a webhook source:

```bash
python manage.py shell -c "
from identity.models import Organisation, User
from ingest.models import IngestionSource
org = Organisation.objects.create(name='Acme')
User.objects.create_user(username='analyst', password='change-me', org=org)
IngestionSource.objects.create(slug='o365', name='M365',
    mapping_config={'title': '\$.title', 'severity': '\$.severity',
                    'correlation_key': '\$.sourceRef'})
"
```

Log in at <http://127.0.0.1:8000/> and fire a mock alert:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/alerts/webhook/o365/ \
  -H "Content-Type: application/json" \
  -d '{"title":"Suspicious inbox rule","severity":3,"sourceRef":"demo-1",
       "event":"mailbox.rule.created","actor":{"email":"attacker@example.net"}}'
```

Escalate the alert from the UI — Amalthea extracts the `attacker@example.net` observable, fires any
playbook bound to `observable.created`, and appends the result to the case timeline.

## TheHive compatibility

Amalthea serves a **TheHive-shaped wire API** (`_id` strings, epoch-millisecond timestamps, severity
1–4 / TLP 0–4 / PAP 0–3, TheHive error envelope) from a Django-clean internal model, so existing
TheHive integrations can point at Amalthea with minimal changes. The compatibility contract applies to
the external integration endpoints and their accepted data formats — **not** to TheHive's UI behaviour.
See [`docs/decisions/ADR-002-thehive-api-compatibility.md`](docs/decisions/ADR-002-thehive-api-compatibility.md)
for the precise scope, and the plan for the full endpoint inventory.

## Architecture

- **Backend:** Django 5.x + DRF + Django Channels (ASGI/Daphne), Celery + Redis for async automation.
- **Database:** PostgreSQL (core state, relations, audit) + Redis (Celery broker, Channels layer).
  Dev defaults to SQLite + in-memory Channels + eager Celery so the MVP loop runs with no external
  services.
- **Frontend:** server-rendered Django templates + HTMX, Tailwind CSS, dark keyboard-driven theme,
  WebSockets for the collaborative live ledger.
- **Tests:** `make check` is the single Definition of Done — ruff, mypy (strict, pinned scope),
  `manage.py check`, migration sync, and pytest. Mutations-prove-the-guard conformance tests enforce
  that schema/contract assertions actually fail when the schema breaks.

## Repository map

```
amalthea/       Django project (settings, ASGI, Celery)
alerts/         Alert lifecycle, webhook ingestion pipeline
cases/          Case/task/timeline/observable-link ledger
observables/    Global artifact extraction, normalisation, hashing
automation/     Playbooks: domain-event dispatch → Celery → timeline feedback
ingest/         IngestionSource config, JSON-path mapping, webhook hardening
core/           Shared enums, time models, domain events
compat/         TheHive wire compatibility (mappers, auth, error envelope)
ui/             Server-rendered analyst UI (templates, HTMX, keys.js)
realtime/       WebSocket consumers (Phase 7 — in progress)
```

## Documentation

- **Plan & phases** — [`docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md`](docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md)
- **Specification** — [`AGENTS.md`](./AGENTS.md) (project spec: modules, schema, MVP criteria)
- **Architecture decisions** — [`docs/decisions/`](docs/decisions/) (ADR-001 framework, ADR-002
  TheHive compatibility)
- **Open work / completed work** — [`TODO.md`](./TODO.md), [`COMPLETED.md`](./COMPLETED.md)
- **Review reports** — [`docs/reviews/`](docs/reviews/) (independent expert gates with reproduction
  evidence)

## Production notes

- Postgres 15+ is required in production (JSONB, GIN indexes, partial indexes, real sequences). See
  `docs/reviews/` for the Postgres-only verification backlog (TODO §3.1) — SQLite dev cannot validate
  those features.
- `DJANGO_SETTINGS_MODULE=amalthea.settings.prod` expects a real secret key, Postgres, and Redis; the
  webhook rate/size limits are configurable via `AMALTHEA_*` environment variables (see
  `amalthea/settings/base.py`).
- Celery runs **eager** in dev so no broker is needed; production workers consume from Redis and
  `transaction.on_commit` guarantees a playbook never runs against a rolled-back case.

## License

AGPL-3.0-or-later (declared in `pyproject.toml`; a `LICENSE` file is pending — TODO §4.5).