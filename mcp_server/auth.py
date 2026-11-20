"""MCP bearer authentication and the effective principal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from mcp.server.auth.provider import AccessToken, TokenVerifier

from mcp_server.models import PHASE1_SCOPES, PHASE2_SCOPES, McpCredential


@dataclass(frozen=True)
class Principal:
    """The authenticated agent identity for one MCP request."""

    credential_id: UUID | None
    credential_prefix: str
    actor_id: UUID
    organisation_id: UUID
    role: str
    scopes: tuple[str, ...]
    auth_mode: str = "static"
    oauth_binding_id: UUID | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _base_url() -> str:
    return (settings.MCP_BASE_URL or "http://localhost:8000").rstrip("/")


class McpTokenVerifier(TokenVerifier):
    """Verify static bearer tokens against ``McpCredential``.

    The verifier is deliberately strict: revoked/expired credentials, inactive
    users, org-less users, and mismatched user/org bindings all fail closed.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        credential = await sync_to_async(self._lookup_and_verify, thread_sensitive=True)(token)
        if credential is None:
            return None

        # Phase 2 adds propose/summary scopes. Unknown or reserved scopes are
        # dropped here so a tampered row can never widen the effective principal.
        allowed_scopes = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
        scopes = [scope for scope in (credential.scopes or []) if scope in allowed_scopes]
        if not scopes:
            return None

        now = timezone.now()
        if credential.last_used_at is None or now - credential.last_used_at > timedelta(minutes=1):
            await sync_to_async(self._touch, thread_sensitive=True)(credential.pk)

        expires_at = int(credential.expires_at.timestamp()) if credential.expires_at else None
        return AccessToken(
            token=token,
            client_id=str(credential.id),
            scopes=scopes,
            expires_at=expires_at,
            resource=_base_url(),
            subject=str(credential.user_id),
            claims={
                "auth_mode": "static",
                "credential_id": str(credential.id),
                "credential_prefix": credential.prefix,
                "organisation_id": str(credential.organisation_id),
                "user_id": str(credential.user_id),
                "role": credential.user.role,
            },
        )

    @staticmethod
    def _lookup_and_verify(token: str) -> McpCredential | None:
        if not isinstance(token, str) or len(token) < 13:
            return None
        credential = McpCredential.lookup(token[:12])
        if credential is None or not credential.verify(token):
            return None
        if not credential.user.is_active:
            return None
        if credential.user.org_id != credential.organisation_id:
            return None
        return credential

    @staticmethod
    def _touch(credential_id: Any) -> None:
        McpCredential.objects.filter(pk=credential_id).update(last_used_at=timezone.now())


def _principal_from_credential(credential: McpCredential, auth_mode: str = "static") -> Principal:
    """Build a Principal from a verified McpCredential, used by the static verifier."""
    return Principal(
        credential_id=credential.id,
        credential_prefix=credential.prefix,
        actor_id=credential.user_id,
        organisation_id=credential.organisation_id,
        role=credential.user.role,
        scopes=tuple(credential.scopes or []),
        auth_mode=auth_mode,
        oauth_binding_id=None,
    )


def principal_from_token(token: AccessToken | None) -> Principal | None:
    """Build the tool-facing principal from the SDK's authenticated token."""
    if token is None:
        return None
    claims = token.claims or {}
    try:
        auth_mode = str(claims.get("auth_mode", "static"))
        if auth_mode in ("oauth", "both"):
            return Principal(
                credential_id=None,
                credential_prefix=str(claims.get("client_id", "oauth")),
                actor_id=UUID(str(claims["user_id"])),
                organisation_id=UUID(str(claims["organisation_id"])),
                role=str(claims["role"]),
                scopes=tuple(token.scopes),
                auth_mode=auth_mode,
                oauth_binding_id=UUID(str(claims["oauth_binding_id"])),
            )
        return Principal(
            credential_id=UUID(str(claims["credential_id"])),
            credential_prefix=str(claims["credential_prefix"]),
            actor_id=UUID(str(claims["user_id"])),
            organisation_id=UUID(str(claims["organisation_id"])),
            role=str(claims["role"]),
            scopes=tuple(token.scopes),
            auth_mode="static",
            oauth_binding_id=None,
        )
    except (KeyError, TypeError, ValueError):
        return None
