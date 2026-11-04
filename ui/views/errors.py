"""Custom error handlers."""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def handler404(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """Custom 404 handler — renders themed 404 template."""
    return render(request, "ui/404.html", status=404)


def handler500(request: HttpRequest) -> HttpResponse:
    """Custom 500 handler — renders themed 500 template without DB access.

    Must not query the database because a 500 often means the DB is unavailable
    or the connection is broken. Renders the template WITHOUT request context
    processors (no `request=`) so that `auth` and `messages` processors don't
    touch the session/user tables.
    """
    from django.http import HttpResponseServerError
    from django.template import loader

    return HttpResponseServerError(loader.render_to_string("ui/500.html"))


def handler403(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """Custom 403 handler — renders themed 403 template."""
    return render(request, "ui/403.html", status=403)
