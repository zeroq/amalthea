# ADR-013 — MCP OAuth 2.1 Resource Server

**Date:** 2026-11-20
**Decision:** Accepted — External IdP resource-server profile; hybrid static-bearer migration.

## 1. Context

Amalthea Phase 2 introduced static bearer credentials (`McpCredential`) for internal agent authentication. Phase 3 adds OAuth 2.1 resource server support so that Amalthea can accept access tokens issued by external identity providers (IdPs). This is a hybrid migration: static credentials remain available in `static` and `both` modes, but `oauth` mode accepts only OAuth access tokens validated against a configured IdP and JWKS.

Key constraints from the plan and related ADRs:
- Amalthea is an OAuth 2.1 **resource server only**; it does **not** become an authorization server.
- An external IdP issues short-lived access tokens (JWTs).
- Tokens are validated locally against a configured issuer and JWKS.
- Tokens are bound to an organisation and service user through explicit `McpOAuthBinding` rows; client-supplied organisation claims are ignored.
- RFC 9728 protected-resource metadata is published for `/mcp`.
- Static bearer credentials continue to work only while `MCP_AUTH_MODE=static` or `MCP_AUTH_MODE=both`; `oauth` mode accepts only OAuth access tokens.
- Amalthea does **not** become an OAuth authorization server in this phase.

## 2. Decision

### 2.1 External IdP, Amalthea as resource server (chosen)

- An external OAuth/OIDC provider issues short-lived access tokens.
- Amalthea validates JWT access tokens locally against a configured issuer and JWKS.
- Tokens are bound to an organisation and service user through an explicit `McpOAuthBinding` row; client-supplied organisation claims are ignored.
- RFC 9728 protected-resource metadata is published for `/mcp`.
- Static bearer credentials continue to work only while `MCP_AUTH_MODE=static` or `MCP_AUTH_MODE=both`; `oauth` mode accepts only OAuth access tokens.
- Amalthea does **not** become an OAuth authorization server in this phase.

### 2.2 Rejected options

| Option | Decision | Rationale |
|---|---|---|
| Static bearer only | Rejected as target | Already implemented, but unsuitable for untrusted/external clients and eventual rotation/audience requirements. |
| `django-oauth-toolkit` as embedded AS | Rejected for Phase 3 | Turns Amalthea into an IdP, adds operational surface, and does not fix client-side OAuth support gaps. Can be revisited as a self-hosted AS deployment outside Amalthea. |
| Token translation gateway | Rejected | Adds a new bearer-to-bearer exchange and replay surface. Static and OAuth verifiers remain separated by mode. |

## 3. Trust boundary

```text
External IdP / AS
  issues signed JWT access token
    aud = https://host/mcp
    scope = mcp:read mcp:propose ...
    sub/client_id identify an agent
        |
        v
MCP ASGI auth layer
  validate typ/alg/iss/aud/exp/client_id/sub/signature
  resolve (issuer, client_id, sub) -> McpOAuthBinding
  binding supplies organisation + service_user + allowed scopes
        |
        v
Principal abstraction
  auth_mode = "oauth"
  credential_id = None
  credential_prefix = ""
  oauth_binding_id = binding.id
  actor_id = binding.service_user_id
  organisation_id = binding.organisation_id
  scopes = token scopes ∩ binding allowed scopes ∩ global allowed scopes
        |
        v
Existing tool/scope/tenant/audit layers
```

The organisation is **never** taken from a token claim or tool argument. It comes only from the active binding.

## 4. Data model

