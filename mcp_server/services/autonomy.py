"""Policy registry and deterministic autonomy evaluator for MCP Phase 4.

Provides:
  - Server-side allowlist registry (exact action names, hard-deny set).
  - AutonomyDecision dataclass with bounded evidence/snapshot.
  - evaluate_proposal_autonomy() with deterministic ordered checks per PLAN-2026-11-21.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Server-side allowlist registry (hard-coded per Phase 4 approved defaults)
# ---------------------------------------------------------------------------

# Exact allowlist entries that org admins may whitelist.  Only these two
# structured entries are permitted in the default allowlist; the registry
# rejects any other entry (wildcards, prefixes, reserved, destructive).
ALLOWED_ACTION_REGISTRY: tuple[str, ...] = (
    "propose_case_comment",
    "propose_task_create",
)

# Hard-deny set: actions that are always rejected regardless of policy
# configuration.  These map to the "v1" autonomous execution prohibitions.
HARD_DENY_SET: frozenset[str] = frozenset(
    {
        "close_case",
        "run_playbook",
        "escalate_alert",
        "apply_case_update",
        "add_observable_with_status",
        "apply_alert_triage",
    }
)

# Forbidden argument keys that must never appear in autonomous execution,
# even if present in an allowlisted proposal's arguments.
FORBIDDEN_ARG_KEYS: frozenset[str] = frozenset(
    {
        "raw_payload",
        "url",
        "header",
        "script",
        "org",
        "owner",
        "role",
        "approval",
    }
)

# ---------------------------------------------------------------------------
# AutonomyDecision dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutonomyDecision:
    """Immutable result of autonomy evaluation.

    Attributes
    ----------
    decision: "allow" | "deny" | "defer"
        The final decision.
    reason_code: str
        Stable machine-readable reason (the first matching check in the
        ordered check sequence).
    mode: "shadow" | "enforce"
        The mode that was in effect (shadow vs enforce).
    evidence: dict[str, Any]
        Bounded, non-secret facts only.  Never raw payloads, tokens, URLs,
        headers, scripts, or unbounded case text.
    policy_snapshot: dict[str, Any]
        Exact policy fields used for this decision at evaluation time.
    """

    decision: str
    reason_code: str
    mode: str
    evidence: dict[str, Any]
    policy_snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Helper: build a bounded policy snapshot (no secrets/raw payloads)
# ---------------------------------------------------------------------------


def _build_policy_snapshot(policy: Any) -> dict[str, Any]:
    """Produce a bounded snapshot of McpOrgPolicy fields for audit/evidence."""
    return {
        "autonomy_enabled": policy.autonomy_enabled,
        "shadow_mode": policy.shadow_mode,
        "allowed_actions": list(policy.allowed_actions) if policy.allowed_actions else [],
        "max_auto_severity": policy.max_auto_severity,
        "daily_action_cap": policy.daily_action_cap,
        "require_four_eyes": policy.require_four_eyes,
    }


# ---------------------------------------------------------------------------
# Helper: build bounded evidence dict (no secrets/raw payloads)
# ---------------------------------------------------------------------------


def _build_evidence(decision_check: str, target_severity: int | None) -> dict[str, Any]:
    """Produce a bounded evidence dict for the autonomy decision.

    Only includes non-secret, non-sensitive facts.  No raw payloads, tokens,
    URLs, headers, or model keys.
    """
    evidence: dict[str, Any] = {
        "check_performed": decision_check,
    }
    if target_severity is not None:
        evidence["target_severity"] = target_severity
    return evidence


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------


def evaluate_proposal_autonomy(
    *,
    policy: Any,
    principal: Any,
    proposal: Any,
    target: Any | None,
    now: datetime,
    daily_count: int,
    stage: str = "proposal_created",
) -> AutonomyDecision:
    """Deterministic ordered autonomy evaluation per PLAN-2026-11-21.

    Check order (first matching check wins -> returns its reason_code):

    1. policy exists and belongs to proposal org
    2. autonomy_enabled=True (kill switch)
    3. shadow_mode determines mode
    4. proposal is pending, not expired, hash intact
    5. risk == "low"
    6. exact action allowlist match
    7. action not in hard-deny set
    8. argument keys subset of allowlist entry's schema
    9. target exists, org matches, target severity <= max_auto_severity
    10. target state valid (case not closed; alert not linked)
    11. four-eyes semantics resolved
    12. daily_count < daily_action_cap
    13. args contain no forbidden fields (raw payload, URL, header, script, org, owner, role, approval)

    Parameters
    ----------
    policy: McpOrgPolicy
        The organisation's policy row (guaranteed to exist by callers that
        follow the plan's lazy-creation pattern).
    principal: Principal
        The authenticated principal (credential + actor) that created the
        proposal.
    proposal: McpActionProposal
        The proposal row to evaluate.
    target: Any | None
        The resolved target model instance (Case, Alert, Observable, Task)
        or None if resolution has not yet occurred.
    now: datetime
        Current UTC time, used for expiry/hash checks.
    daily_count: int
        Number of successful enforce-mode pre_execution executed decisions
        for this org in the current UTC day (pre-computed by the caller).

    Returns
    -------
    AutonomyDecision
        Immutable decision with decision, reason_code, mode, evidence, and
        policy_snapshot.
    """

    # ---- 1. policy exists and belongs to proposal org ----
    if policy.organisation_id != proposal.organisation_id:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("policy_org_mismatch", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="policy_org_mismatch",
            mode="shadow" if policy.shadow_mode else "enforce",
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 2. autonomy_enabled=True (kill switch) ----
    if not policy.autonomy_enabled:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("autonomy_disabled", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="autonomy_disabled",
            mode="shadow" if policy.shadow_mode else "enforce",
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 3. shadow_mode determines mode ----
    mode = "shadow" if policy.shadow_mode else "enforce"

    # ---- 4. proposal is pending, not expired, hash intact ----
    # stage=proposal_created requires PENDING; stage=pre_execution requires APPROVED
    # hash intact check: compare arguments_hash against canonical hash of arguments
    from mcp_server.services.proposals import arguments_hash as _arguments_hash

    if proposal.arguments_hash != _arguments_hash(proposal.arguments):
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("arguments_hash_mismatch", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="arguments_hash_mismatch",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )
    if stage == "proposal_created" and proposal.status != "pending":
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("proposal_not_pending", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="proposal_not_pending",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )
    if stage == "pre_execution" and proposal.status != "approved":
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("proposal_not_approved", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="proposal_not_approved",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    if proposal.is_expired:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("proposal_expired", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="proposal_expired",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 5. risk == "low" ----
    if proposal.risk != "low":
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("risk_not_low", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="risk_not_low",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 6. exact tool_name allowlist match ----
    # policy.allowed_actions stores tool names (propose_case_comment, propose_task_create).
    # We match against proposal.tool_name, not proposal.action (which maps to the
    # canonical service action: propose_case_comment -> append_timeline_event,
    # propose_task_create -> create_task). Hard-deny checks both tool_name and action.
    allowed_actions_list: list[str] = list(policy.allowed_actions) if policy.allowed_actions else []
    proposed_tool = proposal.tool_name
    if proposed_tool not in allowed_actions_list:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("tool_not_allowlisted", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="tool_not_allowlisted",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 7. action not in hard-deny set ----
    # Hard-deny set may contain bare actions (e.g. "close_case") or
    # tool:action pairs (e.g. "propose_case_comment:append_timeline_event").
    # We deny if any of the three possible forms matches.
    bare_tool_match = proposed_tool in HARD_DENY_SET
    bare_action_match = proposal.action in HARD_DENY_SET
    combined_match = f"{proposed_tool}:{proposal.action}" in HARD_DENY_SET
    if bare_tool_match or bare_action_match or combined_match:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("hard_deny", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="hard_deny",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 8. argument keys subset of allowlist entry's schema ----
    # For Phase 4, allowlist entries are exact strings with known schemas.
    # propose_case_comment -> allowed keys: {caseId, markdown}
    # propose_task_create   -> allowed keys: {caseId, title}
    # We validate that the proposal's argument keys are a subset of the
    # hard-coded allowlist schemas for the exact action.
    args = proposal.arguments or {}
    arg_keys = set(args.keys())

    # Build the schema-specific allowed keys for the exact action
    # Schemas are keyed by tool_name (propose_case_comment, propose_task_create),
    # not by the canonical service action.
    action_schema: dict[str, set[str]] = {
        "propose_case_comment": {"caseId", "markdown"},
        "propose_task_create": {"caseId", "title"},
    }
    allowed_keys = action_schema.get(proposed_tool, set())
    if not arg_keys.issubset(allowed_keys):
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("args_not_subset", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="args_not_subset",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 9. target exists, org matches, target severity <= max_auto_severity ----
    if target is None:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("target_none", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="target_not_resolved",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # Resolve target severity from the target object
    target_severity: int | None = None
    if hasattr(target, "severity"):
        try:
            target_severity = int(target.severity)
        except (ValueError, TypeError):
            target_severity = None

    if target_severity is not None:
        max_allowed = policy.max_auto_severity
        if target_severity > max_allowed:
            snapshot = _build_policy_snapshot(policy)
            evidence = _build_evidence("severity_too_high", target_severity)
            return AutonomyDecision(
                decision="deny",
                reason_code="severity_too_high",
                mode=mode,
                evidence=evidence,
                policy_snapshot=snapshot,
            )

    # Also check: target org matches proposal org (tenant scoping)
    target_org = getattr(target, "owner_org", None) or getattr(target, "org", None)
    if target_org is not None and target_org.id != proposal.organisation_id:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("target_org_mismatch", target_severity)
        return AutonomyDecision(
            decision="deny",
            reason_code="target_org_mismatch",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 10. target state valid (case not closed; alert not linked) ----
    # Case must not be Closed; alert must not be already linked to a case
    target_type = getattr(proposal, "target_type", "") or ""
    if target_type == "case":
        case_status = getattr(target, "status", None)
        if case_status is not None and getattr(case_status, "stage", None) == "Closed":
            snapshot = _build_policy_snapshot(policy)
            evidence = _build_evidence("case_closed", target_severity)
            return AutonomyDecision(
                decision="deny",
                reason_code="case_closed",
                mode=mode,
                evidence=evidence,
                policy_snapshot=snapshot,
            )
    elif target_type == "alert":
        # Alert must not already have a case_id (i.e. not already linked)
        if target is not None and getattr(target, "case_id", None) is not None:
            snapshot = _build_policy_snapshot(policy)
            evidence = _build_evidence("alert_already_linked", target_severity)
            return AutonomyDecision(
                decision="deny",
                reason_code="alert_already_linked",
                mode=mode,
                evidence=evidence,
                policy_snapshot=snapshot,
            )

    # ---- 11. four-eyes semantics resolved ----
    # require_four_eyes=True means autonomy returns defer; the proposal stays
    # pending for a human. Agent self-approval does not satisfy four-eyes.
    # Four-eyes is enforced: if policy.require_four_eyes is True, autonomy defers.
    if policy.require_four_eyes:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("four_eyes_required", target_severity)
        return AutonomyDecision(
            decision="defer",
            reason_code="four_eyes_required",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 12. daily_count < daily_action_cap ----
    # Cap counts successful enforce-mode pre_execution executed decisions
    # for this org in the current UTC day. Shadow/failed/pending do NOT consume.
    if daily_count >= policy.daily_action_cap:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("daily_cap_exceeded", daily_count)
        return AutonomyDecision(
            decision="deny",
            reason_code="daily_cap_exceeded",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # ---- 13. args contain no forbidden fields ----
    forbidden_found = arg_keys & FORBIDDEN_ARG_KEYS
    if forbidden_found:
        snapshot = _build_policy_snapshot(policy)
        evidence = _build_evidence("forbidden_fields", None)
        return AutonomyDecision(
            decision="deny",
            reason_code="forbidden_fields",
            mode=mode,
            evidence=evidence,
            policy_snapshot=snapshot,
        )

    # All checks passed -> allow
    snapshot = _build_policy_snapshot(policy)
    evidence = _build_evidence("all_checks_passed", target_severity)
    return AutonomyDecision(
        decision="allow",
        reason_code="autonomy_allowed",
        mode=mode,
        evidence=evidence,
        policy_snapshot=snapshot,
    )
