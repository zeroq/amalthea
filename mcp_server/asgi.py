"""Minimal Phase 0 MCP server mounted inside Amalthea's ASGI app.

The real Phase 1 tool surface, credential verification, and limits live in
separate modules. This module intentionally exposes one echo tool so the ASGI
mount, Streamable HTTP transport, and lifespan composition can be verified
before any case data is reachable.
"""

from __future__ import annotations

from mcp.server import MCPServer
from starlette.applications import Starlette


def build_mcp_server() -> MCPServer:
    """Build the Phase 0 MCP server with one protocol-only tool."""
    server = MCPServer(
        name="amalthea-mcp",
        title="Amalthea MCP",
        description="Phase 0 protocol spike for the Amalthea MCP server.",
        version="0.0.0-phase0",
    )

    @server.tool(
        name="echo",
        description="Echo the supplied message. Phase 0 protocol verification only.",
    )
    def echo(message: str) -> str:
        """Return the message unchanged."""
        return message

    return server


def build_mcp_starlette_app() -> Starlette:
    """Build the Streamable HTTP Starlette app.

    `stateless_http=True` avoids per-client session state for the spike; Phase 1
    revisits this once auth and multi-worker deployment are wired. The session
    manager is one-shot per app instance, so tests build a fresh app instead of
    re-entering this app's lifespan.
    """
    return build_mcp_server().streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=4 * 1024 * 1024,
    )


mcp_starlette_app = build_mcp_starlette_app()
