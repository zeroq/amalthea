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
    CASE_CLOSING_VERDICTS,
    CASE_PHASE_CHOICES,
    CASE_SCOPE_CHOICES,
    CASE_TYPE_CHOICES,
    CASE_VERDICT_VALUES,
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
    "verdict",
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
    "verdict": CASE_VERDICT_VALUES,
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


def enforce_close_rule(case: Case, payload: Mapping[str, Any], *, previous_stage: str) -> None:
    """A case may not enter the Closed stage without a recorded verdict and a closing comment.

    Plan-2026-10-17 §12.8(4), decision D1: the rule lives in the service layer so the UI,
    the single PATCH, the bulk PATCH and create-to-Closed are all blocked identically.
    Called from `apply_case_update` (the choke point) and `case_bulk_update`, which does
    not use the applier.

    `previous_stage` is the case's stage *before* the payload was applied. Re-closing an
    already-closed case is a no-op for this rule (there is nothing left to decide); leaving
    Closed is always allowed.
    """
    new_stage = case.status.stage if case.status_id else ""
    if previous_stage == "Closed" or new_stage != "Closed":
        return

    verdict = payload.get("verdict", case.verdict)
    if verdict not in CASE_CLOSING_VERDICTS:
        allowed = ", ".join(CASE_CLOSING_VERDICTS)
        message = f"Closing a case requires a verdict ({allowed})."
        raise CaseUpdateError("ValidationError", message, {"verdict": [message]})

    comment = str(payload.get("comment") or "").strip()
    if not comment:
        message = "Closing a case requires a closing comment."
        raise CaseUpdateError("ValidationError", message, {"comment": [message]})


def resolve_closed_alert_status() -> Any:
    """The `AlertStatus` that means "closed" (Plan-2026-10-17 §12.8(5), decision D2).

    Prefers the `Closed` status seeded by `alerts/0016_seed_closed_alert_status`; falls back
    to the lowest-`order` status with `stage="Closed"` (which is `Dismissed` on a database
    seeded before that migration); auto-creates one as a last resort so a fresh database can
    never strand a close behind a missing row.
    """
    from alerts.models import AlertStatus

    closed_stage = AlertStatus.objects.filter(stage="Closed")
    preferred = closed_stage.filter(value="Closed").first()
    if preferred is not None:
        return preferred
    fallback = closed_stage.order_by("order", "value").first()
    if fallback is not None:
        return fallback
    status, _created = AlertStatus.objects.get_or_create(
        value="Closed",
        defaults={"stage": "Closed", "order": 5, "description": ""},
    )
    return status


def propagate_close_to_alerts(case: Case, verdict: str, *, actor: Any) -> int:
    """Close every alert linked to `case` and carry the case's verdict onto each.

    Plan-2026-10-17 §12.8(5). An analyst who closes a case must never see a still-open
    linked alert in the next render, so this runs synchronously inside the caller's
    transaction rather than on Celery.

    Uses `QuerySet.update()` — no per-row signals — matching the `merge` helper's recorded
    convention. The ledger carries one case-level summary event rather than one row per
    alert, so closing a case with fifty linked alerts does not bury its closing comment.
    Returns the number of alerts closed.
    """
    from alerts.models import Alert
    from identity.models import AuditLog

    alert_ids = list(Alert.objects.filter(case=case).values_list("id", flat=True))
    if not alert_ids:
        return 0

    closed_status = resolve_closed_alert_status()
    Alert.objects.filter(id__in=alert_ids).update(status=closed_status, verdict=verdict)

    append_timeline_event(
        case,
        title=f"Closed {len(alert_ids)} linked alert(s)",
        kind="alerts-closed",
        actor=actor,
        metadata={"count": len(alert_ids), "verdict": verdict},
    )
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None) if actor else None,
        target_org=case.owner_org,
        action="case.alerts_closed",
        target_type="Case",
        target_id=str(case.id),
        ip=None,
        details={"count": len(alert_ids), "verdict": verdict},
    )
    return len(alert_ids)


