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
  `role` ∈ `superAdmin / admin / analyst / viewer` (default `analyst`; CHECK `identity_user_role_valid`).
  The `viewer` role is enforced read-only on **every** endpoint and under the UI (ADR-006) — see
  [`../decisions/ADR-006-role-semantics.md`](../decisions/ADR-006-role-semantics.md).
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
- **Settings UI — Sources (2026-10-15):** `/settings` landing page + `/settings/sources` token list,
  `/settings/sources/token` (POST, create), `/settings/sources/<id>` (GET detail + POST mapping save),
  `/settings/sources/<id>/revoke` (POST, revoke)
  (`ui/views.py`; guarded by `_require_admin_ui`, accessible to `admin` and `superAdmin`).
  `admin` users see only their own organisation's tokens; `superAdmin` sees all organisations'
  tokens and must select an organisation when creating. The feed is named, not picked: the typed
  source name is slugified and its `IngestionSource` is provisioned on demand, so an existing name
  reuses that mapping profile (ADR-008). Token plaintext is shown **exactly once**
  at creation via a show-once modal; `secret_hash` is never rendered. Revocation sets
  `revoked_at` and is audited. The `,` keyboard shortcut now targets `/settings`.
- **Settings UI — source mapping editor (2026-10-16):** `/settings/sources/<id>` renders the feed's
  identity, its `mapping_config` as an editable rule table (one row per target: paths, transform,
  default), its tokens, and the raw stored config in an advanced JSON box. Both forms POST to the same
  URL with `form=table|json`; a rejected save is a **200 that echoes the input** (never a redirect that
  discards a typed JSONPath) and an unchanged save is a no-op — no write, no `updated_at` bump, no
  audit (AC12). The write is gated exactly like the read (ADR-006 role + ADR-009 org, uniform 404).

## Ingestion tokens

`IngestionToken` is the per-org webhook credential (ADR-004 Q6). It binds an `IngestionSource`
to exactly one `Organisation`. The secret is stored as an Argon2 hash (`secret_hash`); the
plaintext is shown **once** at creation and is never recoverable. The token carries a 12-char
unique `prefix` for O(1) lookup, a `name` (≤200 chars), optional `created_by` (FK to `User`,
nullable, `SET_NULL`), and two independent lifecycle timestamps: `revoked_at` (**terminal**
revocation) and `deactivated_at` (a **reversible** pause). A token accepts webhooks only while both
are `NULL` (`is_active`); `revoked_at` is never cleared, so revocation cannot be undone.

**Auth flow:** `ingest/views.py::_authenticate_token` reads the secret from `X-Webhook-Secret`,
`X-Api-Key`, or `Authorization: Bearer` and resolves it to a *single* token with no prior context —
there is no source in the URL (ADR-008). It matches by `prefix` (one indexed query), then verifies
with Argon2. Revoked **and deactivated** tokens are rejected. The token's `organisation` —
never the request body — stamps the alert's `owner_org`.

**Authentication (ADR-008).** The webhook route carries no source, so there is no "unknown source"
case: the presented secret is looked up by its unique 12-char `prefix` (one indexed query) and
verified against the Argon2 hash. Revoked and deactivated tokens are excluded by the queryset, so such
a secret, an unknown prefix and a wrong secret are one and the same answer ⇒ **401**
`unauthorized`. A source with no usable tokens therefore also rejects every post. The token's
`organisation` — never the request body — stamps the alert's `owner_org`.

**Management UI (Settings → Sources):**
- **List** (`GET /settings/sources`): tokens scoped to caller's org (admin) or all orgs (superAdmin).
  Columns: Source, Organisation (superAdmin only), Name, Prefix, Created, Last used, Issued by,
  Status/Action. The status cell offers only the actions its state permits — an active token can be
  **Deactivated** (reversible) or **Revoked** (terminal); a deactivated one can be **Activated** or
  **Deleted**; a revoked one can only be **Deleted**. `revoked_at` supersedes `deactivated_at`. Empty
  state and webhook endpoint/header hint included.
