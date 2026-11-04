"""Dashboard view."""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from alerts.models import Alert
from automation.models import AutomationRun
from cases.models import (
    Case,
)
from identity.scoping import (
    case_visible_q,
    org_q,
)

from .common import (
    _base_context,
)

logger = logging.getLogger(__name__)


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    """Landing page: what arrived, what is open, what automation has done."""

    counts = {
        "alerts_total": Alert.objects.filter(org_q("owner_org", request.user)).count(),
        "alerts_unlinked": Alert.objects.filter(
            org_q("owner_org", request.user), case__isnull=True, status__stage="New"
        ).count(),
        "cases_total": Case.objects.filter(case_visible_q(request.user)).count(),
        "cases_open": Case.objects.filter(
            case_visible_q(request.user), closed_date__isnull=True
        ).count(),
        "runs_total": AutomationRun.objects.filter(
            case__in=Case.objects.filter(case_visible_q(request.user))
        ).count(),
        "runs_failed": AutomationRun.objects.filter(
            case__in=Case.objects.filter(case_visible_q(request.user)), status="Failed"
        ).count(),
    }
    by_severity = {
        row["severity"]: row["n"]
        for row in Case.objects.filter(case_visible_q(request.user), closed_date__isnull=True)
        .values("severity")
        .annotate(n=Count("id"))
    }
    return render(
        request,
        "ui/dashboard.html",
        _base_context(
            request,
            counts=counts,
            by_severity=by_severity,
            recent_alerts=list(
                Alert.objects.select_related("status", "owner_org")
                .filter(org_q("owner_org", request.user), case__isnull=True, status__stage="New")
                .order_by("-date")[:10]
            ),
            open_cases=list(
                Case.objects.select_related("status", "assignee", "owner_org")
                .filter(case_visible_q(request.user), closed_date__isnull=True)
                .order_by("-severity", "-start_date")[:10]
            ),
            runs=list(
                AutomationRun.objects.select_related("case", "playbook")
                .filter(case__in=Case.objects.filter(case_visible_q(request.user)))
                .order_by("-created_at")[:10]
            ),
        ),
    )