@transaction.atomic
def apply_case_update(case: Case, payload: Mapping[str, Any], *, actor: Any) -> list[str]:
    """Apply a case update from a plain mapping (API `request.data` or a UI POST dict).

    The single source of truth for the updatable field set, tag handling, the timeline events
    (`status-changed`, `assigned`/`Unassigned`, `classification-changed`, `verdict-changed`,
    `severity-changed`, `tags-changed`) and the "no updatable field" error.
    """
    previous_status = case.status.value if case.status_id else None
    previous_stage = case.status.stage if case.status_id else ""
    # Captured before the field loop mutates `case`, so the assignment event below can tell an
    # assignee that actually changed from an update that merely restated the same one.
    previous_assignee_id = case.assignee_id

    # Capture previous classification/verdict/severity values for change detection
    # (Q-D5 audit + timeline, extended by Plan-2026-10-17 §12.8).
    tracked_fields = ("scope", "case_type", "data_classification", "phase", "severity", "verdict")
    previous_values = {f: getattr(case, f) for f in tracked_fields}

    fields = _set_case_fields(case, payload, actor=actor)
    # Enforce the close rule after the payload is applied (so `case.status` is the new stage)
    # but before anything is written, so a rejected close touches nothing at all.
    enforce_close_rule(case, payload, previous_stage=previous_stage)
    tags_touched = any(key in payload for key in ("tags", "addTags", "removeTags"))
    if tags_touched:
        _apply_case_tags(case, payload)

    if not fields and not tags_touched:
        raise CaseUpdateError("BadRequest", "No updatable field supplied", {})

    closing = previous_stage != "Closed" and case.status.stage == "Closed"
    reopening = previous_stage == "Closed" and case.status.stage != "Closed"
    if closing:
        # `stamp_closed_date()` only ever *sets* closed_date; it never persists it, because
        # the save below is `update_fields`-scoped and closed_date is not in `fields`
        # (Plan-2026-10-17 defect D4a — API/edir-modal closes left closed_date NULL).
        case.stamp_closed_date()
        if case.end_date is None:
            case.end_date = case.closed_date
    elif reopening:
        # `stamp_closed_date()` never clears it either, so a reopened case kept a stale
        # closed_date and the open-list filters (`closed_date IS NULL`) hid it.
        case.closed_date = None
    if closing or reopening:
        fields = [f for f in fields if f not in ("closed_date", "end_date")]
        fields += ["closed_date", "end_date"]

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

    # Classification/verdict/severity changes → timeline + audit log (only when value changes)
    _emit_tracked_changes(case, previous_values, actor=actor)

    if tags_touched:
        append_timeline_event(
            case,
            title="Tags updated",
            kind="tags-changed",
            actor=actor,
            metadata={"tags": list(case.tags.values_list("name", flat=True))},
        )

    if closing:
        propagate_close_to_alerts(case, case.verdict, actor=actor)

    return fields


def _capture_classification_values(case: Case) -> dict[str, Any]:
    """Capture the current classification field values for later change detection."""
    return {
        f: getattr(case, f)
        for f in ("scope", "case_type", "data_classification", "phase", "severity", "verdict")
    }


#: Timeline kinds and audit action prefixes for the tracked scalar/enum fields. `severity` and
#: `verdict` were added by Plan-2026-10-17 §12.8(1)/(3); the four classification fields are the
#: pre-existing Q-D5 set.
_TRACKED_FIELD_KINDS: dict[str, tuple[str, str]] = {
    "scope": ("classification-changed", "case.scope"),
    "case_type": ("classification-changed", "case.case_type"),
    "data_classification": ("classification-changed", "case.data_classification"),
    "phase": ("classification-changed", "case.phase"),
    "severity": ("severity-changed", "case.severity"),
    "verdict": ("verdict-changed", "case.verdict"),
}


def _emit_tracked_changes(case: Case, previous: dict[str, Any], *, actor: Any) -> None:
    """Emit TimelineEvent and AuditLog for any tracked field that changed.

    Called after `case.save()` so `case` has the new values. `previous` is a dict
    mapping field name -> old value, as returned by `_capture_classification_values()`.
    """
    from identity.models import AuditLog

    for field_name, old_value in previous.items():
        new_value = getattr(case, field_name)
        if new_value == old_value:
            continue
        kind, action = _TRACKED_FIELD_KINDS[field_name]
        title = (
            f"Verdict set: {old_value} → {new_value}"
            if field_name == "verdict"
            else f"Classification changed: {field_name}"
            if field_name in ("scope", "case_type", "data_classification", "phase")
            else f"{field_name.capitalize()} changed: {old_value} → {new_value}"
        )
        append_timeline_event(
            case,
            title=title,
            kind=kind,
            actor=actor,
            metadata={"field": field_name, "from": old_value, "to": new_value},
        )
        # `target_type` is "Case" (the model's human name, matching the case Audit tab's
        # `target_type="Case"` filter). It used to be written lowercase "case", so no
        # classification audit row was ever visible on the case it described (defect D4b).
        AuditLog.objects.create(
            actor=actor,
            actor_org=getattr(actor, "org", None) if actor else None,
            target_org=case.owner_org,
            action=action,
            target_type="Case",
            target_id=str(case.id),
            ip=None,
            details={"field": field_name, "from": old_value, "to": new_value},
        )