- **Create** (`POST /settings/sources/token`): admin → own org; superAdmin → required org select.
  Validates `source_name` (free text; slugified, row provisioned on demand **inside the token's
  organisation** — the same name in two orgs is two feeds, ADR-009), `name`. Calls
  `IngestionToken.create_token(..., created_by=request.user)`, which refuses any pair whose
  organisations disagree (ADR-009).
  On success: **renders** (200) a show-once modal with the plaintext. Bounded retry on `prefix`
  collision (`unique=True`). Audit: `ingestion_token.created`, plus `ingestion_source.created` when
  the typed name had to provision a new profile.
- **Revoke** (`POST /settings/sources/<id>/revoke`): sets `revoked_at=now()` — terminal. Scoped lookup
  (admin → own org; superAdmin → any). Uniform 404 for cross-org access. Audit: `ingestion_token.revoked`.
- **Deactivate / Activate** (`POST .../deactivate`, `.../activate`): pause and resume via
  `deactivated_at`. Deactivating a revoked token and activating a revoked token are both refused
  (revocation is terminal). Audit: `ingestion_token.deactivated` / `ingestion_token.activated`.
- **Delete** (`POST .../delete`): only an **inactive** token (revoked or deactivated) may be deleted —
  an active credential a live feed still presents must be deactivated first. Deleting a feed's **last**
  token also deletes the feed and its mapping profile (a feed exists only to carry credentials; leaving
  it would keep a feed that can never receive anything listed on Sources). The feed's alerts are **kept**
  (`Alert.ingestion_source` is `SET_NULL`, so they keep their `raw_payload` and verbatim wire `source`
  and merely stop naming a feed). Emptiness is evaluated *after* the token delete, so a feed with any
  remaining token — including a revoked one — survives. Audit: `ingestion_token.deleted`, plus
  `ingestion_source.deleted` when the feed was reaped.

**Audit:** `record_ingestion_token_change` writes `AuditLog` rows with `action` ∈
`{"ingestion_token.created", "ingestion_token.revoked", "ingestion_token.deactivated",
"ingestion_token.activated", "ingestion_token.deleted"}`, `target_type="IngestionToken"`,
`target_id=<uuid>`, `target_org=<token.organisation>`, `actor`, `actor_org`, `ip`. No secret material.
A deletion row is written **before** `token.delete()` in the same transaction, so `target_id` and
`target_org` survive the row.
Provisioning a feed writes `record_ingestion_source_created` (`action="ingestion_source.created"`,
`target_type="IngestionSource"`, `target_org=<source.organisation>` — since ADR-009 the feed is
org-owned, so the row is attributed to the tenant that owns it rather than to no tenant), and reaping
one writes `record_ingestion_source_deleted` (`action="ingestion_source.deleted"`, same target fields,
no `details` — the config is gone, so recording it would make the row a stale copy). Editing a
feed's mapping writes `record_ingestion_source_updated`
(`action="ingestion_source.updated"`, same target fields, `details={"changed": [...]}` = the dot-paths
whose value changed, never the values) — a `superAdmin` edit of a foreign feed is therefore visible as
`actor_org` ≠ `target_org`; a no-op save writes nothing at all (AC12).

Re-applying a feed's mapping to its existing alerts ("Save & apply") writes a second, distinct row:
`record_ingestion_source_mapping_applied` (`action="ingestion_source.mapping_applied"`, same target
fields, `details={"changed": [...], "alerts": {"candidates": n, "updated": m, "unchanged": k,
"refreshed": r}}`) — the config half is the same dot-path list, the alert half is counts. Two rows because
they are two facts: a bulk rewrite of N alerts is not the same blast radius as a rule edit, and the row
that says `updated` > 0 is the one an operator asks about when a severity moves under them months later.
`refreshed` is the sub-count of `unchanged` rows whose `mapping` record was still rewritten (**MAP-12**),
recorded so "0 updated, 19 unchanged" cannot be misread as "nothing happened" when nineteen rows moved.
Neither row contains a config *value* (MAP-7) nor an alert id — the alerts carry their own outcome in
`ingestion_warnings`. The edit row is emitted only when the stored config actually changed, so
"apply again unchanged" is one row, not two.

**End-to-end:** A token minted in the UI works immediately for `POST /api/v1/alerts/webhook/`
with `X-Webhook-Secret`; a revoked token returns **401**.

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