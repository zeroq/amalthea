# PLAN 2026-11-13 — MCP server for AI agents

**Status:** PHASE 0 PARTIAL — dependency and ASGI/protocol spike verified; CrewAI smoke pending
(Python version constraint); Phase 1 implementation not started.

**Author:** planner

**Phase 0/1 brief:** [`IMPL-2026-11-13-mcp-server-phase-0-1.md`](IMPL-2026-11-13-mcp-server-phase-0-1.md)

**Related:** `AGENTS.md` §2 (Modules A–D) and §4 (MVP) · `docs/spec/api.md` ·
`docs/spec/identity-auth.md` · `docs/spec/automation.md` · `docs/spec/realtime.md` ·
`ADR-004` (tenant isolation) · `ADR-006` (role semantics) · `ADR-008` (token-only ingestion) ·
`ADR-011` (playbook scripts) · MCP specification 2026-07-28 · OWASP MCP Security Cheat Sheet

---

## 1. Summary

Amalthea should expose a **remote Model Context Protocol (MCP) server** so an authorised AI agent
can read case context, triage alerts, summarise cases, suggest next actions, and — with human
approval, or through the narrowly scoped AI-summary write path — perform a bounded set of case
mutations.

The MCP server is a **thin, audited adapter over Amalthea's existing domain services**, not a second
application. It runs in the same ASGI/Daphne deployment as Django and Channels, is mounted at
`/mcp/`, and identifies every caller as an Amalthea user + organisation through a dedicated agent
credential. The AI model itself runs in the MCP client; Amalthea stores no model keys and makes no
LLM calls.

The rollout is deliberately conservative:

1. **Read-only** context and triage support.
2. **Proposal-first writes** — the agent proposes; a human approves in Amalthea; the existing
   services execute. The sole direct-write exception is an **auto-posted, labelled AI case summary**
   (append-only timeline content, never a case-state change).
3. **OAuth 2.1 / external-client hardening** before exposing the endpoint beyond a trusted network.
4. **Policy-bounded autonomy** only after shadow-mode evaluation; case closure remains human-approved.

This plan does **not** authorise autonomous case closure, destructive actions, raw-payload access, or
cross-tenant superAdmin access. Auto-posted summaries are explicitly labelled as AI-generated and do
not change status, severity, assignment, tasks, observables, or approvals.

---

## 2. Goals

1. Add a first-class MCP endpoint at `/mcp/` using Streamable HTTP and the official MCP Python SDK
   (v2, protocol 2026-07-28 or the newest pinned compatible revision).
2. Authenticate each request with a dedicated `McpCredential` bound to one user **and** one
   organisation; never trust an organisation id supplied by the model.
3. Enforce the same role, viewer, tenant, and share rules as the REST API and UI through
   `identity.scoping` and shared domain services.
4. Expose bounded read tools and resources for alerts, cases, timelines, observables, tasks, and
   playbook metadata.
5. Expose MCP prompts for triage, case summarisation, and next-action suggestions; the agent does the
   reasoning, Amalthea provides structured data and instructions.
6. Make every tool call auditable: actor, credential, organisation, tool, target, redacted arguments,
   outcome, duration, and request id.
7. Make state-changing writes **proposal-first**: exact arguments are persisted, shown to a human,
   approved or declined, and executed once through the existing service layer.
8. Auto-post an AI-generated case summary directly to the case timeline, append-only and clearly
   labelled, with source-event provenance, sanitisation, idempotency, and rate limits. This is the
   only direct write in v1.
9. Add an opt-in autonomy policy that can auto-approve only a narrow allowlist of low-risk actions,
   with a per-org kill switch.
10. Keep the TheHive-compatible API, UI, and WebSocket contracts unchanged.
11. Ship a tested threat model and CrewAI setup documentation/example for the first supported remote
   MCP client.

## 3. Non-goals

- **No server-side LLM**: no model provider keys, no prompt execution, no embeddings, no vector store.
- **No raw alert payloads by default**: `raw_payload` remains available only through the existing
  authenticated alert-raw endpoint, not as MCP content.
- **No attachments or binary downloads** in the first MCP surface.
- **No superAdmin cross-org access** through MCP in v1. A superAdmin may mint an org-scoped agent
  credential, but the agent sees only that organisation.
- **No destructive operations**: delete, merge cases, share/revoke, user/org management, token
  management, playbook authoring, and bulk operations are not MCP tools.
- **No autonomous case closure**. A closure proposal always requires a human.
- **No general direct mutation tools in Phase 2**. State-changing writes are proposals; the only
  direct write is `post_case_summary`, which appends a labelled AI summary and cannot change case
  status, severity, assignment, tasks, observables, or approvals. Phase 4 may auto-approve a narrow
  allowlist through the same proposal executor.
- **No stdio transport in v1**. The product is multi-user and remote; a local stdio bridge, if ever
  needed, is a separate security review.
- **No change to the TheHive-compatible `/api/v1/` contract.**
- **No OAuth authorization server built from scratch** in Phase 1; Phase 3 decides between a small
  OAuth 2.1 resource-server integration and a delegated identity provider.

---

## 4. Current state

