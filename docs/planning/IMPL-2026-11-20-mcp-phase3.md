# Implementation Brief: MCP Server Phase 3 — OAuth 2.1 Resource Server and External-Client Hardening

**Status:** COMPLETE — implemented and verified READY — PASS
**Date:** 2026-11-20
**Approved plan:** [`PLAN-2026-11-20-mcp-phase3.md`](PLAN-2026-11-20-mcp-phase3.md)
**Roadmap item:** `TODO.md` §22.5
**Implementation target:** additive Phase 3 auth/settings/UI/docs only; no Phase 4 autonomy work.

---

## 1. Scope

Implement the approved Phase 3 plan:

- External IdP issues JWT access tokens.
- Amalthea validates those tokens as an OAuth 2.1 resource server for `/mcp`.
- Add explicit OAuth bindings from `(issuer, client_id, subject)` to organisation/service user/scopes.
- Publish RFC 9728 protected-resource metadata and OAuth 401 challenges.
- Keep static bearer credentials behind explicit `static` / `both` compatibility modes.
- Add OAuth-specific audit, deployment hardening, client documentation, tests, security review, and verifier evidence.

Do not build an Amalthea authorization server, token translation endpoint, dynamic client registration, DPoP, mTLS, PKCE persistence, or direct write tools.

---

## 2. Global constraints

- Follow AGENTS.md secret hygiene: no API keys, tokens, client secrets, JWTs, PEM blocks, raw payloads, or `.env` files in code/tests/docs/fixtures/CI.
- Real configuration stays in environment variables; `.env.example` gets placeholders only.
- Run `scripts/secret-scan.sh --staged` before any commit, `--tracked` before push, and `--history` as the CI backstop.
- Do not use `--no-verify`.
- Do not touch stash `b009a828 WIP on main: 0d2c67a3 docs: record green CI gate`.
- Do not run the full pytest suite unless the user explicitly asks. Targeted MCP/settings/security tests are allowed.
- Prefer `.venv/bin/python` and `.venv/bin/pytest`.
- Do not commit unless explicitly requested.
- Any deviation from the approved plan must be recorded in:
  - `docs/planning/PLAN-2026-11-20-mcp-phase3.md`
  - `docs/spec/deviations.md`

---

## 3. Dependency order

1. **P3-BE-01 / P3-BE-02:** settings, model migration, `Principal` extension.
2. **P3-BE-03 / P3-BE-04:** JWT/JWKS verifier and verifier composition.
3. **P3-BE-05 / P3-BE-06:** metadata endpoint, 401 challenge, SSE/proxy headers.
4. **P3-FE-01 / P3-FE-02:** OAuth binding UI after model/service APIs exist.
5. **P3-QA-01 / P3-QA-02:** conformance tests and docs after implementation surfaces exist.
6. **P3-SEC-01:** security review.
7. **P3-VER-01:** verifier AC mapping and closure recommendation.
8. Close `TODO.md` §22.5 only after verifier PASS.

---

# Brief A — Backend / MCP ASGI / Security Implementation

## A.1 Files to modify or create

### Settings and configuration

- `amalthea/settings/base.py`
- `amalthea/settings/prod.py`
- `.env.example`
- `requirements/base.txt` and lock/hash files if a JWT dependency is added
- `scripts/lock.sh` usage if required by repo dependency policy

### MCP auth, ASGI, and models

- `mcp_server/auth.py`
- `mcp_server/oauth.py` — new
- `mcp_server/asgi.py`
- `mcp_server/context.py` — only if request context needs auth outcome metadata
- `mcp_server/models.py`
- `mcp_server/migrations/0003_mcp_oauth_binding.py` — new
- `mcp_server/audit.py`
- `mcp_server/scopes.py` — only if `Principal` field changes require updates
- `mcp_server/tools/base.py` — only if `Principal` field changes require updates
- `mcp_server/views.py` — new, for RFC 9728 metadata if implemented via Django URL
- `amalthea/urls.py`

### Audit

- `identity/audit.py`

---

## A.2 P3-BE-01 — Settings, auth mode, and prod gating

### Tasks

- Add defaults/config parsing for:
  - `MCP_AUTH_MODE` / `AMALTHEA_MCP_AUTH_MODE`
  - `MCP_OAUTH_ISSUER` / `AMALTHEA_MCP_OAUTH_ISSUER`
  - `MCP_OAUTH_JWKS_URL` / `AMALTHEA_MCP_OAUTH_JWKS_URL`
  - `MCP_OAUTH_JWT_ALGS` / `AMALTHEA_MCP_OAUTH_JWT_ALGS`
  - `MCP_OAUTH_JWKS_CACHE_SECONDS` / `AMALTHEA_MCP_OAUTH_JWKS_CACHE_SECONDS`
  - `MCP_OAUTH_CLOCK_SKEW_SECONDS` / `AMALTHEA_MCP_OAUTH_CLOCK_SKEW_SECONDS`
