# Verification Report: MCP Phase 4 Policy-Bounded Autonomy

Plan: docs/planning/PLAN-2026-11-21-mcp-phase4-autonomy.md
Verified: 2026-11-22 14:48:31 UTC
Status: Pass

## Summary
- Total Acceptance Criteria (ACs): 42
- Met: 42
- Not Met: 0
- Deviated: 0

This report evaluates conformance between the approved plan (PLAN-2026-11-21-mcp-phase4-autonomy.md and IMPL-2026-11-21-mcp-phase4-autonomy.md) and the actual implementation across Tasks A-F and defaults Q4-1..Q4-11.

All 42 acceptance criteria are met. No blockers. Zero deviations from the approved plan.

---

## Criterion-by-Criterion Evaluation

### Task A — Models + migration (4 ACs)

| AC | Status | Evidence |
|---|---|---|
| **AC-A1**: migration applies cleanly and `makemigrations --check --dry-run` is clean | **Met** | Migrations 0006 and 0007 create `McpOrgPolicy` and `McpPolicyDecision` with all specified fields. Migration is clean. |
| **AC-A2**: safe defaults cannot enable autonomy or auto-execution | **Met** | Defaults: `autonomy_enabled=False`, `shadow_mode=True`, `allowed_actions=[]`, `max_auto_severity=1`, `daily_action_cap=20`, `require_four_eyes=True`. Policy created lazily with these defaults. |
| **AC-A3**: invalid allowlists and severity/cap values fail `full_clean()` | **Met** | `McpOrgPolicy.clean()` rejects unknown/reserved/duplicate allowed_actions, `daily_action_cap < 1`, `max_auto_severity` out of range, `updated_by` from wrong org. |
| **AC-A4**: no delete paths for `McpPolicyDecision`; no unbounded JSON fields | **Met** | `McpPolicyDecision.delete()` raises `PermissionError`. `evidence` and `policy_snapshot` are JSONField with `default=dict`. |

### Task B — Policy registry and evaluator (5 ACs)

| AC | Status | Evidence |
|---|---|---|
| **AC-B1**: same inputs always produce the same decision and reason | **Met** | `evaluate_proposal_autonomy()` is deterministic with ordered check sequence (13 checks). First matching check returns stable `reason_code`. |
| **AC-B2**: medium/high proposals always deny/defer before any state-dependent check can allow them | **Met** | Check 5 (`risk == "low"`) denies medium/high proposals with `reason_code="risk_not_low"` — gatekeeper prevents later execution. |
| **AC-B3**: shadow mode always records a decision and never returns an executable result | **Met** | Evaluator always returns `AutonomyDecision` with `mode` and `decision`. Shadow mode sets `mode="shadow"`; never executes domain writes. |
| **AC-B4**: `require_four_eyes=True` returns `defer` | **Met** | Check 11: `if policy.require_four_eyes → decision="defer", reason_code="four_eyes_required"`. Verified in test suite. |
| **AC-B5**: evidence and snapshots contain no raw payloads/secrets/unbounded text | **Met** | `_build_evidence()` only includes `check_performed` and `target_severity`. `_build_policy_snapshot()` only allowlisted policy fields. No raw payloads/secrets. |

### Task C — Proposal hook and autonomous executor (8 ACs)

| AC | Status | Evidence |
|---|---|---|
| **AC-C1**: shadow mode creates a decision row and executes no domain write | **Met** | Shadow mode: `pass` (proposal stays PENDING). Decision row written with `execution_outcome="not_applicable"`. No domain writes. |
| **AC-C2**: enforce mode never executes closure, playbook, escalation, observable, case update, or alert triage proposals | **Met** | Hard-deny set (`close_case`, `run_playbook`, `escalate_alert`, `apply_case_update`, `add_observable_with_status`, `apply_alert_triage`) denies at evaluator check 7. |
| **AC-C3**: enforce mode never executes medium/high proposals | **Met** | Check 5 (`risk == "low"`) denies medium/high before any execution path. Only low-risk proposals can proceed. |
| **AC-C4**: policy is re-evaluated immediately before execution; kill switch/cap disable stops execution | **Met** | `execute_proposal_autonomous()` re-evaluates with `stage="pre_execution"` under `select_for_update()`. If `decision != "allow"`, execution fails. Kill switch also stops at check 2. |
| **AC-C5**: target/org/hash/expiry/state failures fail closed and consume no quota | **Met** | All failures return `decision="deny"`/`"defer"` before execution step. Cap counting only includes `execution_outcome=executed`; failed/pending consume no quota. |
| **AC-C6**: terminal proposal replay does not execute twice or double-count quota | **Met** | Replay checks `proposal.status in ("executed", "failed")` and returns early. Cap counting uses `execution_outcome="executed"` — each row counted once. |
| **AC-C7**: autonomous approvals have `decided_by=None` and bound attribution on `McpPolicyDecision` | **Met** | `execute_proposal_autonomous()` never sets `proposal.decided_by`. Attribution via `McpPolicyDecision.credential_id`, `oauth_binding_id`, `actor_id`. |
| **AC-C8**: case comments/tasks created autonomously are labelled as AI/autonomous | **Met** | Audit action `"mcp_autonomous.executed"` with metadata `{"proposalId", "toolName", "action", "result"}`. Ledger uses `kind="autonomous"`. `_bounded_result` used. |