### 4.1 `McpOAuthBinding` (new, migration `0003_mcp_oauth_binding.py`)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Repo convention. |
| `name` | CharField(100) | Human label in Settings. |
| `issuer` | URLField / CharField(500) | Exact expected `iss`; must match configured issuer. |
| `client_id` | CharField(255) | OAuth client identifier from the IdP. |
| `subject` | CharField(255) | Expected JWT `sub`; required in v1. |
| `organisation` | FK `Organisation`, `PROTECT` | Tenant this agent is bound to. |
| `service_user` | FK `User`, `PROTECT` | Effective actor; must belong to organisation and be active. |
| `allowed_scopes` | JSONField | Subset of `mcp:read`, `mcp:propose`, `mcp:summary`; non-empty. |
| `is_active` | BooleanField default `True` | Revocation/disable without delete. |
| `last_used_at` | DateTime nullable | Atomic touch at most once per minute. |
| `created_by` | FK `User`, `SET_NULL`, nullable | Human administrator. |
| `created_at` / `updated_at` | timestamps | Repo convention. |

**Constraints and indexes:**
- Unique: `(issuer, client_id, subject)`.
- Index: `(organisation, -created_at)` for Settings list.
- Index: `(issuer, client_id, subject)` for fast lookup; covered by unique constraint.
- Validation:
  - `service_user.org_id == organisation.id`.
  - `service_user.is_active`.
  - viewer service users may receive only `mcp:read`.
  - `allowed_scopes` is a non-empty list containing only allowed scopes.
  - reserved scopes `mcp:write` / `mcp:admin` are rejected.

### 4.2 `McpInvocation` additions (same migration)

| Field | Type | Notes |
|---|---|---|
| `auth_mode` | CharField(16) | `static` or `oauth`; default `static` for existing rows. |
| `oauth_binding` | FK `McpOAuthBinding`, `SET_NULL`, nullable | Attribution for OAuth invocations. |

Existing `credential`, `actor`, and `organisation` fields remain. OAuth invocations leave `credential` null, set `oauth_binding`, and set actor/organisation from the binding.

### 4.3 No stored tokens

No access tokens, refresh tokens, client secrets, PKCE verifiers, or JWTs are stored in Amalthea. Audit records contain binding identifiers, request ID, status, and redacted metadata only.

## 5. Configuration and API contract

### 5.1 Settings (new environment variables)

| Setting | Env var | Required in OAuth mode | Notes |
|---|---|---|---|
| `MCP_AUTH_MODE` | `AMALTHEA_MCP_AUTH_MODE` | yes | `static`, `oauth`, or `both`. Default `static` for compatibility. |
| `MCP_OAUTH_ISSUER` | `AMALTHEA_MCP_OAUTH_ISSUER` | yes | Exact `iss` value; HTTPS URL in prod. |
| `MCP_OAUTH_JWKS_URL` | `AMALTHEA_MCP_OAUTH_JWKS_URL` | yes | HTTPS JWKS URI; exact allowlisted URL. |
| `MCP_OAUTH_JWT_ALGS` | `AMALTHEA_MCP_OAUTH_JWT_ALGS` | no | Default `RS256`; comma-separated allowlist. |
| `MCP_OAUTH_JWKS_CACHE_SECONDS` | `AMALTHEA_MCP_OAUTH_JWKS_CACHE_SECONDS` | no | Default `3600`, minimum `300`. |
| `MCP_OAUTH_CLOCK_SKEW_SECONDS` | `AMALTHEA_MCP_OAUTH_CLOCK_SKEW_SECONDS` | no | Default `30`, maximum `60`. |

### 5.2 Derived value

```text
MCP_RESOURCE_URL = MCP_BASE_URL.rstrip("/") + "/mcp"
```

If `MCP_BASE_URL` already ends in `/mcp`, do not append twice.

### 5.3 Prod fail-closed rules

When `MCP_ENABLED=True`:

- `MCP_BASE_URL` must be HTTPS and non-empty.
- `MCP_ALLOWED_HOSTS` must be non-empty and cannot contain `*`.
- `MCP_AUTH_MODE` must be one of `static`, `oauth`, `both`.
- In `oauth` or `both` mode:
  - `MCP_OAUTH_ISSUER` must be a non-empty HTTPS URL.
  - `MCP_OAUTH_JWKS_URL` must be a non-empty HTTPS URL.
  - JWKS host must not be localhost or a private IP literal.
  - `MCP_OAUTH_JWT_ALGS` must not contain `none` or symmetric algorithms such as `HS256`.
