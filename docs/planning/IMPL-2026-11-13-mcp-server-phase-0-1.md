# Implementation Brief — MCP server, Phase 0 + Phase 1

**Plan:** [`PLAN-2026-11-13-mcp-server.md`](PLAN-2026-11-13-mcp-server.md)
**Status:** PHASE 0 PARTIAL / PHASE 1 READY — dependency and ASGI/protocol checks pass; CrewAI
smoke is pending in an isolated Python 3.13 environment.
**Handoff to:** `django-backend` (Phase 0/1), `django-frontend` (Settings → Agent access), `qa-tester`, then `security-auditor` / `verifier`.
**Scope:** Phase 0 dependency/protocol/CrewAI spike + Phase 1 read-only gateway. **Do not implement Phase 2** proposals, summary auto-posting, approvals inbox, OAuth, or autonomy.

**Phase 0 status (2026-11-14):** hashed `mcp==2.3.0` closure, ASGI mount, lifespan, and
`initialize`/`tools/list`/`tools/call` are verified by
`tests/conformance/test_mcp_phase0.py`. CrewAI's smoke is pending because current CrewAI requires
Python `<3.14` while Amalthea runs Python 3.14.6; run it in an isolated Python 3.13 environment.
Phase 1 has not started.

## 0. Locked decisions

- Endpoint: `/mcp` and `/mcp/`, Streamable HTTP, same Daphne/ASGI process.
- Auth: static bearer via dedicated org-scoped `McpCredential`; no OAuth in Phase 1.
- Phase 1 grants only `mcp:read`. The model may store future scopes, but Phase 2 tools do not exist.
- Effective actor: dedicated service user per agent; credential records `created_by`.
- First client: **CrewAI** using `MCPServerHTTP` + bearer header.
- No server-side LLM, no raw payloads/attachments, no writes, no Phase 2 tools.
- Agent credentials UI is **Settings → Agent access** (name accepted by user).
- Keep `/api/v1/`, UI, WebSocket, and TheHive compatibility unchanged.

---

## 1. Phase 0 — dependency, protocol, and CrewAI spike

### 0.1 Dependency spike

**Files:** `requirements/base.txt`, `docs/decisions/ADR-012-mcp-server-boundary.md`

1. Add the official `mcp` Python SDK v2 pin and all new transitive dependencies to the hashed runtime closure. Use the repository's `pip-compile`/`scripts/lock.sh` mechanism; do not leave an unhashed install. Record the exact version and command in ADR-012.
2. Verify the new closure against Django 5, DRF, Channels, Daphne, and existing pins. Stop and report if `mcp` forces incompatible Starlette/Pydantic versions.
3. Create `ADR-012-mcp-server-boundary.md`: ASGI adapter boundary, auth profile, org scoping, no server-side LLM, no raw payloads, Phase 1 read-only, OAuth deferred.

**AC:** clean `--require-hashes` install; import succeeds; no dependency conflict; ADR records exact versions and the lock command.

### 0.2 ASGI / protocol spike

**Files:** temporary branch or `mcp_server/asgi.py`, `amalthea/asgi.py`

1. Build the smallest `MCPServer` with one echo/read tool and `streamable_http_app()`.
2. Mount it before Django; confirm the top-level ASGI lifespan enters `mcp.session_manager.run()`.
3. Test `initialize`, `server/discover` (if supported by the pinned SDK), `tools/list`, and `tools/call` with the official client.
4. Confirm `/mcp` and `/mcp/` both work without a redirect that loses POST bodies.
5. Record whether `stateless_http=True` and `json_response=True` are compatible with CrewAI.

**AC:** protocol calls succeed through the mounted app; `/api/v1/` and `/ws/case/<id>/` still work; clean shutdown; findings recorded in the plan/ADR.

### 0.3 CrewAI spike

**Files:** `docs/integrations/crewai.md`, `examples/mcp/crewai_agent.py`

