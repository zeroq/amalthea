# IMPL-2026-11-21 — MCP Phase 4 Policy-Bounded Autonomy

Status: **Ready for handoff. Defaults accepted by user 2026-11-21.**
Plan: [`PLAN-2026-11-21-mcp-phase4-autonomy.md`](PLAN-2026-11-21-mcp-phase4-autonomy.md)
Backlog: `TODO.md §22.6`

## Scope

Implement `McpOrgPolicy`, deterministic shadow evaluation, a narrow low-risk autonomous executor, the autonomy
settings UI, audit/evidence logging, tests, and a read-only shadow evaluation harness.

Do **not** implement any autonomous behavior outside the exact allowlist below. Do **not** switch any organisation out
of shadow mode as part of implementation.

## Approved decisions

- Q4-1: `require_four_eyes=True` blocks autonomous self-approval; return `defer`.
- Q4-2: initial allowlist includes `propose_case_comment` and `propose_task_create`.
- Q4-2 detail: `propose_task_create` autonomy accepts only `caseId` and `title`; reject `assigneeId`, `dueAt`, and any
  other fields for autonomous execution.
- Q4-3: no `propose_alert_triage` autonomy, even comment-only.
- Q4-4: `post_case_summary` is unchanged; no org policy gating.
- Q4-5: cap counts successful autonomous executions per UTC day; shadow decisions and failed/pending executions do not
  consume quota.
- Q4-6: use existing severity scale from `core.enums.py` (`SEVERITY_MIN=1`, `SEVERITY_MAX=4`, 1=Low); default
  `max_auto_severity=1`.
- Q4-7: `McpPolicyDecision` is append-only (no deletes); `execution_outcome` may transition once from `pending` to a
  terminal outcome.
- Q4-8: superAdmin uses an explicit organisation URL; no implicit/first-org selection.
- Q4-9: autonomous approvals leave `decided_by=None`; attribution is on `McpPolicyDecision` via service user/binding IDs.
- Q4-10: code includes enforce mode, but every org remains shadow-only until a separate reviewed decision.
- Q4-11: autonomous execution is synchronous inside the MCP request.

## Global constraints

- Reuse identity scoping, role rules, audit, existing domain services, and the existing case ledger.
- Never put the autonomy hook in `mcp_server/tools/base.py::run_write()`; it has no proposal identity.
- Never trust model output, tool arguments, case text, or comments as approval evidence.
- Never put raw payloads, attachments, tokens, URLs, headers, scripts, model keys, or unbounded case text into evidence,
  policy snapshots, logs, or audit rows.
- Keep REST/UI/WebSocket contracts unchanged except for the new settings page.
- No `--no-verify`. No destructive `git checkout` / `git reset`. Do not touch stash `b009a828`.
- Use `.venv/bin/python`, `.venv/bin/pytest`; full pytest only when explicitly requested.
- Rerun secret scan after repo changes.
- Record any deviation in the approved plan before merge.

---

## Task A — Backend: models and migration

**Owner:** `django-backend`  
**Files:**
- `mcp_server/models.py`
- `mcp_server/migrations/0006_mcporgpolicy_mcppolicydecision.py` (or generated equivalent)

**Work:**
- Add `McpOrgPolicy`:
  - `organisation` OneToOne to `Organisation`, `PROTECT`, `related_name="mcp_policy"`.
  - `autonomy_enabled=False`, `shadow_mode=True`, `max_auto_severity=1`, `daily_action_cap=20`,
    `require_four_eyes=True`, `allowed_actions=[]`.
  - `updated_by` nullable FK to `User`, `SET_NULL`; `updated_at` auto.
  - Validation: one policy per org; allowed actions from server registry only; no wildcards/unknown/reserved/duplicate
    actions; `daily_action_cap >= 1`; `max_auto_severity` within `SEVERITY_MIN..SEVERITY_MAX`; `updated_by.org_id`
    matches policy org.
