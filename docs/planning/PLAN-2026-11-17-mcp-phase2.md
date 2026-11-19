# Plan: MCP Server Phase 2 — Proposal-First Writes and Labelled AI Summaries

**Status:** APPROVED — implementation handoff issued
**Date:** 2026-11-17
**Parent plan:** [`PLAN-2026-11-13-mcp-server.md`](PLAN-2026-11-13-mcp-server.md) §§6.3–6.4, 7.6–7.7, 8.2–8.3, Phase 2 tasks, Security, Testing
**Architecture decision:** [`ADR-012-mcp-server-boundary.md`](../decisions/ADR-012-mcp-server-boundary.md)
**Phase 1 verification:** [`VERIFICATION-2026-11-15-mcp-phase1.md`](VERIFICATION-2026-11-15-mcp-phase1.md) — READY / PASS

---

## 1. Summary

Phase 1 delivered a read-only, org-scoped MCP gateway at `/mcp/` with static-bearer credentials,
13 read tools, resources, prompts, audit/rate limits, and Settings → Agent access. Phase 2 adds the
smallest safe write boundary:

1. **Proposal-first mutations** — agents create `McpActionProposal` rows; humans approve/decline and
   explicitly execute through an Amalthea `/approvals` inbox. The executor re-checks authorization and
   calls existing domain services.
2. **One direct write** — `post_case_summary` appends an `ai-summary` `TimelineEvent` with fixed
   labelling, source-event provenance, sanitised Markdown, rate limits, and idempotency.
3. **No autonomy** — no Phase 3 OAuth, no Phase 4 auto-approval, no direct state-changing MCP tools,
   no bulk operations, no raw payloads, no cross-tenant access.

This stays aligned with ADR-012: MCP is a thin, audited adapter over Amalthea services, not a second
application.

---

## 2. Goals

- Let a CrewAI/other MCP agent **propose** triage and case changes without muting state directly.
- Give humans an org-scoped approval queue with exact arguments, risk labels, expiry, and audited
  decisions.
- Execute approved actions through the **same services** used by REST/UI, preserving audit, ledger,
  WebSocket, automation, and tenancy semantics.
- Allow a scoped agent to append a clearly labelled AI summary to the case timeline without changing
  case state.
- Keep static-bearer Phase 1 clients working; add `mcp:propose` and `mcp:summary` without adding
  broad `mcp:write` or `mcp:admin`.

---

## 3. Non-goals

- Phase 3 OAuth / protected-resource metadata changes.
- Phase 4 `McpOrgPolicy`, shadow mode, auto-approval, daily autonomy caps, or kill switch UI.
- Any direct MCP mutation tool other than `post_case_summary`.
- Delete, merge, share, user/token management, playbook authoring, bulk actions, or raw payload access.
- Autonomous case closure; closure remains a high-risk human-approved proposal.
- Changes to the existing REST/UI/WebSocket contracts, except additive UI surfaces and additive
  timeline rendering for `ai-summary`.

---

## 4. Architecture

### 4.1 Stack and rationale

- **Django 5.x + DRF + Channels + Daphne**: no new service; `/mcp/` remains in the existing ASGI app.
- **PostgreSQL**: proposal state, idempotency, and audit records need the same transactional
  guarantees as cases/alerts.
- **Celery/Redis**: unchanged. Playbook execution, if proposed, still uses the existing guarded
  executor through a shared service.
- **Django templates + HTMX + Tailwind**: `/approvals` reuses the existing list/modal/pager patterns.
- **MCP Python SDK 2.3.0**: unchanged pinned dependency.

### 4.2 Domain boundary

```text
MCP tool (untrusted arguments)
  -> scope + schema validation
  -> proposal service (create only; zero domain mutation)
  -> McpActionProposal row (pending)
  -> human /approvals decision
  -> execution service
       -> re-resolve target + re-check tenant/role/viewer/state
       -> shared domain service (cases/create.py, cases/update.py, alerts/escalation.py, ...)
       -> append_timeline_event + AuditLog + Channels publish
  -> bounded result back to tool caller via get_action_proposal
```

