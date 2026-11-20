"""MCP server views — Phase 3: OAuth 2.1 resource-server metadata and challenges."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponseNotFound, JsonResponse

from mcp_server.models import PHASE1_SCOPES, PHASE2_SCOPES


def _absolute_url(value: str | None, fallback: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return fallback.rstrip("/")
    if candidate.startswith(("http://", "https://")):
        return candidate.rstrip("/")
    if candidate.startswith("/"):
        return f"{fallback.rstrip('/')}{candidate}".rstrip("/")
    return f"{fallback.rstrip('/')}/{candidate}".rstrip("/")


def _resource_origin(resource_url: str) -> str:
    """Derive the origin from a resource URL by stripping trailing /mcp.

    Per plan §6.3 the metadata `resource` field equals MCP_RESOURCE_URL.
    The authorization_servers URL in the WWW-Authenticate challenge must be
    the origin (e.g. https://mcp.example.com), not /mcp/.well-known/...
    """
    url = resource_url.rstrip("/")
    if url.endswith("/mcp"):
        url = url[:-4]  # strip trailing /mcp
    return url


def oauth_protected_resource_metadata(
    request: HttpRequest,
) -> HttpResponseNotFound | JsonResponse:
    """RFC 9728 protected-resource metadata endpoint at
    ``/.well-known/oauth-protected-resource/mcp``.

    Public, cacheable JSON describing the resource and authorization server.
    Serves only in ``oauth`` or ``both`` mode; returns ``404`` in ``static`` mode.

    Canonical JSON shape (plan §6.3):

    {
        "resource": "https://host/mcp",
        "authorization_servers": ["https://idp.example.com"],
        "scopes_supported": ["mcp:read", "mcp:propose", "mcp:summary"],
        "bearer_methods_supported": ["header"]
    }
    """
    auth_mode = getattr(settings, "MCP_AUTH_MODE", "static")

    # In static mode, do not advertise OAuth metadata; return 404
    if auth_mode == "static":
        return HttpResponseNotFound("Not Found")

    # Build metadata from configured values using django.conf.settings
    base_url = (getattr(settings, "MCP_BASE_URL", "") or "http://localhost:8000").rstrip("/")
    resource_url = _absolute_url(getattr(settings, "MCP_RESOURCE_URL", ""), base_url)
    issuer = getattr(settings, "MCP_OAUTH_ISSUER", "").strip()

    metadata: dict[str, Any] = {
        "resource": resource_url,
    }

    # authorization_servers — include the configured issuer if it looks like an HTTPS URL
    if issuer and issuer.startswith("https://"):
        metadata["authorization_servers"] = [issuer]

    # scopes_supported — list all currently supported MCP scopes
    # Import from models so it stays in sync with the allowed scopes definitions
    metadata["scopes_supported"] = sorted(set(PHASE1_SCOPES) | set(PHASE2_SCOPES))

    # bearer_methods_supported — header-only per plan §6.3
    metadata["bearer_methods_supported"] = ["header"]

    response = JsonResponse(metadata)
    # Add cache headers — public, 1 hour cache for metadata
    response["Cache-Control"] = "public, max-age=3600"
    response["Content-Type"] = "application/json"
    return response


class McpAuthChallengeMiddleware:
    """ASGI middleware that adds ``WWW-Authenticate`` challenge to ``/mcp`` 401 responses
    when OAuth is enabled (mode ``oauth`` or ``both``).

    Rules (plan §6.4):

    - Unauthenticated or invalid OAuth requests to ``/mcp*`` return:

        HTTP/1.1 401 Unauthorized
        WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp", error="invalid_token"

    - No token value, claim dump, or exception text in the challenge or response body.
    - Static mode keeps the existing 401 behavior without ``resource_metadata``.
    - ``both`` mode advertises OAuth metadata but logs legacy static-bearer use.
    - Preserve existing response headers; do not modify non-401 responses.
    - Do not include token values or exception text.
    - Add ``X-Accel-Buffering: no`` for streamable MCP responses.
    - Replace any existing WWW-Authenticate header once; do not append duplicates.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        # Only apply to /mcp* paths
        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self._app(scope, receive, send)
            return

        # Track auth challenge and buffering state
        www_authenticate_pending: str | None = None
        x_accel_buffering: bool | None = None

        async def patched_send(message: Any) -> None:
            nonlocal www_authenticate_pending, x_accel_buffering

            if message["type"] == "http.response.start":
                headers = message.get("headers", [])

                # Determine auth mode
                auth_mode = getattr(settings, "MCP_AUTH_MODE", "static")

                if auth_mode in ("oauth", "both"):
                    # Check if this is a 401 response (status is int, not string)
                    status = message.get("status", 200)
                    if status == 401:
                        # Build the resource_metadata URL from the origin
                        base_url = (
                            getattr(settings, "MCP_BASE_URL", "") or "http://localhost:8000"
                        ).rstrip("/")
                        resource_url = _absolute_url(
                            getattr(settings, "MCP_RESOURCE_URL", ""), base_url
                        )
                        if resource_url:
                            origin = _resource_origin(resource_url)
                            meta_url = origin + "/.well-known/oauth-protected-resource/mcp"
                            # Format: Bearer resource_metadata="<url>", error="invalid_token"
                            www_authenticate_pending = (
                                f'Bearer resource_metadata="{meta_url}", error="invalid_token"'
                            )

                # Add X-Accel-Buffering: no for streamable MCP responses
                # Apply to all /mcp* responses to ensure streaming paths are not buffered
                if path.startswith("/mcp"):
                    x_accel_buffering = True

                # Apply X-Accel-Buffering: no header
                if x_accel_buffering is not None:
                    # Build header list, ensuring X-Accel-Buffering: no is included
                    # and that we don't duplicate WWW-Authenticate
                    header_list: list[tuple[bytes, bytes]] = []
                    has_www_auth = False
                    for name, value in headers:
                        n = name.lower()
                        if n == b"www-authenticate":
                            has_www_auth = True
                            # Keep existing if present; we'll decide below
                            header_list.append((name, value))
                        elif n == b"x-accel-buffering":
                            # Skip; we'll add after
                            pass
                        else:
                            header_list.append((name, value))

                    # Add X-Accel-Buffering: no at the end
                    header_list.append((b"x-accel-buffering", b"no"))

                    # Handle WWW-Authenticate: replace once if we have a pending one
                    if www_authenticate_pending is not None:
                        # Remove any existing WWW-Authenticate and add the new one
                        header_list = [
                            (n, v) for n, v in header_list if n.lower() != b"www-authenticate"
                        ]
                        header_list.append((b"www-authenticate", www_authenticate_pending.encode()))
                    elif not has_www_auth and www_authenticate_pending is None:
                        # No challenge needed; ensure no leftover X-Accel-Buffering
                        pass

                    # If we added a WWW-Authenticate but X-Accel-Buffering wasn't
                    # yet added, add it now
                    if www_authenticate_pending is not None:
                        # Ensure X-Accel-Buffering: no is present
                        has_accel = any(n.lower() == b"x-accel-buffering" for n, _ in header_list)
                        if not has_accel:
                            header_list.append((b"x-accel-buffering", b"no"))

                    message["headers"] = header_list

            await send(message)

        await self._app(scope, receive, patched_send)
