# Security Review: MCP Phase 3 — OAuth 2.1 Resource Server

**Date:** 2026-11-20
**Plan:** [`PLAN-2026-11-20-mcp-phase3.md`](../planning/PLAN-2026-11-20-mcp-phase3.md)
**Verdict:** PASS (after remediation of two blocking findings)

## Scope

Reviewed the uncommitted MCP Phase 3 implementation covering OAuth resource-server validation, auth-mode composition, binding lifecycle, Settings UI, audit/observability, deployment hardening, and production configuration.

## Blocking findings and remediation

| Finding | Severity | Remediation | Status |
|---|---|---|---|
| `both` mode did not emit the required `legacy_static_use` signal when a static bearer authenticated | Critical for Phase 3 AC | `_CompositeVerifier.verify_token()` now logs `mcp oauth legacy_static_use client_id=<id>` after successful static verification; covered by `test_both_mode_logs_legacy_static_use` | Resolved |
| JWKS cache did not refresh on unknown `kid`, causing denial of service during key rotation | High | Per-kid bounded refresh (`cache.add` 60s guard) forces one JWKS re-fetch on unknown `kid`; covered by `test_jwks_rotation_refreshes_on_unknown_kid` | Resolved |

## Confirmed controls

- JWT access tokens are accepted only with `typ=at+jwt|application/at+jwt`, an allowlisted asymmetric algorithm, `kid`, exact issuer, exact resource audience, unexpired `exp`, and bounded `iat`.
- JWKS fetching is HTTPS-only, rejects redirects, and keeps TLS verification enabled; DNS results are screened against private/loopback/link-local/reserved ranges.
- `McpOAuthBinding` is resolved only from exact `(issuer, client_id, subject)` plus `is_active`; organisation and service user come from the binding row, never from token claims.
- Reserved (`mcp:write`, `mcp:admin`) and unknown `mcp:*` scopes are rejected; non-MCP scopes are ignored; final scopes are token ∩ binding ∩ global allowlist.
- `static` mode does not publish RFC 9728 metadata or OAuth 401 challenges; `oauth` mode uses only the OAuth verifier; `both` mode is migration-only and audits legacy static use.
- OAuth 401 challenges include `resource_metadata` and `error="invalid_token"` without token values, claims, or exception text. `/mcp*` responses set `X-Accel-Buffering: no`.
- UI binding create/list/disable routes are admin/superAdmin-only and tenant-scoped, use `<dialog>` modals, and expose no bearer/credential material fields.
- `McpInvocation` records `auth_mode` and `oauth_binding_id` for OAuth calls; rate limiting uses a stable OAuth identity; audit arguments remain allowlisted and hashed.
- Production settings fail closed when OAuth mode is configured without HTTPS issuer/JWKS values, with symmetric/none algorithms, or excessive clock skew.
- Repository secret scans were clean for tracked files and all changed files at handoff.

## Residual risk

No unresolved high or critical findings. Staging validation against a real external IdP remains an operational deployment step, not a code blocker.
