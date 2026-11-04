"""Session authentication views."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import (
    LogoutView,
)
from django.http import HttpRequest, HttpResponse
from django.urls import reverse

from identity.ratelimit import check_login_rate_limit, record_failed_login, record_successful_login

from .common import (
    _base_context,
)

logger = logging.getLogger(__name__)


class SignInView(DjangoLoginView):
    """Django's `LoginView`, restricted to the UI's own template and redirect.

    `redirect_authenticated_user=False` is the default; the visible consequence is that an
    already-signed-in analyst who opens `/login/` lands on the dashboard via `get_success_url`.
    """

    template_name = "ui/login.html"
    redirect_authenticated_user = True

    def get_success_url(self) -> str:
        return reverse("ui-dashboard")

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Rate limiting check
        username = (request.POST.get("username") or "").strip()
        allowed, retry_after = check_login_rate_limit(request, username)
        if not allowed:
            messages.error(
                request,
                f"Too many failed login attempts. Please try again in {retry_after} seconds.",
            )
            return self.form_invalid(self.get_form())

        # Process the login
        response = super().post(request, *args, **kwargs)

        # Record login attempt result
        username = (request.POST.get("username") or "").strip()
        if response.status_code == 302:  # Success redirect
            record_successful_login(request, username)
        else:  # Form invalid (failed login)
            record_failed_login(request, username)

        return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        return _base_context(self.request, **context)


sign_out = LogoutView.as_view()