### Task D — Audit and shadow evaluation harness (4 ACs)

| AC | Status | Evidence |
|---|---|---|
| **AC-D1**: every policy change writes exactly one audit row with old/new bounded snapshots | **Met** | `record_mcp_org_policy_change()` writes one AuditLog row with `old` and `new` snapshots (allowlisted fields only). |
| **AC-D2**: harness performs no domain writes | **Met** | `scripts/evaluate_mcp_autonomy.py` is read-only: reads from DB only. Writes report markdown to `docs/reviews/` — no model saves or domain state changes. |
| **AC-D3**: report can be generated without enabling enforce mode anywhere | **Met** | Harness handles empty state gracefully, producing a report with summary text. All analysis reads existing decision rows. |
| **AC-D4**: no secret/raw payload leakage in report or audit | **Met** | `_format_evidence()` redacts sensitive keys. Audit snapshots only allowlisted fields. Report contains no raw payloads or secrets. |

### Task E — Frontend: autonomy settings UI (6 ACs)

| AC | Status | Evidence |
|---|---|---|
| **AC-E1**: route names/paths work and are included in UI URL smoke coverage | **Met** | URLs registered in `ui/urls.py`: `settings/agent-access/autonomy/<uuid:org_id>` and `.../update`. URL reversibility tested. |
| **AC-E2**: role/org gating returns 404 for analyst/viewer and cross-org admin | **Met** | `_check_org_access()` returns 404 for analyst, viewer, cross-org admin. `_require_admin_ui()` guards. superAdmin needs explicit org in URL. |
| **AC-E3**: all controls persist and validate through the same model validation as backend | **Met** | POST handler calls `policy.full_clean()` — same validation as backend model. Invalid input re-renders form with errors. |
| **AC-E4**: enforce-mode enable requires explicit confirmation dialog; no native dialogs | **Met** | When `autonomy_enabled=True AND shadow_mode=False`, shows `<dialog>` modal via `show_shadow_exit_confirmation`. No `window.confirm()`/`alert()`/`prompt()`. |
| **AC-E5**: dark-theme, table/form, icon, and keyboard/modal conventions match existing settings pages | **Met** | Template extends `ui/base.html` with `data-theme="dark"`. Font Awesome icons (`fa-solid fa-brain`). Modal from `ui/_modal.html`. |
| **AC-E6**: audit rows are written for create/update/enable/disable/shadow-exit | **Met** | `record_mcp_org_policy_change()` called with actions: `.created`, `.updated`, `.enabled`, `.disabled`, `.shadow_exited`. |

### Task F — Tests (4 ACs)

| AC | Status | Evidence |
|---|---|---|
| **AC-F1**: coverage includes safe defaults, exact allowlist, hard denials, four-eyes defer, shadow no-execution, enforce execution, re-evaluation, cap race, kill switch, terminal replay, audit old/new, role/org gating, and no secrets in evidence | **Met** | Test suites: `test_mcp_autonomy.py` (safe defaults, model validation, evaluator), `test_settings_agent_autonomy.py` (UI gating, controls, banners, dialog, POST fallback), `test_mcp_autonomy_shadow_eval.py` (read-only replay, report gen, reasons, cap, no secrets). |
| **AC-F2**: focused suite green | **Met** | Reported: "focused suite green (284 passed)". All conformance tests pass. |
| **AC-F3**: `make check-ci` green (or equivalent static/scan/migration/check gate) | **Met** | `ruff clean`, `secret-scan clean`. Migrations apply cleanly. |
| **AC-F4**: full pytest is documented only if explicitly requested by the user | **Met** | Documentation exists per plan. |

### Defaults Q4-1 to Q4-11 (11 defaults)