- In `oauth` mode, static-bearer-only credentials cannot authorize any request.

### 5.4 Protected-resource metadata (RFC 9728)

```http
GET /.well-known/oauth-protected-resource/mcp
```

```json
{
  "resource": "https://mcp.example.com/mcp",
  "authorization_servers": ["https://idp.example.com"],
  "scopes_supported": ["mcp:read", "mcp:propose", "mcp:summary"],
  "bearer_methods_supported": ["header"]
}
```

Rules:
- `resource` exactly equals `MCP_RESOURCE_URL`.
- No fragment or query string in `resource`.
- Omit zero-valued arrays.
- Do not advertise unsupported DPoP or mTLS fields.
- Serve only in `oauth` or `both` mode.
- Return `404` in `static` mode.

### 5.5 401 challenge

Unauthenticated or invalid OAuth requests to `/mcp*` return:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp", error="invalid_token"
```

Rules:
- No token value, claim dump, or exception text in the challenge or response body.
- Static mode keeps the existing 401 behavior without `resource_metadata`.
- `both` mode advertises OAuth metadata but logs legacy static-bearer use.

### 5.6 Token validation contract

JWT access tokens must satisfy:

- Header `typ` is `at+jwt` or `application/at+jwt`.
- Header `alg` is in `MCP_OAUTH_JWT_ALGS`.
- Signature validates against a key from `MCP_OAUTH_JWKS_URL` matching `kid`.
- `iss` exactly equals `MCP_OAUTH_ISSUER`.
- `aud` contains `MCP_RESOURCE_URL`.
- `exp` is in the future, allowing only configured clock skew.
- `iat`, if present, is not in the future beyond skew.
- `sub` and `client_id` are non-empty strings.
- `scope`, if present, is a space-delimited string.
- ID tokens are rejected even if signed by the same issuer.

**Scope handling:**

- MCP-relevant scopes are those beginning with `mcp:`.
- Unknown `mcp:*` scopes and reserved `mcp:write` / `mcp:admin` reject the token.
- Non-MCP scopes such as `openid` or `profile` are ignored.
- Final scopes are the intersection of token scopes, binding `allowed_scopes`, and global allowed scopes.
- Viewers can never receive `mcp:propose` or `mcp:summary` through a binding.

## 6. UI / UX

### 6.1 Settings → Agent access

Add a second tab/section: **OAuth bindings**.

Static credentials remain under **Static credentials** and are labelled legacy once OAuth mode is configured.

OAuth bindings list shows:
- name
- issuer / client_id / subject
- organisation
- service user
- scopes
- active/disabled state
- last used
- revoke/disable action

Create form fields:
- name
- issuer (default from `MCP_OAUTH_ISSUER`, read-only in non-superAdmin contexts)
- client_id
- subject
- organisation (superAdmin only; otherwise fixed to request user's org)
- service user
- allowed scopes

No secret, token, client secret, or JWT input is shown.

Revoke/disable uses the existing `<dialog>` modal pattern. Native `confirm()` / `alert()` / `prompt()` remain forbidden.

### 6.2 Visibility and role rules

- Viewer: denied.
- Analyst: read-only access to bindings in own org; cannot create/revoke.
- Admin: create/list/disable bindings in own org.
- superAdmin: create/list/disable bindings across orgs, still bound to a chosen org and service user.
- Static credential creation UI is hidden in `oauth` mode and available with a legacy warning in `static` / `both` modes.

### 6.3 Client docs

- Keep the static-bearer instructions for internal CrewAI 1.15.41 use.
- State that CrewAI 1.15.41 does not natively perform MCP OAuth discovery/token acquisition.
- Add a second documented path using a generic MCP client or SDK wrapper that injects an externally acquired bearer token.
- Document required audience, scopes, header-only bearer, and revocation procedure.

## 7. Phases and acceptance criteria

### P3.0 — Decision baseline and ADR

- Confirm the first provider target. Recommended default: **generic RFC 8414/OIDC-capable provider**, with Keycloak used as the staging reference if available.
- Write `docs/decisions/ADR-013-mcp-oauth-resource-server.md`.
- Update ADR-012 to mark OAuth as selected rather than merely deferred.
- Confirm that Amalthea will not host the AS and that no new runtime OAuth server dependency is introduced.
- Record the static-bearer migration policy and target `oauth`-mode cutover owner.

**AC**

- [ ] ADR-013 records issuer/audience validation, binding model, allowed scopes, auth modes, and rejected alternatives.
- [ ] ADR-012 links to ADR-013 and no longer implies that the OAuth profile is undecided.
- [ ] No new OAuth authorization-server dependency is added.
- [ ] The selected provider path is documented as generic; provider-specific claim magic is not used.

### P3.1 — Settings, production gating, and auth-mode plumbing

- Add `MCP_AUTH_MODE` and OAuth settings to `amalthea/settings/base.py`, `prod.py`, and `.env.example`.
- Add startup validation for OAuth mode and JWKS URL safety.
- Add tests for valid/invalid mode combinations.
- Ensure `MCP_ENABLED=False` still short-circuits all MCP metadata and endpoints.

**AC**

- [ ] `MCP_AUTH_MODE=oauth` without issuer/JWKS fails startup in prod settings.
- [ ] `MCP_AUTH_MODE=both` and `oauth` expose OAuth metadata; `static` does not.
- [ ] Static bearer cannot authorize any request in `oauth` mode.
- [ ] OAuth verifier is not consulted in `static` mode.
- [ ] `MCP_RESOURCE_URL` is exactly `https://host/mcp` for `MCP_BASE_URL=https://host` and unchanged for `https://host/mcp`.
- [ ] No secret material is added to `.env.example`.

