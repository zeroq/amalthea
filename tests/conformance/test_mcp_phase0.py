"""Phase 0 MCP ASGI/protocol smoke tests.

These tests deliberately exercise the echo tool only. They prove that the MCP
Streamable HTTP adapter is reachable at `/mcp` and `/mcp/`, that the top-level
lifespan is entered, and that Django still owns non-MCP HTTP paths.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from amalthea.asgi import build_application
from mcp_server.asgi import build_mcp_starlette_app


@contextlib.asynccontextmanager
async def _drive_lifespan(app):
    """Drive the ASGI lifespan protocol the way Daphne does."""
    incoming: asyncio.Queue = asyncio.Queue()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def receive():
        return await incoming.get()

    async def send(message):
        if message["type"] == "lifespan.startup.complete":
            started.set()
        elif message["type"] == "lifespan.shutdown.complete":
            stopped.set()

    scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
    task = asyncio.create_task(app(scope, receive, send))
    await incoming.put({"type": "lifespan.startup"})
    await asyncio.wait_for(started.wait(), 5)
    try:
        yield
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(stopped.wait(), 5)
        await task


async def _mcp_round_trip(client: httpx2.AsyncClient, path: str) -> None:
    async with streamable_http_client(f"http://localhost:8000{path}", http_client=client) as (
        read,
        write,
    ):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.protocol_version
            tools = await session.list_tools()
            assert "echo" in {tool.name for tool in tools.tools}
            result = await session.call_tool("echo", {"message": "hello"})
            assert result.is_error is False
            assert any(getattr(item, "text", None) == "hello" for item in result.content)


def test_mcp_protocol_at_both_paths() -> None:
    async def scenario() -> None:
        app = build_application(build_mcp_starlette_app())
        async with _drive_lifespan(app):
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="http://localhost:8000",
                follow_redirects=True,
            ) as client:
                await _mcp_round_trip(client, "/mcp")
                await _mcp_round_trip(client, "/mcp/")

    asyncio.run(scenario())


def test_django_http_path_is_not_shadowed() -> None:
    async def scenario() -> None:
        app = build_application(build_mcp_starlette_app())
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.get("/healthz")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    asyncio.run(scenario())
