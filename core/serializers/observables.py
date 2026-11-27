"""Observable wire representations."""

from __future__ import annotations

from typing import Any

from alerts.models import AlertObservable
from cases.models import CaseObservable
from observables.models import Observable, ObservableSuggestion, RejectedObservable

from ._common import _iso, _username


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


def observable_suggestion_json(suggestion: ObservableSuggestion) -> dict[str, Any]:
    """`OutputObservableSuggestion` — a suggested observable awaiting analyst review.

    Serializes the suggestion with its provenance, status, and linked observable (if accepted).
    """
    payload: dict[str, Any] = {
        "_id": str(suggestion.id),
        "id": str(suggestion.id),
        "_type": "ObservableSuggestion",
        "dataType": suggestion.data_type.name,
        "data": suggestion.raw_data,
        "normalizedData": suggestion.normalized_data,
        "status": suggestion.status,
        "sourcePaths": suggestion.source_paths,
        "reason": suggestion.reason,
        "filteredOut": suggestion.filtered_out,
        "filterReason": suggestion.filter_reason,
        "enrichmentData": suggestion.enrichment_data or {},
        "sourceAlertId": str(suggestion.source_alert_id) if suggestion.source_alert_id else None,
        "caseId": str(suggestion.case_id) if suggestion.case_id else None,
        "observableId": str(suggestion.observable_id) if suggestion.observable_id else None,
        "reviewedBy": _username(suggestion.reviewed_by),
        "reviewedAt": _iso(suggestion.reviewed_at),
        "createdAt": _iso(suggestion.created_at),
        "updatedAt": _iso(suggestion.updated_at),
    }
    return payload


def rejected_observable_json(obj: RejectedObservable) -> dict[str, Any]:
    """`OutputRejectedObservable` — JSON-safe camelCase wire representation.

    Returns a flat dict with the keys required by the API spec:
    id, type (type name), value (normalized_data), reason (token), reasonLabel,
    isPromoted, alertId, alertTitle, createdAt, updatedAt.
    """
    # Type name from the FK
    type_name = obj.data_type.name if obj.data_type else ""

    # Normalized value
    value = obj.normalized_data or ""

    # Reason token (from REJECTED_REASON_CHOICES) and label
    reason_token = obj.reason or ""
    reason_label = (
        dict(RejectedObservable.REJECTED_REASON_CHOICES).get(reason_token, reason_token)
        if reason_token
        else ""
    )

    # Alert ID and title (nullable)
    alert_id = str(obj.alert.id) if obj.alert else None
    alert_title = obj.alert.title if obj.alert else None

    return {
        "id": str(obj.id),
        "type": type_name,
        "value": value,
        "reason": reason_token,
        "reasonLabel": reason_label,
        "isPromoted": obj.is_promoted,
        "alertId": alert_id,
        "alertTitle": alert_title,
        "createdAt": obj.created_at.isoformat() if obj.created_at else None,
        "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
    }
