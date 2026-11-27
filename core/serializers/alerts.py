"""Alert wire representations."""

from __future__ import annotations

from typing import Any

from alerts.models import Alert

from ._common import _iso, _username


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
        "verdict": alert.verdict,
        "ingestionSource": str(alert.ingestion_source_id) if alert.ingestion_source_id else None,
        "ingestionWarnings": alert.ingestion_warnings or {},
        "createdAt": _iso(alert.created_at),
        "updatedAt": _iso(alert.updated_at),
    }
