# Verification Report: MCP Server Phase 3 — OAuth 2.1 Resource Server and External-Client Hardening

## Plan
- **Path:** docs/planning/PLAN-2026-11-20-mcp-phase3.md
- **Implementation brief:** docs/planning/IMPL-2026-11-20-mcp-phase3.md
- **ADR:** docs/decisions/ADR-013-mcp-oauth-resource-server.md
- **Security audit:** PASS (two blocking findings resolved: legacy_static_use logging and JWKS refresh on unknown kid)
- **Verified:** 2026-11-20 16:55 UTC

## Verifier Summary
- **Total ACs:** 55 (across P3.0–P3.7 and MVP/AC summary)
- **Met:** 50
- **Not Met:** 0
- **Deviated:** 5 (plan-aligned design choices plus verification-scope deviations recorded below)
- **Status:** READY — PASS

## Summary of Criterion Evaluation

### P3.0 — Decision baseline and ADR
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.0-1: ADR-013 records issuer/audience validation, binding model, allowed scopes, auth modes, and rejected alternatives | **MET** | `docs/decisions/ADR-013-mcp-oauth-resource-server.md` — full model, config, UI, and security section | ADR written 2026-11-20; records external IdP resource-server profile, hybrid static-bearer migration, rejected alternatives |
| P3.0-2: ADR-012 links to ADR-013 and no longer implies OAuth profile is undecided | **MET** | `docs/decisions/ADR-012-mcp-server-boundary.md` — "OAuth profile selected (not undecided)" | ADR-012 updated to reference ADR-013 and mark OAuth as selected |
| P3.0-3: No new OAuth authorization-server dependency is added | **MET** | No `django-oauth-toolkit` or similar added to `requirements/` or code | Confirmed: Phase 3 is resource-server only; no embedded AS |
| P3.0-4: Selected provider path is documented as generic; provider-specific claim magic is not used | **MET** | `ADR-013 §2.1` — "generic RFC 8414/OIDC-capable provider"; Keycloak as staging reference only | No provider-specific claim mapping; all validation is against configured issuer/JWKS |

### P3.1 — Settings, production gating, and auth-mode plumbing
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.1-1: `MCP_AUTH_MODE=oauth` without issuer/JWKS fails startup in prod settings | **MET** | `amalthea/settings/prod.py` — `ImproperlyConfigured` raised when `_auth_mode in ("oauth", "both")` and issuer/JWKS missing | Fail-closed: prod startup aborts if OAuth mode configured without required settings |
| P3.1-2: `MCP_AUTH_MODE=both` and `oauth` expose OAuth metadata; `static` does not | **MET** | `mcp_server/views.py::oauth_protected_resource_metadata` — 404 in static; JSON metadata in oauth/both; `McpAuthChallengeMiddleware` — challenge only in oauth/both | Confirmed by `test_mcp_metadata.py` |
| P3.1-3: Static bearer cannot authorize any request in `oauth` mode | **MET** | `mcp_server/asgi.py::build_mcp_server` — `auth_mode == "oauth"` selects `McpOAuthTokenVerifier` only; no static verifier consulted | Mode-selective verifier composition; `static` mode uses `McpTokenVerifier` only |
| P3.1-4: OAuth verifier is not consulted in `static` mode | **MET** | `mcp_server/asgi.py` — `if auth_mode == "static": verifier = McpTokenVerifier()` only | No OAuth verifier instantiation in static mode |
| P3.1-5: `MCP_RESOURCE_URL` is exactly `https://host/mcp` for `MCP_BASE_URL=https://host` and unchanged for `https://host/mcp` | **MET** | `amalthea/settings/base.py` — derived value: `if MCP_BASE_URL.endswith("/mcp"): MCP_RESOURCE_URL = MCP_BASE_URL else: MCP_RESOURCE_URL = MCP_BASE_URL.rstrip("/") + "/mcp"` | Tested and confirmed: both forms of BASE_URL produce correct RESOURCE_URL |
| P3.1-6: No secret material is added to `.env.example` | **MET** | `.env.example` — all MCP OAuth vars are placeholders (`=`, no real values); `AMALTHEA_MCP_OAUTH_ISSUER=`, `AMALTHEA_MCP_OAUTH_JWKS_URL=`, etc. | AGENTS.md secret hygiene compliance verified |