| Area | Today | Gap for MCP |
|---|---|---|
| Runtime | Django 5 + DRF + Channels ASGI; Daphne in prod | No MCP endpoint or SDK dependency |
| ASGI | `ProtocolTypeRouter({"http": django, "websocket": ...})` | Need an HTTP router that mounts MCP before Django |
| Auth | Hashed bearer `ApiKey`; roles `superAdmin/admin/analyst/viewer` | No agent-specific credential, org binding, expiry, or MCP scope |
| Tenancy | `identity/scoping.py`; uniform 404; no RLS | MCP tools must reuse it, never accept `org` arguments |
| Audit | `AuditLog` for cross-org/admin/token actions | No per-tool invocation record or approval record |
| Domain services | Case create/merge/escalation, ledger, observables, playbooks | Some view logic must move to shared services before MCP writes |
| Realtime | Channels case rooms, ledger publisher | Proposal approvals should publish live updates |
| API keys UI | No first-class API-key management page found | Need a Settings → Agent access page or equivalent |
| OAuth/CORS | No OAuth server, no CORS layer | Static bearer bootstrap only; OAuth is Phase 3 |
| Dependencies | Hash-pinned `requirements/base.txt`; no `mcp`, Starlette, or Pydantic | Dependency spike, pinning, and supply-chain review required |

---

## 5. Architecture

### 5.1 Deployment shape

```text
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
                 |
       shared Amalthea domain services
                 |
       PostgreSQL + Redis/Celery + Channels
```

- One process family, one settings module, one database. MCP is not a separate microservice in v1.
- The MCP endpoint is `/mcp/` (no `/api/v1` prefix). It is mounted in `amalthea/asgi.py` before the
  Django catch-all.
- `ProtocolTypeRouter` keeps `websocket` on the existing Channels router.
- The top-level ASGI lifespan must enter `mcp.session_manager.run()`; a mounted sub-application's
  lifespan does not run.
- `stateless_http=True` is the recommended starting point because the 2026-07-28 protocol core is
  stateless and production may run multiple workers. Approval state lives in PostgreSQL, not in a
  connection.
- Reverse proxies must disable response buffering for SSE-capable Streamable HTTP responses; if SSE
  is not required by the chosen client, `json_response=True` can be evaluated in the spike.

### 5.2 Code layout

```text
mcp_server/
  __init__.py
  apps.py
  asgi.py                 # MCPServer construction + Starlette mount
  auth.py                 # credential verification, McpPrincipal, scope guards
  scopes.py               # mcp:read / mcp:propose / mcp:summary / mcp:write / mcp:admin
  audit.py                # invocation + proposal audit helpers
  limits.py               # row, byte, timeout, and rate caps
  schemas.py              # typed input/output models for tools
  tools/
    __init__.py
    search.py
    alerts.py
    cases.py
    observables.py
    tasks.py
    playbooks.py
    proposals.py
  resources.py
  prompts.py
  services/
    __init__.py
    actions.py            # proposal creation/approval/execution
  models.py               # McpCredential, McpInvocation, McpActionProposal, McpOrgPolicy
  admin.py
  urls.py                 # human-facing approval/key pages, if not in ui/
```

- Tool bodies are thin. They parse/validate arguments, resolve the principal, call a shared service,
  serialise a bounded result, and record an invocation.
- **Do not call Amalthea's own HTTP API over loopback.** That duplicates authentication, hides
  failures, and creates a recursive surface. Reuse services.
- Where a view currently owns business logic (triage, comment, observable add, task create, case
  update), extract a service function first; the view and the MCP tool then call the same function.
- UI additions (agent credentials, approvals) follow the existing Django-template + HTMX + `<dialog>`
  patterns, not a separate frontend.

### 5.3 Proposed ADR

Add `docs/decisions/ADR-012-mcp-server-boundary.md` covering:

- MCP as an ASGI-mounted adapter, not a separate service.
- Static bearer bootstrap vs OAuth 2.1; who may mint credentials.
- Org-scoped credentials and the explicit decision to deny superAdmin cross-org MCP reads.
- Proposal-first writes and the human approval boundary.
- No server-side LLM and no raw-payload exposure.
- Dependency pinning and MCP protocol version support.

---

## 6. Data model (proposed)

All new models live in `mcp_server` unless noted. Migrations are additive. UUID primary keys and
`TimeStampedModel` follow repo conventions.

### 6.1 `McpCredential` (Phase 1)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `organisation` | FK `Organisation`, `PROTECT` | **Mandatory**. Bounds every request. |
| `user` | FK `User`, `PROTECT` | Effective actor; role still applies. |
| `name` | CharField(100) | Human label, e.g. "Claude Desktop — SOC". |
| `prefix` | CharField(12), unique | O(1) lookup; mirrors `ApiKey`. |
| `secret_hash` | CharField(255) | Argon2; plaintext shown once. |
| `scopes` | JSONField(list[str]) | `mcp:read`, `mcp:propose`, `mcp:summary`; `mcp:write`/`mcp:admin` reserved. |
| `expires_at` | DateTimeField(null=True) | Optional but recommended; expiry enforced. |
| `revoked_at` | DateTimeField(null=True) | Terminal revocation. |
| `last_used_at` | DateTimeField(null=True) | Updated on successful auth, not every tool. |
| `created_by` | FK `User`, `SET_NULL` | Admin/superAdmin who minted it. |
| `created_at` / `updated_at` | timestamps | Standard. |

Constraints: scope values validated in `clean()`/service; `prefix` unique; `organisation` and `user`
must agree with the credential's tenant intent at creation time (a superAdmin may create a credential
for another org, but the credential is still org-scoped).

Why a separate model instead of widening `ApiKey`:

- Agent credentials have a different lifecycle (org-bound, expiring, scoped for MCP).
- A superAdmin's interactive key must not accidentally become a cross-org agent key.
- Revoking all agent access should not touch ordinary API keys.

### 6.2 `McpInvocation` (Phase 1)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `request_id` | CharField(100), indexed | MCP request id / generated trace id |
| `credential` | FK `McpCredential`, `SET_NULL` | Survives credential deletion |
| `actor` | FK `User`, `SET_NULL` | Effective user |
| `organisation` | FK `Organisation`, `SET_NULL` | Tenant at call time |
| `tool_name` | CharField(100), indexed | e.g. `get_case_digest` |
| `arguments_hash` | CharField(64) | SHA-256 of canonical args |
| `arguments_redacted` | JSONField | No secrets, no raw payload |
| `target_type` / `target_id` | CharFields | Optional resolved target |
| `status` | CharField(20) | `ok`, `denied`, `not_found`, `invalid`, `error`, `rate_limited` |
| `error_code` | CharField(100) | Stable machine token |
| `duration_ms` | PositiveIntegerField | Performance/abuse signal |
| `created_at` | DateTimeField, indexed | Retention job later |