`post_case_summary` bypasses proposals only because it is append-only, labelled, provenance-bound,
rate-limited, and cannot change case state.

### 4.3 Implementation areas

| Area | Files / symbols |
|---|---|
| MCP scopes | `mcp_server/models.py` (`PHASE2_SCOPES`, validation), `mcp_server/auth.py`, `mcp_server/context.py`, `mcp_server/tools/base.py` |
| Proposal model | `mcp_server/models.py`, `mcp_server/migrations/0002_mcpactionproposal.py` |
| Proposal state machine | new `mcp_server/services/proposals.py` |
| Proposal tools | new `mcp_server/tools/proposals.py`, registered in `mcp_server/tools/__init__.py` |
| Summary service | new `mcp_server/services/summaries.py` |
| Shared write services | `cases/update.py`, `cases/create.py`, `alerts/escalation.py`, `observables/extractor.py`, `cases/ledger.py`; extract alert triage / task create / case comment / playbook run as needed |
| Audit | `mcp_server/audit.py`, `identity/audit.py` conventions |
| UI | `ui/urls.py`, `ui/views/approvals.py` (new), `ui/templates/ui/approvals*.html`, `ui/templates/ui/_timeline_entry.html`, rail/nav templates |
| Settings | `ui/views/agents.py`, agent-access form/templates to expose Phase 2 scopes |
| Tests | `tests/conformance/test_mcp_*.py`, new proposal/summary/approvals conformance tests |

---

## 5. Data model

### 5.1 `McpActionProposal` (new)

Migration: `mcp_server/migrations/0002_mcpactionproposal.py`.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Matches repo convention. |
| `organisation` | FK `Organisation`, `PROTECT` | Tenant; must match target. |
| `requested_by` | FK `User`, `SET_NULL` | Effective agent service user. |
| `credential` | FK `McpCredential`, `SET_NULL` | Provenance. |
| `tool_name` | CharField(100) | e.g. `propose_case_update`. |
| `action` | CharField(100) | Canonical service action name. |
| `target_type` / `target_id` | CharField(32/64) | Case/alert/observable/task. |
| `arguments` | JSONField | Exact validated arguments after canonicalisation. |
| `arguments_hash` | CharField(64) | SHA-256 of canonical JSON. |
| `risk` | CharField(10) | `low`, `medium`, `high`. |
| `status` | CharField(16) | `pending`, `approved`, `declined`, `expired`, `executed`, `failed`. |
| `idempotency_key` | CharField(128), unique | Deterministic per credential/tool/target/args. |
| `expires_at` | DateTimeField, indexed | Default now + 24h. |
| `decided_by` | FK `User`, `SET_NULL`, nullable | Human decision maker. |
| `decided_at` | DateTimeField nullable | |
| `executed_at` | DateTimeField nullable | |
| `result` | JSONField default dict | Bounded result; no secrets/raw payload. |
| `created_at` / `updated_at` | timestamps | |

Indexes:
- `(organisation, status, -created_at)` for the inbox.
- `(requested_by, -created_at)` for “my proposals”.
- `(target_type, target_id, status)` for case/alert cards.
- Unique `idempotency_key`.

Rules:
- Transitions: `pending -> approved|declined|expired`; `approved -> executed|failed|expired`.
- `select_for_update()` on approve/decline/execute; stale or expired rows fail closed.
- Arguments are immutable after creation; approval/decision binds to `arguments_hash`.
- Execution re-resolves target and re-checks tenant, role, viewer/read-only, and target state.
- A proposal is never a reusable capability; replay returns the original terminal result.

### 5.2 `TimelineEvent` reuse for summaries

No new model. Use:

```python
TimelineEvent(
    case=case,
    kind="ai-summary",
    title="AI-generated case summary",
    description=sanitised_markdown,
    actor=credential.service_user,
    metadata={
        "generatedBy": "mcp",
        "credentialId": str(credential.id),
        "requestId": request_id,
        "sourceEventIds": [...],
        "contentHash": sha256(case_id + normalised_summary),
    },
)
```

`kind` is a free-form `CharField`; no migration is required. Append-only: no UI/REST edit or delete
path for `ai-summary`.

### 5.3 Scope changes

- `mcp_server.models.PHASE1_SCOPES = ("mcp:read",)` remains.
- Add `PHASE2_SCOPES = ("mcp:propose", "mcp:summary")`.
- Allowed credential scopes become exactly `mcp:read`, `mcp:propose`, `mcp:summary`.
- Keep `mcp:write` and `mcp:admin` reserved and rejected.
- Auth verifies any allowed scope; tool wrappers enforce the specific scope:
  - read tools: `mcp:read`
  - proposal tools + `get_action_proposal`: `mcp:propose`
  - `post_case_summary`: `mcp:summary`
- Viewer/read-only service users never receive `mcp:propose` or `mcp:summary`.

---

## 6. MCP API contract

### 6.1 Proposal tools

All proposal tools return a handle and perform **zero domain mutations**:

```json
{
  "proposalId": "uuid",
  "status": "pending",
  "risk": "medium",
  "expiresAt": "2026-11-18T12:00:00Z",
  "approvalPath": "/approvals/<uuid>"
}
```

| Tool | Arguments | Risk | Domain action on execution |
|---|---|---|---|
| `propose_alert_triage` | `alertId`, optional `status`, `severity`, `assigneeId`, `tags`, `comment` | low/medium | Shared alert update service; timeline comment if supplied |
| `propose_case_update` | `caseId`, allowed scalar/status/assignee/tags fields | medium | `apply_case_update()` |
| `propose_case_comment` | `caseId`, `markdown` | low | `append_timeline_event(kind="comment")` |
| `propose_observable_add` | `caseId`, `type`, `value` | medium | `add_observable_with_status()` / `extract_into_case()` |
| `propose_task_create` | `caseId`, `title`, optional `assigneeId`, `dueAt` | low | New shared task-create service |
| `propose_alert_escalation` | `alertId`, optional `caseId`, `title`, `description`, `assigneeId` | high | `import_alert_to_case()` or `merge_alert_into_case()` |
| `propose_playbook_run` | `caseId`, `playbookId`, optional `observableId` | high | Guarded playbook execution service |
| `propose_case_closure` | `caseId`, `verdict`, `closingComment` | high | `apply_case_update()` close path |
| `get_action_proposal` | `proposalId` | read | Returns bounded status/result summary |

Rules:
- Strict schemas; unknown fields rejected.
- No `organisation`, `actor`, `owner`, role, approval, script, URL, header, or raw-payload arguments.
- Cross-tenant, unknown, or invisible targets return uniform `not_found`.
- `get_action_proposal` only returns proposals owned by the credential’s organisation.

### 6.2 `post_case_summary` direct write

Arguments:

| Argument | Rules |
|---|---|
| `case_id` | UUID/string resolving in the credential org and readable by the service user |
| `summary_markdown` | 1–20,000 chars, sanitised with `ui.markdown.render_markdown()` rules; raw HTML/images rejected |
| `source_event_ids` | 1–100 timeline event IDs, all belonging to `case_id` |
| `idempotency_key` | optional client key; otherwise deterministic from credential + case + content hash |

Behaviour:
- Requires `mcp:summary`; viewer/read-only credentials rejected.
- Validates all source event IDs against the same case before writing.
- Creates one `ai-summary` timeline event via `append_timeline_event()`.
- Returns `{caseId, eventId, createdAt, label: "ai-generated", requiresHumanVerification: true}`.
- Rate limits: one summary per case per 5 minutes; 20 per credential per day.
- Duplicate content for the same case returns the existing event.
- Cannot set status, severity, owner, tags, tasks, observables, playbooks, or closure.