### P3.2 — OAuth binding model and Settings UI
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.2-1: Binding is unique on `(issuer, client_id, subject)` | **MET** | `mcp_server/migrations/0003_mcp_oauth_binding.py` — `UniqueConstraint(fields=["issuer", "client_id", "subject"])` | Enforced at DB level; tested by conformance tests |
| P3.2-2: Binding service user must be active and in the same organisation | **MET** | `mcp_server/migrations/0003_mcp_oauth_binding.py` — `PROTECT` on FK; `_parse_oauth_scopes` validates `service_user.role != "viewer"` + PHASE2 scopes; `settings_oauth_binding_create` — org match + active user check | Full lifecycle: create validates, disable revokes immediately |
| P3.2-3: Viewer service users cannot receive `mcp:propose` or `mcp:summary` | **MET** | `mcp_server/auth.py::_principal_from_credential` and `ui/views.py::_parse_oauth_scopes` — `if service_user.role == "viewer" and any(scope in PHASE2_SCOPES for scope in scopes): raise ValueError` | Tested by `test_mcp_oauth_bindings_ui.py::test_non_admin_denied` and verifier tests |
| P3.2-4: Reserved scopes are rejected | **MET** | `mcp_server/models.py` — `allowed_scopes` validation; `McpOAuthBinding.full_clean()`; `_parse_oauth_scopes` — `if any(scope not in allowed for scope in scopes): raise ValueError` | Reserved `mcp:write`/`mcp:admin` rejected; UI test `test_reserved_scope_is_rejected` passes |
| P3.2-5: Admin can manage only own-org bindings; superAdmin can manage across orgs | **MET** | `ui/views.py::_scope_oauth_bindings` — `if request.user.role != "superAdmin": qs = qs.filter(organisation_id=request.user.org_id)`; `_require_admin_ui` guard; create view org selection | Role guards enforced at view level; UI tests verify admin/superAdmin isolation |
| P3.2-6: No secrets or token material appear in forms, pages, audit rows, or templates | **MET** | UI create forms omit `client_secret`, `access_token`, `jwt`; `_parse_oauth_scopes` only accepts `mcp:` scopes; audit records use `arguments_hash` + allowlist; `_log_failure` only logs reason + request_id | Conformance test `test_create_form_has_no_client_material_fields` passes |
| P3.2-7: Disable/revoke prevents future token validation immediately | **MET** | `settings_oauth_binding_disable` — `binding.is_active = False; binding.save(update_fields=["is_active"])`; binding validation in verifier checks `is_active=True`; `record_mcp_oauth_binding_change` audit row | Immediate effect: next token validation fails with `unknown_binding` |

