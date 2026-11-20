"""Approval inbox for MCP Phase 2 proposals.

Routes:
  GET /approvals                          — list pending proposals for the org
  GET /approvals/<uuid:proposal_id>       — detail with arguments/preview
  POST /approvals/<uuid:proposal_id>/decide  — approve or decline
  POST /approvals/<uuid:proposal_id>/execute   — execute after approval
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from identity.scoping import is_viewer
from mcp_server.models import McpActionProposal
from mcp_server.services.proposals import approve_proposal, decline_proposal, execute_proposal
from ui.pagination import paginate

logger = logging.getLogger(__name__)


def _check_role_decide(proposal: McpActionProposal, request: HttpRequest) -> bool:
    """Return True if the user may decide (approve/decline) this proposal.

    Rules:
      - low risk: analyst or admin or superAdmin may decide
      - medium/high risk: admin or superAdmin only
      - requester may never decide their own non-low proposal
      - viewer/read-only may never decide
    """
    user = request.user
    if is_viewer(user):
        return False
    # Requester may never decide their own non-low proposal
    if proposal.requested_by_id == user.id and proposal.risk != "low":
        return False
    if proposal.risk == "low":
        # low risk: analyst or admin or superAdmin may decide
        user_role = getattr(user, "role", "")
        return user_role in ("analyst", "admin", "superAdmin")
    # medium or high: admin or superAdmin only
    user_role = getattr(user, "role", "")
    return user_role in ("admin", "superAdmin")


def _check_role_execute(proposal: McpActionProposal, request: HttpRequest) -> bool:
    """Return True if the user may execute this proposal.

    Same role rules as decide; execute follows decide checks.
    Also only acts on approved proposals.
    """
    if proposal.status != "approved":
        return False
    return _check_role_decide(proposal, request)


@login_required
def approvals_list(request: HttpRequest) -> HttpResponse:
    """List pending/recent proposals for the organisation."""
    # Viewer/read-only users get 404 (uniform deny, ADR-006)
    if is_viewer(request.user):
        return render(request, "ui/404.html", status=404)

    # All roles are scoped to their own organisation (no superAdmin cross-org path).
    qs = (
        McpActionProposal.objects.filter(organisation_id=request.user.org_id)
        .select_related("requested_by", "credential", "organisation")
        .order_by("-created_at")
    )

    # Only show non-terminal proposals (pending, not yet decided/executed)
    qs = qs.filter(status="pending")

    page_obj = paginate(request, qs)

    # Build annotated list with per-proposal permission flags
    items = []
    for proposal in page_obj.object_list:
        can_decide = _check_role_decide(proposal, request)
        can_execute = _check_role_execute(proposal, request)
        items.append(
            {
                "proposal": proposal,
                "can_decide": can_decide,
                "can_execute": can_execute,
            }
        )
    page_obj.object_list = items

    return render(
        request,
        "ui/approvals.html",
        {
            "page_obj": page_obj,
            "proposal_status_choices": McpActionProposal._meta.get_field("status").choices,
        },
    )


@login_required
def approvals_detail(request: HttpRequest, proposal_id: str) -> HttpResponse:
    """Detail page for one proposal — shows exact arguments, risk, requester, target, expiry, status."""
    proposal = get_object_or_404(
        McpActionProposal.objects.select_related("requested_by", "credential", "organisation"),
        pk=proposal_id,
    )
    # Uniform 404 for viewer or cross-org denial
    user = request.user
    if is_viewer(user):
        return render(request, "ui/404.html", status=404)
    # If the proposal belongs to another organisation, deny with 404 (no superAdmin cross-org path).
    if proposal.organisation_id != request.user.org_id:
        return render(request, "ui/404.html", status=404)

    can_decide = _check_role_decide(proposal, request)
    can_execute = _check_role_execute(proposal, request)

    return render(
        request,
        "ui/approvals_detail.html",
        {
            "proposal": proposal,
            "can_decide": can_decide,
            "can_execute": can_execute,
            "is_expired": proposal.is_expired,
            "decline_url": reverse("ui-approvals-decide", kwargs={"proposal_id": proposal.id}),
            "execute_url": reverse("ui-approvals-execute", kwargs={"proposal_id": proposal.id}),
        },
    )


@login_required
@require_POST
def approvals_decide(request: HttpRequest, proposal_id: str) -> HttpResponse:
    """POST: approve or decline a pending proposal.

    - low risk: analyst or admin may decide
    - medium/high risk: admin or superAdmin only
    - requester may never decide their own non-low proposal
    - viewer/read-only may never decide
    """
    proposal = get_object_or_404(
        McpActionProposal.objects.select_related("requested_by", "credential", "organisation"),
        pk=proposal_id,
    )
    user = request.user

    # Uniform denials: viewer, cross-org, already decided/terminal
    if is_viewer(user):
        return render(request, "ui/404.html", status=404)
    if proposal.organisation_id != user.org_id:
        return render(request, "ui/404.html", status=404)
    if proposal.status != "pending":
        # Already decided/terminal — 404 uniformly
        return render(request, "ui/404.html", status=404)

    # Role check
    if not _check_role_decide(proposal, request):
        # Surface a safe message rather than leaking logic
        messages.error(request, "You do not have permission to decide this proposal.")
        return redirect("ui-approvals-detail", proposal_id=proposal.id)

    # Read the action from the POST form
    action = request.POST.get("action", "")
    if action not in ("approve", "decline"):
        messages.error(request, "Invalid action submitted.")
        return redirect("ui-approvals-detail", proposal_id=proposal.id)

    try:
        if action == "approve":
            approve_proposal(proposal, by_user=user)
        elif action == "decline":
            note = (request.POST.get("note") or "").strip() or None
            decline_proposal(proposal, by_user=user, note=note)
    except ValidationError as e:
        # Surface a safe message; do not leak internal details
        messages.error(request, str(e) or "Could not complete the decision.")
        return redirect("ui-approvals-detail", proposal_id=proposal.id)

    # Redirect back to detail page to show updated state
    return redirect("ui-approvals-detail", proposal_id=proposal.id)


@login_required
@require_POST
def approvals_execute(request: HttpRequest, proposal_id: str) -> HttpResponse:
    """POST: execute an approved proposal.

    Follows the same role checks as decide. Only acts on approved proposals.
    """
    proposal = get_object_or_404(
        McpActionProposal.objects.select_related("requested_by", "credential", "organisation"),
        pk=proposal_id,
    )
    user = request.user

    # Uniform denials: viewer, cross-org, not approved, already decided/terminal
    if is_viewer(user):
        return render(request, "ui/404.html", status=404)
    if proposal.organisation_id != user.org_id:
        return render(request, "ui/404.html", status=404)
    if proposal.status != "approved":
        # Not approved — cannot execute
        messages.error(request, "This proposal must be approved before it can be executed.")
        return redirect("ui-approvals-detail", proposal_id=proposal.id)

    # Role check (follows decide checks)
    if not _check_role_execute(proposal, request):
        messages.error(request, "You do not have permission to execute this proposal.")
        return redirect("ui-approvals-detail", proposal_id=proposal.id)

    try:
        execute_proposal(proposal, by_user=user)
    except ValidationError as e:
        messages.error(request, str(e) or "Could not execute the proposal.")
        return redirect("ui-approvals-detail", proposal_id=proposal.id)

    # Redirect back to detail page to show updated state
    return redirect("ui-approvals-detail", proposal_id=proposal.id)