Indexes: `(organisation, -created_at)`, `(actor, -created_at)`, `(tool_name, -created_at)`.
Retention: default 90 days; configurable; no raw payloads or secrets.

### 6.3 AI summaries — existing `TimelineEvent` (Phase 2)

Auto-posted summaries do **not** need a new model. They use the existing case `TimelineEvent`:

- `kind = "ai-summary"`.
- `title = "AI-generated case summary"` (server-controlled; the model cannot set the title).
- `description` = sanitised Markdown, max 20,000 characters; no raw HTML, images, or attachments.
- `actor` = the credential's service user, so the existing ledger attribution still applies.
- `metadata = {"generatedBy": "mcp", "credentialId": "...", "requestId": "...",
  "sourceEventIds": [...], "contentHash": "..."}`.
- `sourceEventIds` must be a non-empty list of timeline event ids that belong to the same case; the
  server validates them and stores them as provenance. The agent cannot cite another case's events.
- Append-only: no update/delete path for `ai-summary` events. Duplicate content for the same case
  returns the existing event (idempotent) rather than posting again.
- Clearly rendered as AI-generated content in the UI; never presented as an analyst note or evidence
  of a state change.

### 6.4 `McpActionProposal` (Phase 2)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `organisation` | FK `Organisation`, `PROTECT` | Must match target |
| `requested_by` | FK `User`, `SET_NULL` | The agent's effective user |
| `credential` | FK `McpCredential`, `SET_NULL` | Provenance |
| `tool_name` | CharField(100) | Proposal tool, e.g. `propose_case_update` |
| `action` | CharField(100) | Canonical service action |
| `target_type` / `target_id` | CharFields | Case/alert/observable/task |
| `arguments` | JSONField | Exact validated arguments |
| `arguments_hash` | CharField(64) | Binds approval to exact arguments |
| `risk` | CharField(20) | `low`, `medium`, `high` |
| `status` | CharField(20) | `pending`, `approved`, `declined`, `expired`, `executed`, `failed` |
| `idempotency_key` | CharField(100), unique | Proposal + execution dedupe |
| `expires_at` | DateTimeField | Default 24h; stale proposals never execute |
| `decided_by` | FK `User`, `SET_NULL` | Human approver |
| `decided_at` | DateTimeField(null=True) | |
| `executed_at` | DateTimeField(null=True) | |
| `result` | JSONField | Bounded outcome, no secrets |
| `created_at` / `updated_at` | timestamps | |

Rules:

- The requesting user cannot approve their own proposal (four-eyes) when `risk != low`; make this a
  service check and a test.
- Approval transitions are explicit and atomic:
  `pending -> approved|declined|expired`, `approved -> executed|failed|expired`.
- Reusing a proposal, changing arguments after approval, or replaying an execution fails closed.
- A proposal is never a capability. The executor re-resolves the target and re-checks tenant, role,
  viewer, and target state at execution time.

### 6.5 `McpOrgPolicy` (Phase 4)

| Field | Type | Notes |
|---|---|---|
| `organisation` | OneToOne `Organisation`, `PROTECT` | |
| `autonomy_enabled` | Boolean, default `False` | Kill switch default off |
| `shadow_mode` | Boolean, default `True` | Log decisions, execute nothing |
| `allowed_actions` | JSONField(list[str]) | Narrow allowlist |
| `max_auto_severity` | SmallInteger, default 1 | Only low severity |
| `daily_action_cap` | PositiveInteger, default 20 | Per org |
| `require_four_eyes` | Boolean, default `True` | |
| `updated_by` | FK `User`, `SET_NULL` | |
| `updated_at` | DateTimeField | |

No raw model confidence is trusted. Auto-approval is based on deterministic case/alert fields,
allowlisted action names, target state, and rate caps.

---

## 7. API contract

### 7.1 Transport

- Endpoint: `POST /mcp/` (Streamable HTTP). No `/api/v1` prefix.
- Protocol version: pinned to a supported MCP revision; clients negotiate via the SDK. The server
  rejects unsupported versions with a protocol error, not a 500.
- `Origin` and `Host` validation enabled. In production, allow only the configured public origin(s)
  and host(s); never wildcard.
- HTTPS only in production; TLS terminated at the reverse proxy. No API keys in query strings.
- Max request body enforced by the SDK and by the reverse proxy. The default 4 MiB SDK cap is
  acceptable for v1; do not raise it without a use case.
- Rate limit per credential and per IP at the MCP boundary, independent of DRF throttles.

### 7.2 Authentication and scopes

- `Authorization: Bearer <mcp-token>` on every request.
- `TokenVerifier` looks up `McpCredential` by prefix, verifies Argon2, rejects revoked/expired,
  and returns an access token whose `client_id` identifies the credential and whose scopes are the
  credential's scopes intersected with the user's role ceiling.
- Tool scope requirements:
  - `mcp:read` — read tools/resources/prompts.
  - `mcp:propose` — create proposals.
  - `mcp:summary` — auto-post an AI-generated case summary (Phase 2 only).
  - `mcp:write` — reserved for Phase 4 direct writes; not granted in v1.
  - `mcp:admin` — reserved for org policy management; never granted to an agent in v1.