Use the current CrewAI `MCPServerHTTP` config with `url`, `headers={"Authorization": ...}`, `streamable=True`, and `cache_tools_list=True`. Read the token from `AMALTHEA_MCP_TOKEN`. Pin the CrewAI version used by the example.

Current CrewAI 1.15.x requires Python `>=3.10,<3.14`; Amalthea runs Python 3.14.6. Run this
spike in an isolated Python 3.13 venv and keep CrewAI out of the runtime requirements. Use the
current `from crewai.mcp import MCPServerHTTP` import and `mcps=[...]` agent config; do not use the
older `crewai_tools` adapter API.

**AC:** a minimal CrewAI crew lists tools and calls one read tool over Streamable HTTP; no committed token; tested CrewAI/MCP versions documented; any streamable-HTTP caveat recorded.

---

## 2. Backend — Phase 1

### 1.1 App skeleton and settings

**Files:** `mcp_server/__init__.py`, `mcp_server/apps.py`, `amalthea/settings/base.py`, `.env.example`

- Add `mcp_server` to `INSTALLED_APPS` after `query`.
- Add env-backed settings, defaults safe:
  - `MCP_ENABLED` (`AMALTHEA_MCP_ENABLED`) default `False`
  - `MCP_BASE_URL` (`AMALTHEA_MCP_BASE_URL`)
  - `MCP_MAX_ROWS` default `50`
  - `MCP_TIMEOUT_SECONDS` default `15`
  - `MCP_RATE_PER_MINUTE` default `60`
  - `MCP_MAX_BODY_BYTES` default `4 * 1024 * 1024`
  - `MCP_ALLOWED_ORIGINS` (`AMALTHEA_MCP_ALLOWED_ORIGINS`, comma-separated)
  - `MCP_ALLOWED_HOSTS` (`AMALTHEA_MCP_ALLOWED_HOSTS`, default `DJANGO_ALLOWED_HOSTS`)
- Add placeholders to `.env.example`; no secrets.

**AC:** `manage.py check`; MCP disabled returns a clean 404; settings documented; no production defaults weaken auth.

### 1.2 Models and migration

**Files:** `mcp_server/models.py`, `mcp_server/migrations/0001_initial.py`, optional `mcp_server/admin.py`

`McpCredential`:

- `id`, `organisation` FK `PROTECT`, `user` FK `PROTECT`, `name`, unique `prefix`, `secret_hash`, `scopes` JSON, `expires_at`, `revoked_at`, `last_used_at`, `created_by` FK `SET_NULL`, timestamps.
- Validate scope values; Phase 1 only `mcp:read`.
- Bind credential organisation and user organisation at creation; superAdmin may select another org, but the credential remains org-scoped.

`McpInvocation`:

- `id`, `request_id`, `credential` FK `SET_NULL`, `actor` FK `SET_NULL`, `organisation` FK `SET_NULL`, `tool_name`, `arguments_hash`, `arguments_redacted` JSON, optional target type/id, `status`, `error_code`, `duration_ms`, `created_at`.
- Indexes `(organisation, -created_at)`, `(actor, -created_at)`, `(tool_name, -created_at)`.

**AC:** migration applies on SQLite and Postgres-compatible schema; constraints/indexes pass `test_indexes`; no raw secrets or payload columns.

### 1.3 Credential service and auth

**Files:** `mcp_server/credentials.py`, `mcp_server/auth.py`

- Mint: cryptographically random token, 12-char prefix lookup, Argon2 hash, plaintext returned once; retry prefix collision.
- Verify: active, unexpired, unrevoked; update `last_used_at`; return principal.
- Role ceiling: viewer can receive only `mcp:read`; no Phase 1 credential can receive write/propose/summary scopes.
- Org: resolve from credential, never from tool arguments. SuperAdmin credentials are limited to their bound org.
- Refuse org-less user, mismatched user/org, or revoked/expired credential.

**AC:** token shown once; only hash stored; valid/revoked/expired/unknown/org-less all fail closed; no cross-org principal.

### 1.4 ASGI mount and lifecycle

**Files:** `mcp_server/asgi.py`, `amalthea/asgi.py`

