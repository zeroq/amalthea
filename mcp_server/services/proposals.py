"""Proposal state machine and executor for MCP Phase 2.

The executor is deliberately thin: it re-reads the locked proposal, re-checks the
tenant/role/state invariants, delegates the real write to an existing domain
service, and stores only a bounded, non-secret result.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from identity.models import AuditLog, Organisation, User
from mcp_server.models import McpActionProposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical arguments and transition guards
# ---------------------------------------------------------------------------


def canonical_arguments(arguments: dict[str, Any] | None) -> str:
    """Canonical JSON used for both creation and execution-time hash checks."""
    return json.dumps(arguments or {}, sort_keys=True, default=str, separators=(",", ":"))


def arguments_hash(arguments: dict[str, Any] | None) -> str:
    return hashlib.sha256(canonical_arguments(arguments).encode("utf-8")).hexdigest()


def can_transition_to(proposal: McpActionProposal, new_status: str) -> bool:
    """Check the status state machine only."""
    transitions: dict[str, set[str]] = {
        McpActionProposal.StatusChoices.PENDING: {
            McpActionProposal.StatusChoices.APPROVED,
            McpActionProposal.StatusChoices.DECLINED,
            McpActionProposal.StatusChoices.EXPIRED,
        },
        McpActionProposal.StatusChoices.APPROVED: {
            McpActionProposal.StatusChoices.EXECUTED,
            McpActionProposal.StatusChoices.FAILED,
            McpActionProposal.StatusChoices.EXPIRED,
        },
        McpActionProposal.StatusChoices.EXPIRED: set(),
        McpActionProposal.StatusChoices.DECLINED: set(),
        McpActionProposal.StatusChoices.EXECUTED: set(),
        McpActionProposal.StatusChoices.FAILED: set(),
    }
    return new_status in transitions.get(proposal.status, set())


def _decision_role_allowed(proposal: McpActionProposal, user: User | None) -> bool:
    """Human decision/execute role rule, including tenant and four-eyes checks."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "role", "") == "viewer":
        return False
    # No cross-org path: even superAdmin acts only within their own organisation.
    if user.org_id != proposal.organisation_id:
        return False
    if proposal.requested_by_id == user.id and proposal.risk != "low":
        return False
    if proposal.risk in ("medium", "high"):
        return user.role in ("admin", "superAdmin")
    if proposal.risk == "low":
        return user.role in ("analyst", "admin", "superAdmin")
    return False


def transition_allowed(
    proposal: McpActionProposal, new_status: str, *, by_user: User | None = None
) -> bool:
    """Full transition check: state machine, expiry, tenant, role and four-eyes rules."""
    if not can_transition_to(proposal, new_status):
        return False
    if proposal.is_expired:
        return False
    if new_status in (
        McpActionProposal.StatusChoices.EXPIRED,
        McpActionProposal.StatusChoices.FAILED,
    ):
        return True
    return _decision_role_allowed(proposal, by_user)


def _audit(
    proposal: McpActionProposal,
    action: str,
    actor: User | None,
    details: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "proposal_id": str(proposal.id),
        "tool_name": proposal.tool_name,
        "action": proposal.action,
        "risk": proposal.risk,
    }
    if details:
        payload.update(details)
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=proposal.organisation,
        target_type="McpActionProposal",
        target_id=str(proposal.id),
        action=action,
        details=payload,
    )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def approve_proposal(proposal: McpActionProposal, by_user: User) -> McpActionProposal:
    """Approve a pending proposal. Fails closed on stale/expired/unauthorised rows."""
    if not transition_allowed(proposal, McpActionProposal.StatusChoices.APPROVED, by_user=by_user):
        raise ValidationError("Proposal cannot be approved.", code="conflict")

    with db_transaction.atomic():
        proposal = McpActionProposal.objects.select_for_update().get(pk=proposal.pk)
        if not transition_allowed(
            proposal, McpActionProposal.StatusChoices.APPROVED, by_user=by_user
        ):
            raise ValidationError("Proposal status changed while approving.", code="conflict")
        proposal.status = McpActionProposal.StatusChoices.APPROVED
        proposal.decided_by = by_user
        proposal.decided_at = timezone.now()
        proposal.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        _audit(proposal, "mcp_proposal.approved", by_user)
    return proposal


def decline_proposal(
    proposal: McpActionProposal, by_user: User, *, note: str | None = None
) -> McpActionProposal:
    """Decline a pending proposal. Fails closed on stale/expired/unauthorised rows."""
    if not transition_allowed(proposal, McpActionProposal.StatusChoices.DECLINED, by_user=by_user):
        raise ValidationError("Proposal cannot be declined.", code="conflict")

    with db_transaction.atomic():
        proposal = McpActionProposal.objects.select_for_update().get(pk=proposal.pk)
        if not transition_allowed(
            proposal, McpActionProposal.StatusChoices.DECLINED, by_user=by_user
        ):
            raise ValidationError("Proposal status changed while declining.", code="conflict")
        proposal.status = McpActionProposal.StatusChoices.DECLINED
        proposal.decided_by = by_user
        proposal.decided_at = timezone.now()
        proposal.result = {"decision": "declined"}
        if note:
            proposal.result["note"] = note[:200]
        proposal.save(update_fields=["status", "decided_by", "decided_at", "result", "updated_at"])
        _audit(proposal, "mcp_proposal.declined", by_user)
    return proposal