- Role ceiling:
  - `viewer` — `mcp:read` only.
  - `analyst` — `mcp:read`, `mcp:propose`; `mcp:summary` when enabled for the org.
  - `admin`/`superAdmin` — same as analyst for MCP, **bounded by the credential's organisation**.
- `mcp:summary` is deliberately separate from `mcp:write`: auto-posting a labelled summary must not
  become a stepping stone to status, task, observable, or playbook writes.
- A credential with no user, no organisation, or an org-less user is rejected.
- No tool accepts `organisation`, `owner_org`, or equivalent as an argument. The organisation comes
  from the credential.

### 7.3 Read tools (Phase 1)

All read tools are bounded and return a structured payload plus a short text summary. `limit` defaults
to 25 and is capped at 50 unless a lower existing API cap applies.

| Tool | Purpose | Key rules |
|---|---|---|
| `search` | Search cases/alerts/observables | Reuse `query.search`; max 8 terms; clip `q` to 200 chars; no HTML |
| `list_alerts` | Triage queue | Filters: status, severity, source, assignee, flag, since, limit; org-scoped |
| `get_alert` | Alert detail | No `raw_payload`; include ingestion warnings only if non-secret |
| `get_alert_triage_context` | One-call triage bundle | Alert summary + similar cases/alerts (bounded) + observable suggestions |
| `list_cases` | Case queue | Filters: status, severity, assignee, phase, verdict, limit; share-aware |
| `get_case` | Case header/detail | Case JSON, linked alerts, counts; no raw payloads |
| `get_case_digest` | Summarisation bundle | Case + alerts + observables + tasks + last N timeline events + pending runs |
| `get_case_timeline` | Ledger read | Keyset/limit; untrusted text; no cross-case anchor |
| `list_observables` | Case observables | Type/value summary, enrichment metadata only if safe |
| `get_observable` | Observable detail | Include visible linked cases/counts; no hidden tenants |
| `list_tasks` | Case tasks | Filter by case/status/assignee |
| `get_playbook_meta` | Trigger/action vocabulary | Safe metadata only; no config |
| `list_playbooks` | Playbook names/triggers | Name, trigger, active state; **never** URLs, headers, scripts, or config |

Explicitly excluded from read tools:

- `raw_payload`, attachments, API keys, ingestion token secrets, share internals, user password
  hashes, playbook `config`, and audit-log internals.
- Bulk exports, unbounded enumerations, and cross-org search.

### 7.4 Resources (Phase 1)

Resource templates, not a giant static list:

```text
amalthea://case/{case_id}
amalthea://case/{case_id}/timeline
amalthea://alert/{alert_id}
amalthea://observable/{observable_id}
```

- Each returns the same bounded JSON as the matching read tool.
- A resource read does not bypass scope: unknown, cross-org, and unauthorised all produce the same
  MCP "not found" error.
- Resource contents are labelled as untrusted data; the server never injects instructions from case
  text into tool descriptions or prompts.

### 7.5 Prompts (Phase 1)

Prompts are server-authored templates that **ask the client model** to reason. They do not execute a
model.

| Prompt | Arguments | Output intent |
|---|---|---|
| `triage_alert` | alert id, optional similar-case context | Structured recommendation: severity, disposition, escalation, observables, rationale, uncertainties |
| `summarize_case` | case id, max events | Analyst summary: what happened, scope, evidence, current status, open questions, next actions |
| `suggest_case_actions` | case id, optional playbook metadata | Ranked actions with preconditions and risks |

Prompt construction rules:

- Separate `SYSTEM-LIKE INSTRUCTIONS` from `UNTRUSTED CASE DATA` with explicit delimiters.
- Instruct the model to treat all alert/case/observable text as data, never as instructions.
- Do not include raw payloads, secrets, or attachment contents.
- Return a bounded number of events and rows; state truncation explicitly.
- A prompt is not an action. State-changing mutations must go through a proposal tool; the only
  direct write is `post_case_summary` (Phase 2).

### 7.6 `post_case_summary` — auto-posted summary (Phase 2)

This is the single direct-write exception. It appends a labelled, append-only AI summary to the case
timeline. It does not create a proposal and does not require approval, but it also cannot change any
case state.

Arguments:

| Argument | Type | Rules |
|---|---|---|
| `case_id` | UUID/string | Must resolve in the credential's organisation and be readable by the user |
| `summary_markdown` | string | 1–20,000 chars; sanitised Markdown; raw HTML, images, and attachments rejected |
| `source_event_ids` | list[str] | 1–100 timeline event ids, all belonging to `case_id` |

Server behaviour:

- Requires `mcp:summary`; viewer credentials never receive this scope.
- Resolves the case from the credential's organisation; cross-tenant and unknown are uniform
  not-found.
- Validates every `source_event_ids` entry against the same case; reject the call if any id is
  nonexistent, deleted, or belongs to another case.
- Creates `TimelineEvent(kind="ai-summary", title="AI-generated case summary", actor=<service user>,
  description=<sanitised summary>, metadata={...})` (see §6.3).
- Returns the timeline event id, case id, `createdAt`, and a machine-readable
  `label: "ai-generated"` / `requiresHumanVerification: true` marker.
- Rate limit: at most one summary per case per 5 minutes and 20 per credential per day; duplicate
  content for the same case returns the existing event instead of appending a second copy.
- The summary is labelled in the UI and in API output as AI-generated; it is not presented as
  analyst-authored evidence and cannot be used to satisfy an approval or closure requirement.
- The tool cannot set severity, status, owner, tags, tasks, observables, playbooks, or closure.
- Prompt-injection guard: the server does not treat summary text as instructions, does not execute
  links, and renders through the existing Markdown sanitiser.

### 7.7 Proposal tools (Phase 2)

Proposal tools create `McpActionProposal` rows and return an approval handle. They do not mutate the
domain object.

