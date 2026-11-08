"""Case JSON import — TODO §18.4 (IMP-2..5).

One service for every surface (the UI today, an API later): decode, validate, resolve and write
atomically. Locked decisions are in TODO §18.7. The rules that matter here:

* the target organisation is **always the actor's** — the document's ``ownerOrg`` is display data;
* tags are created by name, unknown custom fields are skipped with a warning;
* shares and attachments are never imported (privilege escalation / no bytes);
* a duplicate embedded alert aborts the whole case (no partial import);
* exported ``task-note`` timeline rows are skipped, because every imported ``TaskNote`` is mirrored
  into the ledger exactly the way the UI's note composer does it — importing both would double it.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from alerts.importing import AlertImportError, import_alert_document
from cases.ledger import append_timeline_event
from cases.models import (
    TTP,
    Case,
    CaseCustomFieldValue,
    CaseObservable,
    CaseStatus,
    Comment,
    CustomField,
    Page,
    Procedure,
    Tag,
    Task,
    TaskNote,
    TimelineEvent,
)
from core.enums import (
    CASE_PHASE_CHOICES,
    CASE_SCOPE_CHOICES,
    CASE_TYPE_CHOICES,
    CASE_VERDICT_VALUES,
    DATA_CLASSIFICATION_CHOICES,
    TASK_STATUS_CHOICES,
)
from identity.models import User
from observables.extractor import resolve_observable

MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_NODES = 10_000
MAX_DEPTH = 20
MAX_CHILDREN = 5_000
MAX_TIMELINE = 10_000
MAX_TASKS = 5_000
MAX_NOTES = 5_000
MAX_COMMENTS = 5_000
MAX_PAGES = 5_000
MAX_PROCEDURES = 5_000
MAX_OBSERVABLES = 2_000
MAX_ALERTS = 1_000
SUPPORTED_VERSIONS = (1, 2)
TASK_STATUSES = {value for value, _label in TASK_STATUS_CHOICES}
CASE_SCOPES = {value for value, _label in CASE_SCOPE_CHOICES}
CASE_TYPES = {value for value, _label in CASE_TYPE_CHOICES}
DATA_CLASSIFICATIONS = {value for value, _label in DATA_CLASSIFICATION_CHOICES}
CASE_PHASES = {value for value, _label in CASE_PHASE_CHOICES}
CASE_VERDICTS = set(CASE_VERDICT_VALUES)


class CaseImportError(Exception):
    """A structured import failure safe to show to the operator."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _check_bounds(document: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(document, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise CaseImportError("too_large", "Document contains too many values.")
        if depth > MAX_DEPTH:
            raise CaseImportError("too_deep", "Document nesting is too deep.")
        if isinstance(value, dict):
            if len(value) > MAX_CHILDREN:
                raise CaseImportError("too_large", "A document object has too many keys.")
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            if len(value) > MAX_CHILDREN:
                raise CaseImportError("too_large", "A document list has too many items.")
            stack.extend((child, depth + 1) for child in value)


def _check_finite(document: Any) -> None:
    """`json.loads` accepts ``1e999`` as ``inf``; reject non-finite floats anywhere."""
    if isinstance(document, float) and not math.isfinite(document):
        raise CaseImportError("non_finite_number", "The file contains a non-finite number.")
    if isinstance(document, dict):
        for value in document.values():
            _check_finite(value)
    elif isinstance(document, list):
        for value in document:
            _check_finite(value)