- Build the Starlette/MCP app with bearer verification, Host/Origin validation, body cap, timeout.
- Mount before the Django HTTP app; keep the WebSocket branch.
- Compose the MCP lifespan at the top level.
- Feature gate: when disabled, route to a plain 404; do not import/start MCP state.

**AC:** protocol calls work at `/mcp` and `/mcp/`; Django REST/UI/WebSocket unaffected; disabled mode is clean; startup/shutdown has no orphaned task errors.

### 1.5 Principal, scoping, and limits

**Files:** `mcp_server/auth.py`, `mcp_server/scopes.py`, `mcp_server/limits.py`, `mcp_server/audit.py`

- Principal object: credential, effective user, organisation, role, scopes.
- Reuse `identity.scoping` (`case_visible_q`, `org_q`, `org_filtered`, `resolve_*`); do not duplicate tenant filters.
- Re-check viewer role on every tool; Phase 1 has no writes.
- Rate limit per credential and IP using the existing cache pattern in `ingest/views.py::_throttled`.
- Enforce row, byte, and timeout caps centrally.
- Record one `McpInvocation` per authenticated tool call. Build redacted arguments by allowlist. Do not write DB rows for unauthenticated failures; use security logging.

**AC:** every call audited once; oversized/looping calls capped; 429 on excess; no raw payload/secrets in audit; cross-org is uniform not-found.

### 1.6 Read tools

**Files:** `mcp_server/tools/{search,alerts,cases,observables,tasks,playbooks}.py`, `mcp_server/schemas.py`

Implement only these tools; every schema rejects unknown fields:

| Tool | Source / rule |
|---|---|
| `search` | `query.search.parse_query` + `search_sections`; max 50; no HTML |
| `list_alerts` | org-scoped alert queryset; filters status/severity/source/assignee/flag/since |
| `get_alert` | `identity.scoping.resolve_alert`; use `core.serializers.alert_json`; no `raw_payload` |
| `get_alert_triage_context` | `alert_json` + `cases.correlation.candidate_cases_for_alert`, capped |
| `list_cases` | `case_visible_q`; status/severity/assignee/phase/verdict filters |
| `get_case` | `resolve_case`; `core.serializers.case_json`; no raw payloads |
| `get_case_digest` | case + bounded alerts/observables/tasks/recent events/pending runs |
| `get_case_timeline` | `cases.ledger.events_after`; cap `limit`; return `nextCursor` |
| `list_observables` | org-scoped observable links; `observable_json`; safe enrichment only |
| `get_observable` | `resolve_observable`; visible linked-case count only |
| `list_tasks` | `resolve_task`/case-scoped task query; status/assignee filters |
| `get_playbook_meta` | name, description, trigger event, active; **never** `config` |
| `list_playbooks` | same safe fields; org-scoped; no scripts/URLs/headers |

Output convention: structured `data` + `meta` (`total`, `truncated`, `nextCursor` if applicable) and a short text summary. All strings bounded. No server-side Markdown rendering of untrusted text.

**AC:** all tools are org-scoped; unknown/cross-org ids are uniform not-found; no raw payload/config/secrets; row caps honored; `get_case_digest` has a bounded query count; every tool has a stable input/output schema.

### 1.7 Resources and prompts

**Files:** `mcp_server/resources.py`, `mcp_server/prompts.py`

- Resource templates: `amalthea://case/{id}`, `amalthea://case/{id}/timeline`, `amalthea://alert/{id}`, `amalthea://observable/{id}`.
- Prompts: `triage_alert`, `summarize_case`, `suggest_case_actions`.
- Separate system-like instructions from untrusted case data with explicit delimiters.
- Bounded events/rows; state truncation; no raw payloads, secrets, or attachments.
- Prompts do not execute actions; Phase 1 has no action tools.

**AC:** resources use the same scoping as tools; prompts contain no raw payload; prompt snapshots assert untrusted-data delimiters.

### 1.8 Tests

