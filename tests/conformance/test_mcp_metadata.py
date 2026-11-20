"""RFC 9728 protected-resource metadata conformance tests."""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from mcp_server.models import PHASE1_SCOPES, PHASE2_SCOPES

URL = reverse("mcp_oauth_protected_resource_metadata")


def test_metadata_hidden_in_static_mode(client) -> None:
    with override_settings(MCP_AUTH_MODE="static", MCP_RESOURCE_URL="https://mcp.example.com/mcp"):
        response = client.get(URL)
    assert response.status_code == 404


@pytest.mark.parametrize("mode", ["oauth", "both"])
@override_settings(
    MCP_AUTH_MODE="oauth",
    MCP_BASE_URL="https://mcp.example.com",
    MCP_RESOURCE_URL="https://mcp.example.com/mcp",
    MCP_OAUTH_ISSUER="https://idp.example.com",
)
def test_metadata_contract(client, mode: str) -> None:
    with override_settings(MCP_AUTH_MODE=mode):
        response = client.get(URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "resource": "https://mcp.example.com/mcp",
        "authorization_servers": ["https://idp.example.com"],
        "scopes_supported": sorted(set(PHASE1_SCOPES) | set(PHASE2_SCOPES)),
        "bearer_methods_supported": ["header"],
    }
    assert response["Cache-Control"] == "public, max-age=3600"


@override_settings(
    MCP_AUTH_MODE="oauth",
    MCP_BASE_URL="https://mcp.example.com",
    MCP_RESOURCE_URL="/mcp",
    MCP_OAUTH_ISSUER="https://idp.example.com",
)
def test_metadata_resolves_relative_resource_url(client) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json()["resource"] == "https://mcp.example.com/mcp"


@override_settings(
    MCP_AUTH_MODE="oauth",
    MCP_BASE_URL="https://mcp.example.com",
    MCP_RESOURCE_URL="https://mcp.example.com/mcp",
    MCP_OAUTH_ISSUER="",
)
def test_metadata_omits_missing_issuer(client) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    assert "authorization_servers" not in response.json()