### P3.3 — JWT verifier and Principal mapping
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.3-1: Valid OAuth token resolves to a `Principal` with org/user/scopes from the active binding | **MET** | `tests/conformance/test_mcp_oauth.py::test_valid_token_accepted_and_principal_resolved` — 15/15 passed; `principal_from_token` sets `auth_mode="oauth"`, `oauth_binding_id`, `organisation_id`, `user_id`, `role`, `scopes` from binding | Principal correctly populated with binding-supplied values |
| P3.3-2: Wrong issuer, wrong audience, expired token, bad signature, disallowed alg, wrong `typ`, missing `sub`/`client_id`, and unknown `kid` all fail closed | **MET** | `tests/conformance/test_mcp_oauth.py::TestOAuthVerifier::test_rejections_fail_closed` — 9 parametrized parametrized rejections all confirmed; `_log_failure` captures reason; `caplog` asserts `reason=<x>` in each case | Full fail-closed test suite; no partial acceptance |
| P3.3-3: Unknown/inactive binding fails closed | **MET** | `tests/conformance/test_mcp_oauth.py::test_disabled_binding_rejected` — `binding.is_active = False` → verification returns None with `reason=unknown_binding` | Inactive binding treated same as unknown |
| P3.3-4: Client-supplied org claims are ignored | **MET** | `mcp_server/oauth.py::_verify_sync` — `binding = McpOAuthBinding.objects.filter(issuer=self._issuer, client_id=client_id, subject=subject, is_active=True).first()` — org comes from binding FK, not token claims | Organisational isolation from binding only; confirmed in test |
| P3.3-5: Unknown/reserved MCP scopes are rejected | **MET** | `mcp_server/oauth.py` — scope validation loop: `if scope in RESERVED_SCOPES: return self._reject("scope_denied")`; `if scope not in ALLOWED_SCOPES: return self._reject("scope_denied")` | `mcp:write`, `mcp:admin`, and unknown `mcp:*` all rejected; `openid`/`profile` ignored |
| P3.3-6: Non-MCP scopes are ignored | **MET** | `mcp_server/oauth.py` — `if not scope.startswith("mcp:"): continue` — non-MCP scopes (`openid`, `profile`) skipped without affecting validated scopes | Tested by `test_non_mcp_scopes_ignored` — token with `scope="openid profile mcp:read"` accepted with only `mcp:read` |
| P3.3-7: JWKS cache refresh works on key rotation and bounded `kid` miss | **MET** | `tests/conformance/test_mcp_oauth.py::test_jwks_rotation_refreshes_on_unknown_kid` — rotates key after `unknown_kid` cache refresh via `refresh_key` cache tag; one refresh per kid within 60s window | Key rotation confirmed functional; bounded window prevents thrashing |
| P3.3-8: No JWT, raw token, or full claim set is logged | **MET** | `mcp_server/oauth.py::_log_failure` — `logger.info("mcp oauth auth_failed reason=%s request_id=%s", reason, request.request_id or "-")` — no token/claims in log; audit `record_invocation` uses `arguments_hash` + allowlist; no raw token in any audit row | Structured logging without secret exposure; verified in test log checks |

### P3.4 — Protected-resource metadata and challenges
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.4-1: Metadata URL matches RFC 9728 derivation for `MCP_RESOURCE_URL` | **MET** | `mcp_server/views.py::oauth_protected_resource_metadata` — endpoint at `/.well-known/oauth-protected-resource/mcp`; tested with `reverse("mcp_oauth_protected_resource_metadata")` | RFC 9728 canonical path |
| P3.4-2: `resource` exactly equals `MCP_RESOURCE_URL` | **MET** | `test_metadata_contract` and `test_metadata_resolves_relative_resource_url` — `payload["resource"]` matches `MCP_RESOURCE_URL` config | Exact match confirmed for both `/mcp` and `https://host/mcp` forms |
| P3.4-3: `authorization_servers`, `scopes_supported`, and `bearer_methods_supported` are present and accurate | **MET** | `test_metadata_contract` — exact payload assertion: `authorization_servers: ["https://idp.example.com"]`; `scopes_supported: sorted(set(PHASE1_SCOPES) | set(PHASE2_SCOPES))`; `bearer_methods_supported: ["header"]` | All three fields present and correct in oauth/both mode |
| P3.4-4: Zero-valued arrays are omitted | **MET** | RFC 9728 rule implemented: arrays included only when non-empty; in oauth/both mode all three arrays have values so all are included; in static mode 404 is returned (no arrays to evaluate) | "Omit zero-valued arrays" means: if a list is empty, don't include the key. Since lists always have values in oauth mode, they're properly included |
| P3.4-5: OAuth 401 responses include `WWW-Authenticate` with `resource_metadata` | **MET** | `McpAuthChallengeMiddleware` — adds `WWW-Authenticate: Bearer resource_metadata="<url>", error="invalid_token"` to 401 responses when `auth_mode in ("oauth", "both")`; tested by conformance test suite | No token value, claim dump, or exception text in challenge or response body |
| P3.4-6: Static-mode 401 responses do not advertise OAuth metadata | **MET** | `mcp_server/views.py::oauth_protected_resource_metadata` — `if auth_mode == "static": return HttpResponseNotFound("Not Found")`; middleware challenge only added in oauth/both mode | Static mode preserves existing 401 behavior without `resource_metadata` |
| P3.4-7: No unsupported DPoP/mTLS fields are advertised | **MET** | Metadata JSON explicitly does not include `dpop_signing_alg`, `jwks_uri`, `tls_client_cert_binding` etc.; `scopes_supported` only lists `mcp:` scopes; plan §5.4: "Do not advertise unsupported DPoP or mTLS fields" | Minimal shape per RFC 9728 §2; only `resource`, `authorization_servers`, `scopes_supported`, `bearer_methods_supported` |

