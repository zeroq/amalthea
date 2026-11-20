"""Deployment-hardening conformance tests for the MCP gateway."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from mcp_server.asgi import _transport_security
from mcp_server.auth import Principal
from mcp_server.context import client_ip_from_scope
from mcp_server.limits import is_rate_limited, max_body_bytes, rate_per_minute


def _scope(client_ip: str = "203.0.113.10", xff: bytes = b"") -> dict:
    headers = [(b"host", b"localhost")]
    if xff:
        headers.append((b"x-forwarded-for", xff))
    return {"type": "http", "client": (client_ip, 12345), "headers": headers}


def test_trusted_proxy_count_zero_ignores_xff(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 0
    assert client_ip_from_scope(_scope(xff=b"198.51.100.9")) == "203.0.113.10"


def test_trusted_proxy_count_one_uses_rightmost_entry(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 1
    assert client_ip_from_scope(_scope(xff=b"198.51.100.9")) == "198.51.100.9"


def test_short_proxy_chain_falls_back_to_peer(settings) -> None:
    settings.MCP_TRUSTED_PROXY_COUNT = 2
    assert client_ip_from_scope(_scope(xff=b"198.51.100.9")) == "203.0.113.10"


def test_transport_security_rejects_wildcard_host(settings) -> None:
    settings.MCP_ALLOWED_HOSTS = ["*"]
    with pytest.raises(ImproperlyConfigured):
        _transport_security()


def test_transport_security_uses_explicit_hosts(settings) -> None:
    settings.MCP_ALLOWED_HOSTS = ["mcp.example.com"]
    settings.MCP_ALLOWED_ORIGINS = ["https://app.example.com"]
    config = _transport_security()
    assert "mcp.example.com" in config.allowed_hosts
    assert "mcp.example.com:*" in config.allowed_hosts
    assert "https://app.example.com" in config.allowed_origins


def test_body_cap_has_safe_floor(settings) -> None:
    settings.MCP_MAX_BODY_BYTES = 10
    assert max_body_bytes() == 1024


def test_rate_limit_composed_from_settings(settings) -> None:
    settings.MCP_RATE_PER_MINUTE = 3
    assert rate_per_minute() == 3


@pytest.mark.django_db
def test_rate_limiter_is_per_identity_and_ip(settings) -> None:
    settings.MCP_RATE_PER_MINUTE = 1
    principal = Principal(
        credential_id=None,
        credential_prefix="client-1",
        actor_id="11111111-1111-1111-1111-111111111111",  # type: ignore[arg-type]
        organisation_id="22222222-2222-2222-2222-222222222222",  # type: ignore[arg-type]
        role="analyst",
        scopes=("mcp:read",),
        auth_mode="oauth",
        oauth_binding_id="33333333-3333-3333-3333-333333333333",  # type: ignore[arg-type]
    )
    assert is_rate_limited(principal, "203.0.113.10") is False
    assert is_rate_limited(principal, "203.0.113.10") is True


@override_settings(
    MCP_AUTH_MODE="oauth",
    MCP_OAUTH_JWKS_CACHE_SECONDS=120,
    MCP_RATE_PER_MINUTE=60,
)
def test_shared_cache_defaults_fail_closed(settings) -> None:
    from mcp_server.oauth import JWKS_CACHE_MIN_SECONDS

    assert JWKS_CACHE_MIN_SECONDS >= 300
    assert rate_per_minute() > 0
    # The verifier clamps a too-low configured cache TTL.
    from mcp_server.oauth import McpOAuthTokenVerifier

    assert McpOAuthTokenVerifier()._jwks_cache_seconds >= JWKS_CACHE_MIN_SECONDS
