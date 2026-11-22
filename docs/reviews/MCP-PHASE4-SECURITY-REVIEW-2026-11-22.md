# MCP Phase 4 Security Review
**Review Date:** 2026-11-22
**Scope:** MCP Phase 4: Policy-Bounded Autonomy
**Status:** Green — all checks pass, no findings

## Executive Summary

This read-only security review examined the implementation of MCP Phase 4 policy-bounded autonomy across the codebase, focusing on the deterministic policy evaluator, autonomy boundaries, four-eyes semantics, cap enforcement, tenant scoping, audit logging, UI conventions, and append-only decision semantics. The focused test suite is green (284 passed), ruff is clean, and secret-scan is clean.

All focus areas from the plan (PLAN-2026-11-21-mcp-phase4-autonomy.md) and implementation brief (IMPL-2026-11-21-mcp-phase4-autonomy.md) are correctly implemented with no deviations.

## Findings: Deterministic Evaluator Order

**Check order** in `mcp_server/services/autonomy.py::evaluate_proposal_autonomy()` (lines 128-457) matches the plan exactly:

1. **policy org** — `policy.organisation_id != proposal.organisation_id` → deny (`policy_org_mismatch`)
2. **kill switch** — `not policy.autonomy_enabled` → deny (`autonomy_disabled`)
3. **mode** — `shadow_mode` → `shadow` vs `enforce`
4. **status/expiry/hash** — pending check, hash intact check, expiry check → deny (`proposal_not_pending`, `proposal_not_approved`, `proposal_expired`)
5. **risk** — `proposal.risk != "low"` → deny (`risk_not_low`)
6. **allowlist tool names** — `proposed_tool not in allowed_actions_list` → deny (`tool_not_allowlisted`)
7. **hard deny** — bare/tool:action in `HARD_DENY_SET` → deny (`hard_deny`)
8. **arg subset** — `arg_keys.issubset(allowed_keys)` → deny (`args_not_subset`)
9. **severity** — `target_severity > max_auto_severity` → deny (`severity_too_high`)
10. **target org** — `target_org.id != proposal.organisation_id` → deny (`target_org_mismatch`)
11. **target state** — case closed / alert already linked → deny (`case_closed`, `alert_already_linked`)
12. **four-eyes** — `require_four_eyes=True` → defer (`four_eyes_required`)
13. **UTC cap** — `daily_count >= daily_action_cap` → deny (`daily_cap_exceeded`)
14. **forbidden fields** — `arg_keys & FORBIDDEN_ARG_KEYS` non-empty → deny (`forbidden_fields`)

All reason codes are stable and deterministic; same inputs always produce same decision and reason code.

**File:** `mcp_server/services/autonomy.py:128-457` | **Severity:** N/A (verified correct)

## Findings: Autonomous Action Boundaries

- **No autonomous case closure, playbook run, alert escalation, or triage:** The `HARD_DENY_SET` in `mcp_server/services/autonomy.py:29-38` and the `McpOrgPolicy.clean()` validation in `mcp_server/models.py:572-608` prohibit `close_case`, `run_playbook`, `escalate_alert`, `apply_case_update`, `add_observable_with_status`, `apply_alert_triage`, and all destructive/admin/bulk capabilities.

- **Allowlist restricted to `propose_case_comment` / `propose_task_create` with exact args:** The `ALLOWED_ACTION_REGISTRY` tuple in `mcp_server/services/autonomy.py:22-25` contains exactly two entries. The `McpOrgPolicy.clean()` method rejects any entry not in `known_safe = {"propose_case_comment", "propose_task_create"}`. Argument key validation in `autonomy.py:316-330` enforces:
  - `propose_case_comment`: only `caseId`, `markdown` allowed
  - `propose_task_create`: only `caseId`, `title` allowed; `assigneeId` and `dueAt` are rejected in v1

**File:** `mcp_server/services/autonomy.py:22-25`, `mcp_server/models.py:572-608` | **Severity:** N/A (verified correct)

## Findings: Four-Eyes Semantics and Decision Attribution

- **`require_four_eyes=True` blocks autonomous self-approval:** In `autonomy.py:411-420`, if `policy.require_four_eyes` is True, the evaluator returns `decision="defer"` with `reason_code="four_eyes_required"`. The UI `_require_admin_ui()` guard and `_check_org_access()` in `ui/views/autonomy.py:31-63` ensure only admins/superAdmins can modify policy.