| Tool | Proposed action | Risk |
|---|---|---|
| `propose_alert_triage` | status/severity/assignee/tags/comment on an alert | low–medium |
| `propose_alert_escalation` | import into a new case or merge into an existing case | high |
| `propose_case_update` | status, assignee, severity, phase, verdict, tags | medium |
| `propose_case_comment` | append a Markdown comment/timeline entry | low |
| `propose_observable_add` | add an observable to a case/alert | medium |
| `propose_task_create` | create a task assigned within the org | low |
| `propose_playbook_run` | run an existing playbook against a case/observable | high |
| `propose_case_closure` | close a case with a required verdict | high |
| `get_action_proposal` | read status/result of a proposal | read |

Rules:

- Validate arguments with the same serializer/service rules as REST. Reject unknown fields.
- Re-check tenant, role, viewer, target state, and idempotency **at proposal time and at execution**.
- Never accept a free-form outbound URL, script path, arbitrary header map, or raw payload as an
  argument.
- A proposal returns `proposalId`, `status`, `risk`, `expiresAt`, and a human-facing approval path.
- Phase 2 exposes **no direct state-changing mutation tool**. The only direct write is
  `post_case_summary`; all other writes go through the approval service, not the agent.
- AI summaries are never proposal items. A summary that recommends an action does not execute or
  approve that action.

### 7.8 Errors

- HTTP 401 for missing/invalid/revoked/expired credentials, with `WWW-Authenticate: Bearer`.
- HTTP 403 only for a valid credential lacking a required scope; do not reveal target existence.
- Tool-level failures use MCP tool errors (`isError: true`) with stable codes:
  `not_found`, `forbidden`, `invalid_arguments`, `conflict`, `rate_limited`, `proposal_expired`,
  `internal_error`.
- Never return a traceback, SQL, or a raw exception string to the agent.
- Uniform "not found" for cross-tenant and unknown ids, matching the REST/UI convention.

---

## 8. UI / UX

### 8.1 Settings → Agent access (Phase 1)

- Admin/superAdmin page listing `McpCredential`s for the org (superAdmin may select an org).
- Create: name, effective user, scopes (`read` in Phase 1; `propose`/`summary` in Phase 2), optional
  expiry; show-once secret modal.
- Revoke with confirmation `<dialog>`; no native `confirm()`.
- Show last used and status; never render the hash.
- Audit `mcp_credential.created/revoked` through `AuditLog`.
- Keyboard reachable; icons supplement labels; severity/status always labelled, not colour-only.

### 8.2 Approvals inbox (Phase 2)

- `/approvals` (org-scoped) lists pending proposals with target, action, risk, requester, age, expiry.
- Detail shows the exact arguments and a diff/preview of the intended change.
- Approve/decline require an explicit modal; high-risk actions require an `admin`/`superAdmin`.
- Four-eyes: requester cannot approve a non-low-risk proposal.
- On decision/execution, publish a case event if the proposal targets a case; the ledger remains the
  single write path for case timeline entries.
- Case/alert detail pages show a compact "AI proposals" card linking to the inbox.
- Empty, expired, failed, and already-executed states are explicit.
- No new global keyboard shortcut until the shortcut matrix is reviewed; buttons/forms are keyboard
  operable and the `?` cheat-sheet is updated if a shortcut is added.

### 8.3 AI summaries in the timeline (Phase 2)

- `ai-summary` entries are visually distinct from analyst comments and automation events.
- A persistent label reads **“AI-generated summary — verify against the cited events”**; the label
  remains in the API response and exports, not only in the rendered HTML.
- Show the cited `sourceEventIds` and the credential/agent name; link to the events when visible.
- The summary is read-only in the UI. No “approve summary” action; it is not evidence of triage,
  closure, or containment.
- Empty, duplicate, rate-limited, and invalid-source cases have explicit states.
- Screen-reader text announces the AI label; colour is never the only signal.

### 8.4 Autonomy settings (Phase 4)

- Per-org page, admin/superAdmin only.
- Default: disabled + shadow mode.
- Kill switch, allowlist, severity cap, daily cap, four-eyes toggle.
- Every change audited with old/new policy values; no secrets.
- A prominent banner when autonomy is enabled.

---

## 9. Phases and acceptance criteria

### Phase 0 — dependency + protocol spike (backend)

- **0.1** Add `mcp` (2.x, pinned + hashes) in an isolated branch/venv; confirm Starlette/Pydantic
  dependency resolution against the existing pinned closure; document the exact version and hashes.
  **AC:** `pip install --require-hashes -r requirements/dev.txt` succeeds; `mcp` imports; no
  conflicting Django/Channels versions.
- **0.2** Build a throwaway ASGI mount with `MCPServer` + `streamable_http_app`, compose the lifespan,
  and call `tools/list`/`tools/call` with the official `Client`.
  **AC:** the spike proves the mount path, lifespan, auth-free tool call, and clean shutdown; findings
  are recorded in this plan or ADR-012.
- **0.3** Write ADR-012 and record decisions Q1–Q10 (Q6 auto-post summary, Q10 CrewAI).
  **AC:** ADR merged; any decision that changes this plan is recorded here and in the decision log.
- **0.4** CrewAI compatibility spike: pin a CrewAI version in an isolated environment, connect with
  `MCPServerHTTP` + bearer header over Streamable HTTP, list tools, and call one read tool against
  the throwaway server.
  **AC:** a real CrewAI tool call succeeds; the exact CrewAI/MCP versions and any streamable-HTTP
  caveats are recorded; the spike reads the token from the environment and commits no secret.

### Phase 1 — read-only MCP gateway

