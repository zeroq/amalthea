"""MCP Phase 2 proposal tools.

Each tool creates an `McpActionProposal` row and returns a handle ({proposalId,
status, risk, expiresAt, approvalPath}) without mutating any domain state.
All argument validation is strict; unknown fields are rejected.

Rules:
- Strict schemas; unknown fields rejected.
- No `organisation`, `actor`, `owner`, role, approval, script, URL, header,
  or raw-payload arguments.
- Cross-tenant, unknown, or invisible targets return uniform `not_found`.
- `get_action_proposal` only returns proposals owned by the credential's organisation.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from django.utils import timezone

from mcp_server.auth import Principal
from mcp_server.models import McpActionProposal
from mcp_server.schemas import tool_result
from mcp_server.tools.base import ToolFailureError, run_write

# ---------------------------------------------------------------------------
# Helper: deterministic idempotency key
# ---------------------------------------------------------------------------


def _canonicalise_args(arguments: dict[str, Any]) -> str:
    """Canonical JSON of arguments: sorted keys, compact separators, safe defaults."""
    return json.dumps(arguments or {}, sort_keys=True, default=str, separators=(",", ":"))


def _idempotency_key(
    credential_id: str, tool_name: str, target_type: str, target_id: str, arguments: dict[str, Any]
) -> str:
    canonical = _canonicalise_args(arguments)
    seed = f"{credential_id}:{tool_name}:{target_type}:{target_id}:{canonical}"
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _validate_tool_arguments(arguments: dict[str, Any], allowed: set[str]) -> None:
    """Reject unknown fields; raise ToolFailureError if any are present."""
    unknown = set(arguments.keys()) - allowed
    if unknown:
        raise ToolFailureError(
            "invalid_arguments", message=f"Unknown fields: {', '.join(sorted(unknown))}"
        )


def _check_proposal_idempotency(
    credential_id: str, tool_name: str, target_type: str, target_id: str, arguments: dict[str, Any]
) -> McpActionProposal | None:
    """Check if a proposal with this idempotency key already exists.

    If found, return the existing proposal row. If not, return None.
    This ensures deterministic idempotency: duplicate creation returns the
    existing proposal instead of raising IntegrityError.
    """
    key = _idempotency_key(credential_id, tool_name, target_type, target_id, arguments)
    # The key is derived from the credential UUID, so it is already tenant-unique. Filtering
    # `organisation` by the credential UUID was a cross-column type confusion.
    return McpActionProposal.objects.filter(idempotency_key=key).first()


# ---------------------------------------------------------------------------
# Tool: propose_alert_triage
# ---------------------------------------------------------------------------


def propose_alert_triage(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose triage changes for an alert.

    Arguments:
        alertId (required): UUID string of the target alert.
        status (optional): New status value.
        severity (optional): New severity integer.
        assigneeId (optional): User login or UUID to assign to.
        tags (optional): List of tag names to set.
        comment (optional): Markdown comment to append to the alert's ledger.

    Risk: low / medium. Low if only comment; medium if status/severity/assignee/tags supplied.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"alertId", "status", "severity", "assigneeId", "tags", "comment"}
    _validate_tool_arguments(arguments, allowed)

    alert_id = arguments.get("alertId")
    if not alert_id:
        raise ToolFailureError("invalid_arguments", message="alertId is required")

    # --- Target validation: must exist and belong to credential org ---
    from alerts.models import Alert

    try:
        Alert.objects.get(pk=alert_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Alert not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_alert_triage", "alert", alert_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # --- Determine risk: medium if mutating fields, low if only comment ---
    mutating_fields = any(arguments.get(k) for k in ("status", "severity", "assigneeId", "tags"))
    risk = "medium" if mutating_fields else "low"

    # Build clean arguments (only allow known fields — already validated above)
    clean_args = {k: v for k, v in arguments.items() if k in allowed}

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_alert_triage",
        "alert",
        alert_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_alert_triage",
        action="apply_alert_triage",  # will be mapped to shared service
        target_type="alert",
        target_id=alert_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk=risk,
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    # Compute arguments_hash
    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_case_update
# ---------------------------------------------------------------------------


def propose_case_update(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose case field updates.

    Arguments:
        caseId (required): UUID string of the target case.
        Allowed scalar/enum fields: title, description, summary, severity, tlp, pap, flag,
          scope, case_type, data_classification, phase, verdict, assignee, tags,
          addTags, removeTags.

    Risk: medium. Only medium-risk proposals may be approved by admin/superAdmin.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed_scalar = {
        "title",
        "description",
        "summary",
        "severity",
        "tlp",
        "pap",
        "flag",
        "scope",
        "case_type",
        "data_classification",
        "phase",
        "verdict",
    }
    allowed_task = {"assignee", "tags", "addTags", "removeTags"}
    allowed_fields = {"caseId"} | allowed_scalar | allowed_task
    _validate_tool_arguments(arguments, allowed_fields)

    case_id = arguments.get("caseId")
    if not case_id:
        raise ToolFailureError("invalid_arguments", message="caseId is required")

    # --- Target validation: must exist and belong to credential org ---
    from cases.models import Case

    try:
        Case.objects.get(pk=case_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Case not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_case_update", "case", case_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Allowed fields from cases.update.CASE_SCALAR_FIELDS + task fields
    risk = "medium"

    # Build clean arguments (only allow known fields — already validated above)
    clean_args = {k: v for k, v in arguments.items() if k in allowed_fields}

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_case_update",
        "case",
        case_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_case_update",
        action="apply_case_update",
        target_type="case",
        target_id=case_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk=risk,
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    # Compute arguments_hash
    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_case_comment
# ---------------------------------------------------------------------------


def propose_case_comment(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose a comment on a case.

    Arguments:
        caseId (required): UUID string of the target case.
        markdown (required): Markdown text for the comment.

    Risk: low. Low-risk proposal; analyst/admin may approve/decline.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"caseId", "markdown"}
    _validate_tool_arguments(arguments, allowed)

    case_id = arguments.get("caseId")
    markdown = arguments.get("markdown")

    if not case_id:
        raise ToolFailureError("invalid_arguments", message="caseId is required")
    if not markdown:
        raise ToolFailureError("invalid_arguments", message="markdown is required")

    # --- Target validation: must exist and belong to credential org ---
    from cases.models import Case

    try:
        Case.objects.get(pk=case_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Case not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_case_comment", "case", case_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Clean: only allow caseId and markdown
    clean_args = {"caseId": case_id, "markdown": markdown}

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_case_comment",
        "case",
        case_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_case_comment",
        action="append_timeline_event",
        target_type="case",
        target_id=case_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk="low",
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_observable_add
# ---------------------------------------------------------------------------


def propose_observable_add(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose adding an observable to a case.

    Arguments:
        caseId (required): UUID string of the target case.
        type (required): Observable type name (e.g. ip, domain, hash, mail, fqdn, url).
        value (required): The observable value (e.g. IP address, domain, hash string).

    Risk: medium. New observable creation triggers fan-out and automation.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"caseId", "type", "value"}
    _validate_tool_arguments(arguments, allowed)

    case_id = arguments.get("caseId")
    obs_type = arguments.get("type")
    value = arguments.get("value")

    if not case_id:
        raise ToolFailureError("invalid_arguments", message="caseId is required")
    if not obs_type:
        raise ToolFailureError("invalid_arguments", message="type is required")
    if not value:
        raise ToolFailureError("invalid_arguments", message="value is required")

    # --- Target validation: must exist and belong to credential org ---
    from cases.models import Case

    try:
        Case.objects.get(pk=case_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Case not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_observable_add", "observable", case_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Only allow known observable types
    allowed_types = {"ip", "domain", "hash", "mail", "fqdn", "url"}
    if obs_type not in allowed_types:
        raise ToolFailureError("invalid_arguments", message=f"Unknown observable type: {obs_type}")

    clean_args = {"caseId": case_id, "type": obs_type, "value": value}

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_observable_add",
        "observable",
        case_id,  # using case_id as the target identifier since observable is not yet created
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_observable_add",
        action="add_observable_with_status",
        target_type="case",  # target is the case; observable will be created during execution
        target_id=case_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk="medium",
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_task_create
# ---------------------------------------------------------------------------


def propose_task_create(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose creating a task on a case.

    Arguments:
        caseId (required): UUID string of the target case.
        title (required): Task title/description.
        assigneeId (optional): User login or UUID to assign to.
        dueAt (optional): ISO datetime for due date.

    Risk: low. Low-risk proposal.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"caseId", "title", "assigneeId", "dueAt"}
    _validate_tool_arguments(arguments, allowed)

    case_id = arguments.get("caseId")
    title = arguments.get("title")

    if not case_id:
        raise ToolFailureError("invalid_arguments", message="caseId is required")
    if not title:
        raise ToolFailureError("invalid_arguments", message="title is required")

    # --- Target validation: must exist and belong to credential org ---
    from cases.models import Case

    try:
        Case.objects.get(pk=case_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Case not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_task_create", "case", case_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Build clean arguments (only allow known fields — already validated above)
    assignee_id = arguments.get("assigneeId")
    due_at = arguments.get("dueAt")
    clean_args = {"caseId": case_id, "title": title}
    if assignee_id is not None:
        clean_args["assigneeId"] = assignee_id
    if due_at is not None:
        clean_args["dueAt"] = due_at

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_task_create",
        "case",
        case_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_task_create",
        action="create_task",
        target_type="case",
        target_id=case_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk="low",
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_alert_escalation
# ---------------------------------------------------------------------------


def propose_alert_escalation(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose escalating an alert into a case.

    Arguments:
        alertId (required): UUID string of the source alert.
        caseId (optional): If provided, merge into existing case; otherwise promote to new case.
        title (optional): Override title for the new case.
        description (optional): Override description for the new case.
        assigneeId (optional): User login or UUID to assign to the new case.

    Risk: high. High-risk proposal; admin/superAdmin only for decision.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"alertId", "caseId", "title", "description", "assigneeId"}
    _validate_tool_arguments(arguments, allowed)

    alert_id = arguments.get("alertId")
    if not alert_id:
        raise ToolFailureError("invalid_arguments", message="alertId is required")

    # --- Target validation: must exist and belong to credential org ---
    from alerts.models import Alert

    try:
        Alert.objects.get(pk=alert_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Alert not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_alert_escalation", "alert", alert_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Only allow known fields — already validated above
    allowed = {"caseId", "title", "description", "assigneeId"}
    clean_args = {k: v for k, v in arguments.items() if k in allowed}

    # Risk is always high for alert escalation
    risk = "high"

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_alert_escalation",
        "alert",
        alert_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_alert_escalation",
        action="escalate_alert",
        target_type="alert",
        target_id=alert_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk=risk,
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_playbook_run
# ---------------------------------------------------------------------------


def propose_playbook_run(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose running a playbook against a case.

    Arguments:
        caseId (required): UUID string of the target case.
        playbookId (required): UUID string or name of the playbook to run.
        observableId (optional): UUID string of an optional observable to associate.

    Risk: high. High-risk proposal; admin/superAdmin only for decision.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"caseId", "playbookId", "observableId"}
    _validate_tool_arguments(arguments, allowed)

    case_id = arguments.get("caseId")
    playbook_id = arguments.get("playbookId")

    if not case_id:
        raise ToolFailureError("invalid_arguments", message="caseId is required")
    if not playbook_id:
        raise ToolFailureError("invalid_arguments", message="playbookId is required")

    # --- Target validation: must exist and belong to credential org ---
    from cases.models import Case

    try:
        Case.objects.get(pk=case_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Case not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_playbook_run", "case", case_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Only allow known fields — already validated above
    allowed = {"caseId", "playbookId", "observableId"}
    clean_args = {k: v for k, v in arguments.items() if k in allowed}

    # Risk is always high for playbook run
    risk = "high"

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_playbook_run",
        "case",
        case_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_playbook_run",
        action="run_playbook",
        target_type="case",
        target_id=case_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk=risk,
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: propose_case_closure
# ---------------------------------------------------------------------------


def propose_case_closure(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Propose closing a case.

    Arguments:
        caseId (required): UUID string of the target case.
        verdict (required): The case verdict (e.g. "true_positive", "false_positive").
        closingComment (required): Markdown comment for the closure.

    Risk: high. High-risk proposal; admin/superAdmin only for decision.

    Raises:
        ToolFailureError: if unknown fields are present, target not found,
                          or cross-tenant access.
    """
    # --- Reject unknown fields ---
    allowed = {"caseId", "verdict", "closingComment"}
    _validate_tool_arguments(arguments, allowed)

    case_id = arguments.get("caseId")
    verdict = arguments.get("verdict")
    closing_comment = arguments.get("closingComment")

    if not case_id:
        raise ToolFailureError("invalid_arguments", message="caseId is required")
    if not verdict:
        raise ToolFailureError("invalid_arguments", message="verdict is required")
    if not closing_comment:
        raise ToolFailureError("invalid_arguments", message="closingComment is required")

    # --- Target validation: must exist and belong to credential org ---
    from cases.models import Case

    try:
        Case.objects.get(pk=case_id, owner_org_id=principal.organisation_id)
    except Exception:
        raise ToolFailureError(
            "not_found", status="not_found", message="Case not found or access denied."
        ) from None

    # --- Idempotency check: return existing proposal if key matches ---
    existing = _check_proposal_idempotency(
        str(principal.credential_id), "propose_case_closure", "case", case_id, arguments
    )
    if existing is not None:
        expires_at = existing.expires_at
        return {
            "proposalId": str(existing.id),
            "status": existing.status,
            "risk": existing.risk,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "approvalPath": f"/approvals/{existing.id}",
        }

    # Only allow known fields — already validated above
    allowed = {"caseId", "verdict", "closingComment"}
    clean_args = {k: v for k, v in arguments.items() if k in allowed}

    # Risk is always high for case closure
    risk = "high"

    # Generate deterministic idempotency key
    key = _idempotency_key(
        str(principal.credential_id),
        "propose_case_closure",
        "case",
        case_id,
        clean_args,
    )

    # Create the proposal row
    proposal = McpActionProposal.objects.create(
        organisation_id=principal.organisation_id,
        requested_by_id=principal.actor_id,
        credential_id=principal.credential_id if hasattr(principal, "credential_id") else None,
        tool_name="propose_case_closure",
        action="close_case",
        target_type="case",
        target_id=case_id,
        arguments=clean_args,
        arguments_hash="",  # will be computed on save
        risk=risk,
        idempotency_key=key,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    import hashlib

    canonical = _canonicalise_args(clean_args)
    proposal.arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    proposal.save(update_fields=["arguments_hash"])

    expires_at = proposal.expires_at
    return {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "approvalPath": f"/approvals/{proposal.id}",
    }


# ---------------------------------------------------------------------------
# Tool: get_action_proposal
# ---------------------------------------------------------------------------


def get_action_proposal(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Return a bounded summary of a proposal (read-only).

    Arguments:
        proposalId (required): UUID string of the proposal to look up.

    Returns bounded status/result summary; org-scoped data only.
    Unknown/invalid IDs or cross-org access returns uniform `not_found`.
    """
    _validate_tool_arguments(arguments, {"proposalId"})

    proposal_id = arguments.get("proposalId")
    if not proposal_id:
        raise ToolFailureError("invalid_arguments", message="proposalId is required")

    # Only return proposals owned by the credential's organisation
    from mcp_server.models import McpActionProposal

    try:
        proposal = McpActionProposal.objects.get(
            id=proposal_id,
            organisation_id=principal.organisation_id,
        )
    except McpActionProposal.DoesNotExist:
        raise ToolFailureError(
            "not_found", status="not_found", message="Proposal not found or access denied."
        ) from None

    # Build a bounded result summary (no secrets, raw payloads, unbounded text)
    result: dict[str, Any] = {
        "proposalId": str(proposal.id),
        "status": proposal.status,
        "risk": proposal.risk,
        "toolName": proposal.tool_name,
        "action": proposal.action,
        "targetType": proposal.target_type,
        "targetId": proposal.target_id,
        "expiresAt": proposal.expires_at.isoformat() if proposal.expires_at else None,
        "createdAt": proposal.created_at.isoformat() if proposal.created_at else None,
    }

    # If terminal, include bounded result only.
    if proposal.status in ("executed", "failed") and proposal.result:
        result["result"] = {
            key: (value[:200] if isinstance(value, str) and len(value) > 200 else value)
            for key, value in proposal.result.items()
        }

    return {
        "proposal": result,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(server: Any) -> None:
    """Register proposal tools; each returns a handle and never mutates domain state."""

    def payload(**camel_case_values: Any) -> dict[str, Any]:
        return {key: value for key, value in camel_case_values.items() if value is not None}

    @server.tool(
        name="propose_alert_triage",
        description="Propose status/severity/assignee/tags/comment changes on an alert.",
    )
    async def propose_alert_triage_tool(
        alert_id: str,
        status: str | None = None,
        severity: int | None = None,
        assignee_id: str | None = None,
        tags: list[str] | None = None,
        comment: str | None = None,
    ) -> Any:
        """Create an alert-triage proposal."""
        data = await run_write(
            "propose_alert_triage",
            payload(
                alertId=alert_id,
                status=status,
                severity=severity,
                assigneeId=assignee_id,
                tags=tags,
                comment=comment,
            ),
            propose_alert_triage,
            scope="mcp:propose",
            target_type="alert",
            target_id=alert_id,
        )
        return tool_result(data, total=1, summary="alert triage proposal created")

    @server.tool(
        name="propose_case_update",
        description="Propose a bounded case header update.",
    )
    async def propose_case_update_tool(
        case_id: str,
        title: str | None = None,
        description: str | None = None,
        summary: str | None = None,
        severity: int | None = None,
        tlp: int | None = None,
        pap: int | None = None,
        flag: str | None = None,
        scope: str | None = None,
        case_type: str | None = None,
        data_classification: str | None = None,
        phase: str | None = None,
        verdict: str | None = None,
        assignee: str | None = None,
        tags: list[str] | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
    ) -> Any:
        """Create a case-update proposal."""
        data = await run_write(
            "propose_case_update",
            payload(
                caseId=case_id,
                title=title,
                description=description,
                summary=summary,
                severity=severity,
                tlp=tlp,
                pap=pap,
                flag=flag,
                scope=scope,
                case_type=case_type,
                data_classification=data_classification,
                phase=phase,
                verdict=verdict,
                assignee=assignee,
                tags=tags,
                addTags=add_tags,
                removeTags=remove_tags,
            ),
            propose_case_update,
            scope="mcp:propose",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="case update proposal created")

    @server.tool(
        name="propose_case_comment",
        description="Propose a Markdown comment on a case timeline.",
    )
    async def propose_case_comment_tool(case_id: str, markdown: str) -> Any:
        """Create a case-comment proposal."""
        data = await run_write(
            "propose_case_comment",
            {"caseId": case_id, "markdown": markdown},
            propose_case_comment,
            scope="mcp:propose",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="case comment proposal created")

    @server.tool(
        name="propose_observable_add",
        description="Propose adding an observable to a case.",
    )
    async def propose_observable_add_tool(case_id: str, type: str, value: str) -> Any:
        """Create an observable-add proposal."""
        data = await run_write(
            "propose_observable_add",
            {"caseId": case_id, "type": type, "value": value},
            propose_observable_add,
            scope="mcp:propose",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="observable proposal created")

    @server.tool(
        name="propose_task_create",
        description="Propose a task on a case.",
    )
    async def propose_task_create_tool(
        case_id: str,
        title: str,
        assignee_id: str | None = None,
        due_at: str | None = None,
    ) -> Any:
        """Create a task-create proposal."""
        data = await run_write(
            "propose_task_create",
            payload(caseId=case_id, title=title, assigneeId=assignee_id, dueAt=due_at),
            propose_task_create,
            scope="mcp:propose",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="task proposal created")

    @server.tool(
        name="propose_alert_escalation",
        description="Propose escalating an alert into a new or existing case.",
    )
    async def propose_alert_escalation_tool(
        alert_id: str,
        case_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        assignee_id: str | None = None,
    ) -> Any:
        """Create an alert-escalation proposal."""
        data = await run_write(
            "propose_alert_escalation",
            payload(
                alertId=alert_id,
                caseId=case_id,
                title=title,
                description=description,
                assigneeId=assignee_id,
            ),
            propose_alert_escalation,
            scope="mcp:propose",
            target_type="alert",
            target_id=alert_id,
        )
        return tool_result(data, total=1, summary="alert escalation proposal created")

    @server.tool(
        name="propose_playbook_run",
        description="Propose a manual playbook run against a case.",
    )
    async def propose_playbook_run_tool(
        case_id: str,
        playbook_id: str,
        observable_id: str | None = None,
    ) -> Any:
        """Create a playbook-run proposal."""
        data = await run_write(
            "propose_playbook_run",
            payload(
                caseId=case_id,
                playbookId=playbook_id,
                observableId=observable_id,
            ),
            propose_playbook_run,
            scope="mcp:propose",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="playbook proposal created")

    @server.tool(
        name="propose_case_closure",
        description="Propose closing a case with a verdict and closing comment.",
    )
    async def propose_case_closure_tool(
        case_id: str,
        verdict: str,
        closing_comment: str,
    ) -> Any:
        """Create a case-closure proposal."""
        data = await run_write(
            "propose_case_closure",
            {"caseId": case_id, "verdict": verdict, "closingComment": closing_comment},
            propose_case_closure,
            scope="mcp:propose",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="case closure proposal created")

    @server.tool(
        name="get_action_proposal",
        description="Read the bounded status/result of one proposal owned by this credential's org.",
    )
    async def get_action_proposal_tool(proposal_id: str) -> Any:
        """Read one proposal handle."""
        data = await run_write(
            "get_action_proposal",
            {"proposalId": proposal_id},
            get_action_proposal,
            scope="mcp:propose",
            target_type="proposal",
            target_id=proposal_id,
        )
        return tool_result(data, total=1, summary="proposal")