**Files:** `tests/conformance/test_mcp_credentials.py`, `test_mcp_asgi.py`, `test_mcp_tools.py`, `test_mcp_prompts.py`, `test_mcp_settings_ui.py`, optional `test_mcp_crewai.py`

Required cases:

- Credential mint/verify/revoke/expire; prefix collision; only hash stored; plaintext once.
- ASGI `/mcp` and `/mcp/`; `/api/v1/` and WebSocket regression; disabled flag.
- Cross-org, unknown, org-less, viewer, analyst, admin, superAdmin; no org argument accepted.
- Every read tool: happy path, cross-org, unknown, limit cap, no raw payload.
- Audit redaction and rate limiting.
- Prompt-injection strings in alert/case text cannot alter tool list or prompt structure.
- CrewAI smoke over Streamable HTTP with bearer header.
- Settings UI: admin own org; superAdmin org select; analyst/viewer 404; show-once secret; revoke; audit.

Use the official MCP client for protocol tests and one ASGI HTTP test. Do not add a test dependency unless Phase 0 proves it is already in the closure.

**AC:** focused suite passes; `make check-fast`; migrations check; no secret-scan findings.

---

## 3. Frontend — Settings → Agent access

**Files:** `ui/views/agent_access.py`, `ui/views/__init__.py`, `ui/urls.py`, `ui/templates/ui/settings_agent_access.html`, `_settings_agent_access_form.html`, `_settings_agent_access_created.html`, `_settings_agent_access_revoke.html`, `ui/templates/ui/_settings_nav.html`, `ui/static/ui/app.src.css` if new classes.

Routes:

- `GET /settings/agent-access` → `ui-settings-agent-access`
- `POST /settings/agent-access/create` → `ui-settings-agent-access-create`
- `POST /settings/agent-access/<uuid:credential_id>/revoke` → `ui-settings-agent-access-revoke`

Rules:

- Admin/superAdmin only; analyst/viewer uniform 404 via `_require_admin_ui`/`not_viewer`.
- Admin sees own org; superAdmin may select the org.
- Effective service user must belong to the selected org; show only `mcp:read` in Phase 1.
- Name, user, optional expiry; generate only server-side.
- Show the plaintext token once in a modal/rendered response, with `Cache-Control: no-store`; never put it in a URL, log, or audit row.
- List name, service user, org (superAdmin), status, expiry, last used; revoke with `<dialog>` and confirmation.
- Add an Agent access tab to `_settings_nav.html`; keep existing Sources/Users/Organisations behavior.
- Audit credential creation/revocation through `AuditLog`; no token material.

**AC:** admin/superAdmin can mint/revoke; other roles 404; secret shown once; no secret in URL/log/audit; keyboard accessible; no native `confirm()`; UI smoke tests pass.

---

## 4. Out of scope (do not build)

- Phase 2 `McpActionProposal`, `/approvals`, proposal tools.
- `post_case_summary` / AI summary auto-posting.
- OAuth 2.1, OAuth clients, protected-resource metadata.
- Autonomy policy, kill switch, `McpOrgPolicy`.
- stdio transport, sidecar, server-side LLM, raw payloads, attachments, writes.
- Any change to `/api/v1/` or TheHive compatibility.

---

## 5. Verification commands

```bash
.venv/bin/pytest -q tests/conformance/test_mcp_credentials.py tests/conformance/test_mcp_asgi.py tests/conformance/test_mcp_tools.py tests/conformance/test_mcp_prompts.py tests/conformance/test_mcp_settings_ui.py
make check-fast
.venv/bin/python manage.py makemigrations --check --dry-run
scripts/secret-scan.sh --staged
```

Run the full suite before merge if time permits; the repo's CI gap (§12.11) does not remove the local obligation.

## 6. Reporting back

- List changed files and the exact `mcp`/CrewAI versions.
- Report any deviation from this brief or the plan; update the plan's decision log/deviations.
- Include focused test output and the CrewAI smoke result.
- If Phase 0 exposes a dependency/API incompatibility, stop before building Phase 1 and report the options.