- **1.1** Create `mcp_server` app, settings flags/limits, and `amalthea/asgi.py` mount.
  **AC:** `/mcp/` responds to MCP protocol requests while `/api/v1/` and `/ws/case/<id>/` still work;
  lifecycle starts/stops cleanly; no Django URL catch-all shadows `/mcp/`.
- **1.2** Add `McpCredential` model/migration, token creation/verification/revocation service, and
  Settings → Agent access UI.
  **AC:** plaintext shown once; only hash stored; revoked/expired tokens fail; cross-org credential
  creation is admin/superAdmin-only; audit rows written.
- **1.3** Implement `McpPrincipal`, `TokenVerifier`, scope/role intersection, and uniform tenant
  resolution.
  **AC:** viewer gets read only; analyst gets read/propose; superAdmin credential is bounded to its
  org; org-less/revoked/expired/nonexistent tokens are 401; no `org` argument is accepted.
- **1.4** Add invocation audit, per-credential/IP rate limits, row/byte/time caps, and redaction.
  **AC:** every tool call writes one invocation row with redacted args; oversized/looping requests are
  capped; secrets/raw payloads never appear in audit.
- **1.5** Implement Phase 1 read tools, resources, and prompts (§7.3–§7.5).
  **AC:** every tool has a strict schema and bounded output; unknown/cross-org ids are uniform
  not-found; raw payloads and playbook config are absent; prompts clearly delimit untrusted data.
- **1.6** Add the CrewAI integration: `docs/integrations/crewai.md`, a runnable
  `examples/mcp/crewai_agent.py`, and a local development recipe. Use `MCPServerHTTP` (Streamable
  HTTP) with the token read from `AMALTHEA_MCP_TOKEN`; pin the CrewAI version used by the example.
  **AC:** a minimal CrewAI crew connects to `/mcp/` with `Authorization: Bearer`, lists the read
  tools, and successfully calls `search` and `get_case_digest`; the example uses no committed token;
  the doc states the static-bearer limitation and records the tested CrewAI/MCP versions.
- **1.7** Tests, security review, verification.
  **AC:** protocol conformance, tenant, viewer, auth, rate-limit, redaction, and prompt-injection
  tests pass; independent verifier maps every AC; security-auditor signs off or records residuals.

### Phase 2 — proposal-first writes + auto-posted summaries

- **2.1** Extract shared services for alert triage, case comment, case summary append, observable
  add, task create, case update, and case closure where view logic currently owns them.
  **AC:** REST/UI behaviour is unchanged; services are the only write path used by MCP; existing
  tests stay green.
- **2.2** Add `McpActionProposal` model/migration and the state machine with expiry, idempotency, and
  exact-argument hashing.
  **AC:** illegal transitions fail; expired proposals never execute; replaying an execution is
  idempotent; arguments cannot change after approval.
- **2.3** Implement proposal tools and `get_action_proposal`.
  **AC:** proposal tools perform zero domain mutations; invalid/unknown fields are rejected; no direct
  state-changing mutation tool exists (only `post_case_summary`); a proposal is visible in the inbox.
- **2.4** Build the approvals inbox and case/alert proposal cards with approve/decline/execute.
  **AC:** role and four-eyes rules enforced server-side; execution re-checks tenant/scope/state;
  ledger event and WebSocket publish occur once; failed execution is terminal and audited.
- **2.5** Implement `post_case_summary` as the sole direct write: a labelled, append-only
  `ai-summary` timeline entry with required source-event ids, sanitised Markdown, rate limits, and
  idempotency. Add the timeline label and citation display.
  **AC:** an authorised analyst-scoped credential can auto-post a summary without an approval; a
  viewer/read-only credential cannot; source ids from another case are rejected; duplicate content
  returns the existing event; the UI and API label it AI-generated and require human verification;
  the tool cannot change status, severity, owner, tasks, observables, or closure; no raw payload is
  embedded.
- **2.6** Tests, security review, verification.
  **AC:** approve/decline/replay/expiry/cross-tenant/four-eyes matrix and the `post_case_summary`
  direct-write matrix pass; security review finds no new injection or privilege-escalation path.

### Phase 3 — external-client hardening (OAuth)

- **3.1** Decide and implement the OAuth 2.1 resource-server profile: external identity provider or
  a maintained Django OAuth toolkit; validate audience (`resource`), issuer, expiry, and scopes.
- **3.2** Add protected-resource metadata and client setup docs; support client credentials and/or
  authorization-code + PKCE as decided.
- **3.3** Harden multi-worker/deployment: stateless behavior, SSE/proxy settings, origin allowlist,
  rate limits, body caps, and observability.
  **AC:** a standards-compliant remote MCP client authenticates without static headers; token
  audience validation is tested; revoked/expired tokens fail closed; static-bearer mode can be
  disabled.

### Phase 4 — policy-bounded autonomy

- **4.1** Add `McpOrgPolicy` and a deterministic policy engine.
- **4.2** Auto-approve only allowlisted low-risk proposals; default shadow mode; no closure, no
  destructive actions, no playbook runs.
- **4.3** Add autonomy settings UI, kill switch, daily caps, and four-eyes.
- **4.4** Build a historical shadow-mode evaluation harness and record precision/recall, action
  distribution, and false-positive examples.
  **AC:** autonomy is off by default; shadow mode executes nothing; enabling requires an explicit
  admin action with a warning; the kill switch stops execution immediately; evaluation evidence is
  reviewed before any auto-approval is enabled.

---

## 10. Security

### 10.1 Threat model highlights

