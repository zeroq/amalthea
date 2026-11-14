# ADR-012 — MCP Server Boundary

**Date:** 2026-11-13
**Decision:** Accepted — Phase 0 dependency/ASGI spike verified; Phase 1 implementation pending.
The user approved the recommended defaults (Q1–Q10).

## 1. Context

Amalthea requires a remote Model Context Protocol (MCP) server to authorised AI agents for reading case context, triage support, and bounded case mutations. The MCP server must integrate with the existing Django + DRF + Channels + Daphne ASGI deployment, sharing the same process, database, and authentication infrastructure.

Key constraints from the plan and related ADRs:
- No server-side LLM, no raw payloads, no Phase 2 writes
- Org-scoped credentials, never trust org id from the model
- Same role/viewer/tenant rules as REST API and UI
- Static bearer bootstrap (no OAuth in Phase 1); OAuth deferred to Phase 3
- MCP at `/mcp/`, no `/api/v1` prefix
- Read-only Phase 1; proposal-first writes in Phase 2
- No superAdmin cross-org access through MCP in v1

## 2. Decision

### 1.1 ASGI Adapter Boundary

- **MCP is an ASGI-mounted adapter**, not a separate microservice in v1.
- The MCP Starlette app is mounted at `/mcp/` in the project's top-level `amalthea/asgi.py`, before the Django catch-all.
- `ProtocolTypeRouter` keeps the `websocket` branch on the existing Channels router.
- The top-level ASGI lifespan enters `mcp.session_manager.run()` — a mounted sub-application's lifespan does **not** run, so the composition must be handled at the top level.
- `stateless_http=True` is the recommended starting point because the 2026-07-28 protocol core is stateless and production may run multiple workers.

**Deployment shape:**
```
                          TLS / reverse proxy
                                  |
                          Daphne / ASGI
                                  |
                  +---------------+----------------+
                  |                                |
        /mcp/*  (Starlette)                 everything else (Django)
                  |                                |
        MCP protocol adapter             REST API + server-rendered UI
                  |                        + Channels WebSocket routing
                  |
        tools / resources / prompts
```

### 1.2 Authentication Profile

- **Static bearer bootstrap** in Phase 1: every request carries `Authorization: Bearer <mcp-token>`.
- Credential lookup: `TokenVerifier` looks up `McpCredential` by `prefix`, verifies Argon2, rejects revoked/expired, and returns an access token whose `client_id` identifies the credential and whose scopes are the credential's scopes intersected with the user's role ceiling.
- **OAuth 2.1 is deferred to Phase 3.** Phase 1 uses a dedicated `McpCredential` model with a 12-char unique `prefix` for O(1) lookup.
- `mcp:read` — read tools/resources/prompts.
- `mcp:propose` — create proposals (Phase 2 only).
- `mcp:summary` — auto-post an AI-generated case summary (Phase 2 only).
- `mcp:write` and `mcp:admin` are reserved; not granted to an agent in v1.
- Role ceiling:
  - `viewer` — `mcp:read` only.
  - `analyst` — `mcp:read`, `mcp:propose`; `mcp:summary` when enabled for the org.
  - `admin`/`superAdmin` — same as analyst for MCP, **bounded by the credential's organisation**.
- A credential with no user, no organisation, or an org-less user is rejected.
- No tool accepts `organisation`, `owner_org`, or equivalent as an argument. The organisation comes from the credential.

### 1.3 Org Scoping

- Every `McpCredential` is bound to one `Organisation` (FK `PROTECT`) and one `User` (FK `PROTECT`).
- The credential's organisation is the **authoritative** organisation for every request. The model never supplies `org` as an argument.
- **SuperAdmin may mint an org-scoped credential for another organisation**, but the credential is still org-scoped: the agent sees only that organisation's data.
- Cross-org reads are denied: unknown, cross-org, and unauthorised all produce the same MCP "not found" error, matching the REST/UI convention.
- `identity.scoping` (`case_visible_q`, `org_q`, `org_filtered`, `resolve_*`) is reused for tenant resolution; the MCP auth layer does not duplicate tenant filters.

### 1.4 No Server-Side LLM

- The model runs in the MCP client; Amalthea stores no model keys and makes no LLM calls.
- Prompts are server-authored templates that ask the client model to reason. They do not execute a model.
- Prompt construction rules separate `SYSTEM-LIKE INSTRUCTIONS` from `UNTRUSTED CASE DATA` with explicit delimiters.

