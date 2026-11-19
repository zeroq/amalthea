"""Request-free alert triage service shared by the UI, API and MCP executor.

Only validated scalar fields, in-org assignees, tags and an optional comment are
accepted. `raw_payload` is never read or returned.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from alerts.escalation import resolve_alert_status
from alerts.models import Alert, AlertTagLink
from cases.models import Comment, Tag
from core.enums import SEVERITY_MAX, SEVERITY_MIN
from core.serializers import comment_json
from identity.models import User
from realtime.publisher import publish_case_event

TRIAGE_FIELDS = ("status", "severity", "assigneeId", "tags", "comment")


def resolve_alert_assignee(value: Any, org: Any) -> User | None:
    """Resolve a login or UUID to an active user in the alert's organisation."""
    if value in (None, ""):
        return None
    from uuid import UUID

    try:
        as_uuid: UUID | None = UUID(str(value))
    except (TypeError, ValueError):
        as_uuid = None

    user = None
    if as_uuid is not None:
        user = User.objects.filter(pk=as_uuid, org=org, is_active=True).first()
    if user is None:
        user = User.objects.filter(login=str(value), org=org, is_active=True).first()
    if user is None:
        raise ValidationError({"assigneeId": "Unknown assignee in this organisation."})
    return user


@transaction.atomic
def apply_alert_triage(
    alert: Alert, payload: dict[str, Any], *, actor: User | None = None
) -> dict[str, Any]:
    """Apply an already-authorised alert triage update; return a bounded result."""
    unknown = set(payload or {}) - set(TRIAGE_FIELDS)
    if unknown:
        raise ValidationError(
            {"arguments": f"Unknown fields: {', '.join(sorted(unknown))}"},
            code="invalid_arguments",
        )

    changed: list[str] = []
    model_fields: list[str] = []
    warnings: list[str] = []
    comment_id: str | None = None

    if "status" in payload:
        value = str(payload["status"] or "").strip()
        if not value:
            raise ValidationError({"status": "status is required."}, code="invalid_arguments")
        alert.status = resolve_alert_status(value, warnings=warnings)
        changed.append("status")
        model_fields.append("status")

    if "severity" in payload:
        try:
            severity = int(payload["severity"])
        except (TypeError, ValueError):
            raise ValidationError(
                {"severity": f"severity must be between {SEVERITY_MIN} and {SEVERITY_MAX}."},
                code="invalid_arguments",
            ) from None
        if not SEVERITY_MIN <= severity <= SEVERITY_MAX:
            raise ValidationError(
                {"severity": f"severity must be between {SEVERITY_MIN} and {SEVERITY_MAX}."},
                code="invalid_arguments",
            )
        alert.severity = severity
        changed.append("severity")
        model_fields.append("severity")

    assignee = None
    if "assigneeId" in payload:
        assignee = resolve_alert_assignee(payload["assigneeId"], alert.owner_org)
        alert.assignee = assignee
        changed.append("assignee")
        model_fields.append("assignee")

    tags_added: list[str] = []
    if "tags" in payload:
        names = payload["tags"]
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name.strip() for name in names
        ):
            raise ValidationError(
                {"tags": "tags must be a list of non-empty names."}, code="invalid_arguments"
            )
        for raw_name in names:
            name = raw_name.strip()[:100]
            tag, _created = Tag.objects.get_or_create(name=name)
            AlertTagLink.objects.get_or_create(alert=alert, tag=tag)
            tags_added.append(name)
        changed.append("tags")

    if not changed and not payload.get("comment"):
        raise ValidationError("No updatable field supplied", code="invalid_arguments")

    if model_fields:
        alert.save(update_fields=[*model_fields, "updated_at"])

    if payload.get("comment"):
        message = str(payload["comment"]).strip()
        if not message:
            raise ValidationError({"comment": "comment cannot be blank."}, code="invalid_arguments")
        comment = Comment.objects.create(alert=alert, message=message, created_by=actor)
        comment_id = str(comment.id)
        if alert.case_id:
            from functools import partial

            transaction.on_commit(
                partial(
                    publish_case_event,
                    str(alert.case_id),
                    "comment",
                    {"event": comment_json(comment)},
                )
            )

    return {
        "changed": changed,
        "assignee": assignee.login if assignee else None,
        "tagsAdded": tags_added[:50],
        "commentId": comment_id,
        "warnings": warnings[:20],
        "updatedAt": alert.updated_at.isoformat()
        if alert.updated_at
        else timezone.now().isoformat(),
    }