| Threat | Control |
|---|---|
| Stolen agent token | Short expiry, revocation, org binding, per-key rate limits, audit, no superAdmin cross-org |
| Cross-tenant access | Credential org is authoritative; `identity.scoping`; uniform 404; no org arguments |
| Prompt injection via alert/case text | Treat tool/resource output as untrusted data; never let data change tool descriptions; proposal-first state changes; human approval; summary auto-post limited to a labelled append |
| Misleading or malicious AI summary | Fixed title/label, source-event validation, sanitised Markdown, rate limits/idempotency, no state change, UI/API “verify” warning |
| Tool poisoning / definition drift | Static tool definitions in code; deterministic ordering; document/hash tool catalogue for clients; no dynamic tools from data |
| Approval replay/tampering | Exact argument hash, single-use transition, expiry, execution-time re-resolution, idempotency key |
| Approval fatigue | Narrow action surface; risk levels; four-eyes for high risk; no bulk tools; summaries do not enter the approval queue |
| SSRF / outbound abuse | No URL/header/script arguments; playbooks run only through the existing guarded executor; no raw payload |
| Data leakage in outputs | Allowlist fields; redact secrets; no raw payload/attachments/playbook config; capped rows |
| Audit evasion | One invocation row per call; proposal decisions/executions audited; append-only model |
| DoS / cost abuse | Body cap, row caps, timeout, rate limits, daily caps, concurrency limit |
| Dependency compromise | Pin `mcp` and transitive deps with hashes; review new packages; no auto-update |

### 10.2 Trust boundaries

- Model output is untrusted.
- Tool arguments are untrusted.
- Case/alert/observable text is untrusted.
- Tool descriptions and prompt templates are code-reviewed server assets.
- Approval is a human decision, not a model decision.
- The executor re-checks authorization; approvals are not capabilities.
- An auto-posted AI summary is untrusted, labelled content, not evidence and not an approval.

### 10.3 Secret hygiene

- No MCP token, model key, or raw payload in logs, audit rows, or fixtures.
- MCP token plaintext shown once and never recoverable.
- `McpInvocation.arguments_redacted` must be built by an allowlist, not by deleting known keys from
  arbitrary input.
- Secret scan remains mandatory; new docs use placeholders only.

---

## 11. Testing

### 11.1 Protocol

- `initialize`, `server/discover`, `tools/list`, `tools/call`, `resources/list`,
  `resources/templates/list`, `resources/read`, `prompts/list`, `prompts/get`.
- Unsupported protocol version and malformed JSON-RPC produce protocol errors, not 500s.
- Streamable HTTP `POST` works through the mounted ASGI app; no GET stream assumptions for the
  2026-07-28 revision.
- `Host`/`Origin` rejection and body cap.
- CrewAI `MCPServerHTTP` smoke: list tools and call a read tool against the mounted server with a
  bearer header; pin the tested CrewAI version.

### 11.2 Authorization and tenancy

- Valid/revoked/expired/unknown credential; org-less user; viewer; analyst; admin; superAdmin.
- Same-org, other-org, shared case, and nonexistent target all resolve correctly; cross-org is the
  same not-found.
- No tool accepts `organisation`/`owner_org`; attempts are rejected by strict schemas.
- Read scope cannot create proposals; propose scope cannot post summaries; summary scope cannot
  execute proposals; no scope can execute a proposal directly.

### 11.3 Prompt injection

- Alert/case text containing "ignore previous instructions", fake tool descriptions, and fake
  approval language does not alter tool lists, prompts, or execution.
- A malicious observable value cannot become a tool name or URL.
- Tool output is labelled as data; prompt snapshots assert the delimiters.
- An AI summary that contains instructions, links, HTML, or fake approval language cannot alter
  another tool, execute a proposal, or change case state.

### 11.4 Writes, summaries, and approvals

- Proposal creation does not mutate the target.
- Approve/decline/execute/expire/replay matrix.
- Arguments changed after approval are rejected.
- Four-eyes and role checks.
- Execution re-checks tenant, viewer, and target state; a target moved/deleted after approval fails
  closed.
- Exactly one ledger event and one WebSocket publish per executed action.
- `post_case_summary` posts without approval only with `mcp:summary`; viewer/read-only is rejected;
  foreign/invalid source-event ids are rejected; oversize/oversharing is rejected; duplicate content
  is idempotent; the event is labelled in API and UI; no state field changes.

### 11.5 Regression

- Existing REST/UI/WebSocket suite unchanged.
- `make check`, migrations check, and the full test suite.
- No new N+1 queries for list tools; bounded query count is asserted for `get_case_digest`.

### 11.6 Security and verification

- `security-auditor` reviews auth, tenancy, prompt injection, approval integrity, audit redaction,
  dependency supply chain, and SSRF.
- `verifier` maps every AC in this plan to evidence; deviations are recorded here and in
  `docs/spec/deviations.md`.

---

## 12. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| MCP SDK/spec churn | High | Medium | Pin version; protocol-version spike; keep adapter isolated |
| Static bearer not accepted by some clients | Medium | Medium | Document limitation; Phase 3 OAuth; test chosen client early |
| Dependency conflicts (Starlette/Pydantic) | Medium | High | Phase 0 spike; pin/hash; no upgrade without full suite |
| ASGI mount breaks Channels or lifespan | Medium | High | Compose lifespan; test WebSocket + MCP together; rollback flag |
| Prompt injection from case text | High | High | Read-only first; no raw payload; proposal-first state changes; human approval; data delimiters |
| Auto-posted AI summary is misleading or abusive | Medium | Medium | Fixed label, source-event validation, sanitisation, rate limits/idempotency, no state change, verify warning |
| Approval fatigue / rubber-stamping | Medium | High | Narrow allowlist; risk levels; four-eyes; no bulk actions; summaries stay out of the approval queue |
| Agent token leakage | Medium | High | Expiry, revocation, org binding, rate limits, audit, no superAdmin cross-org |
| Over-broad read exposure | Medium | High | Field allowlists, no raw payload/attachments, redaction, max rows |
| Proposal execution races | Medium | High | Atomic transitions, `select_for_update`, idempotency, execution-time re-check |
| Incomplete service extraction | Medium | Medium | Extract before wiring MCP writes; keep REST/UI tests green |
| Tests become slow/flaky | Medium | Medium | Use official `Client` in-memory for most tests; one HTTP mount test |
| Operating cost/abuse | Low | Medium | Rate limits, caps, daily budgets, kill switch |