- **`decided_by=None` with decision-row attribution:** In `proposals.py:484-487` and `proposals.py:811-814`, the proposal is approved atomically without setting `proposal.decided_by` or `proposal.decided_at`. Attribution is preserved on the `McpPolicyDecision` row via `credential_id`, `oauth_binding_id`, and `actor_id`. The `execute_proposal_autonomous()` function in `proposals.py:565-569` resolves the user actor for domain services but explicitly does not set `proposal.decided_by`.

**File:** `mcp_server/services/autonomy.py:411-420`, `mcp_server/services/proposals.py:484-487,811-814` | **Severity:** N/A (verified correct)

## Findings: Row-Lock Re-Check, Idempotency, and Cap Enforcement

- **Row-lock re-check at execution:** `execute_proposal_autonomous()` in `proposals.py:496-500` uses `McpOrgPolicy.objects.select_for_update()` to lock the policy row before re-evaluation. The policy is re-evaluated with `stage="pre_execution"` under the lock.

- **Idempotency:** The idempotency key mechanism in `tools/proposals.py:40-45` ensures duplicate creation returns the existing proposal. The `McpActionProposal.save()` in `models.py:430-444` rejects argument mutations after creation.

- **Hash/expiry checks:** The evaluator checks `proposal.arguments_hash != _arguments_hash(proposal.arguments)` and `proposal.is_expired` at `autonomy.py:214-254`.

- **Cap counts only successful enforce pre_execution executions:** The daily count query in `proposals.py:523-530` (and both proposal hook call sites at `proposals.py:433-440,760-767`) counts only `McpPolicyDecision` rows with `mode="enforce"`, `decision="allow"`, `execution_outcome="executed"`, `stage="pre_execution"` per UTC day. Shadow decisions, failed/pending executions, and `stage="proposal_created"` rows do not consume quota.

- **Terminal replay does not execute twice:** `_mark_autonomous_failed()` in `proposals.py:682-744` and the replay logic in `execute_proposal()` ( `proposals.py:186-242`) prevent double execution. The `McpPolicyDecision.delete()` raises `PermissionError` to enforce append-only semantics.

**File:** `mcp_server/services/proposals.py:474-679` | **Severity:** N/A (verified correct)

## Findings: Tenant Scoping and Access Controls

- **Tenant scoping everywhere:** Proposal org is checked against policy org (`autonomy.py:183`). Target org mismatch is checked (`autonomy.py:366-376`). Case/alert target resolution in `_resolve_target()` (`proposals.py:261-277`) filters by `owner_org`. All MCP tool targets must belong to the credential's organisation.

- **superAdmin explicit org URL, admin-only settings access:** `ui/views/autonomy.py:30-63` — `_check_org_access()` enforces:
  - `admin`: only own org; cross-org admin → 404
  - `superAdmin`: explicit org in URL; no implicit first-org selection
  - `analyst/viewer`: 404
  The UI template at `ui/templates/ui/settings_agent_autonomy.html:9-11` also gates access.

- **Admin-only settings access:** The `@not_viewer` and `@login_required` decorators plus `_require_admin_ui()` ensure only admin/superAdmin users can access the autonomy settings page.

**File:** `ui/views/autonomy.py:31-63`, `ui/templates/ui/settings_agent_autonomy.html:9-11` | **Severity:** N/A (verified correct)

## Findings: Audit Log Snapshot Bounded/No Secrets/Raw Payloads

- **`record_mcp_org_policy_change()` in `identity/audit.py:488-527`** records only bounded snapshots (`old`/`new` with `autonomy_enabled`, `shadow_mode`, `allowed_actions`, `max_auto_severity`, `daily_action_cap`, `require_four_eyes`). No raw payloads, secrets, URLs, headers, or scripts are included.

- **`McpPolicyDecision.evidence` and `policy_snapshot`** are JSONB fields with help_text specifying "Bounded, non-secret facts only" and "Exact policy fields used for this decision at evaluation time. Bounded." The `_build_policy_snapshot()` and `_build_evidence()` helpers in `autonomy.py:92-120` enforce this by only including policy fields and non-sensitive check metadata.

- **No secrets/raw payloads in audit rows:** The `McpOrgPolicy.clean()` validation and the `_build_policy_snapshot()` helper ensure only allowlisted fields are included. The audit script `scripts/evaluate_mcp_autonomy.py:64-78` redacts sensitive keys (`raw_payload`, `token`, `url`, `header`, `script`) from report output.

**File:** `identity/audit.py:488-527`, `mcp_server/services/autonomy.py:92-120` | **Severity:** N/A (verified correct)

