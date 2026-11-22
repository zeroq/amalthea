# PLAN-2026-11-21 — MCP Phase 4: Policy-Bounded Autonomy

Status: **Implemented and verified PASS 2026-11-22 (42/42 ACs met, 0 deviations); defaults accepted 2026-11-21.**
Baseline: Phase 3 is complete and verified. Phase 4 is the remaining MCP phase.
Source requirements: `TODO.md §22`, `docs/planning/PLAN-2026-11-13-mcp-server.md §5/§8`, `docs/decisions/ADR-012-mcp-server-boundary.md`.

## Summary

Add org-configured, deterministic, default-off autonomy for a narrow set of low-risk MCP proposals. Autonomy must
never replace human approval for medium/high actions, case closure, playbook execution, alert escalation, or any
destructive/admin capability. The first release is **shadow-only**: it records what the policy would do, but executes
nothing. Enforce mode exists in code/config only after separate review of shadow evidence; no org starts in enforce
mode.

## Goals

- Add `McpOrgPolicy` per organisation with safe defaults:
  - `autonomy_enabled=False`
  - `shadow_mode=True`
  - low severity cap
  - daily cap
  - four-eyes required
- Add deterministic policy evaluation that uses only allowlisted action names, proposal risk, target state, severity,
  caps, and bounded non-secret evidence.
- Add an append-only `McpPolicyDecision` log for shadow evaluation, reason codes, and auditability.
- Auto-execute only exact allowlisted low-risk proposals **after** explicit admin enablement and explicit exit from
  shadow mode.
- Keep human approval/execution unchanged.
- Add admin/superAdmin-only Settings → Agent access → Autonomy UI with kill switch, allowlist, caps, four-eyes toggle,
  warning banners, `<dialog>` confirmation, and plain POST fallback.
- Add a historical shadow evaluation harness and evidence report before any enforce-mode decision.

## Non-goals

- No server-side LLM, model confidence scoring, prompt-based approval, or autonomous use of free-text case content as
  evidence.
- No autonomous case closure, playbook run, alert escalation, destructive action, merge/share/delete, user/org/token
  mutation, bulk action, URL/header/script argument, or raw payload access.
- No change to `post_case_summary`; it remains the separately scoped direct-write exception.
- No new MCP transport, new scope, cross-org MCP access, or superAdmin cross-org break-glass path.
- No background Celery executor in the minimal release; autonomous execution stays synchronous because only narrow
  low-risk actions are included.
- No auto-approval by default. No org starts in enforce mode.
- No raw payloads, model keys, tokens, URLs, headers, scripts, or unbounded case text in logs/audit/evidence.

## Architecture

### Current baseline

- MCP ASGI endpoint: `mcp_server/asgi.py`; fail-closed `MCP_ENABLED`, strict tool schemas, static/OAuth/both auth modes.
- Tools:
  - read tools in `mcp_server/tools/{alerts,cases,observables,tasks,playbooks,search}.py`
  - proposal tools in `mcp_server/tools/proposals.py`
  - direct summary tool in `mcp_server/tools/summaries.py`
- Proposal model/state machine: `mcp_server/models.py::McpActionProposal`; executor in
  `mcp_server/services/proposals.py`.
- Human approval UI: `ui/views/approvals.py`, `ui/templates/ui/approvals_*`.
- Settings conventions: `ui/views/agents.py`, `ui/templates/ui/_settings_nav.html`.
- MCP invocation audit: `McpInvocation`; audit helpers in `identity/audit.py`.
- Existing risk levels: proposal tools assign `low`, `medium`, or `high`.

### Proposed flow

1. A low-risk proposal tool creates `McpActionProposal` as `pending`, exactly as today.
2. Immediately after creation, call a shared autonomy hook in `mcp_server/tools/proposals.py`.
3. The hook loads/creates the org policy, evaluates it deterministically, writes `McpPolicyDecision`, and:
   - shadow mode: leaves proposal pending; executes nothing;
   - enforce mode + allow: atomically auto-approves and executes via a dedicated autonomous executor;
   - deny/defer: leaves proposal pending for normal human approval.
4. The autonomous executor re-evaluates policy immediately before writing, re-resolves the target under org scoping,
   calls the same domain service used by the human executor, writes bounded result/audit/ledger records, and records the
   autonomous decision.
5. Human `/approvals` remains unchanged except for displaying provenance for auto-approved/rejected policy decisions.

Do **not** put the policy hook in `mcp_server/tools/base.py::run_write()`; that wrapper has no proposal identity.

## Data model

### `McpOrgPolicy` — new migration

Fields from the existing plan:

| Field | Type / default | Meaning |
|---|---|---|
| `organisation` | OneToOne FK to `Organisation`, `PROTECT`, `related_name="mcp_policy"` | One policy per org |
| `autonomy_enabled` | bool, default `False` | Master kill switch |
| `shadow_mode` | bool, default `True` | Record decisions; execute nothing |
| `allowed_actions` | JSON list, default `[]` | Exact allowlist entries from server-side schema registry |
| `max_auto_severity` | small int, default `1` | Maximum target severity eligible for autonomy |
| `daily_action_cap` | positive int, default `20` | Cap per org per UTC day |
| `require_four_eyes` | bool, default `True` | Default blocks agent self-approval |
| `updated_by` | FK `User`, `SET_NULL`, nullable | Last human editor |
| `updated_at` | datetime, auto | Last update |

Validation:

- `allowed_actions` must be a list of exact server-registered allowlist IDs; no wildcards, prefixes, unknown actions,
  reserved actions, destructive actions, or duplicate entries.
- `daily_action_cap >= 1`.
- `max_auto_severity` must be within known severity range.
- `updated_by`, when set, must belong to the policy organisation.
- Default policy is created lazily on first admin view or first evaluation; creation itself enables nothing.

### `McpPolicyDecision` — new migration

Append-only decision log:

| Field | Purpose |
|---|---|
| `id` UUID | PK |
| `organisation` FK `Organisation`, `PROTECT` | Tenant |
| `policy` FK `McpOrgPolicy`, `SET_NULL`, nullable | Policy in force |
| `proposal` FK `McpActionProposal`, `SET_NULL`, nullable | Source proposal |
| `tool_name`, `action`, `target_type`, `target_id`, `risk` | Decision subject |
| `mode` | `shadow` or `enforce` |
| `decision` | `allow`, `deny`, `defer` |
| `reason_code` | Stable machine-readable reason |
| `evidence` JSON | Bounded, non-secret facts only |
| `policy_snapshot` JSON | Exact policy fields used |
| `credential_id`, `oauth_binding_id`, `actor_id` | Attribution |
| `stage` | `proposal_created` or `pre_execution`; distinguishes the creation-time decision from the executor re-check |
| `execution_outcome` | `not_applicable`, `pending`, `executed`, or `failed`; shadow rows are `not_applicable` |
| `created_at` | Timestamp |

Indexes:
- `(organisation, created_at)`
- `(organisation, mode, decision, execution_outcome, created_at)` for UTC-day cap counting
- `(decision, mode, created_at)`
- `(proposal, created_at)`

Retention: append-only in Phase 4; no purge job. Default is to follow proposal/audit evidence rather than the 90-day
invocation retention. `execution_outcome` may transition once from `pending` to terminal; no decision row is deleted.

## Policy engine

New module: `mcp_server/services/autonomy.py`.

```python
@dataclass(frozen=True)
class AutonomyDecision:
    decision: str  # allow | deny | defer
    reason_code: str
    mode: str  # shadow | enforce
    evidence: dict[str, Any]
    policy_snapshot: dict[str, Any]


def evaluate_proposal_autonomy(
    *,
    policy: McpOrgPolicy,
    principal: Principal,
    proposal: McpActionProposal,
    target: Any | None,
    now: datetime,
    daily_count: int,
) -> AutonomyDecision: ...
```

Deterministic check order, returning the first reason:

1. policy exists and belongs to proposal org;
2. `autonomy_enabled=True` (kill switch);
3. `shadow_mode` determines mode;
4. proposal is `pending`, not expired, hash intact;
5. `risk == "low"`;
6. exact action allowlist match;
7. action not in hard-deny set;
8. argument keys subset of allowlist entry's schema;
9. target exists, org matches, target severity <= `max_auto_severity`;
10. target state valid (case not closed; alert not linked);
11. four-eyes semantics resolved;
12. `daily_count < daily_action_cap`;
13. args contain no forbidden fields (raw payload, URL, header, script, org, owner, role, approval).

No model text, confidence, prompt content, or free-form summary is evidence.

### Initial allowlist (default)

Only exact structured entries:

1. `propose_case_comment` / `append_timeline_event`
   - allowed argument keys: `caseId`, `markdown`
2. `propose_task_create` / `create_task`
   - allowed argument keys: `caseId`, `title`
   - `assigneeId` and `dueAt` are rejected in v1.

Hard deny regardless of policy:

- `propose_case_closure`, `close_case`
- `propose_playbook_run`, `run_playbook`
- `propose_alert_escalation`, `escalate_alert`
- `apply_case_update`
- `add_observable_with_status`
- any delete, merge, share, user, org, token, bulk, URL, header, script, or playbook-authoring capability

### Four-eyes semantics (default)

