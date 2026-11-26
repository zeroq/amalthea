"""Settings → Correlation: per-organisation automatic alert bundling rule (12.3-fu1)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from cases.models import CorrelationRule
from identity.models import AuditLog, Organisation
from identity.scoping import is_admin
from ui.views.common import _base_context, _require_admin_ui, not_viewer

MATCH_MODES = ("correlation_key", "shared_observable")


def _check_org_access(request: HttpRequest, organisation_id) -> HttpResponse | None:
    """Admin may edit only their own org; superAdmin may edit any explicit org."""
    user = request.user
    if user.role == "superAdmin":
        return None
    if user.role == "admin" and user.org_id == organisation_id:
        return None
    return render(request, "ui/404.html", status=404)


def _lazy_create_rule(organisation: Organisation) -> CorrelationRule:
    """Create a disabled rule on first view; never enable anything implicitly."""
    rule, created = CorrelationRule.objects.get_or_create(organisation=organisation)
    if created:
        rule.full_clean()
    return rule


def _snapshot(rule: CorrelationRule) -> dict[str, object]:
    return {
        "enabled": rule.enabled,
        "match_mode": rule.match_mode,
        "window_minutes": rule.window_minutes,
    }


def _context(
    request: HttpRequest,
    organisation: Organisation,
    rule: CorrelationRule,
    *,
    update_url: str,
    form_errors: bool = False,
    submitted: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "active_tab": "ui-settings-correlation",
        "form_errors": form_errors,
        "is_admin": is_admin(request.user),
        "is_superadmin": request.user.role == "superAdmin",
        "match_modes": MATCH_MODES,
        "org": organisation,
        "rule": rule,
        "submitted": submitted or {},
        "update_url": update_url,
    }


@not_viewer
@login_required
def settings_correlation(request: HttpRequest) -> HttpResponse:
    """Redirect to the organisation-specific settings page."""
    guard = _require_admin_ui(request)
    if guard:
        return guard
    return redirect("ui-settings-correlation-org", organisation_id=request.user.org_id)


@not_viewer
@login_required
def settings_correlation_for_org(request: HttpRequest, organisation_id) -> HttpResponse:
    """GET the correlation rule form for one organisation."""
    guard = _require_admin_ui(request)
    if guard:
        return guard
    organisation = get_object_or_404(Organisation, pk=organisation_id)
    access_guard = _check_org_access(request, organisation_id)
    if access_guard:
        return access_guard

    rule = _lazy_create_rule(organisation)
    context = _context(
        request,
        organisation,
        rule,
        update_url=reverse(
            "ui-settings-correlation-update", kwargs={"organisation_id": organisation.id}
        ),
    )
    return render(request, "ui/settings_correlation.html", _base_context(request, **context))


@not_viewer
@login_required
@require_POST
def update_correlation_rule(request: HttpRequest, organisation_id) -> HttpResponse:
    """Validate and update the rule; audit only actual changes."""
    guard = _require_admin_ui(request)
    if guard:
        return guard
    organisation = get_object_or_404(Organisation, pk=organisation_id)
    access_guard = _check_org_access(request, organisation_id)
    if access_guard:
        return access_guard

    rule = _lazy_create_rule(organisation)
    update_url = reverse(
        "ui-settings-correlation-update", kwargs={"organisation_id": organisation.id}
    )

    submitted: dict[str, object] = {
        "enabled": request.POST.get("enabled") == "on",
        "match_mode": request.POST.get("match_mode", ""),
        "window_minutes": request.POST.get("window_minutes", ""),
    }

    try:
        window_minutes = int(str(submitted["window_minutes"]))
    except (TypeError, ValueError):
        window_minutes = 0
    if not 1 <= window_minutes <= 60:
        messages.error(request, "Window must be between 1 and 60 minutes.")
        context = _context(
            request,
            organisation,
            rule,
            update_url=update_url,
            form_errors=True,
            submitted=submitted,
        )
        return render(request, "ui/settings_correlation.html", _base_context(request, **context))

    match_mode = str(submitted["match_mode"])
    if match_mode not in MATCH_MODES:
        messages.error(request, "Choose a valid correlation match mode.")
        context = _context(
            request,
            organisation,
            rule,
            update_url=update_url,
            form_errors=True,
            submitted=submitted,
        )
        return render(request, "ui/settings_correlation.html", _base_context(request, **context))

    old_snapshot = _snapshot(rule)
    new_snapshot: dict[str, object] = {
        "enabled": bool(submitted["enabled"]),
        "match_mode": match_mode,
        "window_minutes": window_minutes,
    }
    if new_snapshot == old_snapshot:
        messages.info(request, "Correlation settings unchanged.")
        return redirect("ui-settings-correlation-org", organisation_id=organisation_id)

    rule.enabled = bool(new_snapshot["enabled"])
    rule.match_mode = str(new_snapshot["match_mode"])
    rule.window_minutes = int(new_snapshot["window_minutes"])
    rule.updated_by = request.user
    try:
        rule.full_clean()
    except Exception:
        messages.error(request, "Correlation settings could not be saved.")
        context = _context(
            request,
            organisation,
            rule,
            update_url=update_url,
            form_errors=True,
            submitted=submitted,
        )
        return render(request, "ui/settings_correlation.html", _base_context(request, **context))

    rule.save(update_fields=["enabled", "match_mode", "window_minutes", "updated_by", "updated_at"])

    break_glass = request.user.role == "superAdmin" and request.user.org_id != organisation.id
    AuditLog.objects.create(
        actor=request.user,
        actor_org=request.user.org,
        target_org=organisation,
        action="case.correlation_rule_changed",
        target_type="CorrelationRule",
        target_id=str(rule.id),
        ip=request.META.get("REMOTE_ADDR"),
        details={
            "old": old_snapshot,
            "new": new_snapshot,
            "break_glass": break_glass,
        },
    )

    if new_snapshot["enabled"]:
        messages.success(request, "Automatic alert bundling is enabled for this organisation.")
    else:
        messages.success(request, "Correlation settings updated.")
    return redirect("ui-settings-correlation-org", organisation_id=organisation_id)