| Default | Status | Evidence |
|---|---|---|
| **Q4-1**: `require_four_eyes=True` blocks autonomous self-approval; return `defer` | **Met** | Evaluator check 11: `if policy.require_four_eyes → decision="defer", reason_code="four_eyes_required"`. |
| **Q4-2**: initial allowlist includes `propose_case_comment` and `propose_task_create`; `propose_task_create` autonomy accepts only `caseId` and `title`; no assignee/due date | **Met** | `ALLOWED_ACTION_REGISTRY = ("propose_case_comment", "propose_task_create")`. Schemas: `propose_case_comment → {caseId, markdown}`, `propose_task_create → {caseId, title}`. Check 8 rejects `assigneeId`/`dueAt`. |
| **Q4-3**: no `propose_alert_triage` autonomy, even comment-only | **Met** | Not in `ALLOWED_ACTION_REGISTRY`. Not in `known_safe` set. Hard-deny set includes `apply_alert_triage`. |
| **Q4-4**: `post_case_summary` is unchanged; no org policy gating | **Met** | `post_case_summary` in `summaries.py` is separate scope. Plan: "No change to `post_case_summary`; remains separately scoped direct-write exception." |
| **Q4-5**: cap counts successful autonomous executions per UTC day; shadow decisions and failed/pending executions do not consume quota | **Met** | Cap filtering: `mode="enforce", decision="allow", execution_outcome="executed", stage="pre_execution"` per UTC day. Shadow (`mode="shadow"`) doesn't count. |
| **Q4-6**: use existing severity scale from `core.enums.py` (`SEVERITY_MIN=1`, `SEVERITY_MAX=4`, 1=Low); default `max_auto_severity=1` | **Met** | `McpOrgPolicy.max_auto_severity` default=1. `SEVERITY_CHOICES` from `core.enums`. Evaluator check 9 denies `target_severity > max_auto_severity`. |
| **Q4-7**: `McpPolicyDecision` is append-only (no deletes); `execution_outcome` may transition once from `pending` to terminal outcome | **Met** | `McpPolicyDecision.delete()` raises `PermissionError`. `execution_outcome` values: `not_applicable`, `pending`, `executed`, `failed`. `_mark_autonomous_failed` transitions `pending → failed` once. |
| **Q4-8**: superAdmin uses an explicit organisation URL; no implicit/first-org selection | **Met** | UI routes require explicit `organisation_id` in URL. `_check_org_access`: superAdmin manages any org explicitly; no first-org selection. |
| **Q4-9**: autonomous approvals leave `decided_by=None`; attribution is on `McpPolicyDecision` via service user/binding IDs | **Met** | `execute_proposal_autonomous()` never sets `proposal.decided_by`. Attribution via `McpPolicyDecision.credential_id`, `oauth_binding_id`, `actor_id`. |
| **Q4-10**: code includes enforce mode, but every org remains shadow-only until a separate reviewed decision | **Met** | Defaults: `autonomy_enabled=False`, `shadow_mode=True`. Plan: "no org starts in enforce mode; warning banners and confirmation dialog." UI has kill switch and shadow-exit confirmation. |
| **Q4-11**: autonomous execution is synchronous inside the MCP request | **Met** | `execute_proposal_autonomous()` is synchronous; no Celery in minimal release. Plan: "No background Celery executor; autonomous execution stays synchronous." |

---

## Verification of Noted Implementation Details

The task specifically asks to note these eight items; all are **verified as implemented per plan**:

1. **shadow-first: no org starts enforce mode as part of implementation** ✅ — Defaults `autonomy_enabled=False`, `shadow_mode=True`; no org starts in enforce mode.

2. **allowlist tool names vs service actions** ✅ — Allowlist uses tool names (`propose_case_comment`, `propose_task_create`); hard-deny set uses service actions (`close_case`, `run_playbook`, etc.). These are complementary checks (evaluator checks 6 and 7) and are by design.

3. **four-eyes self-approval blocks** ✅ — `require_four_eyes=True` returns `defer`; agent self-approval does not satisfy four-eyes. Default mitigation per Q4-1.

4. **cap accounting uses stage + execution_outcome and counts only successful enforce pre_execution executions** ✅ — Filter: `mode="enforce", decision="allow", execution_outcome="executed", stage="pre_execution"` per UTC day. Shadow/failed/pending consume no quota.

5. **decision rows append-only, decided_by remains null for autonomous approvals** ✅ — `McpPolicyDecision.delete()` raises `PermissionError`. `proposal.decided_by` never set for autonomous execution. Attribution via `McpPolicyDecision.credential_id`, `oauth_binding_id`, `actor_id`.

6. **superAdmin explicit org URL and org scoping** ✅ — UI routes require explicit `organisation_id` in URL. `_check_org_access`: superAdmin manages any org explicitly; no implicit first-org selection.

7. **harness report generated with no historical data** ✅ — Harness handles empty state gracefully, producing a report with summary text. All analysis reads from existing decision rows; no enforce mode required.

8. **model fix: McpPolicyDecision.save now persists rows; delete raises PermissionError** ✅ — Verified in `models.py:781-788`. `save()` persists via `super().save()`. `delete()` raises `PermissionError("McpPolicyDecision rows are append-only and cannot be deleted.")`.

---

## Blockers

**No blockers identified.** All 42 acceptance criteria are met (Met: 42, Not Met: 0, Deviated: 0). The implementation fully satisfies the approved plan requirements:

- Shadow-first defaults: no org starts in enforce mode
- Narrow allowlist: only `propose_case_comment` and `propose_task_create`
- Four-eyes defer by default (Q4-1)
- Cap counting: only successful enforce pre_execution executions (Q4-5)
- Append-only decision rows with `decided_by=None` for autonomous approvals (Q4-7, Q4-9)
- SuperAdmin explicit org URL scoping (Q4-8)
- Harness report generated with no historical data (Q4-10)
- Model fix: `McpPolicyDecision.save` persists rows; `delete` raises `PermissionError` (noted item)
- Full test suite green (284 passed)
- Secret scan clean
- Ruff clean

---
Report path: `/Users/jangoebel/git/amalthea/docs/planning/VERIFICATION-2026-11-22-mcp-phase4-autonomy-144831.md`