def parse_case_export(raw: bytes) -> dict[str, Any]:
    """Decode and bound-check an uploaded case export before any database work."""
    if len(raw) > MAX_BYTES:
        raise CaseImportError("oversized_file", "File exceeds the 5 MiB limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaseImportError("malformed_json", "File is not valid UTF-8 text.") from exc
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise CaseImportError("malformed_json", f"Invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CaseImportError("invalid_document", "The document must be a JSON object.")
    if document.get("_type") != "CaseExport":
        raise CaseImportError("wrong_type", "The document _type must be 'CaseExport'.")
    version = document.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise CaseImportError(
            "unsupported_version",
            f"Unsupported version {version!r}. Supported versions: {SUPPORTED_VERSIONS}.",
        )
    _check_finite(document)
    _check_bounds(document)
    case_doc = document.get("case")
    if not isinstance(case_doc, dict) or not str(case_doc.get("title") or "").strip():
        raise CaseImportError("missing_title", "Required field 'case.title' is missing or empty.")
    return document


def _safe_filename(name: str) -> str:
    base = Path(name or "case-export.json").name[:120]
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base) or "case-export.json"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit else text


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


def _parse_dt(value: Any, field: str, warnings: list[str]) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        warnings.append(f"{field}: invalid ISO timestamp; ignored")
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed


def _resolve_user(login: Any, actor: User, field: str, warnings: list[str]) -> User | None:
    value = _text(login)
    if not value:
        return None
    user = User.objects.filter(login=value, org_id=actor.org_id).first()
    if user is None:
        warnings.append(f"{field}: {value!r} is not in this organisation; unassigned")
    return user


def _resolve_choice(
    value: Any,
    allowed: set[str],
    default: str,
    field: str,
    warnings: list[str],
) -> str:
    """Keep an exported vocabulary token only when it is one the platform accepts."""
    wanted = _text(value) or default
    if wanted in allowed:
        return wanted
    warnings.append(f"{field}: unknown {wanted!r}; using {default!r}")
    return default


def _resolve_status(value: Any, warnings: list[str]) -> CaseStatus:
    wanted = _text(value) or "InProgress"
    status = CaseStatus.objects.filter(value=wanted).first()
    if status is not None:
        return status
    warnings.append(f"status: unknown {wanted!r}; using 'InProgress'")
    fallback = CaseStatus.objects.filter(value="InProgress").first()
    if fallback is None:
        fallback = CaseStatus.objects.create(value="InProgress", stage="InProgress", order=2)
    return fallback


def _import_tags(case: Case, rows: Any, warnings: list[str]) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_CHILDREN]):
        raw_name = row.get("name") if isinstance(row, dict) else row
        name = _text(raw_name, 100)
        if not name:
            warnings.append(f"tag[{index}]: empty name; skipped")
            continue
        tag, _created = Tag.objects.get_or_create(name=name)
        case.tag_links.get_or_create(tag=tag)


def _import_custom_fields(case: Case, rows: Any, warnings: list[str]) -> None:
    seen: set[str] = set()
    for index, row in enumerate(_as_list(rows)[:MAX_CHILDREN]):
        if not isinstance(row, dict):
            warnings.append(f"customField[{index}]: not an object; skipped")
            continue
        name = _text(row.get("name"), 100)
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
        CaseCustomFieldValue.objects.get_or_create(
            case=case,
            custom_field=field,
            defaults={"order": index, "value": row.get("value", {})},
        )


def _import_observables(case: Case, rows: Any, actor: User, warnings: list[str]) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_OBSERVABLES]):
        if not isinstance(row, dict):
            warnings.append(f"observable[{index}]: not an object; skipped")
            continue
        type_name = _text(row.get("dataType") or row.get("type"), 100)
        data = _text(row.get("data"))
        if not type_name or not data:
            warnings.append(f"observable[{index}]: missing dataType/data; skipped")
            continue
        resolved = resolve_observable(
            type_name,
            data,
            owner_org=actor.org,
            defaults={
                "tlp": _clamp_int(row.get("tlp"), 2, 0, 4, "observable.tlp", warnings),
                "pap": _clamp_int(row.get("pap"), 2, 0, 3, "observable.pap", warnings),
                "ioc": row.get("ioc") is True,
                "sighted": row.get("sighted") is True,
                "enrichment_data": row.get("enrichmentData")
                if isinstance(row.get("enrichmentData"), dict)
                else {},
            },
        )
        if resolved is None:
            warnings.append(f"observable[{index}]: unknown type {type_name!r}; skipped")
            continue
        observable, _created = resolved
        CaseObservable.objects.get_or_create(
            case=case,
            observable=observable,
            defaults={"added_by": actor, "tags": _as_list(row.get("tags"))},
        )


