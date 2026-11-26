# Amalthea

Open-source **Security Case Management and Orchestration** platform. Amalthea treats **Alerts, Cases, Observables, and automated playbook triggers** as first-class, connected objects.

- **Ingestion:** schema-agnostic webhook `POST /api/v1/alerts/webhook/`, authenticated by per-feed `X-Webhook-Secret`, mapped into normalized alerts.
- **Case management:** triage alerts, escalate to cases, track tasks/timeline, correlate observables across cases.
- **Automation:** domain events trigger Celery playbooks; results are written back to the case timeline.
- **Compatibility:** TheHive-shaped wire API for integration endpoints (not TheHive UI parity). See `docs/decisions/ADR-002-thehive-api-compatibility.md`.

## Stack

- Django 5.x + DRF + Django Channels (ASGI/Daphne)
- PostgreSQL + Redis in production; SQLite + eager Celery/in-memory channels for local dev
- Django templates + HTMX + Tailwind CSS + WebSockets

## Quickstart (local dev)

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

Optional backing services (Postgres/Redis) are defined in `docker-compose.yml`:

```bash
docker compose up -d
# then set DATABASE_URL / REDIS_URL / CELERY_* in .env and migrate
```

## Create initial data

```bash
python manage.py shell -c "
from identity.models import IngestionToken, Organisation, User
from ingest.models import IngestionSource
org = Organisation.objects.create(name='Acme')
User.objects.create_user(username='analyst', password='change-me', org=org)
User.objects.create_user(username='admin', password='change-me', org=org, role='superAdmin')
source = IngestionSource.objects.create(slug='demo', name='Demo',
    mapping_config={'title': '$.title', 'severity': '$.severity', 'correlation_key': '$.sourceRef'})
# legacy flat config example: does not support submappings (v2-only key)
token, secret = IngestionToken.create_token(source=source, organisation=org, name='Demo webhook')
print('X-Webhook-Secret:', secret)
"
```

The webhook secret is shown once; store it securely.

## Send a test alert

```bash
curl -X POST http://127.0.0.1:8000/api/v1/alerts/webhook/ \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $TOKEN" \
  -d '{"title":"Suspicious inbox rule","severity":3,"sourceRef":"demo-1"}'
```

## Development checks

```bash
make hooks      # install pre-commit/pre-push hooks (includes secret scan)
make check      # ruff, mypy, django check, migrations, css, playbook scripts
make check-fast # lint/format only
make secrets    # scan tracked files for secrets
```

`make check` is the static DoD gate; run pytest separately when needed.

## Security & production

- **Never commit secrets.** Use `.env` locally (`.env.example` has placeholders). Hooks/CI enforce secret scanning; see `SECURITY.md`.
- **Production** uses `amalthea.settings.prod`, which fails closed without a strong `DJANGO_SECRET_KEY` and non-empty `DJANGO_ALLOWED_HOSTS`. Configure Postgres/Redis and `AMALTHEA_*` settings as needed.

## Docs

- `AGENTS.md` — project specification
- `docs/planning/` — plans and implementation briefs
- `docs/decisions/` — ADRs
- `TODO.md` / `COMPLETED.md` — work tracking

## License

AGPL-3.0-or-later (see `pyproject.toml`).