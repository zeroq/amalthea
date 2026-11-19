# Implementation Brief: MCP Server Phase 2 — Proposal-First Writes and Labelled AI Summaries

**Status:** READY FOR IMPLEMENTATION
**Date:** 2026-11-17
**Approved plan:** [`PLAN-2026-11-17-mcp-phase2.md`](PLAN-2026-11-17-mcp-phase2.md)
**Parent plan:** [`PLAN-2026-11-13-mcp-server.md`](PLAN-2026-11-13-mcp-server.md)
**Architecture decision:** [`ADR-012-mcp-server-boundary.md`](../decisions/ADR-012-mcp-server-boundary.md)
**Phase 1 verification:** [`VERIFICATION-2026-11-15-mcp-phase1.md`](VERIFICATION-2026-11-15-mcp-phase1.md)

---

## 1. Scope

Implement approved MCP Phase 2:

- Add `mcp:propose` and `mcp:summary`; keep `mcp:write` / `mcp:admin` rejected.
- Add `McpActionProposal`, migration, state machine, idempotency, expiry, and audit.
- Add proposal tools for triage/update/comment/observable/task/escalation/playbook/closure plus
  `get_action_proposal`.
- Add human `/approvals` inbox with approve/decline/execute.
- Add `post_case_summary` as the only direct write, producing labelled `ai-summary` timeline events.
- Extract/write shared domain services so MCP execution uses the same logic as REST/UI.
- Update tests, security review, verification, docs, and TODO §22.4 only after verification passes.

Do **not** implement Phase 3 OAuth, Phase 4 autonomy/policy/kill-switch, direct general mutation tools,
raw payload access, bulk tools, or autonomous closure.

---

## 2. Approved defaults

| Topic | Default |
|---|---|
| High-risk tools | Include in Phase 2, implemented after low/medium executor is green. |
| Approver roles | Low: analyst/admin; medium/high: admin/superAdmin; requester never decides own non-low proposal. |
| Proposal expiry | 24 hours. |
| Summary limits | 1 per case / 5 min, 20 per credential / day, 1–100 source events. |
| Proposal retention | Keep at least 90 days; retain executed/declined rows as audit evidence until a general retention job exists. |
| TheHive mapping | Map `ai-summary` to existing `custom` kind with persistent AI label; no API schema change. |

---

## 3. Global constraints

- MCP remains a thin, audited adapter over existing domain services.
- Existing REST/UI/WebSocket contracts must not change except additive UI surfaces and additive
  `ai-summary` rendering/labels.
- All tool arguments are untrusted. Strict schemas; unknown fields rejected.
- No secrets, tokens, raw payloads, headers, arbitrary URLs, script text, organisation IDs, actor
  overrides, or approval flags in tool arguments.
- Cross-tenant or invisible targets return uniform `not_found`.
- Public repo rules apply: no secrets in code, tests, fixtures, docs, CI, or commit messages.
- CI must remain within the repaired five-minute budget. Do not add slow Phase 2 matrices to
  `make check-ci`; add a few fast smoke tests only if they do not threaten the budget.
- Deviations must be recorded in the approved plan and `docs/spec/deviations.md` before merge.

---

## 4. Dependency graph

```text
B1 scopes
  -> B2 proposal model/state machine
    -> B3 shared services + executor
      -> B4 proposal tools
      -> F2 approvals UI

B3 + B5 summary service
  -> post_case_summary tool
  -> F4 ai-summary timeline rendering

B4 + F2 + F3 + F4
  -> Q1/Q2/Q4 qa
  -> S1 security
  -> V1 verification
  -> docs/TODO closure
```

Backend can parallelise B1/B2 with frontend F4 after the summary event shape is fixed. Frontend F2
depends on B2/B3 field names and status values.

---

## 5. django-backend handoff

### B1 — Scope expansion and credential validation

**Files**
- `mcp_server/models.py`
- `mcp_server/auth.py`
- `mcp_server/context.py`
- `mcp_server/tools/base.py`
- Tests in `tests/conformance/test_mcp_credentials.py`, `test_mcp_scopes_phase2.py` (new)

**Implement**
- Add `PHASE2_SCOPES = ("mcp:propose", "mcp:summary")`.
- Allowed scopes: `mcp:read`, `mcp:propose`, `mcp:summary` only.
- Keep `mcp:write`, `mcp:admin`, and all unknown scopes rejected in `McpCredential.clean()`.
- Tool wrappers enforce exact per-tool scope:
  - read tools: `mcp:read`
  - proposal tools and `get_action_proposal`: `mcp:propose`
  - `post_case_summary`: `mcp:summary`
