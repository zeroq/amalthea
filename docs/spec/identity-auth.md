# Amalthea — Identity, Auth & Settings (implemented)

## Identity model (see [`data-model.md`](./data-model.md) for fields)

- `Organisation` — the tenancy root (the tenant). Since **2026-10-10** (ADR-004) tenancy is
  **strict and mandatory in practice**: `Case`/`Alert`/`Observable.owner_org` are `NOT NULL`, and
  `User.org` is `SET_NULL` — DB-nullable but **deny-by-default**, kept nullable only so
  `createsuperuser` works. An org-less caller sees nothing. Enforcement lives in
  `identity/scoping.py` (no middleware, no Postgres RLS). See [`data-model.md`](./data-model.md).
  **Since 2026-10-11** (PLAN-2026-10-10) `Organisation` extends `TimeStampedModel` with
  `created_at`/`updated_at`; a superAdmin-only directory API (`GET /api/v1/organisation`) returns
  all organisations annotated with member/case/alert/observable counts.
- `User` — `AUTH_USER_MODEL = "identity.User"` (`AbstractUser` + UUIDModel); `login` unique and
  populated from `username` on empty; unknown assignee logins are **not** auto-created
  (ADR-002 §D3/D11, REVIEW D11) — an assignee must exist before a task/alert/case can reference it.
  `role` ∈ `superAdmin / admin / analyst / read-only` (default `analyst`; CHECK `identity_user_role_valid`).
  **Since 2026-10-11** a superAdmin-only user management API (`GET|POST /api/v1/user`,
  `GET|PATCH|DELETE /api/v1/user/{idOrLogin}`) provides full CRUD with password validation,
  last-superAdmin guards, and audit logging.
- `ApiKey` — hashed bearer credential. Plaintext shown **once** at creation; only `key_hash`
  (argon2) stored. `prefix` (first 12 chars of the raw token) is *unique, not merely indexed* —
  `compat.auth` looks a key up by prefix and `.first()`, so a duplicate prefix would silently pick
  one of two rows (REVIEW H6/L2).
- `IngestionToken` (`identity_ingestion_token`) — per-org webhook credential (ADR-004 Q6). Binds an
  `IngestionSource` to exactly one `Organisation`; `secret_hash` (argon2), `prefix` unique,
  plaintext shown once. Ingest auth is **strict**: a tokenless request, an invalid secret, or a
  source with no active token ⇒ **401** (`ingest/views.py::_authenticate_token`), closing the old
  open-source hole. The token's org — never the request body — stamps the alert's `owner_org`.
- `AuditLog` (`identity_auditlog`) — append-only audit trail (ADR-004 Q5). The wired call sites today
  are the **superAdmin cross-org read** hooks (`identity/audit.py::record_cross_org_read` /
  `maybe_record_cross_org_read`, actions `cross_org_read` and `cross_org_read_query`);
  **organisation/user management** hooks (`record_organisation_change`, `record_user_change`,
  actions `organisation.created/updated/deleted`, `user.created/updated/deleted`);
  ADR-004 designates it as the home for role/org/share/token changes as those land.

## Tenant isolation & roles (`identity/scoping.py`, `identity/permissions.py`)