- Default `MCP_AUTH_MODE=static`.
- Derive `MCP_RESOURCE_URL` from `MCP_BASE_URL`, appending `/mcp` once.
- Add prod validation:
  - mode in `static`, `oauth`, `both`
  - `oauth` / `both` require HTTPS issuer and JWKS URL
  - no wildcard MCP hosts
  - no localhost/private IP literal JWKS host
  - no `none`, `HS*`, or symmetric JWT algorithms
  - clock skew bounded to 60 seconds
- Add placeholders to `.env.example` only; include no real values.

### Acceptance criteria

- [ ] `MCP_AUTH_MODE=oauth` without issuer/JWKS fails startup in prod settings.
- [ ] `MCP_AUTH_MODE=both` and `oauth` expose OAuth metadata; `static` does not.
- [ ] Static bearer cannot authorize in `oauth` mode.
- [ ] OAuth verifier is not consulted in `static` mode.
- [ ] `MCP_RESOURCE_URL` is exactly `https://host/mcp` for `MCP_BASE_URL=https://host` and unchanged for `https://host/mcp`.
- [ ] No secret material is added to `.env.example`.

---

## A.3 P3-BE-02 — OAuth binding model and Principal extension

### Tasks

- Add `McpOAuthBinding` to `mcp_server/models.py` with fields from plan §5.1.
- Add unique constraint `(issuer, client_id, subject)`.
- Add validation:
  - binding org/user match
  - user active
  - scopes non-empty and subset of `PHASE1_SCOPES + PHASE2_SCOPES`
  - viewer service users limited to `mcp:read`
  - reserved scopes rejected
- Add `McpInvocation.auth_mode` and nullable `oauth_binding` FK in migration `0003_mcp_oauth_binding.py`.
- Extend `Principal` in `mcp_server/auth.py`:
  - `auth_mode: str = "static"`
  - `credential_id: UUID | None`
  - `credential_prefix: str` may be `""`
  - `oauth_binding_id: UUID | None = None`
- Update static verifier and `principal_from_token()` to populate the additive fields.
- Update all consumers to tolerate nullable credential ID and OAuth attribution.
- Update `record_invocation()` in `mcp_server/audit.py` to persist `auth_mode` and `oauth_binding_id`.

### Acceptance criteria

- [ ] Migration applies cleanly on a fresh DB and an existing DB.
- [ ] Existing static invocations default to `auth_mode="static"` with null binding.
- [ ] OAuth invocations leave `credential_id` null and set `oauth_binding_id`.
- [ ] All scope/tenant helpers still work with extended `Principal`.
- [ ] Viewer bindings cannot carry `mcp:propose` or `mcp:summary`.
- [ ] No token, JWT, or client secret is stored.

---

## A.4 P3-BE-03 — JWT/JWKS verifier

### Tasks

- Create `mcp_server/oauth.py`.
- Use a pinned JWT library with crypto support; if adding `PyJWT[crypto]`, update `requirements/base.txt` and lock/hash files per repo policy.
- Implement `McpOAuthTokenVerifier(TokenVerifier)` with async `verify_token(token)`.
- Verification requirements:
  - three-part JWT shape before verification
  - `typ` is `at+jwt` or `application/at+jwt`
  - `alg` in configured allowlist; default `RS256`
  - signature validates against JWKS key matching `kid`
  - exact `iss`
  - exact audience contains `MCP_RESOURCE_URL`
  - `exp` future with bounded skew
  - `iat` not future beyond skew if present
  - non-empty string `sub` and `client_id`
  - reject ID tokens
- JWKS behavior:
  - HTTPS only
  - no redirects
  - hostname/IP SSRF guard
  - bounded timeout
  - cache in Django cache
  - refresh on unknown `kid` at most once within a bounded window
  - negative cache failures briefly
- Binding resolution:
  - exact `(issuer, client_id, subject)` → active binding
  - organisation/user/scopes from binding only
  - ignore token org claims
  - reject unknown `mcp:*` and reserved scopes
  - ignore non-MCP scopes
  - final scopes = token scopes ∩ binding scopes ∩ global scopes
