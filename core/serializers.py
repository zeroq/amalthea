"""Wire representations for the MVP endpoints.

One module so the same entity cannot be rendered two ways in two places: the alert list, the alert
detail and the merge response all read from `alert_json`, and the case page, the case API and the
import response all read from `case_json`.

Two conventions come from the recorded TheHive 5.8.0 examples and are deliberate:

* the id is exposed as **`_id`** (TheHive's own spelling) *and* as `id`, because a client written
  against either reading should not 400 on the other;
* timestamps are ISO-8601 with a timezone, never naive, because `USE_TZ` is on and a naive
  rendering is a bug that only shows up on the other side of the Atlantic.

`raw_payload` is **not** inlined here. It has its own `/raw` endpoint because D9 promises the
sender's bytes; a list of 200 alerts each carrying a full SIEM event would make that promise
meaningless and the payload enormous.
"""

from __future__ import annotations

from typing import Any

from alerts.models import Alert, AlertObservable, AlertStatus
from automation.models import AutomationRun
from cases.models import (
    Attachment,
    Case,
    CaseObservable,
    CaseStatus,
    Comment,
    CustomField,
    Page,
    Share,
    Tag,
    Task,
    TimelineEvent,
)
from identity.models import Organisation, User
from observables.models import Observable, ObservableType


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _username(user: Any) -> str | None:
    return getattr(user, "login", None) if user is not None else None


def alert_json(alert: Alert) -> dict[str, Any]:
    return {
        "_id": str(alert.id),
        "id": str(alert.id),
        "_type": "alert",
        "type": alert.type,
        "source": alert.source,
        "sourceRef": alert.source_ref,
        "title": alert.title,
        "description": alert.description,
        "summary": alert.summary,
        "severity": alert.severity,
        "status": alert.status.value if alert.status_id else None,
        "date": _iso(alert.date),
        "tlp": alert.tlp,
        "pap": alert.pap,
        "flag": alert.flag,
        "follow": alert.follow,
        "assignee": _username(alert.assignee),
        "caseId": str(alert.case_id) if alert.case_id else None,
        "correlationKey": alert.correlation_key,
        "ingestionSource": str(alert.ingestion_source_id) if alert.ingestion_source_id else None,
        "ingestionWarnings": alert.ingestion_warnings or {},
        "createdAt": _iso(alert.created_at),
        "updatedAt": _iso(alert.updated_at),
    }


def observable_json(link: CaseObservable | AlertObservable) -> dict[str, Any]:
    """One link row as `OutputObservable`.

    The helper takes either parent because the shape is identical for a case link and an alert
    link — only `addedAt` (the link's own creation) differs, and both models carry it. Rendering
    one shape in two places is what this module's docstring exists to prevent.
    """
    observable: Observable = link.observable
    return {
        "_id": str(observable.id),
        "id": str(observable.id),
        "_type": "observable",
        "dataType": observable.data_type.name,
        "data": observable.data,
        "normalizedData": observable.normalized_data,
        "tlp": observable.tlp,
        "pap": observable.pap,
        "ioc": observable.ioc,
        "sighted": observable.sighted,
        "enrichmentData": observable.enrichment_data or {},
        "caseCount": observable.case_observables.count(),
        "addedAt": _iso(link.created_at),
    }


