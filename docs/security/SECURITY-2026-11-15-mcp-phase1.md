# Security Review — Amalthea MCP Phase 1
**Date:** 2026-11-15
**Reviewer:** Security Auditor
**Scope:** Read-only Phase 1 MCP gateway (static bearer, org-scoped credentials, read tools, resources, prompts).
**Reference artifacts:** `IMPL-2026-11-13-mcp-server-phase-0-1.md`, `PLAN-2026-11-13-mcp-server.md`, `ADR-012-mcp-server-boundary.md`, conformance test suites, `make check`.

---
## 1. Scope & Method
- **Reviewed files:** All new/changed backend files per the brief (models.py, credentials.py, auth.py, scopes.py, limits.py, audit.py, schemas.py, tools/*, resources.py, prompts.py, asgi.py, apps.py, context.py); UI files (agents.py, urls.py, templates/ui/{settings_agents.html,_settings_agent_create_form.html,_settings_agent_created.html}); settings base/prod; identity/audit.
- **Method:** Static code inspection, test result review, configuration/environment validation. No file modifications. No full pytest run (per instructions).
- **Baseline test results:** 166 focused conformance tests:
  - `test_mcp_phase0` (2), `test_mcp_asgi` (3), `test_mcp_credentials` (8), `test_mcp_tools` (12),
  - `test_mcp_prompts` (2), `test_mcp_limits` (9), `test_settings_agent_access` (15),
  - `test_ui_urls_smoke` (6), `test_ui_table_markup` (3), `test_wire_boundary` (3), `test_tenant_scoping` (103).
- `make check` passes (ruff, format, mypy, Django check, migrations, CSS drift, playbook scripts).
- Full-suite final rerun: **3773 passed, 3 skipped, 0 failed, 0 errors, 2 subtests passed**
  (`/tmp/amalthea-full-pytest.log`, 891.73s). The earlier 35 setup errors are gone after the MCP
  transaction tests were marked `serialized_rollback=True`; the 3 skips are expected Postgres-only
  planner/race tests on SQLite.

---
## 2. Concrete Concern Evaluation

### 1. Unauthenticated request cost: prefix lookup + Argon2
- **Concern:** Random bearer tokens rarely reach Argon2 because prefix lookup (12-char O(1) filter) misses; an attacker who knows a valid 12-char prefix can trigger Argon2 verification. Is there any pre-auth IP limit? Accepted or classified?
- **Classification:** **Medium (accepted design)** — The 12-char prefix provides 48 bits of entropy; random guessing is infeasible (1 in 2^48). The lookup (`McpCredential.lookup(prefix)`) is a simple database filter; if the prefix is unknown, Argon2 is never reached. There is no separate pre-auth IP limit; the accepted residual cost is one Argon2 verification per request that knows a valid prefix. Edge-proxy rate limiting is recommended for untrusted internet exposure.
- **Evidence:** `mcp_server/models.py:186-187` (`lookup`), `mcp_server/credentials.py:40-41` (`verify_secret` constant-time Argon2), `mcp_server/limits.py:34-58` (`is_rate_limited`).

### 2. Rate limiter placement: inside authenticated calls, not at HTTP boundary
- **Concern:** Rate limiter runs inside authenticated tool/resource/prompt calls (`is_rate_limited` in `run_read_data`), not at the HTTP boundary. Unauthenticated protocol requests are not rate-limited. Plan deviation/gap?
- **Classification:** **Accepted by design** — The MCP protocol authenticates first via `McpTokenVerifier` (401 if invalid/revoked/expired). Rate limiting per-credential and per-IP is an additional gate for authenticated callers. Unauthenticated requests fail at the verifier stage.
- **Evidence:** `mcp_server/tools/base.py:63-74` (`run_read_data` rate check after `require_principal`), `mcp_server/auth.py:44-74` (`McpTokenVerifier.verify_token`).

### 3. Rate limiter uses direct ASGI peer IP, not X-Forwarded-For
- **Concern:** Rate limiter uses `scope["client"][0]` (direct ASGI peer IP). Behind a reverse proxy, all clients may share one per-IP bucket, undermining per-credential/IP rate limits.
- **Classification:** **Fixed — code now includes proxy awareness** (`mcp_server/context.py:30-56`). `MCP_TRUSTED_PROXY_COUNT` defaults to `0`: the direct ASGI peer is used and any `X-Forwarded-For` header is ignored. When set to the exact number of proxies in front of Daphne, the right-most untrusted `XFF` entry is used, validated with `ipaddress.ip_address`, and falls back to the peer on short/invalid chains. Tests in `test_mcp_limits.py` cover default ignore, one proxy, two-proxy leftmost spoofing, short/invalid chain, and no peer. Residual: deployment must set `AMALTHEA_MCP_TRUSTED_PROXY_COUNT` to the correct hop count in production behind a proxy.
- **Evidence:** `mcp_server/context.py:30-56` (`client_ip_from_scope`), `amalthea/settings/base.py` (`MCP_TRUSTED_PROXY_COUNT` env var); tests `test_client_ip_ignores_forwarded_header_by_default`, `test_client_ip_uses_rightmost_entry_for_one_trusted_proxy`, `test_client_ip_resists_leftmost_spoofing_with_two_proxies`, and `test_client_ip_falls_back_to_peer_on_short_or_invalid_chain`.

### 4. Production wildcard handling: AMALTHEA_MCP_ALLOWED_HOSTS fallback
- **Concern:** `AMALTHEA_MCP_ALLOWED_HOSTS` falls back to `DJANGO_ALLOWED_HOSTS`; if that is "*", `_transport_security` raises `ImproperlyConfigured` at app build. Confirm fail-closed and whether `prod.py` should reject earlier.
- **Classification:** **Verified — fail-closed and correct** — `prod.py:32-40` explicitly guards: if `MCP_ENABLED` then `MCP_BASE_URL` must use `https://`, `MCP_ALLOWED_HOSTS` must be non-empty. The `_transport_security` function in `asgi.py:44-47` raises `ImproperlyConfigured` if `"*"` is found in `MCP_ALLOWED_HOSTS`. Production must set `AMALTHEA_MCP_ALLOWED_HOSTS` explicitly; there is no silent wildcard path.
- **Evidence:** `amalthea/settings/prod.py:32-40`, `amalthea/settings/base.py:264-268`, `amalthea/mcp_server/asgi.py:44-47`.

### 5. MCP_AUTH metadata route at /.well-known/oauth-protected-resource
- **Concern:** The `/.well-known/oauth-protected-resource` route is not routed by `build_application`; plan defers protected-resource metadata to Phase 3. Confirm this is intentional and not a Phase 1 defect.
- **Classification:** **Intentional — not a Phase 1 defect** — The plan (Q10, Phase 3) explicitly defers OAuth 2.1 resource-server metadata to Phase 3. The `build_application` function constructs the MCP/HTTP router; `/.well-known/` paths are outside the `/mcp/` prefix and are not part of the Phase 1 surface. No 404 or misrouting occurs because the MCP gate (`path == "/mcp" or path.startswith("/mcp/")`) simply never matches `/.well-known/`, so it falls through to the Django ASGI app as normal. This is by design.
- **Evidence:** `mcp_server/asgi.py:190-203` (`http_app` routing), `PLAN-2026-11-13-mcp-server.md §3.3`, `ADR-012 §1.7`.

### 6. UI displays the 12-char credential prefix; token remains 43 chars/256-bit random
- **Concern:** UI shows the 12-char prefix in the credential table; token is 43 chars (32-byte random). Confirm whether prefix exposure is a finding.
- **Classification:** **Low — minor aesthetic/privacy concern, not a security flaw** — The prefix reveals 12 chars of the 32-byte (256-bit random) token, reducing entropy from 256 bits to ~244 bits of unknown. In practice, this does not enable token recovery. The full plaintext is only shown once in the create modal with `Cache-Control: no-store` and `Pragma: no-cache`. The prefix column in the UI table is a convenience, not a secret. No finding required beyond noting the display.
- **Evidence:** `ui/templates/ui/settings_agents.html:49` (`{{ credential.prefix }}`), `ui/templates/ui/_settings_agent_created.html:17` (`<code id="agent-credential-plaintext">{{ credential_plaintext }}</code>`), response headers `Cache-Control: no-store`, `Pragma: no-cache` (agents.py:154-155).

### 7. Tool error handling: no traceback/raw exception reaches clients
- **Classification:** **Verified — sound** — The `run_read_data` wrapper (`mcp_server/tools/base.py:109-121`) catches all exceptions and maps them to stable MCP tool error codes (`not_found`, `forbidden`, `invalid_arguments`, `conflict`, `rate_limited`, `timeout`, `internal_error`). The `ToolError` is the structured error returned to the client; no Python tracebacks or raw exception strings escape the server. The conformance tests verify safe error codes.
- **Evidence:** `mcp_server/tools/base.py:109-121`, `tests/conformance/test_mcp_tools.py` error-handling test patterns.

### 8. Prompt/resource audit coverage: resources/prompts call run_read_data
- **Classification:** **Verified — sound** — Both `_case/_alert/_observable` resource handlers and all three prompts (`triage_alert`, `summarize_case`, `suggest_case_actions`) go through `run_read_data`, which records exactly one `McpInvocation` row via `record_invocation`. Arguments are redacted through `audit_arguments` (allowlist + truncation). No raw payloads, playbook configs, or secrets appear in audit rows.
- **Evidence:** `mcp_server/audit.py:28-55` (`record_invocation`), `mcp_server/tools/base.py:124-133` (`run_read` → `run_read_data`), `mcp_server/limits.py:61-90` (`audit_arguments`), conformance test `test_run_read_rate_limit_audits_once` and `test_run_read_timeout_audits_and_returns_safe_error` (both assert single row + correct `error_code`).

### 9. UI show-once: Cache-Control/Pragma, no token in URL/session/log/audit
- **Classification:** **Verified — sound** — The create view (`settings_agent_create`) sets `response["Cache-Control"] = "no-store"` and `response["Pragma"] = "no-cache"` (agents.py:154-155). The created-modal template (`_settings_agent_created.html`) displays the plaintext only inside a static `<dialog>` with `show_cancel=False`, `modal_static=True`. The plaintext code element has `data-clipboard-target` for copy but is never placed in a URL, session, log, or audit row. The audit helper `record_mcp_credential_change` (`identity/audit.py:457-480`) records only credential id and org, never plaintext.
- **Evidence:** `ui/views/agents.py:154-155`, `ui/templates/ui/_settings_agent_created.html`, `identity/audit.py:457-480`, conformance test `test_created_modal_is_autoopened_static_and_unique` and `test_get_page_never_contains_a_plaintext_or_show_once_modal`.

### 10. McpCredential.verify updates last_used_at; race condition
- **Classification:** **Fixed — atomic queryset update** (`mcp_server/credentials.py:43-47`). The `verify()` function now uses `McpCredential.objects.filter(pk=credential.pk).update(last_used_at=now)` instead of a non-atomic `credential.last_used_at = now; credential.save(...)`. This is an atomic database-level update, eliminating the race condition where concurrent verifications could overwrite each other's timestamp. The auth path had already used an atomic `_touch` pattern in `mcp_server/auth.py:90-91`.

---
## 3. Verified Controls (sound — no findings)
- **Auth/token lifecycle:** Argon2 hash storage + constant-time verification (`mcp_server/models.py:38-52`), prefix O(1) lookup (`models.py:186-187`), revoked/expired/inactive/org-mismatch all fail closed (`credentials.py:31-45`, `auth.py:76-87`), token audience/resource set to base URL with user_id subject (`auth.py:60-66`). `last_used_at` updated atomically via queryset update (`credentials.py:43-47`).
- **Tenant isolation:** Every tool/resource/prompt derives org from credential; cross-org/unknown UUIDs uniform not-found (`scopes.py:53-78`, all tool registers); no org argument accepted (`tools/base.py:_target()`, all registers); superAdmin break-glass impossible — credential role capped to analyst (`scopes.py:21-35`, `actor_from_principal`); case timeline cursor foreign-case rejection (`cases.py:155-159`).
- **Injection/DoS:** Strict tool schemas with `extra = "forbid"` (`asgi.py:74-84`); row caps via `row_limit()` (50 default) on all list tools; body cap via `max_request_body_size` (4 MiB default) (`asgi.py:154`); timeout via `asyncio.timeout()` (15s default) (`base.py:81`, `tools/base.py:81`); rate limiting per-credential and per-IP fixed window (`limits.py:34-58`); audit redaction via `audit_arguments` allowlist + SHA-256 hash (`limits.py:61-90`, `audit.py:49`); safe serializers exclude raw payloads, playbook config, automation logs, secrets (`serializers.py`); prompt injection delimiters (`ALERT_CONTEXT_JSON:/CASE_DIGEST_JSON:`) and untrusted data instructions (`prompts.py`); `last_used_at` updated atomically (`credentials.py:43-47`).
- **ASGI/transport:** Fail-closed disabled gate (`MCP_ENABLED=False → /mcp*` returns 404) (`asgi.py:184-196`, `base.py:246`); Host/Origin validation with no wildcard allowed in prod (`asgi.py:41-71`); body size, timeout, and rate limits prevent DoS (`limits.py`); WebSocket branch unaffected (separate router) (`asgi.py:211-215`); proxy-aware IP extraction via `client_ip_from_scope()` / `MCP_TRUSTED_PROXY_COUNT` (`context.py:30-56`).
- **UI:** Admin/superAdmin gates via `_require_admin_ui`/`not_viewer` (`ui/views/agents.py:67-71`, `common.py`); org scoping in list/create/revoke (`agents.py:27-33`); CSRF on POST views (Django middleware); HTML escaping in all templates (Django default); show-once plaintext with `Cache-Control: no-store` / `Pragma: no-cache`; audit rows record credential id/org, never plaintext; revoke idempotency (second revoke is no-op, no duplicate audit row); viewer lockout via `@not_viewer`.
- **Settings/prod:** env parsing with fail-closed defaults (`base.py:246-268`); `prod.py` requires `DJANGO_SECRET_KEY >= 50 chars`, `DJANGO_ALLOWED_HOSTS` non-empty, `MCP_ENABLED` guardrails (HTTPS BASE_URL, non-empty ALLOWED_HOSTS) (`prod.py:16-40`); no wildcard hosts in production path; `MCP_TRUSTED_PROXY_COUNT` for proxy-aware rate limiting.

---
## 4. Test / Evidence Gaps & Recommendation
- **Gap 1:** Closed — `last_used_at` race is fixed with atomic queryset update in `mcp_server/credentials.py:43-47`. No action required.
- **Gap 2:** Closed — Rate limiter proxy awareness is implemented via `MCP_TRUSTED_PROXY_COUNT` and `client_ip_from_scope()` in `mcp_server/context.py:30-56`. Default `0` ignores XFF; production must configure the count for proxy deployments. No action required beyond deployment config.
- **Gap 3:** Ops-only — No unbounded-queryset guard beyond Django ORM limits in `case_visible_q` / `org_observables`. If Share/Observable tables grow very large, performance could degrade. This is an ops/tuning concern, not a Phase 1 security finding.
- **Gap 4:** Closed — final full suite is 3773 passed, 3 skipped, 0 failed, 0 errors. The earlier
  transaction-test ordering issue is fixed (`serialized_rollback=True`); no security finding.

**Recommendation: PASS** — The implementation meets all Phase 1 acceptance criteria. The security review is complete with no blocking findings.

**Project gates:** Closed — final full suite is 3773 passed, 3 skipped, 0 failed, 0 errors, and the
CrewAI Python 3.13 smoke is closed. No remaining security or project gate.

**File:** `docs/security/SECURITY-2026-11-15-mcp-phase1.md`

---

## 5. Post-review addendum (2026-11-16)

**Scope:** focused re-review of the new `McpLifecycleApp` in `mcp_server/asgi.py` and the
no-lifespan conformance test. This change was made after the original PASS because a live Daphne
4.2.3 smoke proved Daphne does not implement ASGI lifespan; the previous lifespan-only composition
left the MCP session manager unstarted and `/mcp` answered 500 (`Task group is not initialized`).

**Result: PASS — no new findings.**

- Authentication is configured before the wrapper runs; lazy startup cannot bypass the bearer
  verifier or scope checks.
- The double-checked `asyncio.Lock` pattern starts exactly one runner task under concurrent first
  requests; `_started` is set on both success and failure so waiters cannot hang.
- Startup failures fail closed with a generic `RuntimeError`; tracebacks stay in server logs, not
  HTTP responses. Recovery requires a process restart.
- Lifespan-capable servers still get normal startup/shutdown; Daphne's no-lifespan path is covered
  by `tests/conformance/test_mcp_phase0.py::test_mcp_round_trip_without_asgi_lifespan`.
- CrewAI 1.15.41 smoke (isolated Python 3.13.18) passed against live Daphne: 13 tools discovered,
  `list_cases(limit=5)` returned `is_error=false`. The smoke credential was revoked.

**Project gates:** closed — final full suite is 3773 passed, 3 skipped, 0 failed, 0 errors; CrewAI
is closed. No security findings.
