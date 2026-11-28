"""Case wire representations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from automation.models import AutomationRun
from cases.models import (
    Attachment,
    Case,
    CaseCustomFieldValue,
    CaseObservable,
    CaseTemplate,
    Comment,
    CustomField,
    Page,
    Share,
    Task,
    TimelineEvent,
)
from compat.enums import PAP_LABELS, SEVERITY_LABELS, TLP_LABELS

from ._common import _iso, _username
from .automation import automation_run_json
from .observables import observable_json


def timeline_event_json(event: TimelineEvent) -> dict[str, Any]:
    payload = {
        "_id": str(event.id),
        "id": str(event.id),
        "_type": "event",
        "date": _iso(event.date),
        "endDate": _iso(event.end_date),
        "title": event.title,
        "description": event.description,
        "kind": event.kind,
        "actor": _username(event.actor),
        "metadata": event.metadata or {},
    }
    if event.kind == "ai-summary" and event.metadata:
        metadata = dict(event.metadata)
        provenance = dict(metadata.get("provenance") or {})
        provenance["label"] = "AI-generated summary — verify against the cited events"
        if metadata.get("credential_name"):
            provenance["credential_name"] = metadata["credential_name"]
        metadata["provenance"] = provenance
        payload["metadata"] = metadata
    return payload


def task_json(task: Task) -> dict[str, Any]:
    """`OutputTask` (recorded 5.8.0 OpenAPI): `_id` + ISO timestamps (deviation **P10-y**)."""
    return {
        "_id": str(task.id),
        "id": str(task.id),
        "_type": "Task",
        # No creator columns exist on `Task` (plan §4 "no new fields"), so the audit pair
        # degrades to `null` rather than being omitted — thehive4py's TypedDict reads both keys.
        "_createdBy": None,
        "_createdAt": _iso(task.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(task.updated_at),
        "title": task.title,
        "group": task.group,
        "description": task.description,
        "status": task.status,
        "flag": task.flag,
        "startDate": _iso(task.started_at),
        "endDate": _iso(task.ended_at),
        "order": task.order,
        "dueDate": _iso(task.due_date),
        "assignee": _username(task.assignee),
        "mandatory": task.mandatory,
        "extraData": {},
    }


def custom_event_json(event: TimelineEvent) -> dict[str, Any]:
    """`OutputCustomEvent` — the **wire** view of a ledger row.

    Deliberately not `timeline_event_json`: that is the internal ledger shape the WS `sync`
    protocol and the Phase 9 UI consume (P8-1), while this is the 5.8.0 entity rendering the
    `customEvent` endpoints return. Both read the same row; neither is derived from the other.
    """
    return {
        "_id": str(event.id),
        "id": str(event.id),
        "_type": "CustomEvent",
        "date": _iso(event.date),
        "endDate": _iso(event.end_date),
        "title": event.title,
        "description": event.description,
        # `actor` is the only creator-shaped column a ledger row has.
        "_createdBy": _username(event.actor),
        "_createdAt": _iso(event.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(event.updated_at),
        "caseId": str(event.case_id),
    }


def custom_field_json(field: CustomField) -> dict[str, Any]:
    """`OutputCustomField`.

    `displayName`/`description`/`order`/`mandatory` have no column on `cases.CustomField`
    (plan §4, deviation **P10-d**), so they are emitted as the schema-shaped defaults the
    stored definition actually implies: `displayName` mirrors `name`, and the presentation
    knobs default rather than being dropped — a client validating against the recorded
    `OutputCustomField` requires the keys to exist.
    """
    return {
        "_id": str(field.id),
        "_type": "customField",
        "name": field.name,
        "displayName": field.name,
        "group": field.group,
        "description": "",
        "type": field.type,
        "options": field.options,
        "order": 0,
        "mandatory": False,
        "_createdBy": None,
        "_createdAt": _iso(field.created_at),
        "extraData": {},
    }


def custom_field_value_json(value: CaseCustomFieldValue) -> dict[str, Any]:
    """`OutputCustomFieldValue` — the wire shape for a case custom field value.

    `_id` is the `CaseCustomFieldValue` UUID, matching TheHive's value-id semantics.
    """
    return {
        "_id": str(value.id),
        "name": value.custom_field.name,
        "type": value.custom_field.type,
        "value": value.value,
        "order": value.order,
    }


class CustomFieldWireError(ValueError):
    """A malformed TheHive custom-field write payload."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def custom_field_updates_from_wire(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    """Decode the writable subset of TheHive's ``customFields`` into internal rows.

    Returning `None` means the key was absent. Shape errors are raised here, at the wire
    boundary, so services never see the external `_id` spelling. Read-only wire fields echoed by
    GET (`name`, `type`, `order`) are ignored by construction.
    """
    if "customFields" not in payload:
        return None
    raw_items = payload.get("customFields")
    if not isinstance(raw_items, list):
        raise CustomFieldWireError("customFields must be a list")

    rows: list[dict[str, Any]] = []
    seen_ids: set[UUID] = set()
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping) or "_id" not in item or "value" not in item:
            raise CustomFieldWireError(f"customFields[{index}] requires _id and value")
        try:
            value_id = UUID(str(item["_id"]))
        except (TypeError, ValueError) as exc:
            message = f"customFields[{index}] has an invalid _id"
            raise CustomFieldWireError(message) from exc
        if value_id in seen_ids:
            raise CustomFieldWireError(f"customFields[{index}] repeats a value id")
        seen_ids.add(value_id)
        rows.append({"id": value_id, "value": item["value"]})
    return rows


def case_json(
    case: Case, *, detail: bool = False, timeline_after: str | None = None, timeline_limit: int = 50
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "_id": str(case.id),
        "id": str(case.id),
        "_type": "case",
        "number": case.number,
        "title": case.title,
        "description": case.description,
        "severity": case.severity,
        "status": case.status.value if case.status_id else None,
        "tlp": case.tlp,
        "pap": case.pap,
        "flag": case.flag,
        "summary": case.summary,
        "assignee": _username(case.assignee),
        "startDate": _iso(case.start_date),
        "endDate": _iso(case.end_date),
        "closedDate": _iso(case.closed_date),
        "createdAt": _iso(case.created_at),
        "updatedAt": _iso(case.updated_at),
        "alertCount": case.alerts.count(),
        "scope": case.scope,
        "caseType": case.case_type,
        "dataClassification": case.data_classification,
        "phase": case.phase,
        "verdict": case.verdict,
    }
    if detail:
        payload["observables"] = [
            observable_json(link)
            for link in CaseObservable.objects.select_related("observable__data_type")
            .filter(case=case)
            .order_by("-created_at")
        ]
        # Use keyset pagination for timeline (same as WebSocket sync)
        from uuid import UUID

        from cases.ledger import events_after

        timeline_after_uuid = None
        if timeline_after:
            try:
                timeline_after_uuid = UUID(timeline_after)
            except ValueError:
                pass

        timeline_items = list(events_after(str(case.id), timeline_after_uuid)[: timeline_limit + 1])
        # +1 fetched above to detect has_more
        has_more = len(timeline_items) > timeline_limit
        if has_more:
            timeline_items = timeline_items[:timeline_limit]

        next_cursor = None
        if has_more and timeline_items:
            last_event = timeline_items[-1]
            next_cursor = str(last_event.id)

        payload["timeline"] = [timeline_event_json(e) for e in timeline_items]
        payload["timelinePagination"] = {
            "hasMore": has_more,
            "nextCursor": next_cursor,
        }

        payload["observables"] = [
            observable_json(link)
            for link in CaseObservable.objects.select_related("observable__data_type")
            .filter(case=case)
            .order_by("-created_at")
        ]
        payload["customFields"] = [
            custom_field_value_json(v)
            for v in case.custom_field_values.select_related("custom_field").order_by(
                "order", "custom_field__name"
            )
        ]
        payload["automationRuns"] = [
            automation_run_json(r)
            for r in AutomationRun.objects.filter(case=case).order_by("-created_at")
        ]
        payload["tasks"] = [
            {
                "_id": str(t.id),
                "title": t.title,
                "status": t.status,
                "assignee": _username(t.assignee),
            }
            for t in case.tasks.all()
        ]
    return payload


def comment_json(comment: Comment) -> dict[str, Any]:
    """`OutputComment` (recorded 5.8.0). Case- and alert-scoped comments render identically.

    TheHive's `OutputComment` carries no parent id: a comment is reached through its case or alert,
    so the parent is implicit in the request and is deliberately not echoed. `isEdited` is derived,
    not stored — `updated_by` is only ever set by an edit, which is the question the field answers.
    """
    return {
        "_id": str(comment.id),
        "id": str(comment.id),
        "_type": "comment",
        "_createdBy": _username(comment.created_by),
        "_createdAt": _iso(comment.created_at),
        "_updatedBy": _username(comment.updated_by),
        "_updatedAt": _iso(comment.updated_at),
        "message": comment.message,
        "isEdited": comment.updated_by_id is not None,
        "extraData": {},
        "external": False,
    }


def page_json(page: Page) -> dict[str, Any]:
    """`OutputPage` (recorded 5.8.0): a mutable Markdown page attached to a case.

    `caseId` is emitted even though the hand-written `thehive4py` TypedDict omits it: the recorded
    OpenAPI page carries the parent, and a flow that lists pages from several cases is otherwise
    ambiguous. The audit pair uses the `_createdBy`/`_createdAt` spelling the T2 entities share.
    """
    return {
        "_id": str(page.id),
        "id": str(page.id),
        "_type": "page",
        "_createdBy": _username(page.created_by),
        "_createdAt": _iso(page.created_at),
        "_updatedBy": _username(page.updated_by),
        "_updatedAt": _iso(page.updated_at),
        "caseId": str(page.case_id),
        "title": page.title,
        "content": page.content,
        "order": page.order,
        "category": page.category,
        "extraData": {},
    }


def share_json(share: Share) -> dict[str, Any]:
    """`OutputShare` — the grant of one case to one organisation.

    TheHive's `OutputShare` carries `profileName`/`taskRule`/`observableRule`/`owner`; this
    deployment models a single `permissions` bag, so the profile-shaped keys degrade to the value
    the stored row implies and the actual grant is exposed on the wire as `permissions` + `canWrite`
    (deviation **P2-3**). `organisation` (the id) is echoed alongside `organisationName` so a client
    can render either without a follow-up lookup.
    """
    return {
        "_id": str(share.id),
        "id": str(share.id),
        "_type": "share",
        "_createdBy": _username(share.created_by),
        "_createdAt": _iso(share.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(share.updated_at),
        "caseId": str(share.case_id),
        "organisation": str(share.organisation_id),
        "organisationName": share.organisation.name,
        "owner": False,
        "profileName": "write" if share.can_write else "read-only",
        "permissions": share.permissions or {},
        "canWrite": share.can_write,
    }


def attachment_json(attachment: Attachment) -> dict[str, Any]:
    """`OutputAttachment` (recorded 5.8.0) — a file attached to a case.

    `hashes` is a list on the wire (TheHive records one or more); this deployment stores a single
    SHA-256, so the list carries that one value. `path` is the opaque server-side storage key, never
    the client's filename — `name` is that, and is display data only.
    """
    return {
        "_id": str(attachment.id),
        "id": str(attachment.id),
        "_type": "attachment",
        "_createdBy": _username(attachment.created_by),
        "_createdAt": _iso(attachment.created_at),
        "_updatedBy": _username(attachment.updated_by),
        "_updatedAt": _iso(attachment.updated_at),
        "name": attachment.name,
        "hashes": [attachment.sha256],
        "size": attachment.size,
        "contentType": attachment.content_type,
        "path": attachment.path,
        "extraData": {},
        "external": attachment.external,
    }


def case_template_json(template: CaseTemplate) -> dict[str, Any]:
    """`OutputCaseTemplate` (recorded 5.8.0): a reusable case blueprint.

    `tags`/`tasks`/`customFields` are echoed as the JSON definitions the row stores. The audit
    pair has no creator columns on `CaseTemplate` and degrades to `null`, the same honest-bucket
    choice `task_json` makes.

    When `template.severity` is `None`, output `"severity": null, "severityLabel": ""` so clients
    can distinguish "no severity set" from "severity 1..4".
    """
    if template.severity is None:
        severity_label = ""
    else:
        severity_label = SEVERITY_LABELS.get(template.severity, "")
    return {
        "_id": str(template.id),
        "id": str(template.id),
        "_type": "CaseTemplate",
        "_createdBy": None,
        "_createdAt": _iso(template.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(template.updated_at),
        "name": template.name,
        "displayName": template.display_name,
        "titlePrefix": template.title_prefix,
        "description": template.description,
        "severity": template.severity,
        "severityLabel": severity_label,
        "tags": list(template.tags or []),
        "flag": template.flag,
        "tlp": template.tlp,
        "tlpLabel": TLP_LABELS.get(template.tlp, ""),
        "pap": template.pap,
        "papLabel": PAP_LABELS.get(template.pap, ""),
        "summary": template.summary,
        "scope": template.scope,
        "caseType": template.case_type,
        "phase": template.phase,
        "tasks": list(template.tasks or []),
        "customFields": list(template.custom_fields or []),
        "extraData": {},
    }