### 1.5 No Raw Payloads

- `raw_payload` remains available only through the existing authenticated alert-raw endpoint, not as MCP content.
- Read tools explicitly exclude `raw_payload`, attachments, API keys, ingestion token secrets, share internals, user password hashes, playbook `config`, and audit-log internals.

### 1.6 Dependency Pinning

- `mcp` Python SDK v2.3.0 + `mcp-types` v2.3.0 are added to the hashed runtime closure (`requirements/base.txt`).
- All transitive dependencies are pinned with all-platform hashes via `pip-compile --generate-hashes`.
- The lock command is: `scripts/lock.sh` (regenerates deterministically; run twice produces identical output).
- Verified compatible on Python 3.14.6 with Django 5.2.17, DRF 3.18.1, Channels 4.3.2, Daphne 4.2.3, Starlette 1.10.0, Pydantic 2.14.3. The reviewed closure additions are recorded in `scripts/lock.sh` `BASE_ALLOW`. Focused Phase 0 tests: `tests/conformance/test_mcp_phase0.py`.

### 1.7 Protocol Version

- The SDK advertises `2026-07-28` as its latest protocol revision; the Phase 0 verification client
  negotiated `2025-11-25`.
- Clients negotiate via the SDK. The server rejects unsupported versions with a protocol error, not a 500.
- Streamable HTTP `POST` works through the mounted ASGI app at both `/mcp` and `/mcp/`; no GET stream
  assumptions.

### 1.8 Phase 0 implementation status

- **Verified:** hashed dependency closure, `MCPServer` import, ASGI mount, MCP lifespan composition,
  Streamable HTTP `initialize`/`tools/list`/`tools/call`, both MCP paths, and the existing Django
  `/healthz` path.
- **Not implemented in Phase 0:** `McpCredential`, `McpInvocation`, bearer verification, rate limits,
  read tools, resources, prompts, and the Settings → Agent access UI. Those remain Phase 1.
- **CrewAI client:** current CrewAI releases require Python `>=3.10,<3.14`; the Amalthea runtime is
  Python 3.14.6. The example targets CrewAI 1.15.41 and must be exercised in a separate Python 3.13
  environment. The Phase 0 CrewAI smoke has **not** been executed in the Amalthea venv.

## 3. Records

| # | Decision | Outcome |
|---|---|---|
| 1 | Transport/hosting | Same Daphne/ASGI app at `/mcp/`; no separate sidecar in v1. |
| 2 | v1 authentication | Dedicated org-scoped `McpCredential` static bearer; OAuth 2.1 in Phase 3. |
| 3 | Agent identity | Dedicated service user per agent; credential records the human owner. |
| 4 | Allowed write actions | Phase 2 proposal list in §7.7; delete/merge/share/user/token/playbook-authoring excluded. |
| 5 | Approval UX | Amalthea `/approvals` inbox is authoritative; MCP client confirmation is advisory. |
| 6 | Case summaries | Auto-post directly as labelled `ai-summary` timeline entries (no approval). |
| 7 | Raw payloads/attachments | Never exposed to MCP in v1. |
| 8 | SuperAdmin cross-org | May mint org-scoped credentials, but MCP cannot read across orgs. |
| 9 | Autonomy | Shadow mode only; no auto-execution until evaluation evidence exists. |
| 10 | First MCP client | CrewAI via `MCPServerHTTP`, Streamable HTTP, and a bearer header. |

## 4. Dependency Pinning

The following packages were added to `requirements/base.txt` with all-platform hashes via `pip-compile --generate-hashes`:

- `mcp==2.3.0`
- `mcp-types==2.3.0`
- Transitive deps: `sse-starlette==3.7.0`, `starlette==1.10.0`, `typing-inspection==0.4.5`, `opentelemetry-api==1.45.2`, `python-multipart==0.0.33`, `referencing==0.37.0`, `rpds-py==2026.9.1`, `pydantic==2.14.3`, `pydantic-core==2.50.4`

**Lock command:** `scripts/lock.sh` (deterministic; run twice produces identical output).

## 5. No-Commit Guarantee

- No secrets, tokens, or API keys are committed.
- Configuration lives in environment variables; local `.env` is gitignored.
- `.env.example` holds placeholders only.
- Secret scan is enforced on every commit and push.

---
*Recorded 2026-11-13. The user accepted the recommended defaults except where noted.*