- Ensure viewer/read-only service users cannot be granted `mcp:propose` or `mcp:summary`.

**AC**
- Existing `mcp:read` credentials and all 13 Phase 1 tools behave unchanged.
- `mcp:propose` cannot call summary, execute proposals, or create summary events.
- `mcp:summary` cannot create/decide/execute proposals.
- `mcp:write` and `mcp:admin` remain rejected.
- Errors are stable and do not leak tracebacks or secrets.

---

### B2 — `McpActionProposal` model, migration, and state machine

**Files**
- `mcp_server/models.py`
- `mcp_server/migrations/0002_mcpactionproposal.py` (new)
- `mcp_server/services/proposals.py` (new)
- Tests in `tests/conformance/test_mcp_proposals.py` (new)

**Implement**
- Add fields exactly as approved in plan §5.1: organisation, requested_by, credential, tool_name,
  action, target_type/target_id, arguments, arguments_hash, risk, status, idempotency_key,
  expires_at, decided_by, decided_at, executed_at, result, created_at, updated_at.
- Add indexes from plan §5.1 and unique `idempotency_key`.
- Canonicalise arguments using deterministic JSON (sorted keys, separators, no raw payload).
- Generate deterministic idempotency key from credential + tool + target + canonical args.
- Status rules:
  - `pending -> approved|declined|expired`
  - `approved -> executed|failed|expired`
- Use `select_for_update()` for decide/execute; fail closed on stale hash, expiry, or wrong status.
- Arguments immutable after creation.

**AC**
- Illegal transitions fail with stable `proposal_expired` / `conflict`-class errors.
- Expired proposals cannot be approved or executed.
- Replaying execute returns the original terminal result and never executes twice.
- Hash mismatch or argument mutation fails closed.
- Duplicate creation returns the existing proposal.
- All tenant fields match the credential org.

---

### B3 — Shared write services and proposal executor

**Files**
- New/extracted services under:
  - `alerts/` triage/update helpers
  - `cases/update.py`, `cases/create.py`, `cases/ledger.py`
  - `observables/extractor.py`
  - `automation/` playbook run helpers
  - `cases/task_service.py` or equivalent
- `mcp_server/services/proposals.py`
- `mcp_server/audit.py`
- Tests in existing REST/UI suites plus `test_mcp_proposals.py`

**Implement**
- Extract or wrap request-free services for:
  - alert triage: status/severity/assignee/tags/comment
  - case update
  - case comment
  - observable add/extract
  - task create/toggle/assign
  - alert escalation
  - playbook run
  - case closure
- Keep REST/UI paths delegating to the same services where practical.
- Executor must re-resolve target and re-check organisation, role, viewer/read-only, and target state.
- Successful case-target execution appends one ledger event and publishes one Channels update.
- Write bounded `result` JSON only.

**AC**
- Existing REST/UI/WebSocket behaviour is unchanged.
- Cross-tenant, deleted, hidden, viewer-disallowed, or state-invalid targets fail closed.
- Failed execution is terminal and audited; no partial mutation reported as success.
- No MCP-specific domain logic outside `mcp_server/`.
- Audit includes created, approved, declined, expired, executed, and failed actions.

---

### B4 — MCP proposal tools

**Files**
- `mcp_server/tools/proposals.py` (new)
- `mcp_server/tools/__init__.py`
- `docs/integrations/crewai.md`
- Tests in `tests/conformance/test_mcp_tools.py`, `test_mcp_proposals.py`

**Implement**
- Tools:
  - `propose_alert_triage`
  - `propose_case_update`
  - `propose_case_comment`
  - `propose_observable_add`
  - `propose_task_create`
  - `propose_alert_escalation`
  - `propose_playbook_run`
  - `propose_case_closure`
  - `get_action_proposal`
- Strict Pydantic/SDK schemas; no unknown fields.
- Tools create/read proposal rows only; zero domain mutation.
- Return `{proposalId, status, risk, expiresAt, approvalPath}` for create tools.
- `get_action_proposal` returns bounded status/result and org-scoped data only.
- No direct state-changing tool except summary.

**AC**
- Proposal creation is visible in `/approvals`.
- Unknown/invalid fields are rejected before any row is created.
- High-risk tools are labelled `high`.
- CrewAI can list the new tools and call `propose_case_comment` plus `get_action_proposal`.
- Tool catalogue tests pass.

---

### B5 — `post_case_summary` service and tool