- `require_four_eyes=True` means autonomy returns `defer`; the proposal stays pending for a human.
- Enforce-mode autonomy requires all of:
  - `autonomy_enabled=True`
  - `shadow_mode=False`
  - `require_four_eyes=False`
  - explicit admin confirmation in the UI
- This treats agent self-approval as not satisfying four-eyes.

## API / UI

No REST API change.

### Settings UI

New/changed files:

- `ui/views/autonomy.py`
- `ui/templates/ui/settings_agent_autonomy.html`
- `ui/templates/ui/_settings_agent_autonomy_form.html`
- `ui/templates/ui/_settings_agent_autonomy_enable_form.html`
- `ui/templates/ui/_settings_nav.html`
- `ui/urls.py`
- `ui/views/__init__.py`

Routes:

- `/settings/agent-access/autonomy/<uuid:organisation_id>` → `ui-settings-agent-autonomy`
- `/settings/agent-access/autonomy/<uuid:organisation_id>/update` → `ui-settings-agent-autonomy-update`

Guards:

- `login_required`, `not_viewer`, `_require_admin_ui()`.
- admin: only own org; otherwise 404.
- superAdmin: explicit org in URL; no implicit first org.
- analyst/viewer: 404.

Controls:

- kill switch (`autonomy_enabled`)
- shadow mode
- allowlist checkbox set from server-side registry
- max auto severity
- daily action cap
- require four-eyes
- prominent banner when enabled; stronger warning when enabled and shadow disabled
- `<dialog>` confirmation for enabling enforce mode; no native `confirm()`/`alert()`/`prompt()`
- plain POST fallback

Audit:

- Add `record_mcp_org_policy_change(...)` in `identity/audit.py`.
- Audit old/new allowlisted snapshots only; never raw payloads/secrets.
- Suggested actions: `mcp_org_policy.created`, `.updated`, `.enabled`, `.disabled`, `.shadow_exited`.

## Phases and acceptance criteria

### Phase 1 — Models + deterministic evaluator

- **T1.1 — Add `McpOrgPolicy` and `McpPolicyDecision` with migration.**
  - AC1.1a: one policy per org; safe defaults.
  - AC1.1b: allowlist validation rejects unknown/wildcard/reserved/duplicate actions.
  - AC1.1c: decision log fields/indexes support shadow evidence and cap counting.
- **T1.2 — Add allowlist registry + policy engine.**
  - AC1.2a: exact allowlist entries only; closure/playbook/escalation hard-denied.
  - AC1.2b: deterministic ordered checks with stable `reason_code`s.
  - AC1.2c: evidence and snapshots are bounded and contain no raw payload/secrets.
  - AC1.2d: same inputs always produce same decision and reason.

### Phase 2 — Hook + autonomous executor

- **T2.1 — Hook low-risk proposal tools.**
  - AC2.1a: hook runs only immediately after proposal creation.
  - AC2.1b: shadow mode writes a decision row and executes nothing.
  - AC2.1c: `get_action_proposal` and `post_case_summary` are unaffected.
- **T2.2 — Add `execute_proposal_autonomous()`.**
  - AC2.2a: re-evaluates policy immediately before execution.
  - AC2.2b: uses same domain services as human executor.
  - AC2.2c: re-checks org, target state, expiry, hash, and caps under lock.
  - AC2.2d: writes bounded result, ledger event, audit row, and policy decision.
  - AC2.2e: never executes closure, playbook, escalation, or medium/high proposals.
- **T2.3 — Daily cap / kill switch.**
  - AC2.3a: cap counts successful autonomous executions per org per UTC day using enforce-mode
    `pre_execution` decision rows whose `execution_outcome=executed`.
  - AC2.3b: shadow decisions and failed/pending executions do not consume quota.
  - AC2.3c: cap enforcement is race-safe.
  - AC2.3d: disabling autonomy blocks future autonomous execution immediately.

### Phase 3 — UI + audit

- **T3.1 — Settings page.**
  - AC3.1a: admin/superAdmin only and org-scoped.
  - AC3.1b: all controls persist through validation.
  - AC3.1c: warn banners and enforce-mode `<dialog>` confirmation render.
  - AC3.1d: plain POST fallback works.
- **T3.2 — Audit.**
  - AC3.2a: every policy change writes old/new snapshots.
  - AC3.2b: audit/UI tests cover cross-org 404 and role denial.
  - AC3.2c: no secrets or raw payloads in audit/evidence.

### Phase 4 — Shadow evaluation + release gate

- **T4.1 — Evaluation harness.**
  - AC4.1a: replay historical proposals/decisions in shadow mode without mutating domain state.
  - AC4.1b: report action distribution, allow/deny/defer reasons, estimated auto-approval volume, daily cap hits, and false-positive examples.
  - AC4.1c: harness output is written under `docs/reviews/` or `docs/planning/`.