def timeline_event_json(event: TimelineEvent) -> dict[str, Any]:
    return {
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


def user_json(user: User) -> dict[str, Any]:
    """`OutputUser` — what `POST /api/v1/login` returns.

    `hasKey`/`hasPassword` are computed rather than hardwired: reporting `hasKey: false` for an
    account that does hold a key would be a lie on the wire, and the recorded schema describes
    both as "whether the account has …", i.e. a question, not a constant. `hasMFA`/`locked` are
    constants only because no model column answers them yet (plan §4: no migrations).
    """
    return {
        "_id": str(user.id),
        "_type": "user",
        "login": user.login,
        "name": user.get_full_name() or user.username,
        "org": user.org.name if user.org else None,
        "hasKey": user.api_keys.exists(),
        "hasPassword": user.has_usable_password(),
        "hasMFA": False,
        "locked": False,
        "_createdBy": None,
        "_createdAt": _iso(user.date_joined),
    }


def automation_run_json(run: AutomationRun) -> dict[str, Any]:
    return {
        "_id": str(run.id),
        "id": str(run.id),
        "_type": "automation-run",
        "playbookName": run.playbook_name,
        "triggerEvent": run.trigger_event,
        "status": run.status,
        "outputLog": run.output_log,
        "error": run.error,
        "startedAt": _iso(run.started_at),
        "finishedAt": _iso(run.finished_at),
        "idempotencyKey": run.idempotency_key,
        "triggeredByObservable": (
            str(run.triggered_by_observable_id) if run.triggered_by_observable_id else None
        ),
    }


def case_json(case: Case, *, detail: bool = False) -> dict[str, Any]:
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
    }
    if detail:
        payload["observables"] = [
            observable_json(link)
            for link in CaseObservable.objects.select_related("observable__data_type")
            .filter(case=case)
            .order_by("-created_at")
        ]
        payload["timeline"] = [
            timeline_event_json(e)
            for e in TimelineEvent.objects.filter(case=case).order_by("date", "id")
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


def organisation_json(org: Organisation) -> dict[str, Any]:
    """`OutputOrganisation` for the caller's own organisation.

    `Organisation` is `UUIDModel`-only (plan §4: no timestamps minted for it), so the audit
    pair the recorded schema requires degrades to `null` rather than being invented — the
    same honest-bucket choice `task_json` makes for its creator columns. `taskRule`/
    `observableRule`/`locked` have no column yet and are emitted as the schema-shaped
    constants the stored tenant actually implies.
    """
    return {
        "_id": str(org.id),
        "id": str(org.id),
        "_type": "Organisation",
        "name": org.name,
        "description": org.description,
        "taskRule": "",
        "observableRule": "",
        "locked": False,
        "_createdBy": None,
        "_createdAt": None,
        "_updatedBy": None,
        "_updatedAt": None,
        "extraData": {},
    }


def observable_type_json(otype: ObservableType) -> dict[str, Any]:
    """`OutputObservableType` (recorded 5.8.0 OpenAPI): `isAttachment`/`isCaseSensitive`."""
    return {
        "_id": str(otype.id),
        "id": str(otype.id),
        "_type": "ObservableType",
        "name": otype.name,
        "isAttachment": otype.is_attachment,
        "isCaseSensitive": otype.is_case_sensitive,
        "_createdBy": None,
        "_createdAt": _iso(otype.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(otype.updated_at),
        "extraData": {},
    }


def _status_json(status: CaseStatus | AlertStatus, type_name: str) -> dict[str, Any]:
    """Shared body of `case_status_json`/`alert_status_json`; only `_type` differs.

    `colour` is a documented `InputCreate*` field with no column on either model, so it is
    accepted-and-ignored rather than echoed back as a value nothing stored (ADR D11).
    """
    return {
        "_id": str(status.id),
        "id": str(status.id),
        "_type": type_name,
        "value": status.value,
        "stage": status.stage,
        "order": status.order,
        "description": status.description,
        "hidden": status.hidden,
        "_createdBy": None,
        "_createdAt": _iso(status.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(status.updated_at),
        "extraData": {},
    }


def case_status_json(status: CaseStatus) -> dict[str, Any]:
    """`OutputCaseStatus` (recorded 5.8.0 OpenAPI)."""
    return _status_json(status, "CaseStatus")


def alert_status_json(status: AlertStatus) -> dict[str, Any]:
    """`OutputAlertStatus` (recorded 5.8.0 OpenAPI)."""
    return _status_json(status, "AlertStatus")


def tag_json(tag: Tag) -> dict[str, Any]:
    """`OutputTag`.

    The recorded 5.8.0 schema splits a tag into `namespace` + `predicate`; our `Tag` carries
    one `name`, so a custom tag renders as `namespace="_freetags_"`, `predicate=name`, with
    the taxonomy-only `value` left empty. `hidden` has no column and defaults to `False`.
    """
    return {
        "_id": str(tag.id),
        "id": str(tag.id),
        "_type": "Tag",
        "_createdBy": None,
        "_createdAt": _iso(tag.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(tag.updated_at),
        "namespace": "_freetags_",
        "predicate": tag.name,
        "value": "",
        "description": tag.description,
        "colour": tag.colour,
        "hidden": False,
        "extraData": {},
    }