**Files**
- `mcp_server/services/summaries.py` (new)
- `mcp_server/tools/summaries.py` (new)
- `mcp_server/tools/__init__.py`
- `mcp_server/limits.py`
- `mcp_server/tools/serializers.py`
- Tests in `tests/conformance/test_mcp_summary.py` (new)

**Implement**
- Validate `case_id`, `summary_markdown`, `source_event_ids`, optional `idempotency_key`.
- Sanitise Markdown using the existing UI Markdown rules; reject raw HTML/images/attachments.
- Validate 1–100 source event IDs, all from the same case.
- Rate limits: 1/case/5 min, 20/credential/day.
- Append via `append_timeline_event()` using exact metadata shape from plan §5.2.
- Duplicate content for same case returns existing event.
- Add `ai-generated` label/provenance to safe serializers.
- Return `{caseId, eventId, createdAt, label: "ai-generated", requiresHumanVerification: true}`.

**AC**
- `mcp:summary` posts without proposal.
- Viewer/read-only credentials are rejected.
- Cross-case/nonexistent/empty source IDs rejected.
- No state field can be changed through the tool.
- UI and API/MCP output label the event AI-generated.

---

### B6 — Backend docs and audit follow-through

**Files**
- `docs/integrations/crewai.md`
- `docs/integrations/mcp-approvals.md` (new if needed)
- `docs/spec/deviations.md` if deviations occur

**Implement**
- Document new tools, scopes, approval workflow, summary limitations, and revoke/rotate runbook.
- Record any deviation from approved plan before merge.

**AC**
- Docs match implementation exactly.
- No secret or example token value appears in docs/tests/fixtures.

---

## 6. django-frontend handoff

### F1 — Agent access scope UI

**Files**
- `ui/views/agents.py`
- `ui/templates/ui/_settings_agent_create_form.html`
- `ui/templates/ui/settings_agents.html`
- `ui/templates/ui/_settings_agent_created.html`
- Tests in `tests/conformance/test_settings_agent_access.py`

**Implement**
- Expose `mcp:read`, `mcp:propose`, `mcp:summary` in credential creation.
- Show clear labels and warnings that propose/summary are write-adjacent.
- Do not expose `mcp:write`, `mcp:admin`, or secrets.

**AC**
- Viewer/read-only users cannot grant propose/summary.
- Phase 1 UI remains unchanged for read-only credentials.
- Existing agency create/revoke/show-once modal tests pass.

---

### F2 — `/approvals` inbox

**Files**
- `ui/urls.py`
- `ui/views/approvals.py` (new)
- `ui/templates/ui/approvals.html` (new)
- `ui/templates/ui/approvals_detail.html` (new)
- `ui/templates/ui/_approval_row.html` (new if useful)
- Tests in `tests/conformance/test_approvals_ui.py` (new)

**Implement**
- Routes:
  - `GET /approvals`
  - `GET /approvals/<uuid:proposal_id>`
  - `POST /approvals/<uuid:proposal_id>/decide`
  - `POST /approvals/<uuid:proposal_id>/execute`
- Show exact arguments, risk, requester, target, expiry, status, and intended change.
- Server-side role rules:
  - low: analyst/admin
  - medium/high: admin/superAdmin
  - requester never decides own non-low
  - execute follows decide checks
- Use existing modal/pager/status-pill patterns; no native dialogs.
- Keyboard accessible; Esc/backdrop close; dark theme; text labels for risk/status.

**AC**
- Unauthorised roles cannot decide/execute even with forged POSTs.
- Expired/decided proposals cannot be re-decided.
- No native `confirm()`/`alert()`/`prompt()`.
- No-JS POST fallback works.
- Empty/expired/failed/executed states are explicit.

---

### F3 — Case/alert proposal cards

**Files**
- Case and alert detail templates under `ui/templates/ui/`
- Optional small view-context helper if needed

**Implement**
- Add compact “AI proposals” card with pending count and `/approvals?target=<type>:<id>` link.
- Do not inline approve/execute on those pages.

**AC**
- Cards are org-scoped and show only proposals for the target.
- No extra mutation route is introduced.
- Keyboard/screen-reader labels are present.

---

### F4 — `ai-summary` timeline rendering

**Files**
- `ui/templates/ui/_timeline_entry.html`
- `ui/templates/ui/case_detail.html` or related timeline include
- `static/` CSS only if needed

**Implement**
- Add explicit `ai-summary` branch.
- Visible label: **“AI-generated summary — verify against the cited events”**.
- Show credential/agent name, sanitised Markdown, and linked source-event citations when visible.
- Persistent AI label; no approve/edit/delete action.

