"""Alert JSON import — TODO §19.3 (AIMP-2..5).

A single service so the UI (and any future API) shares one validation, resolution and atomic
write path. Locked decisions (§19.6):

* preserve exported ``status`` by value, fallback + warning;
* import **unlinked** (``caseNumber`` is informational only);
* duplicate ``(owner_org, source, type, source_ref)`` is an atomic hard failure;
* create missing tags, skip unknown custom fields / observable types with a warning;
* never import ``payload_ssdeep`` — recompute it;
* never trust exported ids, orgs or assignee logins outside the actor's organisation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from alerts.escalation import resolve_alert_status
from alerts.hashing import compute_payload_ssdeep
from alerts.models import (
    Alert,
    AlertCustomFieldValue,
    AlertObservable,
    AlertStatus,
    AlertTagLink,
)
from cases.models import Comment, CustomField, Procedure, Tag
from identity.models import User
from observables.extractor import resolve_observable

MAX_BYTES = 5 * 1024 * 1024
MAX_NODES = 10_000
MAX_DEPTH = 20
MAX_CHILDREN = 5_000
SUPPORTED_VERSION = 1


class AlertImportError(Exception):
    """A structured import failure safe to show to the operator."""

    def __init__(self, code: str, message: str, existing_alert: Alert | None = None) -> None:
        self.code = code
        self.message = message
        self.existing_alert = existing_alert
        super().__init__(message)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def parse_alert_export(raw: bytes) -> dict[str, Any]:
    """Decode and bound-check an uploaded export before any database work."""
    if len(raw) > MAX_BYTES:
        raise AlertImportError("oversized_file", "File exceeds the 5 MiB limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AlertImportError("malformed_json", "File is not valid UTF-8 text.") from exc
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise AlertImportError("malformed_json", f"Invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise AlertImportError("invalid_document", "The document must be a JSON object.")
    _check_bounds(document)
    return document


def _check_bounds(document: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(document, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise AlertImportError("too_large", "Document contains too many values.")
        if depth > MAX_DEPTH:
            raise AlertImportError("too_deep", "Document nesting is too deep.")
        if isinstance(value, dict):
            if len(value) > MAX_CHILDREN:
                raise AlertImportError("too_large", "A document object has too many keys.")
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            if len(value) > MAX_CHILDREN:
                raise AlertImportError("too_large", "A document list has too many items.")
            stack.extend((child, depth + 1) for child in value)


def _safe_filename(name: str) -> str:
    base = Path(name or "alert-export.json").name[:120]
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base) or "alert-export.json"


def _clamp_int(raw: Any, default: int, low: int, high: int, field: str, warnings: list[str]) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        warnings.append(f"{field}: invalid value; using {default}")
        return default
    if value < low or value > high:
        warnings.append(f"{field}: clamped to {low}..{high}")
        return min(max(value, low), high)
    return value


def _resolve_status(raw: Any, warnings: list[str]) -> AlertStatus:
    value = str(raw or "New")[:64]
    status = AlertStatus.objects.filter(value=value).first()
    if status is not None:
        return status
    warnings.append(f"status: unknown {value!r}; using the default")
    fallback = AlertStatus.objects.filter(value="New").first()
    if fallback is None:
        fallback = resolve_alert_status("New", warnings=warnings)
    return fallback


def _resolve_assignee(raw: Any, actor: User, warnings: list[str]) -> User | None:
    login = str(raw or "").strip()
    if not login:
        return None
    assignee = User.objects.filter(login=login, org_id=actor.org_id).first()
    if assignee is None:
        warnings.append(f"assignee: {login!r} is not in this organisation; unassigned")
    return assignee


def _resolve_date(raw: Any, warnings: list[str]) -> datetime:
    if not raw:
        return timezone.now()
    parsed = parse_datetime(str(raw))
    if parsed is None:
        warnings.append("date: invalid ISO timestamp; using now")
        return timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed


def _import_observables(alert: Alert, rows: Any, actor: User, warnings: list[str]) -> None:
    if not isinstance(rows, list):
        return
    for index, row in enumerate(rows[:MAX_CHILDREN]):
        if not isinstance(row, dict):
            warnings.append(f"observable[{index}]: not an object; skipped")
            continue
        type_name = str(row.get("dataType") or row.get("type") or "").strip()
        data = str(row.get("data") or "").strip()
        if not type_name or not data:
            warnings.append(f"observable[{index}]: missing dataType/data; skipped")
            continue
        resolved = resolve_observable(type_name, data, owner_org=alert.owner_org)
        if resolved is None:
            warnings.append(f"observable[{index}]: unknown type {type_name!r}; skipped")
            continue
        observable, _created = resolved
        AlertObservable.objects.get_or_create(
            alert=alert, observable=observable, defaults={"added_by": actor}
        )


def _import_tags(alert: Alert, rows: Any, warnings: list[str]) -> None:
    if not isinstance(rows, list):
        return
    seen: set[str] = set()
    for index, row in enumerate(rows[:MAX_CHILDREN]):
        if isinstance(row, dict):
            raw_name = row.get("name")
        else:
            raw_name = row
        name = str(raw_name or "").strip()[:100]
        if not name:
            warnings.append(f"tag[{index}]: empty name; skipped")
            continue
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        tag, _created = Tag.objects.get_or_create(name=name)
        AlertTagLink.objects.get_or_create(alert=alert, tag=tag)


def _import_custom_fields(alert: Alert, rows: Any, warnings: list[str]) -> None:
    if not isinstance(rows, list):
        return
    seen: set[str] = set()
    for index, row in enumerate(rows[:MAX_CHILDREN]):
        if not isinstance(row, dict):
            warnings.append(f"customField[{index}]: not an object; skipped")
            continue
        name = str(row.get("name") or "").strip()[:100]
        if not name:
            warnings.append(f"customField[{index}]: missing name; skipped")
            continue
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        field = CustomField.objects.filter(name=name).first()
        if field is None:
            warnings.append(f"customField[{index}]: unknown field {name!r}; skipped")
            continue
        AlertCustomFieldValue.objects.get_or_create(
            alert=alert,
            custom_field=field,
            defaults={"value": row.get("value", {})},
        )


def _import_comments(alert: Alert, rows: Any, actor: User, warnings: list[str]) -> None:
    if not isinstance(rows, list):
        return
    for index, row in enumerate(rows[:MAX_CHILDREN]):
        message = row.get("message") if isinstance(row, dict) else None
        if not isinstance(message, str) or not message.strip():
            warnings.append(f"comment[{index}]: empty message; skipped")
            continue
        Comment.objects.create(alert=alert, message=message, created_by=actor)


def _import_procedures(alert: Alert, rows: Any, warnings: list[str]) -> None:
    if not isinstance(rows, list):
        return
    for index, row in enumerate(rows[:MAX_CHILDREN]):
        if not isinstance(row, dict):
            warnings.append(f"procedure[{index}]: not an object; skipped")
            continue
        occur_date = (
            parse_datetime(str(row.get("occurDate") or "")) if row.get("occurDate") else None
        )
        Procedure.objects.create(
            alert=alert,
            occur_date=occur_date,
            pattern_id=str(row.get("patternId") or "")[:255],
            pattern_name=str(row.get("patternName") or "")[:255],
            tactic=str(row.get("tactic") or "")[:100],
            description=str(row.get("description") or ""),
        )


def import_alert_document(document: dict[str, Any], *, actor: User, source_filename: str) -> Alert:
    """Create a new alert in ``actor``'s organisation from an `AlertExport` v1 document."""
    if document.get("_type") != "AlertExport":
        raise AlertImportError("wrong_type", "This is not an AlertExport document.")
    if document.get("version") != SUPPORTED_VERSION:
        raise AlertImportError("unsupported_version", "Only AlertExport version 1 is supported.")

    data = document.get("alert")
    if not isinstance(data, dict):
        raise AlertImportError("missing_alert", "The export has no 'alert' object.")

    required = ("title", "type", "source", "sourceRef")
    missing = [name for name in required if not str(data.get(name) or "").strip()]
    if missing:
        raise AlertImportError(
            "missing_required_fields",
            f"Missing required field(s): {', '.join(missing)}.",
        )

    warnings: list[str] = []
    raw_payload = data.get("rawPayload")
    if not isinstance(raw_payload, dict):
        warnings.append("rawPayload: not an object; using {}")
        raw_payload = {}

    with transaction.atomic():
        status = _resolve_status(data.get("status"), warnings)
        source_ref = str(data["sourceRef"])[:255]
        source = str(data["source"])[:100]
        alert_type = str(data["type"])[:100]

        existing = Alert.objects.filter(
            owner_org_id=actor.org_id,
            source=source,
            type=alert_type,
            source_ref=source_ref,
        ).first()
        if existing is not None:
            raise AlertImportError(
                "duplicate",
                "An alert with this source/type/sourceRef already exists.",
                existing_alert=existing,
            )

        try:
            alert = Alert.objects.create(
                owner_org_id=actor.org_id,
                title=str(data["title"])[:500],
                type=alert_type,
                source=source,
                source_ref=source_ref,
                description=str(data.get("description") or ""),
                summary=str(data.get("summary") or ""),
                severity=_clamp_int(data.get("severity"), 2, 1, 4, "severity", warnings),
                tlp=_clamp_int(data.get("tlp"), 2, 0, 4, "tlp", warnings),
                pap=_clamp_int(data.get("pap"), 2, 0, 3, "pap", warnings),
                status=status,
                date=_resolve_date(data.get("date"), warnings),
                assignee=_resolve_assignee(data.get("assignee"), actor, warnings),
                flag=data.get("flag") is True,
                follow=data.get("follow") is True,
                verdict=str(data.get("verdict") or "undefined")[:32] or "undefined",
                correlation_key=str(data.get("correlationKey") or "")[:256],
                raw_payload=raw_payload,
                payload_ssdeep=compute_payload_ssdeep(str(data.get("summary") or ""), raw_payload),
                ingestion_warnings={},
            )
        except IntegrityError as exc:
            raise AlertImportError(
                "duplicate", "An alert with this source/type/sourceRef already exists."
            ) from exc

        _import_observables(alert, document.get("observables"), actor, warnings)
        _import_tags(alert, document.get("tags"), warnings)
        _import_custom_fields(alert, document.get("customFields"), warnings)
        _import_comments(alert, document.get("comments"), actor, warnings)
        _import_procedures(alert, document.get("procedures"), warnings)

        provenance: dict[str, Any] = {
            "at": datetime.now(UTC).isoformat(),
            "by": actor.login,
            "sourceFile": _safe_filename(source_filename),
            "originalId": str(data.get("id") or data.get("_id") or ""),
        }
        if warnings:
            provenance["warnings"] = warnings
        alert.ingestion_warnings = {"imported": provenance}
        alert.save(update_fields=["ingestion_warnings", "updated_at"])

    return alert