### P3.2 — OAuth binding model and Settings UI

- Add `McpOAuthBinding` and `McpInvocation` fields in migration `0003_mcp_oauth_binding.py`.
- Add model validation and audit actions for binding create/disable/update.
- Add Settings → Agent access OAuth bindings list/create/disable UI.
- Add role and tenant guards.

**AC**

- [ ] Binding is unique on `(issuer, client_id, subject)`.
- [ ] Binding service user must be active and in the same organisation.
- [ ] Viewer service users cannot receive `mcp:propose` or `mcp:summary`.
- [ ] Reserved scopes are rejected.
- [ ] Admin can manage only own-org bindings; superAdmin can manage across orgs.
- [ ] No secrets or token material appear in forms, pages, audit rows, or templates.
- [ ] Disable/revoke prevents future token validation immediately.

### P3.3 — JWT verifier and Principal mapping

- Add `mcp_server/oauth.py` with JWT/JWKS verification.
- Add `McpOAuthTokenVerifier` implementing the SDK `TokenVerifier` interface.
- Compose verifiers in `mcp_server/auth.py` based on `MCP_AUTH_MODE`.
- Cache JWKS and metadata failures using the Django cache with bounded TTLs.
- Extend the existing `Principal` abstraction with `auth_mode` and nullable `oauth_binding_id`; static credentials keep their existing values and all tool/audit consumers must tolerate the additive fields.
- Update `record_invocation()` in `mcp_server/audit.py` to persist `auth_mode` and `oauth_binding_id`.

**AC**

- [ ] Valid OAuth token resolves to a `Principal` with org/user/scopes from the active binding.
- [ ] Wrong issuer, wrong audience, expired token, bad signature, disallowed alg, wrong `typ`, missing `sub`/`client_id`, and unknown `kid` all fail closed.
- [ ] Unknown/inactive binding fails closed.
- [ ] Client-supplied org claims are ignored.
- [ ] Unknown/reserved MCP scopes are rejected.
- [ ] Non-MCP scopes are ignored.
- [ ] JWKS cache refresh works on key rotation and bounded `kid` miss.
- [ ] No JWT, raw token, or full claim set is logged.