def _import_tasks(case: Case, rows: Any, actor: User, warnings: list[str]) -> None:
    note_budget = MAX_NOTES
    for index, row in enumerate(_as_list(rows)[:MAX_TASKS]):
        if not isinstance(row, dict):
            warnings.append(f"task[{index}]: not an object; skipped")
            continue
        title = _text(row.get("title"), 500)
        if not title:
            warnings.append(f"task[{index}]: missing title; skipped")
            continue
        status = _text(row.get("status")) or "Waiting"
        if status not in TASK_STATUSES:
            warnings.append(f"task[{index}]: unknown status {status!r}; using 'Waiting'")
            status = "Waiting"
        task = Task.objects.create(
            case=case,
            title=title,
            description=_text(row.get("description")),
            group=_text(row.get("group"), 100),
            status=status,
            flag=row.get("flag") is True,
            assignee=_resolve_user(row.get("assignee"), actor, f"task[{index}].assignee", warnings),
            order=_clamp_int(
                row.get("order"), 0, -2_147_483_648, 2_147_483_647, "task.order", warnings
            ),
            due_date=_parse_dt(row.get("dueDate"), "task.dueDate", warnings),
            started_at=_parse_dt(row.get("startedAt"), "task.startedAt", warnings),
            ended_at=_parse_dt(row.get("endedAt"), "task.endedAt", warnings),
            mandatory=row.get("mandatory") is True,
        )
        for note_index, note_row in enumerate(_as_list(row.get("notes"))):
            if note_budget <= 0:
                warnings.append("task notes: capped at 5000; remaining notes skipped")
                break
            if not isinstance(note_row, dict):
                warnings.append(f"task[{index}].note[{note_index}]: not an object; skipped")
                continue
            message = _text(note_row.get("message"))
            if not message:
                warnings.append(f"task[{index}].note[{note_index}]: empty message; skipped")
                continue
            created_at = _parse_dt(
                note_row.get("createdAt"), f"task[{index}].note[{note_index}].createdAt", warnings
            )
            created_by = (
                _resolve_user(
                    note_row.get("createdBy"),
                    actor,
                    f"task[{index}].note[{note_index}].createdBy",
                    warnings,
                )
                or actor
            )
            note = TaskNote.objects.create(task=task, message=message, created_by=created_by)
            if created_at is not None:
                TaskNote.objects.filter(pk=note.pk).update(created_at=created_at)
            append_timeline_event(
                case,
                title="Task note",
                description=message[:10000],
                kind="task-note",
                actor=created_by,
                metadata={"task_id": str(task.id), "note_id": str(note.id)},
                date=created_at,
            )
            note_budget -= 1


def _import_timeline(case: Case, rows: Any, actor: User, warnings: list[str]) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_TIMELINE]):
        if not isinstance(row, dict):
            warnings.append(f"timeline[{index}]: not an object; skipped")
            continue
        kind = _text(row.get("kind"), 50) or "comment"
        if kind == "task-note":
            # Imported TaskNotes already mirror into the ledger; the exported copy is a duplicate.
            continue
        title = _text(row.get("title") or row.get("message"), 500)
        if not title:
            warnings.append(f"timeline[{index}]: missing title; skipped")
            continue
        actor_value = row.get("actor", row.get("createdBy"))
        event_actor = (
            _resolve_user(actor_value, actor, f"timeline[{index}].actor", warnings) or actor
        )
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        append_timeline_event(
            case,
            title=title,
            description=_text(row.get("description")),
            kind=kind,
            actor=event_actor,
            metadata=metadata,
            date=_parse_dt(row.get("date"), f"timeline[{index}].date", warnings),
            end_date=_parse_dt(row.get("endDate"), f"timeline[{index}].endDate", warnings),
        )