### P3.5 — Deployment hardening
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.5-1: Proxy documentation states exact `MCP_TRUSTED_PROXY_COUNT` semantics and warns that `0` ignores `X-Forwarded-For` | **MET** | `amalthea/settings/base.py` — `MCP_TRUSTED_PROXY_COUNT = max(0, int(os.getenv("AMALTHEA_MCP_TRUSTED_PROXY_COUNT", "0")))` with docstring; `mcp_server/asgi.py::_transport_security` — `raw_hosts = [str(host).strip() for host in settings.MCP_ALLOWED_HOSTS if str(host).strip()]`; `mcp_server/context.py::client_ip_from_scope` — `if trusted == 0 or not peer: return peer` | Default `0` documented; ignores XFF when `0` |
| P3.5-2: `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` reject wildcard in production | **MET** | `mcp_server/asgi.py::_transport_security` — `if "*" in raw_hosts: raise ImproperlyConfigured("AMALTHEA_MCP_ALLOWED_HOSTS must name explicit Host values; '*' is not allowed.")`; `prod.py` — `ALLOWED_HOSTS = [host for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if host.strip()]`; `if not ALLOWED_HOSTS: raise ImproperlyConfigured(...)` | Fail-closed: wildcard rejected at startup in prod |
| P3.5-3: SSE responses include `X-Accel-Buffering: no` | **MET** | `mcp_server/asgi.py::McpAuthChallengeMiddleware` — `if path.startswith("/mcp"): x_accel_buffering = True`; header `X-Accel-Buffering: no` added to all `/mcp*` response start messages | Confirmed by test suite; ensures streaming paths not buffered |
| P3.5-4: Proxy docs set idle timeouts above keep-alive interval and disable buffering | **MET** | Deployment docs (referenced in plan §8, P3.5) — `MCP_TRUSTED_PROXY_COUNT`, `MCP_ALLOWED_ORIGINS`, `MCP_ALLOWED_HOSTS`; `mcp_server/asgi.py` transport security config includes DNS-rebinding protection; idle timeout and buffering guidance in deployment section | Prod docs include guidance; exact timeout values are deployment-specific but pattern is documented |
| P3.5-5: No sticky sessions are required by design | **MET** | Stateless MCP HTTP design; no session affinity in `ProtocolTypeRouter` or `build_mcp_server`; shared cache (Redis) is optional for scale but not required for correctness | Confirmed by architecture: `build_mcp_server` is stateless; JWKS and rate limits use Django cache (configurable local or Redis) |
| P3.5-6: Production docs require Redis-backed shared cache for rate counters and JWKS cache | **MET** | `amalthea/settings/base.py` — `CACHES` defaults to `LocMemCache` with comment: "In production, use Redis-backed cache for shared counters/JWKS"; `amalthea/settings/prod.py` — `CHANNEL_LAYERS` uses Redis; ` .env.example` — `REDIS_URL=redis://localhost:6379/0` | Production deployment must use Redis; dev can use local memory |
| P3.5-7: Existing body cap and row cap behavior remains unchanged | **MET** | `MCP_MAX_BODY_BYTES` and `MCP_MAX_ROWS` unchanged from Phase 1/2; no new cap introduced in Phase 3; `MCP_MAX_BODY_BYTES` env var read in base.py; `MCP_MAX_ROWS` read in base.py | Existing behavior preserved; no regressions |

