"""Rejected observables list and promote view for the UI."""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from observables.models import RejectedObservable
from observables.rejected import promote_rejected_observable, scoped_rejected_observables
from ui.pagination import paginate
from ui.views.common import _base_context, not_viewer

logger = logging.getLogger(__name__)


@not_viewer
@login_required
@require_GET
def rejected_observables_list(request: HttpRequest) -> HttpResponse:
    """List rejected observables with optional status/reason filters and pagination.

    GET params:
      ?status=<token>   one of: (empty for all), pending, promoted
      ?reason=<token>   one of: (empty for all), duplicate, irrelevant, false_positive,
                        incomplete, too_noisy, other
    """
    status_filter = (request.GET.get("status") or "").strip()
    reason_filter = (request.GET.get("reason") or "").strip()

    queryset = scoped_rejected_observables(request.user, status=status_filter, reason=reason_filter)

    page_obj = paginate(request, queryset)
    filtered_from = None
    if status_filter or reason_filter:
        filtered_from = queryset.count() if queryset.count() > page_obj.paginator.count else None

    return render(
        request,
        "ui/rejected_observables.html",
        _base_context(
            request,
            page_obj=page_obj,
            rejected_observables=page_obj.object_list,
            status_filter=status_filter,
            reason_filter=reason_filter,
            filtered_from=filtered_from,
        ),
    )


@not_viewer
@login_required
@require_POST
def rejected_observable_promote(request: HttpRequest, id: str) -> HttpResponse:
    """Promote a rejected observable.

    POST: call promote_rejected_observable, then redirect back to the list preserving filters.
    HTMX: return the rows partial, then close the source dialog via HX-Trigger-After-Swap.

    The ``id`` is the RejectedObservable UUID.
    """
    try:
        observable, _newly_promoted = promote_rejected_observable(request.user, id)
    except RejectedObservable.DoesNotExist:
        messages.error(request, "Rejected observable not found or not visible.")
        return redirect("ui-rejected-observables")

    if request.headers.get("HX-Request"):
        # HTMX request: return the rows partial so the table updates in place
        rows_html = render_to_string(
            "ui/_rejected_observables_rows.html",
            {
                "rejected_observables": [observable],
                "status_filter": request.POST.get("status", ""),
                "reason_filter": request.POST.get("reason", ""),
            },
            request=request,
        )
        return HttpResponse(
            rows_html,
            headers={
                "HX-Trigger-After-Swap": json.dumps(
                    {"closePromoteModal": f"promote-modal-{observable.id}"}
                )
            },
        )

    # Regular POST: redirect back preserving filters
    status_go = request.POST.get("status", "")
    reason_go = request.POST.get("reason", "")
    return redirect(f"{reverse('ui-rejected-observables')}?status={status_go}&reason={reason_go}")