def expire_proposal(proposal: McpActionProposal) -> McpActionProposal:
    """Expire a pending or approved proposal (system action)."""
    with db_transaction.atomic():
        proposal = McpActionProposal.objects.select_for_update().get(pk=proposal.pk)
        if not can_transition_to(proposal, McpActionProposal.StatusChoices.EXPIRED):
            return proposal
        proposal.status = McpActionProposal.StatusChoices.EXPIRED
        proposal.save(update_fields=["status", "updated_at"])
        _audit(proposal, "mcp_proposal.expired", None)
    return proposal


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_proposal(proposal: McpActionProposal, by_user: User) -> dict[str, Any]:
    """Execute an approved proposal once, or replay its terminal result."""
    if proposal.status in (
        McpActionProposal.StatusChoices.EXECUTED,
        McpActionProposal.StatusChoices.FAILED,
        McpActionProposal.StatusChoices.DECLINED,
        McpActionProposal.StatusChoices.EXPIRED,
    ):
        return {
            "status": proposal.status,
            "result": proposal.result or None,
            "target_id": proposal.target_id,
        }
    if proposal.status != McpActionProposal.StatusChoices.APPROVED:
        raise ValidationError("Only approved proposals may be executed.", code="conflict")

    with db_transaction.atomic():
        proposal = McpActionProposal.objects.select_for_update().get(pk=proposal.pk)
        if proposal.status != McpActionProposal.StatusChoices.APPROVED:
            return {
                "status": proposal.status,
                "result": proposal.result or None,
                "target_id": proposal.target_id,
            }
        if proposal.is_expired:
            return _mark_failed(proposal, by_user, "proposal_expired")
        if proposal.arguments_hash != arguments_hash(proposal.arguments):
            return _mark_failed(proposal, by_user, "arguments_hash_mismatch")
        if not _decision_role_allowed(proposal, by_user):
            return _mark_failed(proposal, by_user, "insufficient_role")

        target = _resolve_target(proposal.target_type, proposal.target_id, proposal.organisation)
        if target is None or not _check_org_target_match(target, proposal.organisation):
            return _mark_failed(proposal, by_user, "target_not_found")
        if not _check_target_state(proposal, target):
            return _mark_failed(proposal, by_user, "target_state")

        try:
            # Nested atomic: a domain-service failure rolls back only the service call,
            # not the terminal failed state we need to persist.
            with db_transaction.atomic():
                result = _execute_service_action(proposal, target, by_user)
        except Exception:
            logger.exception("MCP proposal execution failed id=%s", proposal.id)
            return _mark_failed(proposal, by_user, "execution_failed")

        bounded = _bounded_result(result)
        proposal.status = McpActionProposal.StatusChoices.EXECUTED
        proposal.executed_at = timezone.now()
        proposal.result = bounded
        proposal.save(update_fields=["status", "executed_at", "result", "updated_at"])
        _audit(proposal, "mcp_proposal.executed", by_user)

    if proposal.target_type == "case":
        _emit_case_execution_event(proposal, target, bounded, by_user)

    return {"status": "executed", "result": bounded, "target_id": str(target.id)}


def _mark_failed(proposal: McpActionProposal, by_user: User, code: str) -> dict[str, Any]:
    """Mark a proposal failed once inside the current atomic block."""
    result = {"error": code}
    proposal.status = McpActionProposal.StatusChoices.FAILED
    proposal.executed_at = timezone.now()
    proposal.result = result
    proposal.save(update_fields=["status", "executed_at", "result", "updated_at"])
    _audit(proposal, "mcp_proposal.failed", by_user, {"error": code})
    return {"status": "failed", "result": result, "target_id": proposal.target_id}


# ---------------------------------------------------------------------------
# Target resolution and domain-service adapters
# ---------------------------------------------------------------------------


def _resolve_target(target_type: str, target_id: str, org: Organisation) -> Any:
    from alerts.models import Alert
    from cases.models import Case, Task
    from observables.models import Observable

    try:
        if target_type == "case":
            return Case.objects.get(pk=target_id, owner_org=org)
        if target_type == "alert":
            return Alert.objects.get(pk=target_id, owner_org=org)
        if target_type == "observable":
            return Observable.objects.get(pk=target_id, owner_org=org)
        if target_type == "task":
            return Task.objects.get(pk=target_id, case__owner_org=org)
    except Exception:
        return None
    return None