---

## 13. Rollout and operations

- Feature flag: `AMALTHEA_MCP_ENABLED` (default `false`). Disabled means the route returns 404/503
  without importing the SDK? Decide in Phase 0; at minimum no tool execution.
- `AMALTHEA_MCP_BASE_URL` for protected-resource metadata and docs.
- `AMALTHEA_MCP_MAX_ROWS`, `AMALTHEA_MCP_TIMEOUT_SECONDS`,
  `AMALTHEA_MCP_RATE_PER_MINUTE`, `AMALTHEA_MCP_MAX_BODY_BYTES`.
- Runbook: mint/revoke credential, rotate token, enable/disable MCP, read invocation audit, disable
  autonomy, respond to a suspected token leak.
- Monitoring: invocation count/errors/latency by tool and org; 401/403/429 rates; proposal backlog;
  execution failures; no payload contents in metrics.
- Backup/retention: `McpInvocation` retention job (default 90 days); proposals retained as audit
  evidence unless policy says otherwise.

---

## 14. Decisions

Recorded 2026-11-13. The user accepted the recommended defaults except where noted.

| # | Decision | Outcome |
|---|---|---|
| 1 | Transport/hosting | Same Daphne/ASGI app at `/mcp/`; no separate sidecar in v1. |
| 2 | v1 authentication | Dedicated org-scoped `McpCredential` static bearer; OAuth 2.1 in Phase 3. |
| 3 | Agent identity | Dedicated service user per agent; credential records the human owner. |
| 4 | Allowed write actions | Phase 2 proposal list in §7.7; delete/merge/share/user/token/playbook-authoring excluded. |
| 5 | Approval UX | Amalthea `/approvals` inbox is authoritative; MCP client confirmation is advisory. |
| 6 | Case summaries | **Auto-post directly** as a labelled, append-only `ai-summary` timeline entry; no proposal/approval. Scope-limited, provenance-bound, rate-limited, and never a state change. |
| 7 | Raw payloads/attachments | Never exposed to MCP in v1. |
| 8 | SuperAdmin cross-org | May mint org-scoped credentials, but MCP cannot read across orgs. |
| 9 | Autonomy | Shadow mode only; no auto-execution until evaluation evidence exists. |
| 10 | First MCP client | **CrewAI** via `MCPServerHTTP` + Streamable HTTP and a bearer header. |

Residual implementation defaults:

- Invocation audit retention: 90 days, configurable.
- `McpCredential` management: Settings → Agent access UI only in v1; no public credential REST API.
- No local stdio bridge for CrewAI or any v1 client.
- AI summaries: existing `TimelineEvent(kind="ai-summary")` with metadata
  `{"generatedBy": "mcp", "credentialId": "...", "requestId": "...", "sourceEventIds": [...],
  "contentHash": "..."}`; no dedicated model.
- Phase 0/1 implementation brief is issued. Phase 0 dependency/ASGI protocol checks are verified;
  the CrewAI smoke runs in Python 3.13 because CrewAI 1.15.x requires `<3.14`. Phase 1 starts from
  the brief after the Phase 0 findings are accepted.

---

## 15. Handoff (after approval)

**django-backend**

- Phase 0 spike, `requirements` pin, ADR-012.
- `mcp_server` app, `McpCredential`, `McpInvocation`, ASGI mount, TokenVerifier, scopes, limits,
  read tools/resources/prompts.
- Service extraction, proposal executor, and the `post_case_summary` direct-write service in Phase 2.
- CrewAI example/docs and Phase 0 compatibility spike.

**django-frontend**

- Settings → Agent access (create/revoke, show-once modal).
- `/approvals` inbox and proposal cards; `<dialog>` confirmations; keyboard/ARIA; no native dialogs.
- `ai-summary` timeline styling, persistent AI label, and source-event citations.
- Autonomy settings and warning banner (Phase 4).

**qa-tester**

- Protocol/auth/tenant/prompt-injection/approval matrix; official `Client` tests; CrewAI
  `MCPServerHTTP` smoke; `post_case_summary` direct-write matrix; HTTP mount test; regression suite.

**security-auditor**

- Auth, tenancy, prompt injection, approval integrity, AI-summary auto-post safety, audit redaction,
  dependency supply chain, SSRF, and data exposure review. Record residuals in a `SECURITY-*` note.

**verifier**

- Independent AC mapping against this plan; deviations recorded here and in
  `docs/spec/deviations.md`.

---

## 16. Decision log

| Date | Decision | By |
|---|---|---|
| 2026-11-13 | Initial draft; no decisions approved yet | planner |
| 2026-11-13 | Q1–Q5, Q7–Q9 accepted as recommended defaults | user |
| 2026-11-13 | Q6: AI case summaries auto-post directly as labelled `ai-summary` timeline entries (no approval), with provenance/rate/sanitisation guardrails | user |
| 2026-11-13 | Q10: CrewAI is the first supported MCP client (`MCPServerHTTP`, Streamable HTTP, bearer header) | user |
| 2026-11-13 | Phase 0/1 implementation brief issued: `IMPL-2026-11-13-mcp-server-phase-0-1.md` | planner |
| 2026-11-14 | Phase 0: `mcp==2.3.0`/`mcp-types==2.3.0` hashed closure and ASGI/protocol echo spike verified; CrewAI 1.15.41 requires Python `<3.14`, so its smoke moves to an isolated Python 3.13 environment and remains pending | backend |
