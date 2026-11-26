from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from cases.ledger import append_timeline_event
from cases.models import (
    Case,
    CaseCustomFieldValue,
    CaseTagLink,
    CaseTemplate,
    CustomField,
    Tag,
    Task,
)
from cases.template_defaults import (
    parse_case_template_severity,
    render_source_alert_payload,
)
from compat.bulk import bulk_patch
from core import taxonomy_read
from core.enums import (
    CUSTOM_FIELD_TYPES,
)
from core.serializers import (
    case_template_json,
)

from ._common import _actor, _as_int, _as_uuid, _bad, _case_access, _not_found


def _resolve_case_template(identifier: str) -> CaseTemplate | None:
    """Resolve `{idOrName}` as a UUID first, then as the unique `name`."""
    pk = _as_uuid(identifier)
    if pk is not None:
        found = CaseTemplate.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return CaseTemplate.objects.filter(name=identifier).first()


def _apply_template(
    case: Case,
    template: CaseTemplate,
    *,
    actor: Any,
    update_title_prefix: bool = False,
    resync: bool = False,
) -> None:
    """Copy `template`'s defaults onto `case` (AC6.1-P4-c).

    Scalars overwrite except a null template severity, which preserves the case's current severity.
    The description is only filled when the case has none, because a template's prose must not
    clobber what the analyst already wrote. Task and custom-field defaults are copied once, tags
    are attached. One provenance ledger entry records the template's name.

    Tasks are matched on `(title, group)` across every apply so that re-applying the same template
    never creates a duplicate. Tags and custom-field values already use `get_or_create`; the task
    dedup step extends that idempotency to runbook rows. The ledger entry is distinguished so an
    analyst can tell an initial application from a later re-apply.
    """
    # Detect re-apply before we overwrite source_template.
    is_reapply = case.source_template_id == template.id

    if template.severity is not None:
        case.severity = template.severity
    case.tlp = template.tlp
    case.pap = template.pap
    case.flag = template.flag
    # Q-D6: template carries scope/case_type/phase; data_classification is per-incident and not copied.
    case.scope = template.scope
    case.case_type = template.case_type
    case.phase = template.phase
    if template.summary:
        case.summary = template.summary
    if template.description and not case.description:
        case.description = template.description
    if (
        update_title_prefix
        and template.title_prefix
        and not case.title.startswith(template.title_prefix)
    ):
        case.title = f"{template.title_prefix}{case.title}"[:500]
    # Provenance: every application records the template the case's defaults came from. PROTECT on
    # the FK also means a template that seeded cases cannot be deleted out from under them.
    case.source_template = template
    case.save()

    for name in template.tags or []:
        if isinstance(name, str) and name.strip():
            tag, _created = Tag.objects.get_or_create(name=name.strip()[:100])
            CaseTagLink.objects.get_or_create(case=case, tag=tag)

    # Build existing-task identity set on EVERY apply, not only on resync.
    # Identity is (title_truncated_to_500, group_truncated_to_100), case-sensitive exact match.
    existing_tasks: set[tuple[str, str]] = set(case.tasks.values_list("title", "group"))
    for spec in template.tasks or []:
        if not isinstance(spec, Mapping) or not str(spec.get("title") or "").strip():
            continue
        title = str(spec["title"])[:500]
        group = str(spec.get("group") or "")[:100]
        key = (title, group)
        if key in existing_tasks:
            continue
        Task.objects.create(
            case=case,
            title=title,
            group=group,
            description=render_source_alert_payload(str(spec.get("description") or ""), case),
            order=_as_int(spec.get("order")) or 0,
            mandatory=bool(spec.get("mandatory") or False),
        )
        existing_tasks.add(key)

    for spec in template.custom_fields or []:
        if not isinstance(spec, Mapping):
            continue
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        name = name[:100]
        # A template is a blueprint: materialize a missing global definition instead of silently
        # dropping the default. An existing definition wins, including its type.
        field_type = str(spec.get("type") or "").strip().lower()
        if field_type not in dict(CUSTOM_FIELD_TYPES):
            field_type = "string"
        field, _created = CustomField.objects.get_or_create(
            name=name,
            defaults={"type": field_type},
        )
        value = spec.get("value")
        CaseCustomFieldValue.objects.get_or_create(
            case=case,
            custom_field=field,
            defaults={"value": {} if value is None else value},
        )

    # Ledger: distinguish initial apply from re-apply.
    kind = "template-reapplied" if (resync or is_reapply) else "template-applied"
    title = (
        f"Template re-applied: {template.name}"
        if (resync or is_reapply)
        else f"Template applied: {template.name}"
    )
    append_timeline_event(
        case,
        title=title,
        kind=kind,
        actor=actor,
        metadata={"template": template.name},
    )


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_apply_template(request: Request) -> Response:
    """`POST /api/v1/case/_bulk/caseTemplate` — apply a template to existing cases (AC6.1-P4-c).

    `InputApplyCaseTemplate` is `{"ids": [...], "caseTemplate": "<idOrName>", ...}`; the cases are
    resolved best-effort like `_merge`, and each application is its own savepoint.
    """

    payload = request.data if isinstance(request.data, dict) else {}
    identifier = str(payload.get("caseTemplate") or "").strip()
    if not identifier:
        return _bad("caseTemplate is required", {"caseTemplate": ["required"]})
    template = _resolve_case_template(identifier)
    if template is None:
        return _not_found("CaseTemplate")
    update_prefix = bool(payload.get("updateTitlePrefix") or False)
    resolved: CaseTemplate = template

    def apply_one(case: Case, _body: Mapping[str, Any]) -> Response | list[str]:
        _apply_template(case, resolved, actor=_actor(request), update_title_prefix=update_prefix)
        return []

    return bulk_patch(
        request,
        ids_key="ids",
        resolve=lambda req, raw: _case_access(req, raw, write=True),
        apply=apply_one,
    )


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_template_collection(request: Request) -> Response:
    """`GET /api/v1/caseTemplate` lists; `POST` creates (201)."""
    if request.method == "POST":
        return _create_case_template(request)
    return Response(
        [case_template_json(template) for template in CaseTemplate.objects.order_by("name")]
    )


