"""Trailing-slash normalisation for the server-rendered UI.

This middleware is the mirror of Django's ``APPEND_SLASH``: the UI routes are registered
slash-less (``path("cases", ...)``) and ``APPEND_SLASH`` only ever *appends* a slash when the
slash-less form 404s — it never *strips* one. A hand-typed or bookmarked ``/cases/`` therefore
falls straight through to a 404.

Only a path whose slash-less form actually ``resolve()``s is redirected. Paths under ``/api/``,
``/admin/``, ``/static/``, ``/media/``, and ``/ws/`` are **excluded** so a TheHive-compatible
API path or the Django admin is never rewritten.

Method semantics:
- ``GET`` / ``HEAD`` → ``301 Moved Permanently`` (safe methods, cacheable redirect).
- Any other method (``POST``, ``PUT``, ``PATCH``, ``DELETE``, …) → ``308 Permanent Redirect``,
  which preserves the method and body. This is the same reasoning that makes the API register
  explicit ``-slash`` routes rather than relying on a 301 (see
  ``tests/conformance/test_t2_p1_surface.py::test_slashless_and_slashed_body_routes_both_resolve``).
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.urls import Resolver404, resolve

# Path prefixes that must never be rewritten. The API already registers both
# spellings explicitly; the admin manages its own trailing slashes; static/media
# are served by the web server or WhiteNoise; WebSocket paths are handled by
# Channels routing.
_EXCLUDED_PREFIXES = ("/api/", "/admin/", "/static/", "/media/", "/ws/")


class HttpResponsePermanentRedirect308(HttpResponsePermanentRedirect):
    """308 Permanent Redirect — preserves the request method and body.

    A 301 on a POST would drop the body (the browser turns it into a GET).
    308 is the correct status for "this resource has moved, repeat the request
    with the same method and body at the new location" (RFC 7538).
    """

    status_code = 308


class StripTrailingSlashMiddleware:
    """Strip a single trailing slash from UI paths when the slash-less form resolves."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path_info

        # No-op for the root path — stripping would yield an empty string.
        if path == "/":
            return self.get_response(request)

        # Only act on paths that actually end with a slash.
        if not path.endswith("/"):
            return self.get_response(request)

        # Never rewrite excluded prefixes (API, admin, static, media, websockets).
        if any(path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            return self.get_response(request)

        # Candidate path without the trailing slash.
        stripped = path.rstrip("/")

        # Only redirect if the stripped path actually resolves to a view.
        # An unknown path like "/nope/" must stay a 404, never become a 301.
        try:
            resolve(stripped)
        except (Resolver404, ValueError):
            return self.get_response(request)

        # Preserve the query string.
        query = request.META.get("QUERY_STRING", "")
        target = stripped + ("?" + query if query else "")

        # Safe methods → 301; unsafe methods → 308 (preserves method + body).
        if request.method in ("GET", "HEAD"):
            return HttpResponsePermanentRedirect(target)
        return HttpResponsePermanentRedirect308(target)