**AC**
- AI summaries are visually distinct without relying on colour alone.
- Source events link only when the viewer can see them.
- No raw HTML/images rendered from summary Markdown.
- Existing timeline entries unchanged.

---

## 7. qa-tester handoff

### Q1 — Conformance matrix

**Files**
- `tests/conformance/test_mcp_scopes_phase2.py` (new)
- `tests/conformance/test_mcp_proposals.py` (new)
- `tests/conformance/test_mcp_summary.py` (new)
- `tests/conformance/test_approvals_ui.py` (new)
- Extend `tests/conformance/test_mcp_tools.py`, `test_mcp_credentials.py`, `test_settings_agent_access.py`

**Test matrix**
- Scope separation: read/propose/summary; reserved scopes rejected.
- Tenancy: cross-org target/proposal/source event returns not-found.
- Proposal: create/decide/execute/decline/expire/replay/hash/immutability/idempotency.
- Roles: low/medium/high approval, four-eyes, viewer/read-only denied.
- Summary: scope, source validation, sanitisation, rate limits, duplicate idempotency, label.
- Prompt injection: case text/agent content cannot approve/execute or change arguments.
- UI: modal markup, keyboard behaviour, no native dialogs, no-JS fallback.

**AC**
- All new ACs in §§5–6 have direct test evidence.
- Tests fail on the unsafe implementation, not only on happy path.

---

### Q2 — CrewAI smoke

**Files**
- `docs/integrations/crewai.md`
- Test script/log location agreed with backend

**Implement**
- Use the isolated Python 3.13 environment already used in Phase 1.
- Smoke: list tools, call `propose_case_comment`, call `get_action_proposal`, call
  `post_case_summary` against a seeded case.
- Confirm no proposal tool mutates domain state before approval/execute.

**AC**
- CrewAI compatibility evidence recorded.
- No Python 3.14 dependency regression accepted.

---

### Q3 — Regression and gates

**Commands**
- `make check`
- `make check-ci`
- focused Phase 2 conformance suite
- full local pytest suite
- `bash scripts/secret-scan.sh --tracked`
- `bash scripts/secret-scan.sh --history`
- `./scripts/lock.sh --check` when dependencies change

**AC**
- Full local suite green.
- `make check-ci` remains within five-minute CI budget.
- Secret scans clean.
- Dependency lock unchanged or hash-verified.

---

## 8. security-auditor handoff

**Files**
- `docs/security/SECURITY-2026-11-17-mcp-phase2.md` (new)

**Review**
- Approval integrity: hash binding, locks, expiry, replay, idempotency, stale-target checks.
- Role/tenant boundary: requester four-eyes, high-risk admin gate, viewer/read-only denial.
- Prompt injection: summaries/comments/case text cannot trigger approval or execution.
- Summary safety: sanitisation, provenance, rate limits, no state change, labels.
- SSRF/execution abuse: playbook run has no free-form URL/header/script args.
- Audit/redaction: no secrets, tokens, raw payloads, or unbounded data in logs/results.
- Supply chain: no new dependency without hash/ADR justification.

**AC**
- Security verdict is `PASS` or documents accepted residuals.
- Any High/Medium finding is fixed and retested before verification.

---

## 9. verifier handoff

**Files**
- `docs/planning/VERIFICATION-2026-11-17-mcp-phase2.md` (new)

**Implement**
- Map every AC in `PLAN-2026-11-17-mcp-phase2.md` §8 and this brief to evidence.
- Mark each as met/not met/deviated.
- Check that no non-goal was implemented.
- Confirm full suite, focused suite, security review, CI budget, and docs closure.

**AC**
- Verdict `READY — PASS` before TODO closure.
- Deviations recorded in plan and `docs/spec/deviations.md`.

---

## 10. Final closure checklist

Only after all agent gates pass:

- Update `TODO.md` §22.4 to closed/done.
- Update priority table/header with Phase 2 verified status.
- Add `COMPLETED.md` entry with commit(s), test evidence, security note, verification note.
- Update parent plan decision log with Phase 2 verified date.
- Do not mark complete based on implementation intent; require verifier PASS.

---

## 11. Commit/PR expectations

- No commit/push has been requested yet for this brief; create commits only when instructed.
- Use the repo’s existing hooks and secret scans; never bypass with `--no-verify`.
- Keep code changes reviewable by area: backend foundation, backend tools/summary, frontend approvals,
  tests/security/docs.
