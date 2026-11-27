"""Case share UI views (§13.3)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from identity.models import Organisation
from identity.scoping import (
    is_case_owner,
    resolve_case,
)
from identity.services import (
    resolve_organisation,
)
from ui.pagination import paginate

from .common import (
    _base_context,
    not_viewer,
)


@not_viewer
@login_required
def case_share_list(request: HttpRequest, case_id: str) -> HttpResponse:
    """List and manage case shares (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if not is_case_owner(request.user, case):
        return render(request, "ui/404.html", status=404)

    from cases.models import Share

    shares_qs = case.shares.select_related("organisation").order_by("created_at")
    available_orgs = Organisation.objects.exclude(
        id__in=shares_qs.values_list("organisation_id", flat=True)
    ).exclude(id=case.owner_org_id)

    if request.method == "POST":
        org_id = request.POST.get("organisation")
        permissions = request.POST.get("permissions", "read")
        if org_id:
            # A tampered/blank id must not raise out of the query compiler (F7 class).
            org = resolve_organisation(org_id)
            if org is None:
                messages.error(request, "Select a valid organisation.")
                return redirect("ui-case-share-list", case_id=case.number)
            share, created = Share.objects.update_or_create(
                case=case,
                organisation=org,
                defaults={"permissions": {"read": True, "write": permissions == "readwrite"}},
            )
            messages.success(
                request, f"Share {'created' if created else 'updated'} for {org.name}."
            )
        return redirect("ui-case-share-list", case_id=case.number)

    # Build full list of share dicts (for template compatibility)
    all_shares = []
    for share in shares_qs:
        all_shares.append(
            {
                "share": share,
                "revoke_url": reverse(
                    "ui-case-share-revoke", kwargs={"case_id": case.number, "share_id": share.id}
                ),
            }
        )

    # Pagination for the shares list (primary list P) — use the frozen helper.
    # Paginator accepts a sequence, so the list-of-dicts shape the template expects is preserved.
    page_obj = paginate(request, all_shares)
    filtered_from = None  # No filters on this list
    shares = page_obj.object_list

    return render(
        request,
        "ui/case_share_list.html",
        _base_context(
            request,
            case=case,
            page_obj=page_obj,
            filtered_from=filtered_from,
            shares=shares,
            available_orgs=available_orgs,
        ),
    )


@not_viewer
@login_required
@require_POST
def case_share_revoke(request: HttpRequest, case_id: str, share_id: str) -> HttpResponse:
    """Revoke a case share (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if not is_case_owner(request.user, case):
        return render(request, "ui/404.html", status=404)

    from cases.models import Share

    share = get_object_or_404(Share, pk=share_id, case=case)
    org_name = share.organisation.name
    share.delete()
    messages.success(request, f"Revoked share for {org_name}.")
    return redirect("ui-case-share-list", case_id=case.number)
