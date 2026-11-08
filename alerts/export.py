"""Alert JSON export — TODO §19.2 (AIMP-1).

Builds the `AlertExport` v1 document from an :class:`alerts.models.Alert`. Reuses the shared
wire serializers so an alert export and `GET /api/v1/alert/{id}` never drift.

`rawPayload` is inlined deliberately (same recorded deviation as the case export). The internal
`payload_ssdeep` is never exported; it is recomputed on import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alerts.models import Alert
from core.serializers import alert_json, observable_json, procedure_json


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def alert_export_document(alert: Alert) -> dict[str, Any]:
    """Return the `AlertExport` v1 document for ``alert``."""
    alert_payload: dict[str, Any] = {
        **alert_json(alert),
        "rawPayload": alert.raw_payload or {},
    }
    if alert.case_id:
        alert_payload["caseNumber"] = alert.case.number

    observables = [
        observable_json(link)
        for link in alert.alert_observables.select_related("observable__data_type").order_by(
            "-created_at"
        )
    ]
    tags = [
        {"name": link.tag.name}
        for link in alert.tag_links.select_related("tag").order_by("tag__name")
    ]
    custom_fields = [
        {"name": value.custom_field.name, "value": value.value}
        for value in alert.custom_field_values.select_related("custom_field").order_by(
            "order", "custom_field__name"
        )
    ]
    comments = [
        {
            "message": comment.message,
            "createdAt": _iso(comment.created_at),
            "createdBy": comment.created_by.login if comment.created_by else None,
        }
        for comment in alert.comments.select_related("created_by").order_by("created_at")
    ]
    procedures = [
        procedure_json(procedure) for procedure in alert.procedures.order_by("occur_date", "id")
    ]

    return {
        "_type": "AlertExport",
        "version": 1,
        "exportedAt": datetime.now(UTC).isoformat(),
        "alert": alert_payload,
        "observables": observables,
        "tags": tags,
        "customFields": custom_fields,
        "comments": comments,
        "procedures": procedures,
    }