- **Scope:** `Case.owner_org` (or a `Share` granting the caller's org read/write) for cases;
  direct `owner_org` for alerts/observables; `superAdmin` sees all. Cross-tenant and non-existent
  resolve to the **same 404** — no existence oracle. Org-less callers are denied.
- **Mechanism:** the helpers `case_visible_q`, `org_q`, `org_filtered`, `resolve_case`,
  `resolve_alert`, `resolve_observable`, `is_case_owner` are shared by HTTP views, the query engine,
  and the WebSocket consumer. No middleware org-scope and no Postgres RLS (ADR-004 Q1).
- **Roles:** enforced by DRF permission classes in `identity/permissions.py` —
  `IsSuperAdmin`, `IsOrgAdminOrAbove` (`superAdmin`/`admin`), `IsAnalystOrAbove`
  (`superAdmin`/`admin`/`analyst`; playbook authoring/run), and `IsAuthenticatedAndOrgMember`
  (org-less users denied).
- **Sharing:** `Share` is the only sanctioned cross-tenant path, additive/default-deny, and only the
  case's owner org (or `superAdmin`) may grant/revoke.
- **Audit:** a `superAdmin` reading another org's case/alert/observable writes an `AuditLog` row.
- **SuperAdmin management (O1–O3, 2026-10-11):** All organisation/user management endpoints
  (`POST|PATCH|DELETE /api/v1/organisation/...`, `GET|POST /api/v1/user`,
  `PATCH|DELETE /api/v1/user/{idOrLogin}`) enforce `IsSuperAdmin` at the view level and return
  uniform **404** for non-superAdmin callers — no tenant/user enumeration. The legacy
  `GET|PATCH /api/v1/organisation/{idOrName}` for the caller's own org is unchanged.
- **Settings UI (O4, 2026-10-11):** server-rendered pages `ui/urls.py` →
  `/settings/organisations`, `/settings/organisations/<id>/edit|delete`,
  `/settings/users`, `/settings/users/<id>/edit|delete` (`ui/views.py`; guarded by
  `_require_superadmin_ui`, which renders `ui/404.html` for non-superAdmin). The rail item and the
  `,` keyboard shortcut (`ui/static/ui/keys.js`) are shown only to a `superAdmin`. Destructive
  actions use `<dialog>` modals (`ui/templates/ui/_modal.html`), never native `confirm()`. The
  deletion guard is shared with the API via `identity/services.py::delete_organisation` (atomic,
  refuses `reassign` without a real different target).

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
| secrets | none | `ALLOWED_HOSTS=["*"]` overridden | deterministic test keys | same | **fail-closed** — requires `DJANGO_SECRET_KEY` ≥50 chars + non-empty `DJANGO_ALLOWED_HOSTS` or `ImproperlyConfigured` |
| webhook caps | `WEBHOOK_MAX_BODY_SIZE` (5 MiB), `WEBHOOK_MAX_DEPTH` (30) | same | same | same | env-able |

`TIME_ZONE = "UTC"`, `USE_TZ = True`, `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`,
`AUTH_USER_MODEL = "identity.User"`, `LOGIN_URL = "login"` (a URL name, so the route survives
moves).

## Production hardening (implemented 2026-10-08 — closes F9)

`amalthea/settings/prod.py` **fails closed** and forces transport/cookie security:

- `SECRET_KEY` is re-read from `DJANGO_SECRET_KEY`; a missing value, the dev placeholder
  `dev-only-insecure-change-me`, or a value shorter than 50 chars raises `ImproperlyConfigured`
  (message tells the operator how to generate one).
- `DJANGO_ALLOWED_HOSTS` must yield at least one non-blank host, else `ImproperlyConfigured`
  (whitespace-only is refused).
- Forced: `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`,
  `SECURE_HSTS_SECONDS=31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`,
  `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY="same-origin"`, `X_FRAME_OPTIONS="DENY"`.
- `SECURE_PROXY_SSL_HEADER` is deliberately **not** auto-set (avoid trusting spoofable headers) —
  set it in the deployment if TLS terminates at a proxy.

Remaining, not addressed here: no CORS layer (`CORS_ALLOWED_ORIGINS` absent); API-key auth is the
documented cross-origin path.

## Secret hygiene (repo-wide guard)

The repository is public, so "never commit a secret" is enforced, not merely documented:
`scripts/secret-scan.sh` (deterministic patterns + filename guard) runs in the pre-commit hook
(staged, including docs-only commits), the pre-push hook (tracked tree), and the CI `secrets` job
(full history, with gitleaks pinned by SHA). Install hooks with `make hooks`; run manually with
`make secrets`. Policy and remediation: [`SECURITY.md`](../SECURITY.md).

## Evidence

`tests/conformance/test_authz.py` (131 tests: per-verb matrix, scope fail-closed, `read` vs
`readwrite`, 401-vs-403, prefix collisions), `test_webhook_hardening.py` (webhook auth + throttles),
`test_identity_contracts.py` (User/ApiKey shapes), `test_prod_settings.py` (fail-closed prod +
forced hardening flags), `tests/conformance/test_tenant_scoping.py` (tenant truth table, route-
enumeration conformance, per-org webhook tokens, superAdmin audit),
`tests/conformance/test_org_user_management.py` (O1–O3 org/user management: CRUD, deletion guards,
last-superAdmin protection, audit logging), `compat/auth.py` docstrings.
Settings table sourced from
`amalthea/settings/{base,dev,test,test_pg,prod}.py`. Deviations register: [`deviations.md`](./deviations.md);
ADR: [`../decisions/ADR-004-tenant-isolation.md`](../decisions/ADR-004-tenant-isolation.md);
secret policy: [`SECURITY.md`](../SECURITY.md).