- Add `McpPolicyDecision`:
  - org/policy/proposal links; `tool_name`, `action`, `target_type`, `target_id`, `risk`.
  - `mode` (`shadow|enforce`), `decision` (`allow|deny|defer`), `reason_code`.
  - `stage` (`proposal_created|pre_execution`).
  - `execution_outcome` (`not_applicable|pending|executed|failed`).
  - bounded `evidence`, bounded `policy_snapshot`.
  - attribution: `credential_id`, `oauth_binding_id`, `actor_id`.
  - indexes from the plan, including the UTC-day cap index.

**Acceptance criteria:**
- AC-A1: migration applies cleanly and `makemigrations --check --dry-run` is clean.
- AC-A2: safe defaults cannot enable autonomy or auto-execution.
- AC-A3: invalid allowlists and severity/cap values fail `full_clean()`.
- AC-A4: no delete paths for `McpPolicyDecision`; no unbounded JSON fields.

---

## Task B — Backend: policy registry and evaluator

**Owner:** `django-backend`  
**Files:**
- New: `mcp_server/services/autonomy.py`
- Extend if needed: `mcp_server/scopes.py`, `mcp_server/limits.py`

**Work:**
- Add server-side allowlist registry with exact entries:
  - `propose_case_comment` / `append_timeline_event` / allowed keys `{caseId, markdown}`.
  - `propose_task_create` / `create_task` / allowed keys `{caseId, title}`.
- Add hard-deny set: `close_case`, `run_playbook`, `escalate_alert`, `apply_case_update`,
  `add_observable_with_status`, `apply_alert_triage`, plus all destructive/admin/bulk/token/org/user/playbook-authoring
  capabilities.
- Add `AutonomyDecision` dataclass and `evaluate_proposal_autonomy()` per plan.
- Ordered checks: policy/org → enabled → mode → pending/not expired/hash intact → low risk → exact allowlist → hard deny
  → argument-key subset → target org/state/severity → four-eyes → daily cap → forbidden fields.
- Return stable `reason_code`s; evidence only bounded non-secret facts.
- Cap count queries `McpPolicyDecision` rows for org, UTC day, `mode=enforce`, `decision=allow`,
  `stage=pre_execution`, `execution_outcome=executed`.

**Acceptance criteria:**
- AC-B1: same inputs always produce the same decision and reason.
- AC-B2: medium/high proposals always deny/defer before any state-dependent check can allow them.
- AC-B3: shadow mode always records a decision and never returns an executable result.
- AC-B4: `require_four_eyes=True` returns `defer`.
- AC-B5: evidence and snapshots contain no raw payloads/secrets/unbounded text.

---

## Task C — Backend: proposal hook and autonomous executor

**Owner:** `django-backend`  
**Files:**
- `mcp_server/tools/proposals.py`
- `mcp_server/services/proposals.py`
- `mcp_server/services/autonomy.py`
- `mcp_server/audit.py` or `identity/audit.py` where existing MCP audit helpers live

**Work:**
- After a new proposal row is created by `propose_case_comment` or `propose_task_create`, call the autonomy hook once.
- Existing proposals returned by idempotency must not be re-evaluated as newly created.
- Write a `proposal_created` decision row.
- In shadow mode, leave proposal `pending`; return the normal proposal handle unchanged.
- In enforce mode with `allow`:
  - atomically approve without setting `decided_by` to a human or service user;
  - call a dedicated `execute_proposal_autonomous()`;
  - re-evaluate policy with `stage=pre_execution` under policy-row lock;
  - re-resolve target under org scoping;
  - re-check expiry, hash, state, severity, cap, and allowlist;
  - call the same domain services as the human executor;
  - set `execution_outcome=executed|failed` once;
  - write bounded result, ledger/audit records, and keep `decided_by=None`.
