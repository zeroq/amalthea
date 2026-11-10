"""Case template CRUD and apply-template views."""

from __future__ import annotations

import copy
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from cases.models import (
    Case,
    CaseTemplate,
    Tag,
)
from cases.template_defaults import (
    DEFAULT_TEMPLATE_CUSTOM_FIELDS,
    DEFAULT_TEMPLATE_TASKS,
    parse_case_template_severity,
)
from cases.views import (
    _apply_template,
    _resolve_case_template,
)
from core.enums import (
    PAP_CHOICES,
    TLP_CHOICES,
)
from identity.models import AuditLog, User
from identity.scoping import (
    resolve_case,
)
from ui.pagination import paginate

from .common import (
    _base_context,
    _case_detail_redirect,
    not_viewer,
)

logger = logging.getLogger(__name__)


@not_viewer
@login_required
def case_apply_template(request: HttpRequest, case_id: str) -> HttpResponse:
    """Apply a case template to a case."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if request.method == "GET":
        templates = CaseTemplate.objects.all().order_by("name")
        return render(
            request,
            "ui/case_template_apply.html",
            _base_context(request, case=case, templates=templates),
        )
    # POST
    template_id = (request.POST.get("template_id") or "").strip()
    if not template_id:
        messages.error(request, "Template is required.")
        return redirect("ui-case-apply-template", case_id=case.number)
    template = _resolve_case_template(template_id)
    if template is None:
        raise Http404()
    _apply_template(case, template, actor=request.user)
    messages.success(request, f"Applied template '{template.name}' to case {case.number}.")
    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def case_template_list(request: HttpRequest) -> HttpResponse:
    """List case templates."""
    queryset = CaseTemplate.objects.all().order_by("name")
    page_obj = paginate(request, queryset)
    filtered_from = None  # No filters on this list
    return render(
        request,
        "ui/case_template_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
        ),
    )


def _parse_template_form(request: HttpRequest) -> dict:
    """Parse and validate case template form data. Returns dict of cleaned values or raises ValueError."""

    name = (request.POST.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if len(name) > 200:
        name = name[:200]

    # Parse tasks JSON
    tasks_raw = (request.POST.get("tasks") or "").strip()
    tasks = []
    if tasks_raw:
        try:
            tasks = json.loads(tasks_raw)
            if not isinstance(tasks, list):
                raise ValueError("Tasks must be a JSON array.")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid tasks JSON: {e}") from e

    # Parse custom_fields JSON
    custom_fields_raw = (request.POST.get("custom_fields") or "").strip()
    custom_fields = []
    if custom_fields_raw:
        try:
            custom_fields = json.loads(custom_fields_raw)
            if not isinstance(custom_fields, list):
                raise ValueError("Custom fields must be a JSON array.")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid custom_fields JSON: {e}") from e

    # flag is a boolean field - checkbox sends "on" when checked
    flag_raw = request.POST.get("flag")
    flag = flag_raw in ("on", "true", "1", True)

    # Parse severity: "none" -> None, absent/empty -> None, otherwise use parser
    severity_raw = request.POST.get("severity")
    if severity_raw is None or severity_raw.strip() == "":
        severity = None
    else:
        try:
            severity = parse_case_template_severity(severity_raw)
        except ValueError as exc:
            raise ValueError(f"Invalid severity: {exc}") from exc

    return {
        "name": name,
        "display_name": (request.POST.get("display_name") or "").strip(),
        "title_prefix": (request.POST.get("title_prefix") or "").strip(),
        "description": (request.POST.get("description") or "").strip(),
        "summary": (request.POST.get("summary") or "").strip(),
        "flag": flag,
        "severity": severity,
        "tlp": int(request.POST.get("tlp") or 2),
        "pap": int(request.POST.get("pap") or 2),
        "tags": [t.strip() for t in (request.POST.get("tags") or "").split(",") if t.strip()],
        "tasks": tasks,
        "custom_fields": custom_fields,
    }


@not_viewer
@login_required
def case_template_create(request: HttpRequest) -> HttpResponse:
    """Create a case template.

    GET: any authenticated user.
    POST: requires analyst role or above (excludes read-only).
    """
    if request.method == "GET":
        all_tag_names = list(Tag.objects.order_by("name").values_list("name", flat=True))
        return render(
            request,
            "ui/case_template_form.html",
            _base_context(
                request,
                form_tasks=copy.deepcopy(DEFAULT_TEMPLATE_TASKS),
                form_custom_fields=copy.deepcopy(DEFAULT_TEMPLATE_CUSTOM_FIELDS),
                all_tag_names=all_tag_names,
                form_severity=None,
            ),
        )

    # POST - requires analyst role or above (mirrors playbook_create gate)
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    try:
        data = _parse_template_form(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-template-create")

    # The create form ships prefilled; keep the defaults when the submission is empty so a
    # template created without editing still seeds the runbook. API-created templates remain
    # explicit and are never injected with defaults.
    if not data["tasks"]:
        data["tasks"] = copy.deepcopy(DEFAULT_TEMPLATE_TASKS)
    if not data["custom_fields"]:
        data["custom_fields"] = copy.deepcopy(DEFAULT_TEMPLATE_CUSTOM_FIELDS)

    if CaseTemplate.objects.filter(name=data["name"]).exists():
        messages.error(request, "Template with that name already exists.")
        return redirect("ui-case-template-create")

    CaseTemplate.objects.create(**data)
    messages.success(request, f"Template '{data['name']}' created.")
    return redirect("ui-case-template-list")


@not_viewer
@login_required
def case_template_detail(request: HttpRequest, template_id: str) -> HttpResponse:
    """View a case template."""
    try:
        import uuid

        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)

    return render(
        request,
        "ui/case_template_detail.html",
        _base_context(
            request,
            template=template,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
        ),
    )


def _template_source_cases(user: User) -> QuerySet[Case]:
    """Cases seeded by a template that `user` is allowed to **write** to.

    Templates are global definitions, but cases are tenant-owned (ADR-004). Editing a global
    template must not turn "apply to existing cases" into a cross-tenant write, so this mirrors
    `resolve_case(..., write=True)`: the caller's own org, or a share that grants `write`.
    """
    if getattr(user, "role", "") == "superAdmin":
        return Case.objects.all()
    org_id = getattr(user, "org_id", None)
    if org_id is None:
        return Case.objects.none()
    return Case.objects.filter(
        Q(owner_org_id=org_id) | Q(shares__organisation_id=org_id, shares__permissions__write=True)
    ).distinct()


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def case_template_edit(request: HttpRequest, template_id: str) -> HttpResponse:
    """Edit a case template.

    GET: any authenticated user.
    POST: requires analyst role or above (excludes read-only).

    `apply_to_existing=1` re-applies the edited defaults to the cases this template seeded
    (`Case.source_template`). Re-application is additive for the runbook (tags, custom fields and
    tasks that already exist are left alone) so editing a template is idempotent.
    """
    try:
        import uuid

        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)

    if request.method == "GET":
        all_tag_names = list(Tag.objects.order_by("name").values_list("name", flat=True))
        source_case_count = (
            _template_source_cases(request.user).filter(source_template=template).count()
        )
        return render(
            request,
            "ui/case_template_form.html",
            _base_context(
                request,
                template=template,
                mode="edit",
                form_tasks=template.tasks,
                form_custom_fields=template.custom_fields,
                all_tag_names=all_tag_names,
                source_case_count=source_case_count,
                form_severity=template.severity,
            ),
        )

    # POST - requires analyst role or above (mirrors playbook_detail update gate)
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    # POST - update
    try:
        data = _parse_template_form(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-template-edit", template_id=template.id)

    # Check name uniqueness (excluding self)
    if CaseTemplate.objects.exclude(pk=template.pk).filter(name=data["name"]).exists():
        messages.error(request, "Template with that name already exists.")
        return redirect("ui-case-template-edit", template_id=template.id)

    for field, value in data.items():
        setattr(template, field, value)
    template.save()

    apply_to_existing = request.POST.get("apply_to_existing") == "1"
    reapplied = 0
    if apply_to_existing:
        # Only the cases this template seeded, and only those the caller may already write to.
        # One transaction so a failure halfway through does not leave half a runbook resynced.
        source_cases = _template_source_cases(request.user).filter(source_template=template)
        with transaction.atomic():
            for case in source_cases:
                _apply_template(case, template, actor=request.user, resync=True)
                reapplied += 1

    # Audit the mutation (mirror API audit pattern)

    AuditLog.objects.create(
        actor=request.user,
        actor_org=getattr(request.user, "org", None),
        target_org=None,  # templates are global
        action="template.updated",
        target_type="CaseTemplate",
        target_id=str(template.id),
        details={"reapplied_cases": reapplied} if apply_to_existing else {},
        ip=request.META.get("REMOTE_ADDR"),
    )

    if apply_to_existing:
        messages.success(
            request,
            f"Template '{template.name}' updated and re-applied to {reapplied} case(s).",
        )
    else:
        messages.success(request, f"Template '{template.name}' updated.")
    return redirect("ui-case-template-detail", template_id=template.id)


@not_viewer
@login_required
@require_POST
def case_template_delete(request: HttpRequest, template_id: str) -> HttpResponse:
    """Delete a case template. Requires analyst role or above (excludes read-only)."""
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    try:
        import uuid

        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)
    name = template.name
    template_id_value = str(template.pk)
    # `Case.source_template` is SET_NULL, so deleting the template detaches the cases it seeded
    # instead of blocking the delete. Count them first so the confirmation is honest.
    unlinked = template.cases_from_template.count()
    template.delete()

    AuditLog.objects.create(
        actor=request.user,
        actor_org=getattr(request.user, "org", None),
        target_org=None,  # templates are global
        action="template.deleted",
        target_type="CaseTemplate",
        target_id=template_id_value,
        details={"name": name, "unlinked_cases": unlinked},
        ip=request.META.get("REMOTE_ADDR"),
    )

    if unlinked:
        messages.success(
            request,
            f"Template '{name}' deleted. {unlinked} case(s) kept their applied defaults and now "
            "have no source template.",
        )
    else:
        messages.success(request, f"Template '{name}' deleted.")
    return redirect("ui-case-template-list")
