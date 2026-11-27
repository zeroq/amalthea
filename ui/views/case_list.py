"""Case list and bulk UI views (§13.3)."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from cases.create import DEFAULT_CASE_STATUS
from cases.models import (
    Case,
    CaseStatus,
)
from core import taxonomy_read
from core.enums import (
    PAP_CHOICES,
    SEVERITY_CHOICES,
    SUPER_ADMIN_ROLE,
    TLP_CHOICES,
)
from identity.models import User
from identity.scoping import (
    is_admin,
    is_viewer,
    resolve_case,
)
from ui.pagination import PICKER_PAGE_SIZE, paginate
from ui.severity_filter import build_severity_filter_links, parse_severity_filter

from .common import (
    _base_context,
    not_viewer,
)


def _case_bulk_redirect(request: HttpRequest) -> HttpResponse:
    next_url = (request.POST.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("ui-case-list")


@not_viewer
@login_required
@require_POST
def case_bulk(request: HttpRequest) -> HttpResponse:
    """Bulk case triage: status and/or assignee, per-item isolation."""
    ids = request.POST.getlist("ids")
    status_val = (request.POST.get("status") or "").strip()
    assignee_val = (request.POST.get("assignee") or "").strip()

    if not ids:
        messages.error(request, "No cases selected.")
        return _case_bulk_redirect(request)

    payload: dict[str, Any] = {}
    if status_val:
        status_obj = CaseStatus.objects.filter(value=status_val).first()
        if status_obj is None:
            messages.error(request, f"Invalid status: {status_val}")
            return _case_bulk_redirect(request)
        if status_obj.stage == "Closed":
            messages.error(
                request, "Closing cases in bulk requires a verdict; use case detail close."
            )
            return _case_bulk_redirect(request)
        payload["status"] = status_obj.value
    if assignee_val:
        if assignee_val == "__unassigned__":
            payload["assignee"] = None
        else:
            assignee = User.objects.filter(
                org=request.user.org, login=assignee_val, is_active=True
            ).first()
            if assignee is None:
                messages.error(request, f"Invalid assignee: {assignee_val}")
                return _case_bulk_redirect(request)
            payload["assignee"] = assignee.login

    if not payload:
        messages.error(request, "Select a status or assignee to apply.")
        return _case_bulk_redirect(request)

    results = []
    updated = 0
    for raw in ids:
        case = resolve_case(request.user, str(raw), write=True)
        if case is None:
            results.append({"id": str(raw), "status": 404, "message": "not found"})
            continue
        try:
            with db_transaction.atomic():
                fields: list[str] = []
                if "status" in payload:
                    case.status = CaseStatus.objects.get(value=payload["status"])
                    fields.append("status")
                if "assignee" in payload:
                    raw_assignee = payload.get("assignee")
                    if raw_assignee in (None, ""):
                        case.assignee = None
                    else:
                        case.assignee = User.objects.filter(
                            org=request.user.org, login=str(raw_assignee), is_active=True
                        ).first()
                    fields.append("assignee")
                if not fields:
                    results.append(
                        {
                            "id": str(case.id),
                            "status": 400,
                            "message": "No updatable field supplied",
                        }
                    )
                    continue
                case.save(update_fields=[*fields, "updated_at"])
        except Exception as exc:
            results.append({"id": str(raw), "status": 400, "message": str(exc)[:200]})
            continue
        results.append({"id": str(case.id), "status": 200})
        updated += 1

    failed = len(results) - updated
    if failed:
        first = next((r for r in results if r.get("status") != 200), None)
        detail = f" First failure: {first.get('message')}" if first else ""
        messages.warning(request, f"Bulk update: {updated} updated, {failed} failed.{detail}")
    else:
        messages.success(request, f"Updated {updated} case(s).")
    return _case_bulk_redirect(request)


def _case_create_form_context(
    post_data: Any,
    status_options: Any,
    assignees: Any,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    """Build the context extras for the case create form (page or modal).

    `post_data` is a `QueryDict` (`None` on a GET): `dict(...)` would yield lists and render as
    `['x']`, so scalars are read with `.get()` (SEC-2026-10-11 N1). The numeric selects fall back
    to the model defaults, which is what the service applies when the wire omits them.
    """
    can_classify = is_admin(request.user) if request is not None else False
    form_values = {
        "title": "",
        "description": "",
        "summary": "",
        "severity": "2",
        "tlp": "2",
        "pap": "2",
        "status": DEFAULT_CASE_STATUS,
        "assignee": "",
        "tags": "",
        "flag": "",
        "scope": "unset",
        "case_type": "unset",
        "data_classification": "unset",
        "phase": "initial_triage",
    }
    if post_data is not None:
        for key in (
            "title",
            "description",
            "summary",
            "severity",
            "tlp",
            "pap",
            "status",
            "assignee",
            "tags",
            "scope",
            "case_type",
            "phase",
        ):
            form_values[key] = post_data.get(key, "")
        # data_classification is admin-only (Q-D5); only echo back if the actor may classify
        if can_classify:
            form_values["data_classification"] = post_data.get("data_classification", "")
        form_values["flag"] = "on" if post_data.get("flag") == "on" else ""
    return {
        "form_values": form_values,
        "case_status_options": status_options,
        "assignees": assignees,
        "severity_choices": SEVERITY_CHOICES,
        "tlp_choices": TLP_CHOICES,
        "pap_choices": PAP_CHOICES,
        "scope_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False
        ),
        "case_type_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False
        ),
        "data_classification_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_DATA_CLASSIFICATION, include_hidden=False
        ),
        "phase_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False
        ),
        "can_classify": can_classify,
    }


def _render_case_list(
    request: HttpRequest,
    *,
    open_new_case: bool = False,
    post_data: Any = None,
    status_options: Any = None,
    assignees: Any = None,
) -> HttpResponse:
    """Render the case list, optionally with the New-case modal open and pre-filled.

    Supports independent `scope` (open/all/closed) and `mine` (cases assigned to the
    signed-in user) filters. Only when the user can mutate (not a viewer) do we
    include the form context; viewers must not trigger those user/status queries
    at all.
    """
    from identity.scoping import case_visible_q

    # Scope is a tri-state: "open" (default), "all", or "closed". An unrecognised
    # value falls back to "open" so a malformed query never 500s.
    scope = request.GET.get("scope", "open")
    if scope not in ("open", "all", "closed"):
        scope = "open"

    mine = request.GET.get("mine") == "1"
    selected_severities = parse_severity_filter(request.GET)

    base_queryset = Case.objects.select_related("status", "assignee", "owner_org").filter(
        case_visible_q(request.user)
    )
    unfiltered_count = base_queryset.count()

    if mine:
        base_queryset = base_queryset.filter(assignee=request.user)

    if scope == "open":
        queryset = base_queryset.filter(closed_date__isnull=True)
    elif scope == "closed":
        queryset = base_queryset.filter(closed_date__isnull=False)
    else:  # scope == "all"
        queryset = base_queryset

    # Apply severity filter after org/scope/mine, before pagination.
    # Multiple selected severities combine with OR (Django ``severity__in``).
    if selected_severities:
        queryset = queryset.filter(severity__in=selected_severities)

    # Pagination for the cases list (primary list P). The assignees picker
    # remains capped at PICKER_PAGE_SIZE (class K, not paged).
    # "all" is not a filter; "open"/"closed" narrow the set.
    has_filter = scope != "all" or mine or bool(selected_severities)
    page_obj = paginate(request, queryset.order_by("-severity", "-start_date"))
    filtered_from = (
        unfiltered_count if has_filter and unfiltered_count > page_obj.paginator.count else None
    )

    heading = {
        ("open", False): "Open cases",
        ("all", False): "All cases",
        ("closed", False): "Closed cases",
        ("open", True): "My open cases",
        ("all", True): "My cases",
        ("closed", True): "My closed cases",
    }[(scope, mine)]

    case_status_options = CaseStatus.objects.all().order_by("order", "value")
    case_assignees = (
        User.objects.filter(org=request.user.org, is_active=True)
        .select_related("org")
        .order_by("login")[:PICKER_PAGE_SIZE]
    )
    context = {
        "page_obj": page_obj,
        "filtered_from": filtered_from,
        "scope": scope,
        "mine": mine,
        "heading": heading,
        "open_new_case": open_new_case,
        "selected_severities": selected_severities,
        "severity_filter_links": build_severity_filter_links(request, selected_severities),
        "can_delete_cases": is_admin(request.user),
        "can_delete_any_case": request.user.role == SUPER_ADMIN_ROLE,
        "case_status_options": case_status_options,
        "case_assignees": case_assignees,
    }

    # Only include form context for users who can actually create cases.
    if not is_viewer(request.user):
        if status_options is None:
            status_options = CaseStatus.objects.all().order_by("order", "value")
        if assignees is None:
            assignees = (
                User.objects.filter(org=request.user.org, is_active=True)
                .select_related("org")
                .order_by("login")[:PICKER_PAGE_SIZE]
            )
        context.update(
            _case_create_form_context(post_data, status_options, assignees, request=request)
        )

    partial = request.GET.get("partial")
    if request.headers.get("HX-Request") and partial == "rows":
        return render(request, "ui/_case_list_rows.html", context)

    return render(request, "ui/case_list.html", _base_context(request, **context))


@login_required
@require_GET
def case_list(request: HttpRequest) -> HttpResponse:
    """Open cases by default; closed ones on request.

    The `new=1` query parameter opens the New-case modal (for the `n` shortcut
    and for deep-linking into the modal state).
    """
    return _render_case_list(request, open_new_case=request.GET.get("new") == "1")