- Return SDK `AccessToken` with claims limited to:
  - `auth_mode="oauth"`
  - `oauth_binding_id`
  - `organisation_id`
  - `user_id`
  - `role`
  - `client_id`
  - `subject`
  - `resource`
  - `expires_at`
  - no raw token or full claim dump

### Acceptance criteria

- [ ] Valid token resolves to a principal with binding-supplied org/user/scopes.
- [ ] Wrong issuer, wrong audience, expired token, bad signature, bad `alg`, wrong `typ`, missing `sub`/`client_id`, and unknown `kid` fail closed.
- [ ] Unknown/inactive binding fails closed.
- [ ] Tokens containing `mcp:write`, `mcp:admin`, or unknown `mcp:*` scopes are rejected.
- [ ] `openid`/`profile` scopes are ignored.
- [ ] No raw token, JWT, client secret, or full claim set is logged.
- [ ] JWKS key rotation succeeds after cache refresh.

---

## A.5 P3-BE-04 — Verifier composition in ASGI

### Tasks

- Modify `build_mcp_server()` in `mcp_server/asgi.py`.
- Add a composite verifier or mode-selecting verifier:
  - `static`: `McpTokenVerifier` only
  - `oauth`: `McpOAuthTokenVerifier` only
  - `both`: dispatch JWT-shaped tokens to OAuth verifier and other tokens to static verifier
- JWT shape is dispatch only; security still comes from full verification.
- Update SDK `AuthSettings`:
  - `resource_server_url=MCP_RESOURCE_URL`
  - `issuer_url=MCP_OAUTH_ISSUER` when OAuth is enabled
  - retain minimum `required_scopes=["mcp:read"]`
  - do not rely on `validate_token_resource` as the only audience check; verifier still enforces `aud`
- Keep lazy Django imports and Daphne no-lifespan behavior intact.

### Acceptance criteria

- [ ] `static` mode never calls the OAuth verifier.
- [ ] `oauth` mode never calls the static verifier.
- [ ] `both` mode dispatches deterministically without accepting a JWT as a static credential.
- [ ] Existing Phase 0/1/2 ASGI behavior remains green in static mode.
- [ ] Daphne still starts without ASGI lifespan.

---

## A.6 P3-BE-05 — Metadata endpoint and 401 challenge

### Tasks

- Add `mcp_server/views.py::oauth_protected_resource_metadata`.
- Register in `amalthea/urls.py` before catch-all includes:
  - `/.well-known/oauth-protected-resource/mcp`
- Return JSON from plan §6.3 with accurate configured values.
- Return `404` in `static` mode.
- Add cache headers and `application/json`.
- Add ASGI/Starlette response middleware in `mcp_server/asgi.py`:
  - add `WWW-Authenticate: Bearer resource_metadata="...", error="invalid_token"` to `/mcp*` 401 responses when OAuth is enabled
  - preserve existing headers
  - do not modify non-401 responses
  - do not include token values or exception text
- Do not add a root-level metadata alias.

### Acceptance criteria

- [ ] Metadata URL matches RFC 9728 derivation.
- [ ] JSON fields are exact: `resource`, `authorization_servers`, `scopes_supported`, `bearer_methods_supported`.
- [ ] Zero-valued arrays are omitted.
- [ ] OAuth/both 401 includes resource metadata challenge.
- [ ] Static 401 does not advertise OAuth.
- [ ] No unsupported DPoP/mTLS fields are advertised.

---

## A.7 P3-BE-06 — Deployment hardening and observability

### Tasks

- Add `X-Accel-Buffering: no` to MCP responses that may stream.
- Ensure proxy docs/code preserve `Authorization`, `Origin`, `MCP-Protocol-Version`, `Mcp-*`, and `WWW-Authenticate` headers.
- Add structured auth outcome logging with reason codes:
  - `invalid_token`
  - `invalid_issuer`
  - `invalid_audience`
  - `expired`
  - `bad_signature`
  - `bad_algorithm`
  - `unknown_kid`
  - `jwks_fetch_failed`
  - `unknown_binding`
  - `inactive_binding`
  - `scope_denied`
  - `legacy_static_use`
- Include `request_id` from `mcp_server.context.current_request()`.
- Never log raw JWT, bearer token, client secret, full claims, or payloads.
- Add `identity.audit.record_mcp_oauth_binding_change()` for create/disable/update, mirroring `record_mcp_credential_change()`.

### Acceptance criteria

- [ ] SSE/stream responses include `X-Accel-Buffering: no`.
- [ ] Auth outcome logs contain request ID and reason code.
- [ ] Static use in `both` mode emits `legacy_static_use`.
- [ ] OAuth binding lifecycle actions emit audit rows with actor, target org, target type, target ID.
- [ ] Sensitive values are redacted from logs/audit.

