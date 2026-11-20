"""MCP server ASGI lifecycle and authenticated Starlette mount.

The MCP endpoint lives in the same Daphne/ASGI process as Django at ``/mcp``
(and ``/mcp/``).  This module owns:

- the Phase 1 server/tool/resource/prompt construction,
- static-bearer verification via ``McpTokenVerifier``,
- host/origin/body-size transport safety,
- the fail-closed ``MCP_ENABLED`` gate,
- MCP session-manager lifecycle.  Daphne does not implement ASGI lifespan, so
  the manager starts lazily on the first ``/mcp`` request; lifespan-capable
  servers still get normal startup/shutdown handling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, cast

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_server.context import McpRequestContextMiddleware
from mcp_server.oauth import McpOAuthTokenVerifier
from mcp_server.views import McpAuthChallengeMiddleware

logger = logging.getLogger(__name__)


def _base_url() -> str:
    return (settings.MCP_BASE_URL or "http://localhost:8000").rstrip("/")


def _absolute_url(value: str | None, fallback: str) -> str:
    """Resolve a possibly-relative configured URL to an absolute URL.

    Tests and local configs commonly set MCP_RESOURCE_URL="/mcp"; AuthSettings
    requires an absolute http(s) URL, so resolve against MCP_BASE_URL.
    """
    candidate = str(value or "").strip()
    if not candidate:
        return fallback.rstrip("/")
    if candidate.startswith(("http://", "https://")):
        return candidate.rstrip("/")
    if candidate.startswith("/"):
        return f"{fallback.rstrip('/')}{candidate}".rstrip("/")
    return f"{fallback.rstrip('/')}/{candidate}".rstrip("/")


def _transport_security() -> TransportSecuritySettings:
    """Build DNS-rebinding/host/origin settings from the environment."""
    raw_hosts = [str(host).strip() for host in settings.MCP_ALLOWED_HOSTS if str(host).strip()]
    if "*" in raw_hosts:
        raise ImproperlyConfigured(
            "AMALTHEA_MCP_ALLOWED_HOSTS must name explicit Host values; '*' is not allowed."
        )
    if not raw_hosts:
        raw_hosts = ["localhost", "127.0.0.1"]

    allowed_hosts: list[str] = []
    for host in raw_hosts:
        allowed_hosts.append(host)
        # Host headers usually include the port; accept any port for bare names.
        if not host.endswith(":*") and ":" not in host:
            allowed_hosts.append(f"{host}:*")

    allowed_origins: list[str] = []
    for origin in settings.MCP_ALLOWED_ORIGINS:
        origin = str(origin).strip().rstrip("/")
        if not origin:
            continue
        allowed_origins.append(origin)
        if ":" not in origin.split("://")[-1]:
            allowed_origins.append(f"{origin}:*")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _strict_tool_schemas(server: MCPServer) -> None:
    """Make generated tool argument models reject unknown fields.

    The SDK's ``from_function`` models ignore extras by default.  MCP input
    schemas must fail closed for typos and future write-shaped fields.
    """
    for tool in server._tool_manager._tools.values():
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema()


def build_mcp_server(*, require_auth: bool = True) -> MCPServer:
    """Build the MCP server with mode-selective verifier composition (Phase 3).

    Auth mode determines which verifier is active:
      - "static":  McpTokenVerifier only (Phase 1/2 bearer credentials)
      - "oauth":   McpOAuthTokenVerifier only (OAuth 2.1 access-tokens)
      - "both":    dispatch JWT-shaped tokens to OAuth verifier,
                   other tokens to static verifier
    """
    auth = None
    verifier = None

    auth_mode = getattr(settings, "MCP_AUTH_MODE", "static").lower()

    # Validate auth mode early — reject unknown values
    if auth_mode not in ("static", "oauth", "both"):
        raise ImproperlyConfigured(
            f"AMALTHEA_MCP_AUTH_MODE must be one of: static, oauth, both. Got: {auth_mode}"
        )

    if require_auth:
        base = _base_url()

        # Build AuthSettings with the resource server URL and issuer
        # resource_server_url must be MCP_RESOURCE_URL (the full /mcp URL),
        # not just the base URL.
        configured_resource = getattr(settings, "MCP_RESOURCE_URL", "") or f"{base}/mcp"
        resource_url = _absolute_url(configured_resource, base)
        issuer_url = _absolute_url(getattr(settings, "MCP_OAUTH_ISSUER", ""), base)

        auth_kwargs: dict[str, Any] = {
            "issuer_url": AnyHttpUrl(issuer_url if auth_mode in ("oauth", "both") else base),
            "resource_server_url": AnyHttpUrl(resource_url)
            if auth_mode in ("oauth", "both")
            else None,
            "required_scopes": ["mcp:read"],
            "validate_token_resource": False,
        }

        auth = AuthSettings(**auth_kwargs)

        # Mode-selective verifier composition
        if auth_mode == "static":
            from mcp_server.auth import McpTokenVerifier

            verifier = McpTokenVerifier()
        elif auth_mode == "oauth":
            verifier = McpOAuthTokenVerifier()
        elif auth_mode == "both":
            from mcp_server.auth import McpTokenVerifier

            oauth_verifier = McpOAuthTokenVerifier()
            static_verifier = McpTokenVerifier()

            class _CompositeVerifier:
                """Dispatch JWT-shaped tokens to OAuth verifier and other tokens
                to the static bearer verifier.

                Dispatch is based on the presence of JWT dot-delimited segments
                (three-part header.payload.signature shape), not on the ``typ``
                header alone. This avoids relying on header values that may be
                absent or spoofed.

                Security still comes from full verification — no token is silently
                accepted as a static credential when OAuth mode would reject it.
                """

                async def verify_token(self, token: str) -> Any:
                    # Quick peek at the header to determine token shape.
                    # Dispatch by JWT structure (three dot-separated parts) rather
                    # than the typ header, which may be absent or unreliable.
                    try:
                        parts = token.split(".")
                        is_jwt = len(parts) == 3 and all(p.strip() for p in parts)
                    except Exception:
                        is_jwt = False

                    if is_jwt:
                        # OAuth verification failed; do not fall back to static
                        # that would silently accept an invalid OAuth token.
                        return await oauth_verifier.verify_token(token)

                    # Non-JWT-shaped tokens → static bearer verifier.
                    result = await static_verifier.verify_token(token)
                    if result is not None:
                        logger.info(
                            "mcp oauth legacy_static_use client_id=%s",
                            getattr(result, "client_id", "-"),
                        )
                    return result

            verifier = _CompositeVerifier()

    server = MCPServer(
        name="amalthea-mcp",
        title="Amalthea MCP",
        description="Read-only security case management for agents.",
        instructions=(
            "Read-only security case management. All data is scoped to the credential's "
            "organisation. IDs from other organisations return not_found."
        ),
        version="0.1.0",
        auth=auth,
        token_verifier=verifier,
    )

    from mcp_server import prompts, resources
    from mcp_server.tools import register_all

    register_all(server)
    resources.register(server)
    prompts.register(server)
    _strict_tool_schemas(server)
    return server


class McpLifecycleApp:
    """Run the MCP session manager without relying on ASGI lifespan.

    Daphne does not implement the ASGI lifespan protocol
    (django/daphne#264), so a mounted ``streamable_http_app`` would otherwise
    answer every request with ``RuntimeError: Task group is not initialized``.
    The wrapper starts the manager lazily on the first HTTP request, and also
    handles lifespan when the server does send it (Uvicorn, test clients).

    The manager may only be run once per instance, so this app is one-shot:
    build a new wrapper after shutdown rather than reusing it.
    """

    def __init__(self, app: ASGIApp, session_manager: StreamableHTTPSessionManager) -> None:
        self._app = app
        self._session_manager = session_manager
        self._lock: asyncio.Lock | None = None
        self._started: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._runner: asyncio.Task[None] | None = None
        self._start_error: BaseException | None = None

    def _ensure_primitives(self) -> None:
        # Construct loop-bound primitives on first use, never at import time.
        if self._started is None:
            self._started = asyncio.Event()
            self._stop = asyncio.Event()
            self._lock = asyncio.Lock()

    async def _run_manager(self) -> None:
        assert self._started is not None
        assert self._stop is not None
        try:
            async with self._session_manager.run():
                self._started.set()
                await self._stop.wait()
        except asyncio.CancelledError as exc:
            self._start_error = self._start_error or exc
            self._started.set()
            raise
        except BaseException as exc:  # pragma: no cover - defensive startup path
            self._start_error = exc
            self._started.set()
            logger.exception("MCP session manager failed")

    async def _ensure_started(self) -> None:
        self._ensure_primitives()
        assert self._lock is not None
        assert self._started is not None
        if self._started.is_set():
            self._raise_start_error()
            return
        async with self._lock:
            if not self._started.is_set():
                if self._runner is None:
                    self._runner = asyncio.create_task(
                        self._run_manager(), name="amalthea-mcp-session-manager"
                    )
                await self._started.wait()
        self._raise_start_error()

    def _raise_start_error(self) -> None:
        if self._start_error is not None:
            raise RuntimeError("MCP session manager failed to start") from self._start_error

    async def _shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._runner is not None and not self._runner.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner

    async def _handle_lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self._ensure_started()
                except Exception as exc:
                    logger.exception("MCP session manager startup failed")
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self._shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(receive, send)
            return
        if scope["type"] == "http":
            await self._ensure_started()
        await self._app(scope, receive, send)

    async def aclose(self) -> None:
        """Stop the manager (used by tests that do not drive lifespan)."""
        await self._shutdown()


def build_mcp_starlette_app(
    *,
    include_echo: bool = False,
    require_auth: bool = True,
    server: MCPServer | None = None,
) -> McpLifecycleApp:
    """Build the Streamable HTTP app.

    ``include_echo``/``require_auth=False`` exist only for the Phase 0
    conformance test and local protocol probes; production construction uses
    the defaults.
    """
    mcp_server = server or build_mcp_server(require_auth=require_auth)

    if include_echo and "echo" not in mcp_server._tool_manager._tools:

        @mcp_server.tool(
            name="echo",
            description="Echo the supplied message. Conformance-only tool.",
        )
        def echo(message: str) -> str:
            """Return the message unchanged."""
            return message

        _strict_tool_schemas(mcp_server)

    app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=max(1024, int(settings.MCP_MAX_BODY_BYTES)),
        transport_security=_transport_security(),
    )
    app.add_middleware(McpRequestContextMiddleware)
    return McpLifecycleApp(app, mcp_server.session_manager)


async def _noop_lifespan(scope: Any, receive: Any, send: Any) -> None:
    """Complete ASGI lifespan cleanly when MCP is disabled."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def build_application(
    mcp_starlette_app: ASGIApp | None = None,
    *,
    mcp_enabled: bool | None = None,
    include_echo: bool = False,
) -> ProtocolTypeRouter:
    """Build the top-level ASGI router.

    ``mcp_enabled`` defaults to ``settings.MCP_ENABLED``.  Tests may pass an
    explicit app and/or override the gate.  When disabled, ``/mcp*`` returns
    404 and no MCP lifespan state starts.
    """
    enabled = settings.MCP_ENABLED if mcp_enabled is None else bool(mcp_enabled)
    if enabled and mcp_starlette_app is None:
        mcp_starlette_app = build_mcp_starlette_app(include_echo=include_echo)

    django_asgi_app = get_asgi_application()

    async def http_app(scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            if mcp_starlette_app is None:
                response = JSONResponse({"detail": "Not Found"}, status_code=404)
                await response(scope, receive, send)
                return
            if path == "/mcp/":
                scope = dict(scope)
                scope["path"] = "/mcp"
                scope["raw_path"] = b"/mcp"
            await mcp_starlette_app(scope, receive, send)
        else:
            await django_asgi_app(scope, receive, send)

    from realtime import routing as realtime_routing

    # Wrap the HTTP app with the OAuth 401 challenge middleware when OAuth is enabled
    http_app_wrapped = McpAuthChallengeMiddleware(http_app)

    lifespan = mcp_starlette_app if mcp_starlette_app is not None else _noop_lifespan
    return ProtocolTypeRouter(
        {
            "http": http_app_wrapped,
            "websocket": AuthMiddlewareStack(
                URLRouter(cast(Any, realtime_routing.websocket_urlpatterns))
            ),
            "lifespan": lifespan,
        }
    )
