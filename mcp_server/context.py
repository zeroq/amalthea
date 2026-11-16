"""Per-request context shared by the MCP transport and tool audit wrapper."""

from __future__ import annotations

import ipaddress
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from django.conf import settings
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class McpRequestContext:
    request_id: str
    client_ip: str | None


_request_context: ContextVar[McpRequestContext | None] = ContextVar(
    "mcp_request_context",
    default=None,
)


def current_request() -> McpRequestContext:
    return _request_context.get() or McpRequestContext(request_id="", client_ip=None)


def client_ip_from_scope(scope: Scope) -> str | None:
    """Return the rate-limit client IP, honouring configured trusted proxies.

    ``AMALTHEA_MCP_TRUSTED_PROXY_COUNT`` defaults to ``0``: the direct ASGI peer
    is used and any ``X-Forwarded-For`` header is ignored.  When set to the
    exact number of proxies in front of Daphne, the right-most untrusted entry
    is used.  Invalid or short chains fall back to the peer, never to a
    caller-supplied value.
    """
    client = scope.get("client")
    peer: str | None = str(client[0]) if client else None
    trusted = max(0, int(getattr(settings, "MCP_TRUSTED_PROXY_COUNT", 0)))
    if trusted == 0 or not peer:
        return peer

    headers: dict[bytes, bytes] = {key.lower(): value for key, value in scope.get("headers", [])}
    raw_forwarded: bytes = headers.get(b"x-forwarded-for", b"")
    if not raw_forwarded:
        return peer
    forwarded = raw_forwarded.decode("latin-1", "ignore")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(parts) < trusted:
        return peer
    try:
        return str(ipaddress.ip_address(parts[-trusted]))
    except ValueError:
        return peer


class McpRequestContextMiddleware:
    """Capture a bounded request id and client IP for audit/rate limiting."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_request_id = headers.get(b"x-request-id", b"")[:100]
        request_id = raw_request_id.decode("ascii", "ignore") or uuid.uuid4().hex
        token = _request_context.set(
            McpRequestContext(request_id=request_id, client_ip=client_ip_from_scope(scope))
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _request_context.reset(token)
