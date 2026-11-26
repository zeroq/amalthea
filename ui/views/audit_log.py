"""Read-only superAdmin audit log viewer (§6.13)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from identity.models import AuditLog, Organisation
from ui.pagination import paginate

from .common import _base_context, _require_superadmin_ui, not_viewer


@not_viewer
@login_required
def settings_audit_log(request: HttpRequest) -> HttpResponse:
    """Read-only audit log for superAdmins (ADR-004 Q5 / TODO §6.13)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    qs = AuditLog.objects.select_related("actor", "actor_org", "target_org").order_by("-created_at")

    action = (request.GET.get("action") or "").strip()
    target_type = (request.GET.get("target_type") or "").strip()
    actor = (request.GET.get("actor") or "").strip()
    target_org = (request.GET.get("target_org") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if action:
        qs = qs.filter(action__icontains=action)
    if target_type:
        qs = qs.filter(target_type__icontains=target_type)
    if actor:
        qs = qs.filter(Q(actor__login__icontains=actor) | Q(actor__username__icontains=actor))
    if target_org:
        qs = qs.filter(target_org__name__icontains=target_org)
    if q:
        qs = qs.filter(
            Q(action__icontains=q)
            | Q(target_type__icontains=q)
            | Q(target_id__icontains=q)
            | Q(actor__login__icontains=q)
            | Q(actor__username__icontains=q)
            | Q(actor_org__name__icontains=q)
            | Q(target_org__name__icontains=q)
            | Q(ip__icontains=q)
        )

    page_obj = paginate(request, qs)
    # Filters are in the querystring; filtered_from is the total unfiltered count only when helpful.
    filtered_from = (
        AuditLog.objects.count() if any([action, target_type, actor, target_org, q]) else None
    )

    context = _base_context(
        request,
        page_obj=page_obj,
        filtered_from=filtered_from,
        audit_logs=list(page_obj.object_list),
        filters={
            "action": action,
            "target_type": target_type,
            "actor": actor,
            "target_org": target_org,
            "q": q,
        },
        action_choices=AuditLog.objects.order_by("action")
        .values_list("action", flat=True)
        .distinct()[:200],
        target_type_choices=AuditLog.objects.order_by("target_type")
        .values_list("target_type", flat=True)
        .distinct()[:200],
        org_choices=Organisation.objects.order_by("name")[:200],
    )
    return render(request, "ui/settings_audit_log.html", context)