### 6.3 Errors

Stable tool error codes: `not_found`, `forbidden`, `invalid_arguments`, `conflict`, `rate_limited`,
`proposal_expired`, `internal_error`. No tracebacks, SQL, raw exception text, payloads, or secrets.

---

## 7. UI / UX

### 7.1 `/approvals` inbox

- Routes:
  - `GET /approvals` — list pending/recent proposals for the org.
  - `GET /approvals/<uuid:proposal_id>` — detail with exact arguments and intended-change preview.
  - `POST /approvals/<uuid:proposal_id>/decide` — approve or decline with optional note.
  - `POST /approvals/<uuid:proposal_id>/execute` — explicit execution after approval.
- Reuse `ui/templates/ui/_modal.html`, `_list_summary.html`, `_pager.html`, and existing status-pill
  conventions; no native `confirm()`/`alert()`/`prompt()`.
- Dark theme, WCAG-AA, severity/risk always labelled in text, not colour alone.
- Keyboard operable; add a rail link. No new global shortcut unless the shortcut matrix is reviewed.
- Decision rules:
  - View: org analysts/admins/superAdmins (superAdmin still scoped to selected org).
  - `low`: analyst/admin may approve/decline.
  - `medium` and `high`: admin/superAdmin only; requester can never decide their own non-low proposal.
  - `high` also requires admin/superAdmin regardless of requester.
  - Execute follows the same role checks as approve.
- Empty, expired, failed, executed, and already-decided states are explicit.

### 7.2 Case/alert proposal cards

- Compact “AI proposals” card on case and alert detail, showing pending proposal count and link to
  `/approvals?target=<type>:<id>`.
- No inline approve/execute on the case page in v1; the inbox is authoritative.

### 7.3 AI summary timeline rendering

- Add an explicit `ai-summary` branch to `ui/templates/ui/_timeline_entry.html`.
- Persistent visible label: **“AI-generated summary — verify against the cited events”**.
- Show credential/agent name, `sourceEventIds` links when visible, and sanitised Markdown.
- Add the same label to `mcp_server.tools.serializers.safe_timeline_event()` output so API/MCP
  consumers see it too.
- Read-only in UI; no approve/edit/delete action.

---

## 8. Phases and acceptance criteria

### P2.0 — Baseline and shared-service extraction

- Extract or wrap request-free write services for alert triage, task create/toggle, case comment, and
  playbook run; reuse existing `apply_case_update()`, `create_case()`, escalation helpers, observable
  helpers, and `append_timeline_event()`.
- Keep current REST/UI paths delegating to the same services where practical.

**AC**
- Existing REST/UI/WebSocket behaviour is unchanged.
- Full local suite remains green before MCP write wiring starts.
- No MCP-specific domain logic lives outside `mcp_server/`.

### P2.1 — Scopes and credential UI

- Add `mcp:propose` / `mcp:summary`, update validation, auth, tool wrapper, and Settings → Agent access
  scope selection.
- Viewer service users cannot be granted write-ish scopes.

**AC**
- A Phase 1 `mcp:read` credential behaves exactly as before.
- A credential with `mcp:propose` cannot call `post_case_summary`; one with `mcp:summary` cannot create
  proposals or execute them.
- `mcp:write` and `mcp:admin` remain rejected.
- Settings UI shows only allowed scopes with clear labels and no secrets.

### P2.2 — Proposal model and state machine

- Add `McpActionProposal`, migration, indexes, choices, canonical argument hashing, deterministic
  idempotency key, expiry, and transition guards in `mcp_server/services/proposals.py`.

