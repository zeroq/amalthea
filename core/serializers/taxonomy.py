"""Taxonomy wire representations."""

from __future__ import annotations

from typing import Any

from alerts.models import AlertStatus
from cases.models import TTP, CaseStatus, Procedure, Tag
from observables.models import ObservableType

from ._common import _iso


def ttp_json(ttp: TTP) -> dict[str, Any]:
    """Amalthea extension — the editable TTP vocabulary row (no TheHive 5.8 equivalent)."""
    return {
        "_id": str(ttp.id),
        "id": str(ttp.id),
        "_type": "TTP",
        "name": ttp.name,
        "ttpCode": ttp.ttp_code,
        "tactic": ttp.tactic,
        "description": ttp.description,
        "createdAt": _iso(ttp.created_at),
        "updatedAt": _iso(ttp.updated_at),
    }


def procedure_json(procedure: Procedure) -> dict[str, Any]:
    """`OutputProcedure` — the fields TheHive's procedure endpoints return, plus our `ttpId`.

    `occurDate` is ISO-8601 like every other timestamp this API emits (the recorded TheHive shape
    is epoch milliseconds; the house rule here is documented in the module docstring and the
    deviation is recorded in `docs/spec/deviations.md`).
    """
    return {
        "_id": str(procedure.id),
        "id": str(procedure.id),
        "_type": "Procedure",
        "caseId": str(procedure.case_id) if procedure.case_id else None,
        "alertId": str(procedure.alert_id) if procedure.alert_id else None,
        "occurDate": _iso(procedure.occur_date),
        "patternId": procedure.pattern_id or None,
        "patternName": procedure.pattern_name or None,
        "tactic": procedure.tactic or None,
        "tacticLabel": procedure.tactic or None,
        "description": procedure.description,
        "ttpId": str(procedure.ttp_id) if procedure.ttp_id else None,
        "createdAt": _iso(procedure.created_at),
        "updatedAt": _iso(procedure.updated_at),
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
    `label` is an Amalthea extension (human display name); TheHive status shape has no label.
    """
    return {
        "_id": str(status.id),
        "id": str(status.id),
        "_type": type_name,
        "value": status.value,
        "label": status.display_label,
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