def _check_org_target_match(target: Any, org: Organisation) -> bool:
    if hasattr(target, "owner_org_id"):
        return target.owner_org_id == org.id
    case = getattr(target, "case", None)
    if case is not None:
        return case.owner_org_id == org.id
    return False


def _check_target_state(proposal: McpActionProposal, target: Any) -> bool:
    """Fail closed on deleted/closed targets; closure proposals are the exception."""
    if proposal.action == "close_case":
        return True
    if proposal.target_type == "case":
        return getattr(getattr(target, "status", None), "stage", "") != "Closed"
    if proposal.target_type == "alert":
        return target.case_id is None
    return True


def _resolve_user(identifier: Any, org: Organisation) -> User | None:
    if not identifier:
        return None
    return (
        User.objects.filter(org=org, is_active=True)
        .filter(pk=identifier if _is_uuid(identifier) else None)
        .first()
        or User.objects.filter(org=org, is_active=True, login=str(identifier)).first()
    )


def _is_uuid(value: Any) -> bool:
    from uuid import UUID

    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return parse_datetime(str(value))


def _execute_service_action(
    proposal: McpActionProposal, target: Any, by_user: User
) -> dict[str, Any]:
    """Dispatch to the shared domain service for the proposed action."""
    args = proposal.arguments or {}
    action = proposal.action

    if action == "apply_alert_triage":
        from alerts.triage import apply_alert_triage

        payload = {key: value for key, value in args.items() if key != "alertId"}
        return apply_alert_triage(target, payload, actor=by_user)

    if action == "apply_case_update":
        from cases.update import apply_case_update

        payload = {key: value for key, value in args.items() if key != "caseId"}
        return {"changed": apply_case_update(target, payload, actor=by_user)}

    if action == "append_timeline_event":
        from cases.ledger import append_timeline_event

        event = append_timeline_event(
            target,
            title="Case comment",
            kind="comment",
            description=str(args.get("markdown") or ""),
            actor=by_user,
        )
        return {"eventId": str(event.id)}

    if action == "add_observable_with_status":
        from observables.extractor import add_observable_with_status

        resolved = add_observable_with_status(
            target, str(args.get("type") or ""), str(args.get("value") or "")
        )
        if resolved is None:
            raise ValidationError("Unknown observable type.", code="invalid_arguments")
        observable, created = resolved
        return {"observableId": str(observable.id), "created": created}

    if action == "create_task":
        from cases.task_service import create_task

        assignee = _resolve_user(args.get("assigneeId"), target.owner_org)
        task = create_task(
            target,
            str(args.get("title") or ""),
            assignee=assignee,
            due_date=_parse_datetime(args.get("dueAt")),
        )
        return {"taskId": str(task.id)}

    if action == "escalate_alert":
        from alerts.escalation import import_alert_to_case, merge_alert_into_case

        case_id = args.get("caseId")
        if case_id:
            case = _resolve_target("case", str(case_id), proposal.organisation)
            if case is None:
                raise ValidationError("Case not found.", code="not_found")
            event = merge_alert_into_case(target, case, actor=by_user)
            return {"caseId": str(case.id), "eventId": str(event.id)}
        assignee = _resolve_user(args.get("assigneeId"), proposal.organisation)
        case, warnings = import_alert_to_case(
            target,
            title=args.get("title"),
            description=args.get("description"),
            assignee=assignee,
            actor=by_user,
        )
        return {"caseId": str(case.id), "warnings": list(warnings)[:20]}

    if action == "run_playbook":
        from automation.services import run_playbook_service

        return run_playbook_service(target, args, actor=by_user)

    if action == "close_case":
        from cases.update import apply_case_update

        payload = {
            "status": "Closed",
            "verdict": args.get("verdict"),
            "comment": args.get("closingComment"),
        }
        return {"changed": apply_case_update(target, payload, actor=by_user)}

    raise ValidationError(f"Unknown proposal action: {action}", code="invalid_arguments")


# ---------------------------------------------------------------------------
# Result bounding and ledger feedback
# ---------------------------------------------------------------------------


def _bounded_result(value: Any, *, depth: int = 0) -> Any:
    """Recursively bound result data; never include raw payloads or unbounded text."""
    if depth >= 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "pk"):
        return str(value.pk)
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded_result(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bounded_result(item, depth=depth + 1) for item in list(value)[:20]]
    return str(value)[:200]


def _emit_case_execution_event(
    proposal: McpActionProposal, target: Any, result: dict[str, Any], by_user: User
) -> None:
    """Append one bounded execution event to the case ledger."""
    from cases.ledger import append_timeline_event

    append_timeline_event(
        target,
        title="MCP proposal executed",
        kind="automation",
        description=f"Executed {proposal.tool_name}.",
        actor=by_user,
        metadata={
            "proposalId": str(proposal.id),
            "toolName": proposal.tool_name,
            "action": proposal.action,
            "result": _bounded_result(result),
        },
    )