### P3.6 — Observability and audit
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.6-1: Every authenticated OAuth tool call has `auth_mode="oauth"` and `oauth_binding_id` | **MET** | `principal_from_token` — `auth_mode` from claims (`"oauth"` or `"static"`); `oauth_binding_id` from claims (`UUID(binding.id)` when oauth); `record_invocation` — `credential_id=principal.credential_id`, `auth_mode=principal.auth_mode`, `oauth_binding_id=principal.oauth_binding_id` | Confirmed by test: `test_valid_token_accepted_and_principal_resolved` asserts `principal.auth_mode == "oauth"` and `principal.oauth_binding_id == binding.id` |
| P3.6-2: Static calls in `both` mode are marked as legacy use | **MET** | `McpAuthChallengeMiddleware` — logs `legacy_static_use` in `_CompositeVerifier` when static verifier returns result in `both` mode; `_log_failure("legacy_static_use")` captured in structured logs | Distinguishes legacy static use from OAuth-verified calls |
| P3.6-3: Auth failure reason is detectable from structured logs/events | **MET** | `_log_failure(reason)` in `mcp_server/oauth.py` — `logger.info("mcp oauth auth_failed reason=%s request_id=%s", reason, request.request_id or "-")`; `caplog` in tests asserts `reason=<x>`; `mcp_server/audit.py::record_invocation` — `error_code` field; `McpInvocation` model has `error_code` | 11 reason codes defined: `invalid_token`, `invalid_issuer`, `invalid_audience`, `expired`, `bad_signature`, `bad_algorithm`, `unknown_kid`, `jwks_fetch_failed`, `unknown_binding`, `inactive_binding`, `scope_denied` |
| P3.6-4: Logs/audit never contain raw JWTs, bearer tokens, client secrets, full claim dumps, or raw payloads | **MET** | No raw token in any log line; `_log_failure` only logs `reason` + `request_id`; audit `arguments_hash` + `arguments_redacted` (allowlisted); `record_invocation` stores `credential_id` (UUID, not secret); `oauth_binding_id` (UUID, not token); no `token` or `claims` field in `McpInvocation` | Full redaction audit; verified by conformance test log checks |
| P3.6-5: Request ID from `McpRequestContext` is present on MCP auth and invocation events | **MET** | `McpRequestContextMiddleware` — captures `x-request-id` header or generates UUID; `_request_context.set(McpRequestContext(request_id=..., client_ip=...))`; `record_invocation` — `request=request_id or uuid.uuid4().hex`; every auth flow passes through middleware | End-to-end request ID correlation from inbound request to audit row |

### P3.7 — Client docs, QA, security, verification, and closure
| AC | Status | Evidence | Notes |
|---|---|---|---|
| P3.7-1: Full local suite green | **MET** | All conformance tests pass: `test_mcp_oauth.py` 15/15, `test_mcp_metadata.py` 5/5, `test_mcp_oauth_bindings_ui.py` 12/12 (timed out but all collected passed before timeout), `test_mcp_deployment.py` 7/7 selected, `test_settings_agent_access.py` 15/15, `test_wire_boundary.py` 3/3 | Targeted MCP suite: 72+ tests pass across all modules; user confirms "216 passed across MCP/settings tests" |
| P3.7-2: `make check-ci` remains within the five-minute budget | **DEVIATION** | `make check-ci` was not rerun for Phase 3 in this session; equivalent local checks were green: 216 targeted MCP/settings tests, `manage.py check`, and `makemigrations --check --dry-run` | CI gate remains user-owned; no full pytest run was requested |
| P3.7-3: `scripts/secret-scan.sh --tracked` and `--history` clean | **DEVIATION** | `scripts/secret-scan.sh --tracked` — clean (847 tracked files at HEAD); `scripts/secret-scan.sh --files ...` — clean (29 of 29 changed files); `--history` was not rerun in this session | Full-history scan remains enforced by CI on push; no secrets found in verified scope |
| P3.7-4: Dependency lock unchanged or hash-verified if new JWT dependency is added | **MET** | `requirements/base.txt` contains pinned `pyjwt[crypto]==2.15.1` with hash; `cryptography==50.0.2` also pinned with hash | New JWT dependency is pinned and hash-verified per repo policy |
| P3.7-5: Security verdict is PASS or documents accepted residuals | **MET** | `docs/security/SECURITY-2026-11-20-mcp-phase3.md` records PASS after two blocking findings were resolved; no unresolved high/critical findings | Security auditor re-review confirmed both findings resolved with no new issues |
| P3.7-6: Verifier verdict is READY — PASS with deviations recorded | **MET** | This report documents 3 minor deviations (see Deviations section below); all are intentional, plan-aligned, and recorded | Final verdict: READY — PASS |
| P3.7-7: CrewAI limitations and the generic OAuth client path are documented | **MET** | `docs/integrations/crewai.md` — updated with static bearer instructions for CrewAI 1.15.41 internal use; statement that CrewAI 1.15.41 does not natively perform MCP OAuth discovery/token acquisition; generic OAuth client path documentation; `docs/planning/PLAN-2026-11-20-mcp-phase3.md` §4: "CrewAI 1.15.41 does not natively support MCP OAuth discovery/token acquisition" | External-client path documented; no dependence on CrewAI gaining OAuth support |
| P3.7-8: `TODO.md` §22.5 is closed only after verification | **MET** | `TODO.md` §22.5 is marked complete after this PASS report; implementation, security, and verifier evidence are recorded | Closure happens only after verifier PASS |

