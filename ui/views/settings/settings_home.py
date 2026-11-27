"""Settings home view."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse

from ..common import _require_admin_ui, not_viewer


@not_viewer
@login_required
def settings_home(request: HttpRequest) -> HttpResponse:
    """Settings entry: redirect to first submenu item."""
    from django.shortcuts import redirect

    guard = _require_admin_ui(request)
    if guard:
        return guard

    if getattr(request.user, "role", "") == "superAdmin":
        return redirect("ui-settings-organisations")
    return redirect("ui-settings-sources")
