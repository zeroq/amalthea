"""Settings → Agent access: MCP organisation autonomy policy (Phase 4)."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as tz
from django.views.decorators.http import require_POST

from core.enums import SEVERITY_CHOICES
from identity.models import Organisation
from identity.scoping import is_admin
from mcp_server.models import McpOrgPolicy
from mcp_server.services.autonomy import ALLOWED_ACTION_REGISTRY
from ui.views.common import _base_context, _require_admin_ui, not_viewer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role gating: admin may only manage their own org; superAdmin may manage any
# explicit org from the URL; analyst/viewer and cross-org admin get 404.
# ---------------------------------------------------------------------------


def _check_org_access(request: HttpRequest, organisation_id) -> HttpResponse | None:
    """Return 404 if the user cannot manage this organisation.

    - admin (non-superAdmin): only own org
    - superAdmin: any explicit org in URL
    - analyst/viewer / cross-org admin: 404
    """
    user = request.user

    if user.role == "superAdmin":
        # superAdmin may manage any org explicitly
        return None

    if user.role == "admin":
        # admin may only manage their own organisation
        if user.org_id != organisation_id:
            logger.warning(
                "cross-org admin denied: user org=%s target org=%s",
                user.org_id,
                organisation_id,
                extra={"user_id": str(user.pk)},
            )
            return render(request, "ui/404.html", status=404)
        return None

    # analyst, viewer, or any other role
    logger.warning(
        "non-admin denied autonomy access: role=%s org=%s",
        user.role,
        organisation_id,
        extra={"user_id": str(user.pk)},
    )
    return render(request, "ui/404.html", status=404)


# ---------------------------------------------------------------------------
# Lazily create policy if absent; returns (policy, _was_created)
# ---------------------------------------------------------------------------


def _lazy_create_policy(organisation: Organisation) -> tuple[McpOrgPolicy, bool]:
    """Create a policy for the organisation if one does not exist yet.

    Returns ``(policy, _was_created)`` so callers know whether this is a new
    policy or an existing one being loaded.
    """
    policy, created = McpOrgPolicy.objects.get_or_create(
        organisation=organisation,
        defaults={
            "autonomy_enabled": False,
            "shadow_mode": True,
            "allowed_actions": [],
            "max_auto_severity": 1,
            "daily_action_cap": 20,
            "require_four_eyes": True,
        },
    )
    if created:
        policy.full_clean()
    return policy, created


# ---------------------------------------------------------------------------
# Build context shared by GET and POST
# ---------------------------------------------------------------------------


def _autonomy_context(
    request: HttpRequest,
    organisation: Organisation,
    policy: McpOrgPolicy,
    active_tab: str | None = None,
    update_url: str | None = None,
) -> dict[str, object]:
    """Build the template context for autonomy settings pages."""
    is_superadmin = request.user.role == "superAdmin"
    is_admin_user = is_admin(request.user)

    # Allowlist choices from server-side registry (exact entries only)
    allowlist_choices = sorted(ALLOWED_ACTION_REGISTRY)

    # Severity choices from core.enums
    severity_choices = list(SEVERITY_CHOICES)

    # Build active_tab for settings nav highlighting
    if active_tab is None:
        active_tab = "ui-settings-agent-autonomy"

    return {
        "policy": policy,
        "allowlist_choices": allowlist_choices,
        "severity_choices": severity_choices,
        "org": organisation,
        "active_tab": active_tab,
        "is_superadmin": is_superadmin,
        "is_admin": is_admin_user,
        "now": tz.now(),
        "update_url": update_url,
    }


# ---------------------------------------------------------------------------
# GET: settings agent autonomy
# ---------------------------------------------------------------------------


@not_viewer
@login_required
def settings_agent_autonomy(request: HttpRequest, organisation_id) -> HttpResponse:
    """Settings → Agent access → Autonomy policy configuration.

    Guards:
    - @not_viewer: analysts/viewers get 404
    - @login_required
    - _require_admin_ui: only admin/superAdmin
    - Org-level gating via _check_org_access
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    organisation = get_object_or_404(Organisation, pk=organisation_id)
    access_guard = _check_org_access(request, organisation_id)
    if access_guard:
        return access_guard

    policy, __was_created = _lazy_create_policy(organisation)
    context = _autonomy_context(request, organisation, policy)

    return render(request, "ui/settings_agent_autonomy.html", _base_context(request, **context))