def _create_case_template(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return _bad("name is required", {"name": ["required"]})
    if CaseTemplate.objects.filter(name=name[:100]).exists():
        return _bad("name already exists", {"name": ["already exists"]})

    # Validate enum fields — empty/null means "use the model default" (consistent with Case create/update)
    scope_val = payload.get("scope")
    if scope_val in (None, ""):
        scope_val = "unset"
    case_type_val = payload.get("caseType", payload.get("case_type"))
    if case_type_val in (None, ""):
        case_type_val = "unset"
    phase_val = payload.get("phase")
    if phase_val in (None, ""):
        phase_val = "initial_triage"

    allowed_scope = taxonomy_read.codes(taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False)
    allowed_case_type = taxonomy_read.codes(taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False)
    allowed_phase = taxonomy_read.codes(taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False)

    if scope_val not in allowed_scope:
        return _bad(
            f"scope must be one of: {', '.join(allowed_scope)}",
            {"scope": [f"must be one of: {', '.join(allowed_scope)}"]},
        )
    if case_type_val not in allowed_case_type:
        return _bad(
            f"caseType must be one of: {', '.join(allowed_case_type)}",
            {"caseType": [f"must be one of: {', '.join(allowed_case_type)}"]},
        )
    if phase_val not in allowed_phase:
        return _bad(
            f"phase must be one of: {', '.join(allowed_phase)}",
            {"phase": [f"must be one of: {', '.join(allowed_phase)}"]},
        )

    severity_raw = payload.get("severity")
    try:
        severity_val = parse_case_template_severity(severity_raw)
    except ValueError:
        return _bad(
            "severity must be null, omitted, or an integer 1..4",
            {"severity": ["must be null, omitted, or an integer 1..4"]},
        )

    template = CaseTemplate.objects.create(
        name=name[:100],
        display_name=str(payload.get("displayName") or "")[:200],
        title_prefix=str(payload.get("titlePrefix") or "")[:100],
        description=str(payload.get("description") or ""),
        severity=severity_val,
        tlp=int(payload.get("tlp") or 2),
        pap=int(payload.get("pap") or 2),
        flag=bool(payload.get("flag") or False),
        summary=str(payload.get("summary") or ""),
        scope=scope_val,
        case_type=case_type_val,
        phase=phase_val,
        tasks=payload.get("tasks") if isinstance(payload.get("tasks"), list) else [],
        custom_fields=(
            payload.get("customFields") if isinstance(payload.get("customFields"), list) else []
        ),
        tags=_template_tags(payload),
    )
    return Response(case_template_json(template), status=status.HTTP_201_CREATED)


def _template_tags(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("tags")
    if not isinstance(raw, list):
        return []
    return [str(name)[:100] for name in raw if isinstance(name, str) and name.strip()]


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def case_template_detail(request: Request, template_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/caseTemplate/{idOrName}`."""
    template = _resolve_case_template(template_id)
    if template is None:
        return _not_found("CaseTemplate")
    if request.method == "DELETE":
        try:
            template.delete()
        except ProtectedError:
            return _bad(
                "CaseTemplate still seeds cases",
                {"template": ["in use by cases"]},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_case_template(template, request)
    return Response(case_template_json(template))


def _update_case_template(template: CaseTemplate, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if CaseTemplate.objects.exclude(pk=template.pk).filter(name=name[:100]).exists():
            return _bad("name already exists", {"name": ["already exists"]})
        template.name = name[:100]
        fields.append("name")
    for key, attr, limit in (
        ("displayName", "display_name", 200),
        ("titlePrefix", "title_prefix", 100),
    ):
        if key in payload:
            setattr(template, attr, str(payload[key] or "")[:limit])
            fields.append(attr)
    for key in ("description", "summary"):
        if key in payload:
            setattr(template, key, str(payload[key] or ""))
            fields.append(key)
    if "severity" in payload:
        try:
            severity_val = parse_case_template_severity(payload["severity"])
        except ValueError:
            return _bad(
                "severity must be null, omitted, or an integer 1..4",
                {"severity": ["must be null, omitted, or an integer 1..4"]},
            )
        template.severity = severity_val
        fields.append("severity")
    for key in ("tlp", "pap"):
        if key in payload:
            setattr(template, key, int(payload[key]))
            fields.append(key)
    if "flag" in payload:
        template.flag = bool(payload["flag"])
        fields.append("flag")

    # Enum fields for CaseTemplate (scope, caseType, phase) — validate against vocabularies
    if "scope" in payload:
        val = str(payload["scope"]).strip()
        allowed_scope = taxonomy_read.codes(taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False)
        if val not in allowed_scope:
            return _bad(
                f"scope must be one of: {', '.join(allowed_scope)}",
                {"scope": [f"must be one of: {', '.join(allowed_scope)}"]},
            )
        template.scope = val
        fields.append("scope")
    if "caseType" in payload:
        val = str(payload["caseType"]).strip()
        allowed_case_type = taxonomy_read.codes(
            taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False
        )
        if val not in allowed_case_type:
            return _bad(
                f"caseType must be one of: {', '.join(allowed_case_type)}",
                {"caseType": [f"must be one of: {', '.join(allowed_case_type)}"]},
            )
        template.case_type = val
        fields.append("case_type")
    if "phase" in payload:
        val = str(payload["phase"]).strip()
        allowed_phase = taxonomy_read.codes(taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False)
        if val not in allowed_phase:
            return _bad(
                f"phase must be one of: {', '.join(allowed_phase)}",
                {"phase": [f"must be one of: {', '.join(allowed_phase)}"]},
            )
        template.phase = val
        fields.append("phase")

    if "tasks" in payload:
        template.tasks = payload["tasks"] if isinstance(payload["tasks"], list) else []
        fields.append("tasks")
    if "customFields" in payload:
        template.custom_fields = (
            payload["customFields"] if isinstance(payload["customFields"], list) else []
        )
        fields.append("custom_fields")
    if "tags" in payload:
        template.tags = _template_tags(payload)
        fields.append("tags")
    if not fields:
        return _bad("No updatable field supplied", {})
    template.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)
