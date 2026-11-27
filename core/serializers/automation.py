"""Automation wire representations."""

from __future__ import annotations

from typing import Any

from automation.models import AutomationRun, Playbook

from ._common import _iso


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
        # `manual` vs `trigger` — who started the run (plan §5.1). Kept next to the key so a
        # reader of one run sees both the dedupe identity and the actor that produced it.
        "triggeredBy": run.triggered_by,
        "triggeredByObservable": (
            str(run.triggered_by_observable_id) if run.triggered_by_observable_id else None
        ),
    }


def playbook_json(playbook: Playbook) -> dict[str, Any]:
    """One playbook as the authoring endpoints return it (plan §6.1).

    An Amalthea extension, not a TheHive entity (deviation **P12-2**), so the wire keys are the
    plain `triggerEvent`/`isActive` spellings the plan specifies rather than a recorded schema's.
    `config` is inlined: unlike `raw_payload` it is analyst-authored configuration, not adversary
    telemetry, and the edit form cannot round-trip what it cannot see.

    Adds `ownerOrg` (org ID) and `ownerOrgName` so the API surface conveys scoping information
    without exposing secret reference data.
    """
    return {
        "_id": str(playbook.id),
        "id": str(playbook.id),
        "_type": "playbook",
        "name": playbook.name,
        "description": playbook.description,
        "triggerEvent": playbook.trigger_event,
        "isActive": playbook.is_active,
        "config": playbook.config or {},
        "ownerOrg": str(playbook.owner_org_id),
        "ownerOrgName": playbook.owner_org.name if playbook.owner_org else None,
        "createdAt": _iso(playbook.created_at),
        "updatedAt": _iso(playbook.updated_at),
    }