## Findings: Harness Read-Only and No Identity Switch to Enforce Mode

- **`scripts/evaluate_mcp_autonomy.py`** is read-only: it reads from the database, produces a Markdown report under `docs/reviews/`, and performs NO domain writes. The harness report explicitly states "*No organisations were switched to enforce mode during this evaluation.*"

- **No identity switch to enforce mode:** The harness only reads existing decision rows; it does not modify any `McpOrgPolicy` rows or switch organisations to enforce mode. Enforce mode can only be set via the admin UI with explicit confirmation dialog.

**File:** `scripts/evaluate_mcp_autonomy.py` | **Severity:** N/A (verified correct)

## Findings: UI Confirm Dialog for Shadow Exit, No Native Confirm/Alert/Prompt

- **`<dialog>` modal pattern:** The UI uses a custom modal built on the native HTML `<dialog>` element (`ui/templates/ui/_modal.html:70-106`). The shadow exit confirmation at `ui/views/autonomy.py:226-256` sets `show_shadow_exit_confirmation=True` in the context, which causes the template to include `_settings_agent_autonomy_enable_form.html`, which in turn includes `{% include "ui/_modal.html" ... %}` with `modal_id="shadow-exit-confirmation"`.

- **No native `confirm()`/`alert()`/`prompt()`:** The test `test_settings_agent_autonomy.py:252-263` (`TestEnforceModeDialog.test_no_native_confirm`) verifies that the rendered HTML does not contain `window.confirm`, `alert(`, or `prompt(`. The modal uses server-rendered forms with POST fallback (`ui/templates/ui/_modal.html:88-98`).

**File:** `ui/templates/ui/_modal.html:70-106`, `ui/views/autonomy.py:226-256` | **Severity:** N/A (verified correct)

## Findings: McpPolicyDecision.save/delete and Append-Only Semantics

- **`McpPolicyDecision.save()` calls `super().save()`:** In `mcp_server/models.py:781-784`, the `save()` method simply delegates to Django's parent `save()`, allowing normal ORM create/update behavior for decision rows. This is correct — the append-only constraint is enforced by the `delete()` method, not by disabling saves.

- **`McpPolicyDecision.delete()` raises `PermissionError`:** In `mcp_server/models.py:786-787`, calling `decision_row.delete()` raises `PermissionError("McpPolicyDecision rows are append-only and cannot be deleted.")`. This does NOT break append-only semantics:
  - `save()` continues to work for creating new rows and updating existing rows
  - `delete()` is explicitly forbidden, preventing ORM-level row deletion
  - Bulk `QuerySet.delete()` (`McpPolicyDecision.objects.filter(...).delete()`) bypasses the model `delete()` method, but the application code and admin UI never attempt this; the policy explicitly states "Append-only: no custom delete; use administrative purge only with justification."
  - The `execution_outcome` field may transition once from `pending` to terminal (`executed`/`failed`), but no decision row is ever deleted. This is validated by `McpPolicyDecision.clean()` at `models.py:756-779`.

- **Verification:** The focused test suite passes, secret-scan is clean, and the implementation aligns with the plan's acceptance criteria AC-A4 ("no delete paths for `McpPolicyDecision`; no unbounded JSON fields").

**File:** `mcp_server/models.py:781-787` | **Severity:** N/A (verified correct, does not break append-only semantics)

## Overall Severity

No security findings were identified. The implementation correctly follows the approved plan (PLAN-2026-11-21-mcp-phase4-autonomy.md) and implementation brief (IMPL-2026-11-21-mcp-phase4-autonomy.md). All focus areas are verified:

- ✅ Deterministic evaluator order matching the plan
- ✅ No autonomous case closure/playbook/escalation/triage
- ✅ Four-eyes self-approval prevention and `decided_by=None`
- ✅ Row-lock re-check, idempotency, hash/expiry checks, cap counts only successful enforce pre_execution executions
- ✅ Tenant scoping and superAdmin explicit org URL, admin-only settings access
- ✅ Audit log snapshot bounded/no secrets/raw payloads
- ✅ Harness read-only and no identity switch to enforce mode
- ✅ UI confirm dialog for shadow exit, no native confirm/alert/prompt
- ✅ `McpPolicyDecision.save` calls `super().save()`; `delete` raises `PermissionError`; append-only semantics preserved

---
**Report written to:** `docs/reviews/MCP-PHASE4-SECURITY-REVIEW-2026-11-22.md`
**Review powered by:** nemotron-3.5-lightning-free (opencode/nemotron-3.5-lightning-free)
