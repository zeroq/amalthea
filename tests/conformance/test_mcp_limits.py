"""MCP limits, audit redaction and timeout behaviour."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from django.core.cache import cache
from mcp.server.mcpserver.exceptions import ToolError

from identity.models import Organisation, User
from mcp_server.auth import Principal
from mcp_server.context import client_ip_from_scope
from mcp_server.credentials import mint
from mcp_server.limits import audit_arguments, is_rate_limited, row_limit
from mcp_server.models import McpInvocation
from mcp_server.tools.base import run_read_data


@pytest.fixture
def principal(db) -> Principal:
    org = Organisation.objects.create(name="Limit Org")
    user = User.objects.create(
        login="limit-user",
        username="limit-user",
        email="limit@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )
    credential, _ = mint(organisation=org, user=user, name="limits", created_by=user)
    return Principal(
        credential_id=credential.id,
        credential_prefix=credential.prefix,
        actor_id=user.id,
        organisation_id=org.id,
        role="analyst",
        scopes=("mcp:read",),
    )


def test_row_limit_clamps_to_configured_maximum(settings) -> None:
    settings.MCP_MAX_ROWS = 50
    assert row_limit(None) == 50
    assert row_limit(0) == 1
    assert row_limit(999) == 50
    assert row_limit(10) == 10


def test_rate_limiter_is_per_credential_and_per_ip(settings, principal) -> None:
    cache.clear()
    settings.MCP_RATE_PER_MINUTE = 1
    assert is_rate_limited(principal, "203.0.113.7") is False
    assert is_rate_limited(principal, "203.0.113.7") is True

    other_ip = replace(principal, credential_id=uuid4())
    assert is_rate_limited(other_ip, "203.0.113.8") is False
    assert is_rate_limited(other_ip, "203.0.113.8") is True
    assert is_rate_limited(principal, "203.0.113.8") is True, "an exhausted IP stays exhausted"


def test_audit_arguments_allowlists_truncates_and_drops_unknown(principal) -> None:
    redacted = audit_arguments(
        {
            "case_id": "c-1",
            "limit": 5,
            "flag": False,
            "query": "x" * 500,
            "raw_payload": {"secret": "do-not-store"},
            "nested": ["also", "dropped"],
        }
    )
    assert redacted == {"case_id": "c-1", "limit": 5, "flag": False, "query": "x" * 200}


# serialized_rollback keeps this transaction test from leaving post_migrate
# content types that would break later serialized-rollback migration tests.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_run_read_rate_limit_audits_once(settings, principal, monkeypatch) -> None:
    cache.clear()
    settings.MCP_RATE_PER_MINUTE = 1
    monkeypatch.setattr("mcp_server.tools.base.require_principal", lambda: principal)

    async def scenario() -> None:
        assert await run_read_data("ping", {}, lambda _principal: "ok") == "ok"
        with pytest.raises(ToolError, match="rate_limited"):
            await run_read_data("ping", {}, lambda _principal: "ok")

    asyncio.run(scenario())
    rows = list(McpInvocation.objects.filter(tool_name="ping").order_by("created_at"))
    assert [row.status for row in rows] == ["ok", "rate_limited"]
    assert rows[1].error_code == "rate_limited"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_run_read_timeout_audits_and_returns_safe_error(principal, monkeypatch) -> None:
    monkeypatch.setattr("mcp_server.tools.base.require_principal", lambda: principal)
    monkeypatch.setattr("mcp_server.tools.base.timeout_seconds", lambda: 0.05)

    def slow(_principal):
        time.sleep(0.2)
        return "too slow"

    async def scenario() -> None:
        with pytest.raises(ToolError, match="timeout"):
            await run_read_data("slow", {}, slow)

    asyncio.run(scenario())
    row = McpInvocation.objects.get(tool_name="slow")
    assert row.status == "error"
    assert row.error_code == "timeout"


def _http_scope(*, peer: str | None = "10.0.0.5", forwarded: str | None = None) -> dict:
    return {
        "type": "http",
        "client": (peer, 12345) if peer else None,
        "headers": [(b"x-forwarded-for", forwarded.encode())] if forwarded else [],
    }


def test_client_ip_ignores_forwarded_header_by_default(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 0
    assert client_ip_from_scope(_http_scope(forwarded="203.0.113.9")) == "10.0.0.5"


def test_client_ip_uses_rightmost_entry_for_one_trusted_proxy(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 1
    assert client_ip_from_scope(_http_scope(forwarded="198.51.100.7, 203.0.113.9")) == "203.0.113.9"


def test_client_ip_resists_leftmost_spoofing_with_two_proxies(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 2
    scope = _http_scope(forwarded="192.0.2.1, 198.51.100.7, 203.0.113.9")
    assert client_ip_from_scope(scope) == "198.51.100.7"


def test_client_ip_falls_back_to_peer_on_short_or_invalid_chain(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 2
    assert client_ip_from_scope(_http_scope(forwarded="203.0.113.9")) == "10.0.0.5"
    assert client_ip_from_scope(_http_scope(forwarded="not-an-ip, 203.0.113.9")) == "10.0.0.5"
    assert client_ip_from_scope(_http_scope(peer=None, forwarded="203.0.113.9")) is None