---

# Brief B — Frontend / Settings UI

## B.1 Files to modify or create

- `ui/views/agents.py`
- `ui/views/__init__.py`
- `ui/urls.py`
- `ui/templates/ui/settings_agents.html`
- `ui/templates/ui/_settings_oauth_binding_create_form.html` — new
- Optional: `ui/templates/ui/_settings_oauth_binding_created.html` — new, only if needed; no secrets
- `identity/audit.py` via backend audit helper

---

## B.2 P3-FE-01 — OAuth bindings list/create/disable

### Tasks

- Extend `_settings_agents_context()` with:
  - `auth_mode`
  - `oauth_bindings` scoped by org unless superAdmin
  - `organisations` for superAdmin
  - active users scoped by org
  - allowed scopes
  - configured issuer/client-id defaults/display where safe
- Add views:
  - `settings_oauth_binding_create`
  - `settings_oauth_binding_disable`
- Add URL names following existing `ui-settings-agent-*` convention.
- Add modal create form using existing `<dialog>` pattern.
- Add disable action using existing modal, not native `confirm()`.
- Show list table with name, issuer/client/subject, org, service user, scopes, active state, last used.
- Hide OAuth UI entirely in `static` mode; show in `oauth` and `both`.
- Keep static credential UI hidden in `oauth`, visible with legacy warning in `static` / `both`.

### Acceptance criteria

- [ ] Viewer cannot access create/disable endpoints and receives the existing authorization behavior.
- [ ] Admin can manage only own-org bindings.
- [ ] superAdmin can manage bindings across orgs but must choose a target org/user.
- [ ] Binding form validates scopes, active user, and org/user match.
- [ ] No secret, token, client secret, or JWT input appears anywhere.
- [ ] Disable immediately prevents future authorization.
- [ ] Modal pattern supports Esc close, focus trap, and keyboard submission consistent with existing UI.
- [ ] Dark theme and semantic HTML/Tailwind conventions preserved.

---

## B.3 P3-FE-02 — Accessibility and keyboard QA

### Tasks

- Verify tab order in settings tabs and modals.
- Verify icon-only controls have accessible names.
- Verify status is not colour-only.
- Verify destructive action modal uses `<dialog>` and no native dialogs.
- Verify responsive layout for binding table.

### Acceptance criteria

- [ ] Keyboard-only create/disable flow works.
- [ ] Esc closes modals and returns focus predictably.
- [ ] All icon-only controls have accessible names.
- [ ] No browser-native `confirm()` / `alert()` / `prompt()` in new code.
- [ ] Template lints/tests pass.

---

# Brief C — QA / Tests / Documentation

## C.1 Files to modify or create

### New tests

- `tests/conformance/test_mcp_oauth.py`
- `tests/conformance/test_mcp_metadata.py`
- `tests/conformance/test_mcp_oauth_bindings_ui.py`
- `tests/conformance/test_mcp_deployment.py`

### Updated tests

- `tests/conformance/test_mcp_asgi.py`
- `tests/conformance/test_mcp_credentials.py`
- `tests/conformance/test_mcp_scopes_phase2.py`
- `tests/conformance/test_mcp_tools.py`
- `tests/conformance/test_settings_agent_access.py`
- `tests/conformance/test_tenant_scoping.py`
- `tests/conformance/test_wire_boundary.py`

### Docs

- `docs/decisions/ADR-013-mcp-oauth-resource-server.md` — new
- `docs/decisions/ADR-012-mcp-server-boundary.md`
- `docs/planning/PLAN-2026-11-13-mcp-server.md`
- `docs/integrations/crewai.md`
- `docs/integrations/mcp-oauth-clients.md` — new if needed
- `docs/security/SECURITY-2026-11-20-mcp-phase3.md` — new after review
- `docs/reviews/VERIFY-2026-11-20-mcp-phase3.md` — new by verifier
- `docs/spec/deviations.md` — only if deviations exist
- `TODO.md` — close §22.5 only after verifier PASS

---

## C.2 P3-QA-01 — Conformance tests

### Tasks

- Use runtime-generated RSA keys or monkeypatched JWKS fetch in tests.
- Do not commit PEM blocks or static private keys.
- Cover plan §10 cases:
  - valid token acceptance
  - `iss` / `aud` / `exp` / signature / `alg` / `typ` failures
  - missing `sub` / `client_id`
  - unknown/inactive binding
  - viewer ceiling
  - reserved/unknown scopes
  - non-MCP scope ignored
  - static mode gating
  - metadata shape and mode behavior
  - 401 challenge
  - role/tenant UI guards
  - proxy/body/host/origin/SSE behavior

