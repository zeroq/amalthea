# Amalthea — Identity, Auth & Settings (implemented)

## Identity model (see [`data-model.md`](./data-model.md) for fields)

- `Organisation` — the tenancy root. `User.org` is a `SET_NULL` FK.
- `User` — `AUTH_USER_MODEL = "identity.User"` (`AbstractUser` + UUIDModel); `login` unique and
  populated from `username` on empty; unknown assignee logins are **not** auto-created
  (ADR-002 §D3/D11, REVIEW D11) — an assignee must exist before a task/alert/case can reference it.
- `ApiKey` — hashed bearer credential. Plaintext shown **once** at creation; only `key_hash`
  (argon2) stored. `prefix` (first 12 chars of the raw token) is *unique, not merely indexed* —
  `compat.auth` looks a key up by prefix and `.first()`, so a duplicate prefix would silently pick
  one of two rows (REVIEW H6/L2).

## Authentication order (`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`, `amalthea/settings/base.py`)

1. `compat.auth.ApiKeyAuthentication` — `Authorization: Bearer <token>`; 12-char prefix lookup,
   argon2 verify; revoked ⇒ `AuthenticationFailed("Invalid API key")`; a key with no user binds
   `AnonymousUser` + the key (keeps the scope check intact).
2. `rest_framework.authentication.BasicAuthentication`.
3. `rest_framework.authentication.SessionAuthentication`.

Header challenge: `authenticate_header` → `"Bearer"`.

## Permission policy (`compat.auth.ScopePermission`)

Listed **after** `IsAuthenticated` in `DEFAULT_PERMISSION_CLASSES`:

- An anonymous caller is a 401 before `ScopePermission` is reached.
- A `scope="read"` ApiKey on any verb other than `GET/HEAD/OPTIONS` ⇒ **403 `Forbidden`**
  (fail-closed: read keys cannot PATCH/POST/DELETE — deviation P10-7).
- Everything else passes (`True`) — session and basic auth are unaffected; `login`/`logout` carry
  an explicit `AllowAny` replacing the list outright.

## Errors

`compat.errors.thehive_exception_handler` — envelope in [`api.md`](./api.md). Notable: login
failure ⇒ **400, never 401** (no account-existence oracle, P10-6); valid-unique-vs-prefix collisions
covered above; `NotAuthenticated` ⇒ 401 `AuthenticationError`.

## Throttles

- Ingest webhook: per-source `wh:src:{slug}` and per-IP `wh:ip:{ip}` cache-keyed sliding windows —
  default 60/min and 300/min respectively (both configurable; 429 `rateLimitExceeded`).
- DRF global: `AnonRateThrottle` `anon: 100/min`, `UserRateThrottle` `user: 1000/min`.

## Settings per environment (`amalthea/settings/{base,dev,test,test_pg,prod}.py`)

| concern | base | dev | test | test_pg | prod |
|---|---|---|---|---|---|
| `DEBUG` | — | `True` | `False` | `False` | `False` |
| DB | `DATABASES` overridden per env | sqlite `db/dev.sqlite3` | sqlite `:memory:` | Postgres `amalthea/amalthea@127.0.0.1:5432`:image `amalthea` | env-driven (`POSTGRES_*`) |
| `CHANNEL_LAYERS` | memory (tests/dev relay); prod: `REDIS_URL` | memory | memory | memory | Redis `amalthea_channel` prefix |
| `CELERY_BROKER_URL` | `REDIS_URL` default `redis://localhost:6379/0` | same | `memory://` | `memory://` | env-driven |
| secrets | none | `ALLOWED_HOSTS=["*"]` overridden | deterministic test keys | same | env-driven `SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` |
| webhook caps | `WEBHOOK_MAX_BODY_SIZE` (5 MiB), `WEBHOOK_MAX_DEPTH` (30) | same | same | same | env-able |

`TIME_ZONE = "UTC"`, `USE_TZ = True`, `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`,
`AUTH_USER_MODEL = "identity.User"`, `LOGIN_URL = "login"` (a URL name, so the route survives
moves).

## Deferred prod-hardening (not implemented — recorded, see F9)

- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` are **not** forced in production settings today
  (the repo is HTTPS-deployed by convention, but the flags are not set — a known hardening item).
- No CORS layer (`CORS_ALLOWED_ORIGINS` absent). API-key auth is the documented cross-origin path.

## Evidence

`tests/conformance/test_authz.py` (131 tests: per-verb matrix, scope fail-closed, `read` vs
`readwrite`, 401-vs-403, prefix collisions), `test_webhook_hardening.py` (webhook auth + throttles),
`test_identity_contracts.py` (User/ApiKey shapes), `compat/auth.py` docstrings. Settings table
sourced from `amalthea/settings/{base,dev,test,test_pg,prod}.py` (prod: `DEBUG=False`,
env-enforced `SECRET_KEY`/`ALLOWED_HOSTS`). Deviations register: [`deviations.md`](./deviations.md).