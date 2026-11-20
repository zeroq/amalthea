"""OAuth 2.1 resource-server conformance tests for the MCP verifier.

These exercise the verifier directly: JWT validation, issuer/audience/expiry,
algorithm allowlisting, exact binding lookup, scope ceilings, audit principal
mapping, and reason-code logging. No raw token value is ever logged or asserted.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import base64url_encode

from identity.models import Organisation, User
from mcp_server.auth import principal_from_token
from mcp_server.models import McpOAuthBinding
from mcp_server.oauth import McpOAuthTokenVerifier

ISSUER = "https://idp.example.com"
RESOURCE = "https://mcp.example.com/mcp"
JWKS_URL = "https://idp.example.com/.well-known/jwks.json"


def _rsa_keys() -> tuple[str, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private_pem, private.public_key()


def _jwk(public_key: Any, kid: str = "test-key") -> dict[str, str]:
    numbers = public_key.public_numbers()

    def encode(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64url_encode(raw).decode()

    return {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": encode(numbers.n),
        "e": encode(numbers.e),
    }


def _token(private_pem: str, *, kid: str = "test-key", **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "subject-1",
        "client_id": "client-1",
        "scope": "mcp:read",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    for key in [key for key, value in claims.items() if value is None]:
        del claims[key]
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"typ": "at+jwt", "kid": kid})


@pytest.fixture
def key_material() -> tuple[str, dict[str, list[dict[str, str]]]]:
    private_pem, public_key = _rsa_keys()
    return private_pem, {"keys": [_jwk(public_key)]}


@pytest.fixture
def binding(db: None) -> McpOAuthBinding:
    org = Organisation.objects.create(name="OAuth org")
    user = User.objects.create(
        login="oauth-agent",
        username="oauth-agent",
        email="oauth@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )
    return McpOAuthBinding.objects.create(
        name="test-binding",
        issuer=ISSUER,
        client_id="client-1",
        subject="subject-1",
        organisation=org,
        service_user=user,
        allowed_scopes=["mcp:read"],
        is_active=True,
    )


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch, key_material: tuple[str, dict[str, Any]]):
    _, jwks = key_material
    monkeypatch.setattr("mcp_server.oauth._fetch_jwks", lambda url: jwks)
    return McpOAuthTokenVerifier()


@pytest.fixture(autouse=True)
def oauth_environment(settings) -> None:
    settings.MCP_AUTH_MODE = "oauth"
    settings.MCP_BASE_URL = "https://mcp.example.com"
    settings.MCP_RESOURCE_URL = RESOURCE
    settings.MCP_OAUTH_ISSUER = ISSUER
    settings.MCP_OAUTH_JWKS_URL = JWKS_URL
    settings.MCP_OAUTH_JWT_ALGS = ["RS256"]


@pytest.mark.django_db
class TestOAuthVerifier:
    def test_valid_token_accepted_and_principal_resolved(
        self, binding: McpOAuthBinding, key_material: tuple[str, dict[str, Any]], verifier
    ) -> None:
        private_pem, _ = key_material
        access_token = verifier._verify_sync(_token(private_pem))
        assert access_token is not None
        assert list(access_token.scopes) == ["mcp:read"]
        principal = principal_from_token(access_token)
        assert principal is not None
        assert principal.auth_mode == "oauth"
        assert principal.oauth_binding_id == binding.id
        assert principal.organisation_id == binding.organisation_id
        assert principal.actor_id == binding.service_user_id

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"iss": "https://wrong.example.com"}, "invalid_issuer"),
            ({"iss": None}, "bad_signature"),
            ({"aud": "https://other.example.com/mcp"}, "invalid_audience"),
            ({"aud": None}, "bad_signature"),
            ({"exp": int(time.time()) - 120}, "expired"),
            ({"sub": "someone-else"}, "unknown_binding"),
            ({"client_id": "other-client"}, "unknown_binding"),
            ({"scope": "mcp:write"}, "scope_denied"),
            ({"scope": "mcp:unknown"}, "scope_denied"),
        ],
    )
    def test_rejections_fail_closed(
        self,
        binding: McpOAuthBinding,
        key_material: tuple[str, dict[str, Any]],
        verifier,
        caplog: pytest.LogCaptureFixture,
        overrides: dict[str, Any],
        reason: str,
    ) -> None:
        caplog.set_level("INFO", logger="mcp_server.oauth")
        private_pem, _ = key_material
        assert verifier._verify_sync(_token(private_pem, **overrides)) is None
        assert f"reason={reason}" in caplog.text

    def test_non_mcp_scopes_ignored(
        self, binding: McpOAuthBinding, key_material: tuple[str, dict[str, Any]], verifier
    ) -> None:
        private_pem, _ = key_material
        access_token = verifier._verify_sync(_token(private_pem, scope="openid profile mcp:read"))
        assert access_token is not None
        assert list(access_token.scopes) == ["mcp:read"]

    def test_wrong_algorithm_rejected(
        self, binding: McpOAuthBinding, key_material: tuple[str, dict[str, Any]], verifier, caplog
    ) -> None:
        caplog.set_level("INFO", logger="mcp_server.oauth")
        _private_pem, _ = key_material
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": RESOURCE,
                "sub": "subject-1",
                "client_id": "client-1",
                "scope": "mcp:read",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            },
            "x" * 32,
            algorithm="HS256",
            headers={"typ": "at+jwt", "kid": "test-key"},
        )
        assert verifier._verify_sync(token) is None
        assert "reason=bad_algorithm" in caplog.text

    def test_disabled_binding_rejected(
        self, binding: McpOAuthBinding, key_material: tuple[str, dict[str, Any]], verifier, caplog
    ) -> None:
        caplog.set_level("INFO", logger="mcp_server.oauth")
        binding.is_active = False
        binding.save(update_fields=["is_active"])
        private_pem, _ = key_material
        assert verifier._verify_sync(_token(private_pem)) is None
        assert "reason=unknown_binding" in caplog.text

    def test_jwks_rotation_refreshes_on_unknown_kid(
        self,
        binding: McpOAuthBinding,
        key_material: tuple[str, dict[str, Any]],
        verifier,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        private_pem, _ = key_material
        assert verifier._verify_sync(_token(private_pem)) is not None

        rotated_private, rotated_public = _rsa_keys()
        monkeypatch.setattr(
            "mcp_server.oauth._fetch_jwks",
            lambda url: {"keys": [_jwk(rotated_public, "rotated-key")]},
        )
        access_token = verifier._verify_sync(_token(rotated_private, kid="rotated-key"))
        assert access_token is not None

    def test_last_used_is_touched_at_most_once_per_minute(
        self, binding: McpOAuthBinding, key_material: tuple[str, dict[str, Any]], verifier
    ) -> None:
        private_pem, _ = key_material
        assert verifier._verify_sync(_token(private_pem)) is not None
        binding.refresh_from_db()
        first = binding.last_used_at
        assert first is not None
        assert verifier._verify_sync(_token(private_pem)) is not None
        binding.refresh_from_db()
        assert binding.last_used_at == first