def _import_comments(case: Case, rows: Any, actor: User, warnings: list[str]) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_COMMENTS]):
        if not isinstance(row, dict):
            warnings.append(f"comment[{index}]: not an object; skipped")
            continue
        message = _text(row.get("message"))
        if not message:
            warnings.append(f"comment[{index}]: empty message; skipped")
            continue
        created_by = (
            _resolve_user(row.get("createdBy"), actor, f"comment[{index}].createdBy", warnings)
            or actor
        )
        comment = Comment.objects.create(case=case, message=message, created_by=created_by)
        created_at = _parse_dt(row.get("createdAt"), f"comment[{index}].createdAt", warnings)
        if created_at is not None:
            Comment.objects.filter(pk=comment.pk).update(created_at=created_at)


def _import_pages(case: Case, rows: Any, actor: User, warnings: list[str]) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_PAGES]):
        if not isinstance(row, dict):
            warnings.append(f"page[{index}]: not an object; skipped")
            continue
        title = _text(row.get("title"), 500)
        if not title:
            warnings.append(f"page[{index}]: missing title; skipped")
            continue
        Page.objects.create(
            case=case,
            title=title,
            content=_text(row.get("content")),
            order=_clamp_int(
                row.get("order"), 0, -2_147_483_648, 2_147_483_647, "page.order", warnings
            ),
            category=_text(row.get("category"), 100),
            created_by=actor,
        )


def _import_procedures(case: Case, rows: Any, warnings: list[str]) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_PROCEDURES]):
        if not isinstance(row, dict):
            warnings.append(f"procedure[{index}]: not an object; skipped")
            continue
        ttp = None
        raw_ttp_id = row.get("ttpId")
        if raw_ttp_id:
            try:
                ttp = TTP.objects.filter(pk=UUID(str(raw_ttp_id))).first()
            except (ValueError, TypeError, AttributeError):
                warnings.append(f"procedure[{index}].ttpId: invalid id; dropped")
        Procedure.objects.create(
            case=case,
            occur_date=_parse_dt(row.get("occurDate"), f"procedure[{index}].occurDate", warnings),
            pattern_id=_text(row.get("patternId"), 255),
            pattern_name=_text(row.get("patternName"), 255),
            tactic=_text(row.get("tactic"), 100),
            description=_text(row.get("description")),
            ttp=ttp,
        )


def _import_alerts(case: Case, rows: Any, actor: User, source_filename: str) -> None:
    for index, row in enumerate(_as_list(rows)[:MAX_ALERTS]):
        if not isinstance(row, dict):
            raise CaseImportError("alert_invalid", f"alert[{index}] is not an object.")
        if row.get("_type") == "AlertExport":
            document = row
        else:
            # v1 case exports embedded the bare alert fields, without the AlertExport wrapper.
            document = {"_type": "AlertExport", "version": 1, "alert": row}
        try:
            alert = import_alert_document(
                document,
                actor=actor,
                source_filename=source_filename,
            )
        except AlertImportError as exc:
            code = "alert_duplicate" if exc.code == "duplicate" else "alert_import_failed"
            raise CaseImportError(code, f"Embedded alert[{index}]: {exc.message}") from exc
        alert.case = case
        alert.save(update_fields=["case", "updated_at"])


def _import_closing_entry(case: Case, actor: User) -> None:
    if case.status.stage != "Closed":
        return
    if TimelineEvent.objects.filter(case=case, kind="case-closed").exists():
        return
    append_timeline_event(
        case,
        title="Case closed",
        description="Closing entry synthesised on import (the export had none).",
        kind="case-closed",
        actor=actor,
        date=case.closed_date or timezone.now(),
    )


