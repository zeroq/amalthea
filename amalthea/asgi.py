"""
ASGI config for Amalthea.

The MCP Streamable HTTP adapter is mounted at `/mcp` before the Django HTTP
application. The Channels WebSocket branch stays unchanged. MCP's own lifespan
must be run at the top level, because a sub-application's lifespan is not run by
ProtocolTypeRouter.
"""

import os
from typing import Any

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "amalthea.settings.dev")

django_asgi_app = get_asgi_application()

from mcp_server.asgi import mcp_starlette_app  # noqa: E402
from realtime import routing as realtime_routing  # noqa: E402


def build_http_app(mcp_app: Any) -> Any:
    """Return an ASGI HTTP router that sends `/mcp*` to MCP and the rest to Django."""

    async def http_app(scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            await mcp_app(scope, receive, send)
        else:
            await django_asgi_app(scope, receive, send)

    return http_app


def build_application(mcp_app: Any) -> ProtocolTypeRouter:
    """Build the top-level router around an MCP app (fresh per lifespan in tests)."""
    return ProtocolTypeRouter(
        {
            "http": build_http_app(mcp_app),
            "websocket": AuthMiddlewareStack(URLRouter(realtime_routing.websocket_urlpatterns)),
            "lifespan": mcp_app,
        }
    )


application = build_application(mcp_starlette_app)