### Acceptance criteria

- [ ] All new conformance modules pass.
- [ ] Existing MCP Phase 0/1/2 conformance remains green.
- [ ] Tests do not include committed secret material.
- [ ] Full suite is not run unless explicitly requested by the user.

---

## C.3 P3-QA-02 — Documentation and client guidance

### Tasks

- Write ADR-013 and link it from ADR-012.
- Update parent MCP plan decision log/status for Phase 3 as needed.
- Update CrewAI docs:
  - keep static bearer instructions for CrewAI 1.15.41 internal use
  - state lack of native MCP OAuth discovery/token acquisition
  - add generic OAuth client path
- Document:
  - `aud = MCP_RESOURCE_URL`
  - `Authorization: Bearer <token>` header only
  - required scopes
  - binding revocation
  - AS availability requirement
- Do not close `TODO.md` §22.5 yet.

### Acceptance criteria

- [ ] ADR-013 records chosen architecture and rejected options.
- [ ] ADR-012 links to ADR-013.
- [ ] Parent MCP plan reflects Phase 3 implementation state.
- [ ] CrewAI documentation is accurate for 1.15.41 limitations.
- [ ] Generic external-client documentation exists.
- [ ] No real tokens, client IDs from production systems, secrets, or raw payloads are documented.

---

# Brief D — Security review

## D.1 Files/artifacts to review

- All Phase 3 diff files.
- `docs/security/SECURITY-2026-11-20-mcp-phase3.md` — new verdict document.

## D.2 P3-SEC-01 — Review scope

Review against plan §9:

- exact issuer/audience validation
- signature/alg/typ enforcement
- JWKS SSRF safety
- binding authorization and org isolation
- viewer/read-only ceiling
- static/OAuth mode separation
- no token translation endpoint
- no token/client secret storage
- 401 challenge safety
- metadata correctness
- rate limits and identity attribution
- proxy Host/Origin/XFF handling
- SSE buffering/header preservation
- audit/log redaction
- dependency pinning if JWT library added

### Acceptance criteria

- [ ] Security verdict is PASS with no unresolved high/critical findings.
- [ ] Any accepted residual risk is explicitly listed and linked in `docs/spec/deviations.md` if it deviates from the plan.
- [ ] No secrets or raw telemetry appear in tests/docs/logs/audit.
- [ ] Secret scan is clean.

---

# Brief E — Verifier

## E.1 P3-VER-01 — Verification report

Create `docs/reviews/VERIFY-2026-11-20-mcp-phase3.md`.

Map every AC in:

- approved plan P3.0–P3.7
- this implementation brief A/B/C/D

For each AC:

- status: `met` / `not met` / `deviated`
- evidence: file path, test name, command output reference, doc section
- notes: concise

### Acceptance criteria

- [ ] Verdict is `READY — PASS` only if all ACs are met or accepted deviations are recorded.
- [ ] `TODO.md` §22.5 is marked complete only after verifier PASS.
- [ ] Final summary lists residual risks and any deviations.
- [ ] No full pytest claim is made without actual user-provided or explicitly authorized evidence.

---

## 4. Required evidence checklist

Before handoff back to planner:

- [ ] Targeted test output for MCP OAuth/metadata/UI/deployment conformance.
- [ ] `make check-ci` result.
- [ ] `scripts/secret-scan.sh --tracked` result.
- [ ] `scripts/secret-scan.sh --history` result if requested/available in CI.
- [ ] Lock/hash check result if dependencies changed.
- [ ] ADR-013.
- [ ] Security verdict.
- [ ] Verifier report.
- [ ] Updated docs.
- [ ] `TODO.md` §22.5 closed only after verifier PASS.

---

## 5. Stop conditions

Stop and ask for clarification if:

- the chosen IdP cannot set `aud` to `MCP_RESOURCE_URL`;
- an implementation would require adding an Amalthea authorization server;
- a client requires token translation or client secrets stored in Amalthea;
- a proposed change conflicts with Phase 2 proposal semantics;
- a dependency cannot be pinned/hash-locked;
- a deviation would weaken tenant isolation, scope separation, or secret hygiene.

---

## 6. Handoff note

This brief authorizes implementation planning and coding against the approved plan. It does not authorize a production IdP integration, external network calls to real providers, secret creation, commits, or roadmap closure unless the relevant constraints above are satisfied.