def import_case_document(
    document: dict[str, Any],
    *,
    actor: User,
    source_filename: str,
) -> Case:
    """Create a Case and all its children from a parsed ``CaseExport`` document.

    One transaction: any failure (including a duplicate embedded alert) leaves no case behind.
    """
    if actor.org is None:
        raise CaseImportError(
            "no_organisation",
            "Actor has no organisation; a case must have an owner organisation.",
        )

    source_filename = _safe_filename(source_filename)
    case_doc = _as_dict(document.get("case"))
    warnings: list[str] = []
    if document.get("version") == 1:
        warnings.append("version: v1 export imported; missing v2 fields are left at defaults")

    with transaction.atomic():
        verdict = _resolve_choice(
            case_doc.get("verdict"), CASE_VERDICTS, "undefined", "verdict", warnings
        )
        status = _resolve_status(case_doc.get("status"), warnings)
        if status.stage == "Closed" and verdict == "undefined" and not case_doc.get("verdict"):
            warnings.append("closed case: no verdict; set to 'undefined'")

        case = Case(
            title=_text(case_doc.get("title"), 500),
            description=_text(case_doc.get("description")),
            summary=_text(case_doc.get("summary")),
            severity=_clamp_int(case_doc.get("severity"), 2, 1, 4, "severity", warnings),
            tlp=_clamp_int(case_doc.get("tlp"), 2, 0, 4, "tlp", warnings),
            pap=_clamp_int(case_doc.get("pap"), 2, 0, 3, "pap", warnings),
            flag=case_doc.get("flag") is True,
            status=status,
            assignee=_resolve_user(case_doc.get("assignee"), actor, "assignee", warnings),
            scope=_resolve_choice(case_doc.get("scope"), CASE_SCOPES, "unset", "scope", warnings),
            case_type=_resolve_choice(
                case_doc.get("caseType"), CASE_TYPES, "unset", "caseType", warnings
            ),
            data_classification=_resolve_choice(
                case_doc.get("dataClassification"),
                DATA_CLASSIFICATIONS,
                "unset",
                "dataClassification",
                warnings,
            ),
            phase=_resolve_choice(
                case_doc.get("phase"), CASE_PHASES, "initial_triage", "phase", warnings
            ),
            verdict=verdict,
            start_date=_parse_dt(case_doc.get("startDate"), "startDate", warnings)
            or timezone.now(),
            end_date=_parse_dt(case_doc.get("endDate"), "endDate", warnings),
            closed_date=_parse_dt(case_doc.get("closedDate"), "closedDate", warnings),
            owner_org=actor.org,
        )
        case.save()

        _import_tags(case, case_doc.get("tags"), warnings)
        _import_custom_fields(case, case_doc.get("customFields"), warnings)
        _import_observables(
            case,
            document.get("observables", case_doc.get("observables")),
            actor,
            warnings,
        )
        _import_tasks(case, document.get("tasks", case_doc.get("tasks")), actor, warnings)
        _import_timeline(case, document.get("timeline", case_doc.get("timeline")), actor, warnings)
        _import_comments(case, document.get("comments", case_doc.get("comments")), actor, warnings)
        _import_pages(case, document.get("pages", case_doc.get("pages")), actor, warnings)
        _import_procedures(case, document.get("procedures", case_doc.get("procedures")), warnings)
        _import_alerts(case, document.get("alerts"), actor, source_filename)

        if _as_list(document.get("shares")):
            warnings.append("shares: skipped (cross-organisation grants are never imported)")
        if _as_list(document.get("attachments")):
            warnings.append("attachments: skipped (the export carries metadata only, no bytes)")
        if _as_list(document.get("automationRuns")):
            warnings.append("automationRuns: skipped (automation history is not replayed)")

        _import_closing_entry(case, actor)

        source_template = _text(case_doc.get("sourceTemplate"), 255) or None
        provenance: dict[str, Any] = {
            "at": timezone.now().isoformat(),
            "by": actor.login,
            "sourceFile": source_filename,
            "originalNumber": case_doc.get("number"),
            "originalId": case_doc.get("id") or case_doc.get("_id"),
        }
        if source_template:
            provenance["sourceTemplate"] = source_template
        if warnings:
            provenance["warnings"] = warnings
        case.ingestion_warnings = {"imported": provenance}
        case.save(update_fields=["ingestion_warnings", "updated_at"])

        append_timeline_event(
            case,
            title="Case imported from JSON",
            description=f"Imported from {source_filename}.",
            kind="import",
            actor=actor,
            metadata={
                "sourceFile": source_filename,
                "originalNumber": case_doc.get("number"),
                "originalId": case_doc.get("id") or case_doc.get("_id"),
            },
        )

    return case
