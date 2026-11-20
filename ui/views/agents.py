"""Settings → Agent access: MCP credential lifecycle (admin/superAdmin)."""

from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from identity.audit import record_mcp_credential_change, record_mcp_oauth_binding_change
from identity.models import Organisation, User
from mcp_server.credentials import mint, revoke
from mcp_server.models import (
    PHASE1_SCOPES,
    PHASE2_SCOPES,
    McpCredential,
    McpOAuthBinding,
)
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


def _scope_oauth_bindings(request: HttpRequest):
    qs = McpOAuthBinding.objects.select_related("organisation", "service_user", "created_by")
    if request.user.role != "superAdmin":
        qs = qs.filter(organisation_id=request.user.org_id)
    return qs.order_by("-created_at")


def _settings_agents_context(request: HttpRequest) -> dict:
    is_superadmin = request.user.role == "superAdmin"
    page_obj = paginate(request, _scope_credentials(request))
    users = User.objects.filter(is_active=True).select_related("org")
    if not is_superadmin:
        users = users.filter(org_id=request.user.org_id)
    configured_issuer = str(getattr(settings, "MCP_OAUTH_ISSUER", "") or "").strip()
    return {
        "page_obj": page_obj,
        "credentials": list(page_obj.object_list),
        "oauth_bindings": list(_scope_oauth_bindings(request)[:100]),
        "oauth_enabled": getattr(settings, "MCP_AUTH_MODE", "static") in ("oauth", "both"),
        "configured_issuer": configured_issuer,
        "show_org_column": is_superadmin,
        "is_superadmin": is_superadmin,
        "organisations": Organisation.objects.order_by("name") if is_superadmin else None,
        "users": users.order_by("login")[:500],
        "mcp_url": request.build_absolute_uri("/mcp"),
        "allowed_scopes": list(PHASE1_SCOPES) + list(PHASE2_SCOPES),
        "phase1_scopes": list(PHASE1_SCOPES),
        "phase2_scopes": list(PHASE2_SCOPES),
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


def _parse_scopes(request: HttpRequest, service_user: User) -> list[str]:
    """Return validated MCP scopes from the create form.

    No selection means the Phase 1 read-only default. Reserved/unknown scopes
    and Phase 2 scopes for viewer service users are refused.
    """
    scopes = request.POST.getlist("scopes") or list(PHASE1_SCOPES)
    allowed = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
    if any(scope not in allowed for scope in scopes):
        raise ValueError("Unknown or reserved MCP scope requested.")
    if not scopes:
        raise ValueError("At least one scope is required.")
    if service_user.role == "viewer" and any(scope in PHASE2_SCOPES for scope in scopes):
        raise ValueError("Viewer service users cannot be granted propose or summary scopes.")
    return list(dict.fromkeys(scopes))


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
        scopes = _parse_scopes(request, service_user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-settings-agent-access")

    credential, plaintext = mint(
        organisation=organisation,
        user=service_user,
        name=name,
        created_by=request.user,
        expires_at=expires_at,
        scopes=scopes,
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


def _parse_oauth_scopes(request: HttpRequest) -> list[str]:
    values = request.POST.getlist("scopes") or request.POST.getlist("allowed_scopes")
    if not values and request.POST.get("scopes"):
        values = [request.POST["scopes"]]
    if not values and request.POST.get("allowed_scopes"):
        values = str(request.POST["allowed_scopes"]).split()
    scopes = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not scopes:
        return list(PHASE1_SCOPES)
    allowed = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
    if any(scope not in allowed for scope in scopes):
        raise ValueError("Unknown or reserved MCP scope requested.")
    return scopes


@not_viewer
@login_required
def settings_oauth_bindings(request: HttpRequest) -> HttpResponse:
    """Alias route used by the OAuth bindings list/reverse name."""
    return settings_agents(request)


@not_viewer
@login_required
@require_POST
def settings_oauth_binding_create(request: HttpRequest) -> HttpResponse:
    """Create an external-IdP OAuth binding. No secrets are accepted or stored."""
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
    issuer = (request.POST.get("issuer") or "").strip()
    configured_issuer = str(getattr(settings, "MCP_OAUTH_ISSUER", "") or "").strip()
    client_id = (request.POST.get("client_id") or "").strip()
    subject = (request.POST.get("subject") or "").strip()
    service_user_login = (request.POST.get("service_user_login") or "").strip()

    if not name or not issuer or not client_id or not subject:
        messages.error(request, "Name, issuer, client ID and subject are required.")
        return redirect("ui-settings-agent-access")
    if len(name) > 100 or len(client_id) > 255 or len(subject) > 255 or len(issuer) > 500:
        messages.error(request, "One or more fields exceed their maximum length.")
        return redirect("ui-settings-agent-access")
    if not configured_issuer or issuer != configured_issuer:
        messages.error(request, "Issuer must match the configured MCP OAuth issuer.")
        return redirect("ui-settings-agent-access")

    service_user = (
        User.objects.filter(
            login=service_user_login or request.user.login,
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
        scopes = _parse_oauth_scopes(request)
        binding = McpOAuthBinding(
            name=name,
            issuer=issuer,
            client_id=client_id,
            subject=subject,
            organisation=organisation,
            service_user=service_user,
            created_by=request.user,
            allowed_scopes=scopes,
            is_active=True,
        )
        binding.full_clean()
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-settings-agent-access")
    except Exception as exc:
        # ValidationError messages are safe; never log request.POST (may contain identifiers).
        messages.error(request, "; ".join(getattr(exc, "messages", ["Invalid OAuth binding."])))
        return redirect("ui-settings-agent-access")

    binding.save()
    record_mcp_oauth_binding_change(
        request,
        actor=request.user,
        action="mcp_oauth_binding.created",
        binding=binding,
    )
    logger.info(
        "mcp oauth binding created id=%s org=%s actor=%s",
        binding.id,
        organisation.id,
        request.user.pk,
    )

    context = _settings_agents_context(request)
    context.update(oauth_binding_created=True, oauth_binding=binding)
    response = render(request, "ui/settings_agents.html", _base_context(request, **context))
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _scoped_oauth_binding(request: HttpRequest, binding_id: str) -> McpOAuthBinding | None:
    qs = McpOAuthBinding.objects.select_related("organisation", "service_user")
    if request.user.role != "superAdmin":
        qs = qs.filter(organisation_id=request.user.org_id)
    try:
        return qs.get(pk=binding_id)
    except (McpOAuthBinding.DoesNotExist, ValueError, TypeError):
        return None


@not_viewer
@login_required
@require_POST
def settings_oauth_binding_disable(request: HttpRequest, binding_id: str) -> HttpResponse:
    """Disable an OAuth binding; verification fails closed immediately."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    binding = _scoped_oauth_binding(request, binding_id)
    if binding is None:
        return render(request, "ui/404.html", status=404)
    if not binding.is_active:
        messages.info(request, "That OAuth binding is already disabled.")
        return redirect("ui-settings-agent-access")

    binding.is_active = False
    binding.save(update_fields=["is_active", "updated_at"])
    record_mcp_oauth_binding_change(
        request,
        actor=request.user,
        action="mcp_oauth_binding.revoked",
        binding=binding,
    )
    logger.info("mcp oauth binding disabled id=%s actor=%s", binding.id, request.user.pk)
    messages.success(request, f"Disabled OAuth binding '{binding.name}'.")
    return redirect("ui-settings-agent-access")
