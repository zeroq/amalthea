"""Settings → Agent access: MCP credential lifecycle (admin/superAdmin)."""

from __future__ import annotations

import logging
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from identity.audit import record_mcp_credential_change
from identity.models import Organisation, User
from mcp_server.credentials import mint, revoke
from mcp_server.models import McpCredential
from ui.pagination import paginate

from .common import _base_context, _require_admin_ui, not_viewer

logger = logging.getLogger(__name__)


def _scope_credentials(request: HttpRequest):
    qs = McpCredential.objects.select_related("organisation", "user", "created_by").order_by(
        "-created_at"
    )
    if request.user.role != "superAdmin":
        qs = qs.filter(organisation_id=request.user.org_id)
    return qs


def _settings_agents_context(request: HttpRequest) -> dict:
    is_superadmin = request.user.role == "superAdmin"
    page_obj = paginate(request, _scope_credentials(request))
    users = User.objects.filter(is_active=True).select_related("org")
    if not is_superadmin:
        users = users.filter(org_id=request.user.org_id)
    return {
        "page_obj": page_obj,
        "credentials": list(page_obj.object_list),
        "show_org_column": is_superadmin,
        "is_superadmin": is_superadmin,
        "organisations": Organisation.objects.order_by("name") if is_superadmin else None,
        "users": users.order_by("login")[:500],
        "mcp_url": request.build_absolute_uri("/mcp"),
    }


def _parse_expiry(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError("Expiry must be a date and time.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if parsed <= timezone.now():
        raise ValueError("Expiry must be in the future.")
    return parsed


@not_viewer
@login_required
def settings_agents(request: HttpRequest) -> HttpResponse:
    """List agent credentials and render the create modal (admin/superAdmin)."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    return render(
        request,
        "ui/settings_agents.html",
        _base_context(request, **_settings_agents_context(request)),
    )


@not_viewer
@login_required
@require_POST
def settings_agent_create(request: HttpRequest) -> HttpResponse:
    """Mint an MCP credential and show its plaintext exactly once."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    is_superadmin = request.user.role == "superAdmin"
    if is_superadmin:
        organisation = Organisation.objects.filter(pk=request.POST.get("organisation")).first()
        if organisation is None:
            messages.error(request, "Select a valid organisation.")
            return redirect("ui-settings-agent-access")
    else:
        organisation = request.user.org

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-settings-agent-access")
    if len(name) > 100:
        messages.error(request, "Name must be 100 characters or fewer.")
        return redirect("ui-settings-agent-access")

    service_user = (
        User.objects.filter(
            login=(request.POST.get("user_login") or "").strip(),
            org=organisation,
            is_active=True,
        )
        .select_related("org")
        .first()
    )
    if service_user is None:
        messages.error(request, "Select an active service user in that organisation.")
        return redirect("ui-settings-agent-access")

    try:
        expires_at = _parse_expiry(request.POST.get("expires_at", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-settings-agent-access")

    credential, plaintext = mint(
        organisation=organisation,
        user=service_user,
        name=name,
        created_by=request.user,
        expires_at=expires_at,
    )
    record_mcp_credential_change(
        request,
        actor=request.user,
        action="mcp_credential.created",
        credential=credential,
    )
    logger.info(
        "mcp credential created id=%s org=%s actor=%s",
        credential.id,
        organisation.id,
        request.user.pk,
    )

    context = _settings_agents_context(request)
    context.update(
        credential_created=True,
        credential_plaintext=plaintext,
        credential=credential,
    )
    response = render(request, "ui/settings_agents.html", _base_context(request, **context))
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _scoped_credential(request: HttpRequest, credential_id: str) -> McpCredential | None:
    qs = McpCredential.objects.select_related("organisation", "user")
    if request.user.role != "superAdmin":
        qs = qs.filter(organisation_id=request.user.org_id)
    try:
        return qs.get(pk=credential_id)
    except (McpCredential.DoesNotExist, ValueError, TypeError):
        return None


@not_viewer
@login_required
@require_POST
def settings_agent_revoke(request: HttpRequest, credential_id: str) -> HttpResponse:
    """Revoke one MCP credential permanently."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    credential = _scoped_credential(request, credential_id)
    if credential is None:
        return render(request, "ui/404.html", status=404)
    if credential.is_revoked:
        messages.info(request, "That credential is already revoked.")
        return redirect("ui-settings-agent-access")

    revoke(credential.pk)
    record_mcp_credential_change(
        request,
        actor=request.user,
        action="mcp_credential.revoked",
        credential=credential,
    )
    logger.info("mcp credential revoked id=%s actor=%s", credential.id, request.user.pk)
    messages.success(request, f"Revoked agent credential '{credential.name}'.")
    return redirect("ui-settings-agent-access")