**AC**
- Illegal transitions fail with a stable error.
- Expired proposals cannot be approved or executed.
- Replaying execute returns the original terminal result; it never executes twice.
- Arguments cannot be changed after creation; hash mismatch fails closed.
- Unique constraint prevents duplicate proposal rows.

### P2.3 — Executor and audit

- Implement execution that re-resolves target, re-checks org/role/viewer/state, calls shared services,
  writes bounded result, emits one audit row, and one ledger/Channels event for case targets.
- Add proposal audit actions: created, approved, declined, expired, executed, failed.

**AC**
- Target moved/deleted/restricted after approval fails closed.
- Cross-tenant proposals never execute.
- Exactly one ledger event and one WebSocket publish per successful case-target execution.
- Failed execution is terminal and audited; no partial domain mutation is reported as success.
- Audit/result rows contain no secrets, raw payloads, or unbounded text.

### P2.4 — MCP proposal tools

- Add low/medium tools first, then high-risk tools, plus `get_action_proposal`.
- Register tools in `mcp_server/tools/__init__.py`; update tool catalogue docs/tests.

**AC**
- Proposal tools create a row and return the handle without mutating the target.
- Unknown/invalid fields are rejected by strict schema.
- No direct state-changing tool exists; `post_case_summary` is the only direct write.
- A proposal created by a tool is visible in `/approvals`.
- High-risk tools are risk-labelled `high` and require admin decision.

### P2.5 — `post_case_summary`

- Add `mcp_server/services/summaries.py`, tool wrapper, rate limits, idempotency, API label, and timeline
  rendering.

**AC**
- Authorised `mcp:summary` credential posts an `ai-summary` event without approval.
- Viewer/read-only credential is rejected.
- Source IDs from another case, nonexistent IDs, or empty lists are rejected.
- Duplicate content returns the existing event; rate limits return `rate_limited`.
- UI and MCP/API output label the event AI-generated and require human verification.
- The tool cannot change any case state field.

### P2.6 — Approvals UI

- Build `/approvals` list/detail/decide/execute, proposal cards, modals, rail link, and accessibility
  behaviour.

**AC**
- Role and four-eyes rules are enforced server-side, not only in the template.
- Smoketests cover no-JS POST fallback and HTMX/modal path.
- Keyboard navigation and Esc/backdrop close work; no native dialogs.
- Risk/status are text-labelled and visible to screen readers.

### P2.7 — Security, QA, verification, docs

- Security review, independent verification, CrewAI smoke update, docs, full suite, CI gate.

**AC**
- Security review records no new High/Medium finding or documents accepted residual.
- Verifier maps every AC in this plan to evidence and records deviations.
- CrewAI lists proposal tools and can call `propose_case_comment` and `get_action_proposal`.
- Full local suite green; `make check-ci` still under the 5-minute CI budget.
- TODO §22.4 closed only after verification.

---

## 9. Testing plan

### 9.1 New conformance tests

- `test_mcp_proposals.py`: create/decide/execute/expire/replay/hash/tenant/role matrix.
- `test_mcp_summary.py`: scope, source-event validation, sanitisation, rate limit, idempotency, label.
- `test_approvals_ui.py`: list/detail, role gating, four-eyes, modal markup, no native dialogs.
- `test_mcp_scopes_phase2.py`: read/propose/summary separation; reserved scopes rejected.
- Extend `test_mcp_tools.py` tool catalogue assertions for new tools.
- Extend prompt-injection tests so summary text and case data cannot approve/execute anything.

### 9.2 Regression

- Existing REST/UI/WebSocket suite unchanged.
- `make check-ci` remains fast; do not add the full proposal matrix to CI smoke.
- Full suite locally before security review and final verification.
- Postgres conformance run locally where feasible.

---

## 10. Security

- **Prompt injection**: all tool arguments and case text remain untrusted; no tool names/URLs/approvals
  are derived from content.
- **Approval integrity**: exact argument hash, single-use transitions, expiry, `select_for_update`,
  execution-time recheck.