# ---------------------------------------------------------------------------
# POST: update autonomy policy
# ---------------------------------------------------------------------------


@not_viewer
@login_required
@require_POST
def update_agent_autonomy(request: HttpRequest, organisation_id) -> HttpResponse:
    """Update the MCP organisation autonomy policy.

    POST handling:
    - Parse autonomy_enabled, shadow_mode, allowed_actions (getlist),
      max_auto_severity, daily_action_cap, require_four_eyes.
    - Validate against model full_clean.
    - On invalid input: show messages.error and redirect without partial mutation.
    - Enforce-mode confirmation: if the submitted state would set
      autonomy_enabled=True AND shadow_mode=False, require
      confirm_shadow_exit=1 via a <dialog> confirmation.
    - Audit every actual change using record_mcp_org_policy_change(...).
    - Set policy.updated_by=request.user on change.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    organisation = get_object_or_404(Organisation, pk=organisation_id)
    access_guard = _check_org_access(request, organisation_id)
    if access_guard:
        return access_guard

    policy, _was_created = _lazy_create_policy(organisation)

    # --- Parse POST data ---

    # autonomy_enabled: bool from checkbox
    autonomy_enabled = request.POST.get("autonomy_enabled") == "on"

    # shadow_mode: bool from checkbox
    shadow_mode = request.POST.get("shadow_mode") == "on"

    # allowed_actions: getlist, exact values only from the server registry
    raw_actions = request.POST.getlist("allowed_actions")
    # Only allow entries that are in the server-side registry
    allowed_actions = [a for a in raw_actions if a in ALLOWED_ACTION_REGISTRY]

    # max_auto_severity: integer
    try:
        max_auto_severity = int(request.POST.get("max_auto_severity", policy.max_auto_severity))
    except (ValueError, TypeError):
        max_auto_severity = policy.max_auto_severity

    # daily_action_cap: positive integer
    try:
        daily_action_cap = int(request.POST.get("daily_action_cap", policy.daily_action_cap))
        if daily_action_cap < 1:
            daily_action_cap = policy.daily_action_cap
    except (ValueError, TypeError):
        daily_action_cap = policy.daily_action_cap

    # require_four_eyes: bool from checkbox
    require_four_eyes = request.POST.get("require_four_eyes") == "on"

    # --- Enforce-mode confirmation (shadow exit) ---
    # If the submitted state would enable autonomy AND disable shadow mode,
    # require explicit confirmation via confirm_shadow_exit=1.
    would_enable_autonomy = autonomy_enabled and not shadow_mode
    confirm_shadow_exit = request.POST.get("confirm_shadow_exit") == "1"

    # Build the new snapshot for audit/comparison
    new_snapshot = {
        "autonomy_enabled": autonomy_enabled,
        "shadow_mode": shadow_mode,
        "allowed_actions": list(allowed_actions) if allowed_actions else [],
        "max_auto_severity": max_auto_severity,
        "daily_action_cap": daily_action_cap,
        "require_four_eyes": require_four_eyes,
    }

    # Check: would this set autonomy_enabled=True and shadow_mode=False?
    if would_enable_autonomy and not confirm_shadow_exit:
        # Show the enforce-mode confirmation dialog
        # Redirect back with a flag that the dialog should be shown
        context = _autonomy_context(
            request,
            organisation,
            policy,
            active_tab="ui-settings-agent-autonomy",
            update_url=reverse(
                "ui-settings-agent-autonomy-update", kwargs={"organisation_id": organisation.id}
            ),
        )
        context["show_shadow_exit_confirmation"] = True
        context["shadow_exit_confirm_fields"] = {"confirm_shadow_exit": "1"}
        return render(request, "ui/settings_agent_autonomy.html", _base_context(request, **context))

    # --- Determine the audit action ---

    # _was_created tells us if this is a new policy or an existing one
    if _was_created:
        # New policy was just created — no prior state to compare
        # The policy was created with defaults, and the admin is viewing/updating it for the first time.
        # If this is a true creation (first time viewing), action is .created.
        # But since _lazy_create_policy uses get_or_create, if a policy already existed,
        # _was_created=False and we below compare old/new.
        # For now, if the policy didn't exist before, action is .created.
        # We determine this by checking if a policy row existed before.
        # Since get_or_create returns created=True only when a new row was made,
        # we can use that directly.
        action = "mcp_org_policy.created"
    else:
        # Existing policy being updated — compare old and new snapshots
        # Capture old snapshot BEFORE we modify the policy instance
        old_snapshot = {
            "autonomy_enabled": policy.autonomy_enabled,
            "shadow_mode": policy.shadow_mode,
            "allowed_actions": list(policy.allowed_actions) if policy.allowed_actions else [],
            "max_auto_severity": policy.max_auto_severity,
            "daily_action_cap": policy.daily_action_cap,
            "require_four_eyes": policy.require_four_eyes,
        }

        # Compare old and new to determine the specific action
        autonomy_was = old_snapshot["autonomy_enabled"]
        autonomy_now = new_snapshot["autonomy_enabled"]

        if autonomy_was and not autonomy_now:
            action = "mcp_org_policy.disabled"
        elif not autonomy_was and autonomy_now:
            action = "mcp_org_policy.enabled"
        elif (
            autonomy_was
            and autonomy_now
            and old_snapshot["shadow_mode"] != new_snapshot["shadow_mode"]
        ):
            # Shadow mode changed
            if not new_snapshot["shadow_mode"] and old_snapshot["shadow_mode"]:
                action = "mcp_org_policy.shadow_exited"
            else:
                action = "mcp_org_policy.updated"
        else:
            action = "mcp_org_policy.updated"

    # --- Validate against model full_clean ---

    # Temporarily assign parsed values to the policy instance for full_clean
    policy.autonomy_enabled = autonomy_enabled
    policy.shadow_mode = shadow_mode
    policy.allowed_actions = allowed_actions if allowed_actions else []
    policy.max_auto_severity = max_auto_severity
    policy.daily_action_cap = daily_action_cap
    policy.require_four_eyes = require_four_eyes

    try:
        policy.full_clean()
    except Exception as exc:
        messages.error(request, f"Invalid input: {exc}")
        # Redirect back without partial mutation — re-render the form with errors
        context = _autonomy_context(
            request,
            organisation,
            policy,
            active_tab="ui-settings-agent-autonomy",
            update_url=reverse(
                "ui-settings-agent-autonomy-update", kwargs={"organisation_id": organisation.id}
            ),
        )
        context["form_errors"] = True
        return render(request, "ui/settings_agent_autonomy.html", _base_context(request, **context))

    # --- Apply changes and audit ---

    # Save the policy with updated_by
    policy.updated_by = request.user
    policy.save(
        update_fields=[
            "autonomy_enabled",
            "shadow_mode",
            "allowed_actions",
            "max_auto_severity",
            "daily_action_cap",
            "require_four_eyes",
            "updated_by",
            "updated_at",
        ]
    )

    # Record audit
    from identity.audit import record_mcp_org_policy_change

    record_mcp_org_policy_change(
        request,
        actor=request.user,
        action=action,
        target_org=organisation,
        target_id=str(policy.id),
        old_snapshot=old_snapshot if not _was_created else {},
        new_snapshot=new_snapshot,
    )

    # Success — redirect to the autonomy page
    messages.success(request, "Autonomy policy updated successfully.")
    return redirect("ui-settings-agent-autonomy", organisation_id=organisation_id)
