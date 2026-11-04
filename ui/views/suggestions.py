"""Observable suggestion views."""

from __future__ import annotations

import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from identity.scoping import (
    resolve_case,
)
from observables.models import ObservableSuggestion

from .common import (
    _case_detail_redirect,
    not_viewer,
)

logger = logging.getLogger(__name__)


@not_viewer
@login_required
@require_POST
def suggestion_accept(request: HttpRequest, suggestion_id: uuid.UUID) -> HttpResponse:
    """Accept a single suggestion, creating the CaseObservable link and firing triggers."""
    from observables.suggestions import AcceptSuggestionError, accept_suggestion

    suggestion = get_object_or_404(
        ObservableSuggestion.objects.select_related(
            "case", "data_type", "owner_org", "source_alert"
        ),
        pk=suggestion_id,
    )
    if suggestion.case_id is None:
        messages.error(request, "Suggestion is not associated with a case.")
        return redirect("ui-case-list")

    case = resolve_case(request.user, str(suggestion.case_id), write=True)
    if case is None:
        raise Http404()

    if suggestion.status == "accepted":
        messages.success(request, "Suggestion accepted and linked to case.")
        return _case_detail_redirect(request, case.number)

    try:
        accept_suggestion(suggestion, actor=request.user)
    except AcceptSuggestionError:
        messages.error(request, "Failed to accept suggestion: unknown observable type")
        return _case_detail_redirect(request, case.number)

    messages.success(request, "Suggestion accepted and linked to case.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def suggestion_reject(request: HttpRequest, suggestion_id: uuid.UUID) -> HttpResponse:
    """Reject a single suggestion."""

    suggestion = get_object_or_404(
        ObservableSuggestion.objects.select_related("case", "data_type", "owner_org"),
        pk=suggestion_id,
    )
    case = suggestion.case
    if case is None:
        messages.error(request, "Suggestion is not associated with a case.")
        return redirect("ui-case-list")

    case = resolve_case(request.user, str(case.id), write=True)
    if case is None:
        raise Http404()

    if suggestion.status == "rejected":
        messages.success(request, "Suggestion rejected.")
        return _case_detail_redirect(request, case.number)

    if suggestion.status == "accepted":
        messages.error(request, "Cannot reject an accepted suggestion")
        return _case_detail_redirect(request, case.number)

    suggestion.status = "rejected"
    suggestion.reviewed_by = request.user
    suggestion.reviewed_at = timezone.now()
    suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    messages.success(request, "Suggestion rejected.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def suggestion_ignore(request: HttpRequest, suggestion_id: uuid.UUID) -> HttpResponse:
    """Ignore a single suggestion."""

    suggestion = get_object_or_404(
        ObservableSuggestion.objects.select_related("case", "data_type", "owner_org"),
        pk=suggestion_id,
    )
    case = suggestion.case
    if case is None:
        messages.error(request, "Suggestion is not associated with a case.")
        return redirect("ui-case-list")

    case = resolve_case(request.user, str(case.id), write=True)
    if case is None:
        raise Http404()

    if suggestion.status == "ignored":
        messages.success(request, "Suggestion ignored.")
        return _case_detail_redirect(request, case.number)

    if suggestion.status == "accepted":
        messages.error(request, "Cannot ignore an accepted suggestion")
        return _case_detail_redirect(request, case.number)

    suggestion.status = "ignored"
    suggestion.reviewed_by = request.user
    suggestion.reviewed_at = timezone.now()
    suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    messages.success(request, "Suggestion ignored.")
    return _case_detail_redirect(request, case.number)


def _bulk_suggestion_action(
    request: HttpRequest,
    case_id: str,
    target_status: str,
    verb: str,
) -> HttpResponse:
    """Apply a review status to the selected suggestions of one case.

    Shared by the bulk accept/reject/ignore UI actions. The suggestions are scoped to the case
    the analyst can write to (`resolve_case(..., write=True)`), so a bulk action can never reach
    a suggestion in another case or another org.

    The per-item semantics live in `observables.suggestions.bulk_review_suggestions`, which the
    JSON API uses too — the UI and the API cannot drift.
    """
    from observables.suggestions import bulk_review_suggestions

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    suggestion_ids = request.POST.getlist("suggestion_ids")
    if not suggestion_ids:
        messages.error(request, "No suggestions selected.")
        return _case_detail_redirect(request, case.number)

    suggestions = ObservableSuggestion.objects.select_related(
        "case", "source_alert", "data_type", "owner_org"
    ).filter(id__in=suggestion_ids, case=case)

    with transaction.atomic():
        results = bulk_review_suggestions(
            suggestions, target_status=target_status, actor=request.user
        )

    errors = [error for _suggestion, error in results if error is not None]
    succeeded = len(results) - len(errors)
    if errors:
        messages.error(
            request,
            f"{verb} {succeeded} suggestion(s); {len(errors)} failed: {errors[0]}",
        )
    else:
        messages.success(request, f"{verb} {succeeded} suggestion(s).")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def suggestions_bulk_accept(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk accept selected suggestions."""
    return _bulk_suggestion_action(request, case_id, "accepted", "Accepted")


@not_viewer
@login_required
@require_POST
def suggestions_bulk_reject(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk reject selected suggestions."""
    return _bulk_suggestion_action(request, case_id, "rejected", "Rejected")


@not_viewer
@login_required
@require_POST
def suggestions_bulk_ignore(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk ignore selected suggestions."""
    return _bulk_suggestion_action(request, case_id, "ignored", "Ignored")