### P3.4 — Protected-resource metadata and challenges

- Add metadata handler for `/.well-known/oauth-protected-resource/mcp`.
- Add 401 challenge middleware/wrapper for `/mcp*` in OAuth-capable modes.
- Return `404` for metadata in static-only mode.
- Add cache headers and content type.

**AC**

- [ ] Metadata URL matches RFC 9728 derivation for `MCP_RESOURCE_URL`.
- [ ] `resource` exactly equals `MCP_RESOURCE_URL`.
- [ ] `authorization_servers`, `scopes_supported`, and `bearer_methods_supported` are present and accurate.
- [ ] Zero-valued arrays are omitted.
- [ ] OAuth 401 responses include `WWW-Authenticate` with `resource_metadata`.
- [ ] Static-mode 401 responses do not advertise OAuth metadata.
- [ ] No unsupported DPoP/mTLS fields are advertised.

### P3.5 — Deployment hardening

- Document proxy trust, Host/Origin, rate limiting, body caps, and stateless multi-worker behavior.
- Add `X-Accel-Buffering: no` for SSE responses or response paths.
- Ensure reverse-proxy docs preserve `Authorization`, `Origin`, `MCP-Protocol-Version`, `Mcp-*`, and `WWW-Authenticate` headers.
- Verify shared cache guidance for JWKS and rate limits in production.
- Keep Daphne no-lifespan lazy startup behavior documented.

**AC**

- [ ] Proxy documentation states exact `MCP_TRUSTED_PROXY_COUNT` semantics and warns that `0` ignores `X-Forwarded-For`.
- [ ] `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` reject wildcard in production.
- [ ] SSE responses include `X-Accel-Buffering: no`.
- [ ] Proxy docs set idle timeouts above keep-alive interval and disable buffering.
- [ ] No sticky sessions are required by design.
- [ ] Production docs require Redis-backed shared cache for rate counters and JWKS cache.
- [ ] Existing body cap and row cap behavior remains unchanged.

### P3.6 — Observability and audit

- Add OAuth auth outcome events with request ID correlation.
- Extend `McpInvocation` attribution for OAuth mode.
- Add logging for JWKS failures, unknown bindings, scope denials, and static legacy use in `both` mode.
- Define redaction allowlist.

**AC**

- [ ] Every authenticated OAuth tool call has `auth_mode="oauth"` and `oauth_binding_id`.
- [ ] Static calls in `both` mode are marked as legacy use.
- [ ] Auth failure reason is detectable from structured logs/events.
- [ ] Logs/audit never contain raw JWTs, bearer tokens, client secrets, full claim dumps, or raw payloads.
- [ ] Request ID from `McpRequestContext` is present on MCP auth and invocation events.

### P3.7 — Client docs, QA, security, verification, and closure

- Update CrewAI docs and add generic OAuth client path.
- Add conformance tests for all OAuth paths.
- Run full suite, CI gate, secret scans, lock check.
- Security review of OAuth RS boundary.
- Independent verifier maps all ACs.
- Close `TODO.md` §22.5 only after verifier PASS.

**AC**

- [ ] Full local suite green.
- [ ] `make check-ci` remains within the five-minute budget.
- [ ] `scripts/secret-scan.sh --tracked` and `--history` clean.
- [ ] Dependency lock unchanged or hash-verified if new JWT dependency is added.
- [ ] Security verdict is PASS or documents accepted residuals.
- [ ] Verifier verdict is READY — PASS with deviations recorded.
- [ ] CrewAI limitations and the generic OAuth client path are documented.
- [ ] `TODO.md` §22.5 is closed only after verification.

## 8. Security

### 8.1 Required controls