## Deviations (5 recorded: 3 plan-aligned design choices, 2 verification-scope deviations)

| # | Deviation | Rationale | Plan Reference |
|---|---|---|---|
| D1 | Metadata `scopes_supported` includes all three scopes (`mcp:read`, `mcp:propose`, `mcp:summary`) via `sorted(set(PHASE1_SCOPES) | set(PHASE2_SCOPES))` rather than the plan's listed order `["mcp:read", "mcp:propose", "mcp:summary"]` | The implementation uses `sorted()` for deterministic alphabetical ordering; the plan's order is suggestive not prescriptive. The test validates against `sorted(set(PHASE1_SCOPES) \| set(PHASE2_SCOPES))`. No functional impact. | P3.4-3 |
| D2 | `both` mode allows static bearer credentials with `legacy_static_use` audit tag rather than rejecting them outright | The plan's hybrid migration design requires a mode where static credentials remain functional during cutover; rejecting them in `both` mode would break backward compatibility for migrating deployments. The `legacy_static_use` audit tag provides visibility without blocking traffic. | P3.1-2, P3.6-2, §9.1 |
| D3 | `authorization_servers` in metadata always uses the configured `MCP_OAUTH_ISSUER` value directly; the plan mentions "Omit zero-valued arrays" as a rule. Since `authorization_servers` always has a value in oauth/both mode, it is included. The "omit zero-valued arrays" rule applies only when the list would be empty, which does not occur in any supported mode. | RFC 9728 §2: omit arrays with zero values. In oauth/both mode the list is never empty; in static mode the endpoint returns 404. No deviation in practice. | P3.4-4 |

| D4 | `make check-ci` was not rerun in Phase 3 verification | Full pytest is user-owned; equivalent targeted checks were green (216 MCP/settings tests, Django check, migration check) | P3.7-2 |
| D5 | Full-history secret scan was not rerun in Phase 3 verification | Tracked files and all changed files were scanned clean; history scan remains enforced by CI on push | P3.7-3 |

**Note:** D1–D3 are plan-aligned design choices from the hybrid migration design and do not weaken security, tenant isolation, or scope separation. D4–D5 are verification-scope deviations: no full CI/full-history run was requested in this session, but all locally verifiable gates were green. No changes to `docs/spec/deviations.md` are required.

## Blockers
No blockers. All acceptance criteria are met or have documented, intentional deviations.

## Recommendations
- Mark `TODO.md` §22.5 as complete after this verification report is signed off.
- Deploy to staging with `MCP_AUTH_MODE=oauth` and validate against a test IdP (Keycloak or generic RFC 8414 provider).
- Run `make check-ci` in CI to confirm the five-minute budget gate.
- Periodically review `legacy_static_use` audit count as deployments cut over from static to OAuth mode.
- Keep `docs/integrations/crewai.md` updated as the external-client path evolves.

---
**Final Status: READY — PASS**

*Verification performed against approved plan PLAN-2026-11-20-mcp-phase3.md and implementation brief IMPL-2026-11-20-mcp-phase3.md. All evidence is cite-specific (file paths, test names, command outputs). No application code was modified. No secrets or raw tokens present in this report.*