- **T4.2 — Verification/security.**
  - AC4.2a: targeted MCP/settings suites green.
  - AC4.2b: full suite, static gate, migrations check, secret scan green.
  - AC4.2c: security review specifically covers self-approval, cap race, kill switch, and no raw payload leakage.
  - AC4.2d: no org is switched to enforce mode as part of implementation; that is a separate reviewed decision.

## Security

- Model output, tool arguments, and case text remain untrusted.
- Approval remains a human decision; autonomy is an explicit, org-configured exception for exact low-risk actions.
- Executor re-checks authorization and policy; approval/proposal identity is not a capability.
- Hard-deny closure/playbook/escalation and all destructive/admin capabilities.
- Keep raw payloads, tokens, URLs, headers, scripts, and model keys out of policy evidence/audit.
- Bound all JSON evidence and policy snapshots.
- Enforce tenant scoping everywhere, including superAdmin.
- Keep human `/approvals` behavior unchanged.
- Any future enforce-mode enablement requires shadow evidence review and explicit admin action.

## Testing

New suites:

- `tests/conformance/test_mcp_autonomy.py`
- `tests/conformance/test_settings_agent_autonomy.py`
- `tests/conformance/test_mcp_autonomy_shadow_eval.py`

Extend:

- `tests/conformance/test_mcp_proposals.py`
- `tests/conformance/test_mcp_scopes_phase2.py`
- `tests/conformance/test_mcp_limits.py`
- `tests/conformance/test_approvals_ui.py`
- `tests/conformance/test_mcp_deployment.py`
- `tests/conformance/test_tenant_scoping.py`
- `tests/conformance/test_ui_urls_smoke.py`
- icon/table/modal conformance guards as needed

Required coverage includes: safe defaults, exact allowlist, hard denials, four-eyes defer, shadow no-execution, enforce
execution, re-evaluation, cap race, kill switch, audit old/new, role/org gating, no secrets in evidence, and UI
conventions.

## Risks

- **R1 — Self-approval.** Four-eyes default is the mitigation; enforce requires explicit disabling.
- **R2 — Approval fatigue.** Narrow allowlist, caps, and shadow evidence.
- **R3 — Untrusted free text.** Case comment is proposed for autonomy; evidence must not parse or trust it, and admins
  must review shadow examples before enabling.
- **R4 — Cap race.** Enforce cap under policy row lock.
- **R5 — Overreach from allowlist drift.** Server-side registry only; no wildcard/free-form action IDs.
- **R6 — Operational surprise.** No org starts in enforce mode; warning banners and confirmation dialog.

## Open questions

**Resolved 2026-11-21:** the user accepted all defaults. Q4-1…Q4-11 below are approved decisions;
no Phase 4 behavior may deviate from them without recording a deviation in this plan before merge.

- **Q4-1 — Four-eyes semantics:** should `require_four_eyes=True` block autonomous self-approval entirely?
  - **Default:** yes; defer to human.
- **Q4-2 — Allowlist breadth:** include `propose_task_create` in initial allowlist?
  - **Default:** yes, but only `caseId` + `title`; no assignee/due date.
- **Q4-3 — Alert triage comment-only:** include `propose_alert_triage` when only `comment` is supplied?
  - **Default:** no; defer to later.
- **Q4-4 — Summary governance:** should `post_case_summary` gain org policy gating?
  - **Default:** no; unchanged separate scoped direct write.
- **Q4-5 — Cap counting:** successful autonomous executions, not shadow decisions, per UTC day?
  - **Default:** yes.
- **Q4-6 — Severity mapping:** use existing numeric severity scale and allow only `<= 1` by default?
  - **Default:** yes.
- **Q4-7 — Decision retention:** append-only with proposal evidence rather than 90-day invocation purge?
  - **Default:** yes.
- **Q4-8 — SuperAdmin UX:** explicit org URL instead of selector?
  - **Default:** yes.
- **Q4-9 — Attribution:** autonomous approvals leave `decided_by=None` with service user/binding IDs on the decision row?
  - **Default:** yes.
- **Q4-10 — Enforce threshold:** should code include enforce mode but leave all orgs shadow-only until separate review?
  - **Default:** yes; no numeric threshold.
- **Q4-11 — Execution timing:** synchronous in the MCP request for minimal Phase 4?
  - **Default:** yes.

## Next action

- Implementation brief issued:
  [`IMPL-2026-11-21-mcp-phase4-autonomy.md`](IMPL-2026-11-21-mcp-phase4-autonomy.md).
- Implement T1→T4 in order, recording deviations in this plan before merge.
- Do not switch any organisation out of shadow mode as part of implementation.