- Exact `iss` match; no issuer normalization or substring matching.
- Exact audience/resource check against `MCP_RESOURCE_URL`.
- Signature verification with pinned algorithms; `none` and symmetric algorithms rejected.
- JWT access-token type required; ID tokens rejected.
- Short clock skew only.
- JWKS URL allowlisted, HTTPS-only, and SSRF-protected.
- No redirects followed when fetching JWKS unless host is explicitly allowlisted; recommended: no redirects.
- Binding lookup requires exact issuer/client_id/subject match.
- Organisation and service user come only from active binding.
- Viewer ceiling enforced at binding creation and token validation.
- Static bearer and OAuth verifiers are mode-gated and never silently blended in `oauth` mode.
- No token exchange, token forwarding, or static-to-OAuth bridge.
- No OAuth metadata advertised in static-only mode.
- No raw token or claim dumps in logs, audit, error bodies, or metrics.
- Rate limits still apply per authenticated principal and client IP.
- Host, Origin, body-cap, row-cap, and timeout controls remain enabled.

### 8.2 Threats explicitly addressed

| Threat | Control |
|---|---|
| Audience confusion | Exact `aud` check against `MCP_RESOURCE_URL`; no override in v1. |
| Token passthrough | Static verifier disabled in `oauth` mode; no translation endpoint. |
| Scope downgrade/upgrades | Token scopes intersect binding and global allowlists; reserved scopes rejected. |
| Org spoofing | Org from binding only; org claims and tool args ignored. |
| JWKS SSRF | HTTPS-only allowlisted URL, private/localhost rejection, no redirects. |
| Stale keys | JWKS cache TTL and bounded `kid` miss refresh. |
| Proxy IP spoofing | `MCP_TRUSTED_PROXY_COUNT` rules; default ignores `X-Forwarded-For`. |
| SSE leakage/ buffering | `X-Accel-Buffering: no`, redaction, request-scoped streams. |
| Multi-worker inconsistency | Stateless MCP HTTP; shared cache in prod; no PKCE/nonce state in Amalthea. |
| Audit leakage | Allowlisted redaction; no tokens or raw payloads. |
| Static-bearer longevity | Explicit `oauth` mode and legacy-use audit in `both` mode. |

## 9. Decision log

| Date | Decision | By |
|---|---|---|
| 2026-11-20 | Phase 3 planning started after MCP Phase 2 verification and closure | user |
| 2026-11-20 | Recommended architecture: external IdP resource-server profile with hybrid static-bearer migration | planner |
| 2026-11-20 | Phase 3 plan approved; implementation handoff issued | user |

## 10. Open questions and recommended defaults

| # | Question | Recommended default |
|---|---|---|
| OQ-1 | First IdP target | Generic RFC 8414/OIDC-capable provider; Keycloak as staging reference if available. No provider-specific claim mapping. |
| OQ-2 | Default auth mode | `static` for rollout compatibility; external/untrusted deployments must set `oauth`. |
| OQ-3 | Binding key | Require exact `(issuer, client_id, subject)`; do not bind on `client_id` alone. |
| OQ-4 | JWT algorithms | `RS256` only in v1; expand to ES256/PS256 later if required. |
| OQ-5 | Resource identifier | `MCP_RESOURCE_URL = MCP_BASE_URL + /mcp`; no separate audience override. |
| OQ-6 | Root metadata alias | Do not add; canonical RFC 9728 path only unless a selected client requires it. |
| OQ-7 | Static-bearer removal | Do not remove in Phase 3; schedule a later cleanup after `oauth` adoption. |
| OQ-8 | AMALTHEA_MCP_AUTH_MODE default when unset | `static`, preserving Phase 1/2 behavior. |

## 11. Links

- Related: [ADR-012 — MCP Server Boundary](../decisions/ADR-012-mcp-server-boundary.md)
- Related: [PLAN-2026-11-20-mcp-phase3.md](../planning/PLAN-2026-11-20-mcp-phase3.md)