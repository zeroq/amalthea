"""Safe wire shapes for MCP reads.

The HTTP serializers in ``core.serializers`` deliberately expose more than an
external agent should receive (for example case detail includes automation run
output logs).  These helpers select only bounded fields and drop raw payloads,
playbook config, and unbounded metadata.
"""

from __future__ import annotations

from typing import Any

from core.serializers import _iso, _username, alert_json, case_json, task_json


def _clip(value: Any, limit: int = 2000) -> str:
    return str(value or "")[:limit]


def safe_alert(alert: Any) -> dict[str, Any]:
    """``alert_json`` without ingestion warnings or unbounded free text."""
    payload = alert_json(alert)
    payload.pop("ingestionWarnings", None)
    payload["title"] = _clip(payload.get("title"), 500)
    payload["description"] = _clip(payload.get("description"))
    payload["summary"] = _clip(payload.get("summary"))
    payload["sourceRef"] = _clip(payload.get("sourceRef"), 500)
    payload["correlationKey"] = _clip(payload.get("correlationKey"), 500)
    return payload


def safe_case(case: Any) -> dict[str, Any]:
    """Case header only; never ``detail=True`` and never unbounded free text."""
    payload = case_json(case, detail=False)
    payload["title"] = _clip(payload.get("title"), 500)
    payload["description"] = _clip(payload.get("description"))
    payload["summary"] = _clip(payload.get("summary"))
    return payload


def safe_task(task: Any) -> dict[str, Any]:
    payload = task_json(task)
    payload["title"] = _clip(payload.get("title"), 500)
    payload["description"] = _clip(payload.get("description"))
    return payload


def safe_observable(
    observable: Any,
    *,
    case_count: int | None = None,
    include_enrichment: bool = False,
) -> dict[str, Any]:
    data_type = getattr(observable.data_type, "name", None)
    payload: dict[str, Any] = {
        "_id": str(observable.id),
        "id": str(observable.id),
        "_type": "observable",
        "dataType": data_type,
        "data": str(observable.data)[:2000],
        "normalizedData": str(observable.normalized_data)[:2000],
        "tlp": observable.tlp,
        "pap": observable.pap,
        "ioc": observable.ioc,
        "sighted": observable.sighted,
        "createdAt": _iso(observable.created_at),
        "updatedAt": _iso(observable.updated_at),
    }
    if case_count is not None:
        payload["caseCount"] = case_count
    if include_enrichment:
        payload["enrichmentData"] = _safe_enrichment(observable.enrichment_data)
    return payload


def _safe_enrichment(value: Any) -> dict[str, Any]:
    """Keep only small, analyst-facing enrichment fields."""
    if not isinstance(value, dict):
        return {}
    allowed = {"score", "verdict", "source", "link", "summary", "lastSeen", "tags"}
    safe: dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, str):
            safe[key] = item[:500]
        elif isinstance(item, (int, float, bool)) or item is None:
            safe[key] = item
        elif isinstance(item, list):
            safe[key] = [str(entry)[:100] for entry in item[:10]]
    return safe


def safe_timeline_event(event: Any) -> dict[str, Any]:
    """Ledger row without free-form metadata (which automation may populate)."""
    return {
        "_id": str(event.id),
        "id": str(event.id),
        "_type": "event",
        "date": _iso(event.date),
        "endDate": _iso(event.end_date),
        "title": str(event.title or "")[:500],
        "description": str(event.description or "")[:2000],
        "kind": event.kind,
        "actor": _username(event.actor),
    }


def safe_automation_run(run: Any) -> dict[str, Any]:
    """No ``output_log``, ``error``, ``celery_task_id`` or playbook config."""
    return {
        "id": str(run.id),
        "playbookName": run.playbook_name,
        "triggerEvent": run.trigger_event,
        "status": run.status,
        "triggeredBy": run.triggered_by,
        "startedAt": _iso(run.started_at),
        "finishedAt": _iso(run.finished_at),
    }


def safe_playbook(playbook: Any) -> dict[str, Any]:
    """Never return ``config`` (URLs, headers, scripts)."""
    return {
        "id": str(playbook.id),
        "name": playbook.name,
        "description": str(playbook.description or "")[:1000],
        "triggerEvent": playbook.trigger_event,
        "isActive": playbook.is_active,
    }
