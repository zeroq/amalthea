"""MCP ASGI conformance: auth, both paths, fail-closed gate and Django passthrough."""

from __future__ import annotations

import asyncio

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from mcp_server.asgi import build_application, build_mcp_starlette_app
from mcp_server.credentials import mint
from mcp_server.models import McpInvocation
from tests.conformance.conftest import _drive_lifespan


async def _round_trip(client: httpx2.AsyncClient, path: str) -> None:
    async with streamable_http_client(f"http://localhost:8000{path}", http_client=client) as (
        read,
        write,
    ):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.protocol_version
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"get_case", "list_cases", "search"}.issubset(names)
            assert "echo" not in names
            result = await session.call_tool("list_cases", {"limit": 5})
            assert result.is_error is False
            assert result.structured_content is not None


# serialized_rollback keeps this transaction test from leaving post_migrate
# content types that would break later serialized-rollback migration tests.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_authenticated_protocol_at_both_paths_and_audit() -> None:
    org = Organisation.objects.create(name="ASGI Org")
    user = User.objects.create(
        login="asgi-agent",
        username="asgi-agent",
        email="asgi@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    case = Case.objects.create(owner_org=org, status=status, title="ASGI case")
    _, token = mint(organisation=org, user=user, name="asgi", created_by=user)

    async def scenario() -> None:
        app = build_application(build_mcp_starlette_app(), mcp_enabled=True)
        async with _drive_lifespan(app):
            transport = httpx2.ASGITransport(app=app)
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://localhost:8000", headers=headers
            ) as client:
                await _round_trip(client, "/mcp")
                await _round_trip(client, "/mcp/")

                wrong = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    headers={
                        "Authorization": "Bearer invalid-token-that-is-long-enough",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
                assert wrong.status_code == 401

                # Tool-level unknown fields fail closed, and the case id stays org-scoped.
                async with streamable_http_client(
                    "http://localhost:8000/mcp", http_client=client
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        bad = await session.call_tool(
                            "get_case", {"case_id": str(case.id), "unexpected": 1}
                        )
                        assert bad.is_error is True

    asyncio.run(scenario())
    rows = list(McpInvocation.objects.filter(tool_name="list_cases"))
    assert rows
    assert all(row.status == "ok" for row in rows)
    assert rows[0].arguments_redacted["limit"] == 5
    assert rows[0].organisation_id == org.id


@pytest.mark.django_db
def test_disabled_gate_and_django_passthrough() -> None:
    async def scenario() -> None:
        app = build_application(mcp_enabled=False)
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.get("/mcp")
            assert response.status_code == 404
            response = await client.post("/mcp/", json={})
            assert response.status_code == 404
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

    asyncio.run(scenario())


def test_top_level_router_keeps_websocket_branch() -> None:
    from mcp_server.asgi import build_application

    app = build_application(mcp_enabled=False)
    assert set(app.application_mapping) == {"http", "websocket", "lifespan"}