- **Privilege escalation**: no `mcp:write`; scopes separated; no superAdmin cross-org path.
- **Cross-tenant**: credential org is authoritative; uniform not-found.
- **Summary abuse**: fixed title/label, sanitised Markdown, source-event provenance, rate limits,
  idempotency, no state change.
- **Approval fatigue**: narrow action list, risk labels, no bulk tools, summaries stay out of inbox.
- **Outbound abuse**: no free-form URL/header/script args; playbook run uses existing guarded executor.
- **Audit/redaction**: allowlisted redaction; no raw payload, token, or secret in logs/results.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Service extraction causes regressions | Small extractions first; REST/UI delegate to same service; full suite before MCP wiring. |
| Approval replay/race | Unique idempotency key, row locks, atomic transitions, execution-time recheck. |
| Agent persuades human/rubber-stamping | Exact argument preview, risk labels, four-eyes, narrow action list. |
| Malicious AI summary | Sanitisation, provenance, rate limits, independent label, no state change. |
| Playbook run as SSRF/privilege path | High risk, admin approval, no URL/header args, existing executor. |
| Scope creep into Phase 4 | No policy model, no auto-approval, no autonomy UI, no shadow mode. |
| CI budget regression | Keep new slow tests out of `make check-ci`; use local full suite as gate. |

---

## 12. Rollout and operations

- Feature remains controlled by `AMALTHEA_MCP_ENABLED` (default false).
- Existing credentials are `mcp:read` only; admins must explicitly mint new scopes.
- Document approval workflow, summary limitations, and revoke/rotate runbook in
  `docs/integrations/crewai.md` and a new `docs/integrations/mcp-approvals.md` if needed.
- Monitor: proposal count by action/risk/status, approval latency, execution failures, summary rate
  limits, 401/403/429 rates. No payload contents in metrics.
- Retention: keep proposals as audit evidence for at least 90 days; decide long-term retention in open
  question OQ-5.

---

## 13. Open questions and recommended defaults

| # | Question | Recommended default |
|---|---|---|
| OQ-1 | Include all high-risk proposal tools in the first Phase 2 increment? | Yes, but implement low/medium first; high-risk tools land behind the same executor after it is green. |
| OQ-2 | Who may approve? | Low: analyst/admin/non-superAdmin break-glass; medium/high: admin/superAdmin; requester never decides own non-low. |
| OQ-3 | Proposal expiry? | 24 hours. |
| OQ-4 | Summary limits? | 1 per case / 5 min, 20 per credential / day, 1–100 source events. |
| OQ-5 | Proposal retention? | 90 days minimum; keep executed/declined rows as audit evidence until a general audit-retention job exists. |
| OQ-6 | TheHive API mapping for `ai-summary`? | Map to existing `custom` kind with persistent AI label in metadata; no API schema change. |

---

## 14. Decision log

| Date | Decision | By |
|---|---|---|
| 2026-11-13 | Proposal-first writes; summaries auto-post as labelled `ai-summary`; CrewAI first | user |
| 2026-11-16 | Phase 1 verified READY; no Phase 2 implementation started | verifier |
| 2026-11-17 | Focused Phase 2 execution plan drafted | planner |
| 2026-11-17 | Phase 2 plan approved with recommended defaults; implementation brief issued | user |

---

## 15. Approval and handoff

On approval, issue `docs/planning/IMPL-2026-11-17-mcp-phase2.md` and hand off:

- **django-backend**: scopes, model/migration, services, executor, proposal tools, summary tool.
- **django-frontend**: `/approvals`, cards, AI-summary timeline rendering, scope UI.
- **qa-tester**: conformance matrix, regression, CrewAI smoke.
- **security-auditor**: approval integrity, injection, tenancy, redaction, SSRF review.
- **verifier**: AC mapping and deviation record.

Deviations from this plan must be recorded here and in the implementation brief before merge.
