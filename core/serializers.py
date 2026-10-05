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

from alerts.models import Alert
from automation.models import AutomationRun
from cases.models import Case, CaseObservable, TimelineEvent
from observables.models import Observable


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


def observable_json(link: CaseObservable) -> dict[str, Any]:
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