- Do not emit human approval audit actions for autonomous execution; use distinct autonomous audit actions.
- Leave `get_action_proposal`, `post_case_summary`, and human `/approvals` execution unchanged.

**Acceptance criteria:**
- AC-C1: shadow mode creates a decision row and executes no domain write.
- AC-C2: enforce mode never executes closure, playbook, escalation, observable, case update, or alert triage proposals.
- AC-C3: enforce mode never executes medium/high proposals.
- AC-C4: policy is re-evaluated immediately before execution; kill switch/cap disable stops execution.
- AC-C5: target/org/hash/expiry/state failures fail closed and consume no quota.
- AC-C6: terminal proposal replay does not execute twice or double-count quota.
- AC-C7: autonomous approvals have `decided_by=None` and bound attribution on `McpPolicyDecision`.
- AC-C8: case comments/tasks created autonomously are labelled as AI/autonomous in the same manner as existing MCP
  provenance conventions.

---

## Task D — Backend: policy-change audit and shadow evaluation harness

**Owner:** `django-backend`  
**Files:**
- `identity/audit.py`
- New: `scripts/evaluate_mcp_autonomy.py`
- New report path under `docs/reviews/` (for example `docs/reviews/MCP-PHASE4-SHADOW-EVAL-2026-11-21.md`)

**Work:**
- Add `record_mcp_org_policy_change(...)` with actions such as `mcp_org_policy.created`, `.updated`, `.enabled`,
  `.disabled`, `.shadow_exited`.
- Audit only allowlisted old/new snapshots.
- Add a read-only harness that replays historical MCP proposals/decisions in shadow mode and writes a report with:
  - action distribution;
  - allow/deny/defer reasons;
  - estimated autonomous volume;
  - daily cap hits;
  - example false-positive candidates.

**Acceptance criteria:**
- AC-D1: every policy change writes exactly one audit row with old/new bounded snapshots.
- AC-D2: harness performs no domain writes.
- AC-D3: report can be generated without enabling enforce mode anywhere.
- AC-D4: no secret/raw payload leakage in report or audit.

---

## Task E — Frontend: autonomy settings UI

**Owner:** `django-frontend`  
**Files:**
- New: `ui/views/autonomy.py`
- `ui/views/__init__.py`
- `ui/urls.py`
- New templates:
  - `ui/templates/ui/settings_agent_autonomy.html`
  - `ui/templates/ui/_settings_agent_autonomy_form.html`
  - `ui/templates/ui/_settings_agent_autonomy_enable_form.html`
- Modify:
  - `ui/templates/ui/_settings_nav.html`
  - `ui/templates/ui/settings_agents.html` (link from Agent access)

**Routes:**
- `settings/agent-access/autonomy/<uuid:organisation_id>` → `ui-settings-agent-autonomy`
- `settings/agent-access/autonomy/<uuid:organisation_id>/update` → `ui-settings-agent-autonomy-update`

**Work:**
- Guards: `login_required`, `not_viewer`, `_require_admin_ui()`.
- admin: own org only; other org → 404.
- superAdmin: explicit org from URL; no implicit selection.
- analyst/viewer: 404.
- Controls: kill switch, shadow mode, allowlist checkboxes from server registry, max auto severity, daily cap,
  four-eyes toggle.
- Show prominent warning when autonomy enabled; stronger warning when enabled and shadow disabled.
- Use custom `<dialog>` confirmation via the shared modal pattern for enforce-mode enablement. No native
  `confirm()`/`alert()`/`prompt()`.
- Plain POST fallback; HTMX optional.

**Acceptance criteria:**
- AC-E1: route names/paths work and are included in UI URL smoke coverage.
- AC-E2: role/org gating returns 404 for analyst/viewer and cross-org admin.
- AC-E3: all controls persist and validate through the same model validation as backend.
- AC-E4: enforce-mode enable requires explicit confirmation dialog; no native dialogs.
- AC-E5: dark-theme, table/form, icon, and keyboard/modal conventions match existing settings pages.
- AC-E6: audit rows are written for create/update/enable/disable/shadow-exit.

