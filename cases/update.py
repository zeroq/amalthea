"""Request-free case update logic shared by the API and the UI (P2 C2).

Extracted from `cases.views._update_case` so the DRF view and the server-rendered
`ui-case-edit` POST call the exact same applier — one definition for the field set, tag
handling, timeline kinds and error shape, so the two surfaces cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction

from alerts.escalation import resolve_case_status
from cases.ledger import append_timeline_event
from cases.models import Case, CaseTagLink, Tag
from cases.tagging import tag_names_from_payload
from core.enums import (
    CASE_PHASE_CHOICES,
    CASE_SCOPE_CHOICES,
    CASE_TYPE_CHOICES,
    DATA_CLASSIFICATION_CHOICES,
    PAP_MAX,
    PAP_MIN,
    SEVERITY_MAX,
    SEVERITY_MIN,
    TLP_MAX,
    TLP_MIN,
)
from identity.scoping import is_admin

#: Scalar/enum fields the update surface accepts. One definition shared by the single PATCH
#: (`_update_case`), the bulk PATCH (`_bulk_update_cases`) and the UI (`ui-case-edit`).
CASE_SCALAR_FIELDS = (
    "title",
    "description",
    "summary",
    "severity",
    "tlp",
    "pap",
    "flag",
    "scope",
    "case_type",
    "data_classification",
    "phase",
)

#: The three enum scalars carry a DB `CheckConstraint` (core/enums.py). Validating here means an
#: out-of-range value is a clean 400 / form error instead of an unhandled `IntegrityError` 500 —
#: and it fixes the API and the UI in one place (SEC-2026-10-11 L1).
_CASE_RANGE_FIELDS: dict[str, tuple[int, int]] = {
    "severity": (SEVERITY_MIN, SEVERITY_MAX),
    "tlp": (TLP_MIN, TLP_MAX),
    "pap": (PAP_MIN, PAP_MAX),
}

#: String enum fields with their allowed values (derived from core/enums.py, never hardcoded).
#: An out-of-vocabulary value raises CaseUpdateError("BadRequest", ...); an empty/None value is
#: dropped (keeps the model default) — mirroring the _CASE_RANGE_FIELDS tolerance.
_CASE_ENUM_FIELDS: dict[str, tuple[str, ...]] = {
    "scope": tuple(v for v, _ in CASE_SCOPE_CHOICES),
    "case_type": tuple(v for v, _ in CASE_TYPE_CHOICES),
    "data_classification": tuple(v for v, _ in DATA_CLASSIFICATION_CHOICES),
    "phase": tuple(v for v, _ in CASE_PHASE_CHOICES),
}


class CaseUpdateError(ValueError):
    """Invalid input, carrying the exact 400 body the API returns.

    The DRF wrapper translates this into a `Response`; the UI turns it into a form error, so
    both surfaces report the same thing for the same input (P2 C2).
    """

    def __init__(self, error_type: str, message: str, fields: dict[str, list[str]]) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.fields = fields

    def to_response_payload(self) -> dict[str, Any]:
        return {"type": self.error_type, "message": self.message, "fields": self.fields}


def _resolve_assignee(value: Any, org: Any) -> Any:
    """Assignee value → `User` **within `org`**, or `None` to unassign.

    Delegates to the API's `_resolve_login` (login first, then UUID; an unknown value
    unassigns rather than raising — ADR §D11) so the UI and the API read an assignee
    identically. The owner org is threaded through so a crafted payload cannot bind the case
    to a principal in another organisation (SEC-2026-10-11 M1). Imported lazily because
    `cases.views` imports this module.
    """
    from cases.views import _resolve_login

    return _resolve_login(value, org=org)


def _set_case_fields(
    case: Case, payload: Mapping[str, Any], *, warnings: list[str] | None = None, actor: Any
) -> list[str]:
    """Apply the scalar/enum FK fields of `payload` to `case`; return the touched columns.

    `status` goes through the ADR-002 §D11 tolerant resolver (an unknown value is
    auto-created at its own stage) — the UI validates the posted value against the existing
    vocabulary *before* calling, so a form field can never mint a new status.

    `warnings` is an out-parameter for a caller that must not swallow the resolver's record of an
    auto-created status: `cases.create.create_case` stores it on `ingestion_warnings`. The update
    path passes nothing and keeps discarding it (the recorded behaviour of a PATCH).

    `actor` is required for the `data_classification` admin gate (Q-D5).
    """
    fields: list[str] = []
    for key in CASE_SCALAR_FIELDS:
        if key not in payload:
            continue
        value = payload[key]

        # Integer range fields (severity, tlp, pap)
        bounds = _CASE_RANGE_FIELDS.get(key)
        if bounds is not None:
            low, high = bounds
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = None
            if value is None or not low <= value <= high:
                message = f"{key} must be between {low} and {high}"
                raise CaseUpdateError("BadRequest", message, {key: [message]})
            setattr(case, key, value)
            fields.append(key)
            continue

        # String enum fields (scope, case_type, data_classification, phase)
        allowed = _CASE_ENUM_FIELDS.get(key)
        if allowed is not None:
            # Admin gate for data_classification (Q-D5): admin/superAdmin only
            if key == "data_classification" and not is_admin(actor):
                message = "data_classification may only be changed by an administrator"
                raise CaseUpdateError("AuthorizationError", message, {key: [message]})
            # Empty/None keeps the model default — mirror _CASE_RANGE_FIELDS tolerance
            if value is None or value == "":
                continue
            value_str = str(value).strip()
            if value_str not in allowed:
                message = f"{key} must be one of: {', '.join(allowed)}"
                raise CaseUpdateError("BadRequest", message, {key: [message]})
            setattr(case, key, value_str)
            fields.append(key)
            continue

        # Regular scalar fields
        setattr(case, key, value)
        fields.append(key)

    if "status" in payload:
        case.status = resolve_case_status(
            str(payload["status"]), warnings=warnings if warnings is not None else []
        )
        fields.append("status")
    if "assignee" in payload:
        case.assignee = _resolve_assignee(payload["assignee"], case.owner_org)
        fields.append("assignee")
    return fields


def _apply_case_tags(case: Case, payload: Mapping[str, Any]) -> None:
    """`tags` (replace), `addTags`/`removeTags` (delta) — the recorded update shapes.

    Applied on the `CaseTagLink` through-table, matching the tag-link endpoint: a tag that
    does not exist yet is created on attach.
    """
    names = tag_names_from_payload(dict(payload))
    if names is not None:
        CaseTagLink.objects.filter(case=case).exclude(tag__name__in=names).delete()
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            CaseTagLink.objects.get_or_create(case=case, tag=tag)
    for key, attach in (("addTags", True), ("removeTags", False)):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for name in raw:
            if not isinstance(name, str) or not name.strip():
                continue
            if attach:
                tag, _created = Tag.objects.get_or_create(name=name.strip()[:100])
                CaseTagLink.objects.get_or_create(case=case, tag=tag)
            else:
                CaseTagLink.objects.filter(case=case, tag__name=name.strip()).delete()


@transaction.atomic
def apply_case_update(case: Case, payload: Mapping[str, Any], *, actor: Any) -> list[str]:
    """Apply a case update from a plain mapping (API `request.data` or a UI POST dict).

    The single source of truth for the updatable field set, tag handling, the timeline events
    (`status-changed`, `assigned`/`Unassigned`, `classification-changed`) and the "no updatable field" error.
    """
    previous_status = case.status.value if case.status_id else None
    # Captured before the field loop mutates `case`, so the assignment event below can tell an
    # assignee that actually changed from an update that merely restated the same one.
    previous_assignee_id = case.assignee_id

    # Capture previous classification values for change detection (Q-D5 audit + timeline)
    classification_fields = ("scope", "case_type", "data_classification", "phase")
    previous_classification = {f: getattr(case, f) for f in classification_fields}

    fields = _set_case_fields(case, payload, actor=actor)
    tags_touched = any(key in payload for key in ("tags", "addTags", "removeTags"))
    if tags_touched:
        _apply_case_tags(case, payload)

    if not fields and not tags_touched:
        raise CaseUpdateError("BadRequest", "No updatable field supplied", {})

    if fields:
        case.save(update_fields=[*fields, "updated_at"])
    case.refresh_from_db()

    new_status = case.status.value if case.status_id else None
    if new_status != previous_status:
        append_timeline_event(
            case,
            title=f"Status changed: {previous_status} → {new_status}",
            description=str(payload.get("comment") or ""),
            kind="status-changed",
            actor=actor,
            metadata={"from": previous_status, "to": new_status},
        )
    if case.assignee_id != previous_assignee_id:
        login = case.assignee.login if case.assignee else None
        append_timeline_event(
            case,
            title=f"Assigned to {login}" if login else "Unassigned",
            kind="assigned",
            actor=actor,
            metadata={"assignee": login},
        )

    # Classification changes → timeline + audit log (only when value actually changes)
    _emit_classification_changes(case, previous_classification, actor=actor)

    return fields


def _capture_classification_values(case: Case) -> dict[str, str]:
    """Capture the current classification field values for later change detection."""
    classification_fields = ("scope", "case_type", "data_classification", "phase")
    return {f: getattr(case, f) for f in classification_fields}


def _emit_classification_changes(case: Case, previous: dict[str, str], *, actor: Any) -> None:
    """Emit TimelineEvent and AuditLog for any classification fields that changed.

    Called after `case.save()` so `case` has the new values. `previous` is a dict
    mapping field name -> old value, as returned by `_capture_classification_values()`.
    """
    from identity.models import AuditLog

    for field_name, old_value in previous.items():
        new_value = getattr(case, field_name)
        if new_value != old_value:
            append_timeline_event(
                case,
                title=f"Classification changed: {field_name}",
                kind="classification-changed",
                actor=actor,
                metadata={"field": field_name, "from": old_value, "to": new_value},
            )
            AuditLog.objects.create(
                actor=actor,
                actor_org=getattr(actor, "org", None) if actor else None,
                target_org=case.owner_org,
                action=f"case.{field_name}",
                target_type="case",
                target_id=str(case.id),
                ip=None,
            )