---

## Task F — Tests

**Owner:** `qa-tester`  
**Files:**
- New:
  - `tests/conformance/test_mcp_autonomy.py`
  - `tests/conformance/test_settings_agent_autonomy.py`
  - `tests/conformance/test_mcp_autonomy_shadow_eval.py`
- Extend:
  - `tests/conformance/test_mcp_proposals.py`
  - `tests/conformance/test_mcp_scopes_phase2.py`
  - `tests/conformance/test_mcp_limits.py`
  - `tests/conformance/test_approvals_ui.py`
  - `tests/conformance/test_mcp_deployment.py`
  - `tests/conformance/test_tenant_scoping.py`
  - `tests/conformance/test_ui_urls_smoke.py`
  - `tests/conformance/test_ui_icons.py`
  - `tests/conformance/test_ui_table_markup.py`
  - `tests/conformance/test_agent_access_ui_markup.py`
  - `tests/conformance/test_settings_agent_access.py`

**Acceptance criteria:**
- AC-F1: coverage includes safe defaults, exact allowlist, hard denials, four-eyes defer, shadow no-execution,
  enforce execution, re-evaluation, cap race, kill switch, terminal replay, audit old/new, role/org gating, and no
  secrets in evidence.
- AC-F2: focused suite green.
- AC-F3: `make check-ci` green (or equivalent static/scan/migration/check gate).
- AC-F4: full pytest is documented only if explicitly requested by the user.

**Suggested focused command:**
```bash
.venv/bin/python -m pytest -q \
  tests/conformance/test_mcp_autonomy.py \
  tests/conformance/test_settings_agent_autonomy.py \
  tests/conformance/test_mcp_autonomy_shadow_eval.py \
  tests/conformance/test_mcp_proposals.py \
  tests/conformance/test_mcp_scopes_phase2.py \
  tests/conformance/test_mcp_limits.py \
  tests/conformance/test_approvals_ui.py \
  tests/conformance/test_mcp_deployment.py \
  tests/conformance/test_tenant_scoping.py \
  tests/conformance/test_ui_urls_smoke.py \
  tests/conformance/test_ui_icons.py \
  tests/conformance/test_ui_table_markup.py \
  tests/conformance/test_agent_access_ui_markup.py \
  tests/conformance/test_settings_agent_access.py
```

---

## Task G — Security review and verification

**Owners:** `security-auditor`, `verifier`  
**Files:**
- New report under `docs/security/`
- New verification report under `docs/reviews/` or `docs/planning/`

**Acceptance criteria:**
- AC-G1: security review specifically covers self-approval, cap race, kill switch, target re-resolution, raw payload
  leakage, and approval-boundary erosion.
- AC-G2: verifier evaluates every task AC above as Met/Not Met/Deviated with evidence.
- AC-G3: 0 blockers before merge.
- AC-G4: any deviation is recorded in the approved plan and linked from verification.

---

## Dependencies and order

1. Task A before B/C/E.
2. Task B before C/D.
3. Task C before D harness can replay meaningful decisions, but D can be drafted in parallel.
4. Task E after A/B registry is stable.
5. Task F continuously; Task G after implementation and green gates.
6. No deployment/config change enables enforce mode.

## Definition of done

- All Task A–G ACs Met.
- Focused tests and static/CI gate green.
- Secret scan clean.
- No organisation enabled for autonomy in shipped defaults.
- Plan updated with any deviations (none recorded).
- TODO §22.6 closed after independent verification PASS 2026-11-22:
  `docs/planning/VERIFICATION-2026-11-22-mcp-phase4-autonomy-144831.md`.
- Security review PASS 2026-11-22: `docs/reviews/MCP-PHASE4-SECURITY-REVIEW-2026-11-22.md`.
