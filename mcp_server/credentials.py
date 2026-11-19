"""Credential lifecycle services for the MCP gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from mcp_server.models import McpCredential


def mint(
    *,
    organisation: Any,
    user: Any,
    name: str,
    created_by: Any | None = None,
    expires_at: datetime | None = None,
    scopes: list[str] | None = None,
) -> tuple[McpCredential, str]:
    """Mint a credential and return ``(credential, plaintext)`` once."""
    return McpCredential.mint(
        organisation=organisation,
        user=user,
        name=name,
        created_by=created_by,
        expires_at=expires_at,
        scopes=scopes,
    )


def verify(token: str) -> McpCredential | None:
    """Return the active credential for a presented token, or ``None``.

    The lookup is by the 12-character prefix, then Argon2 verification.  A
    malformed/short token is rejected without a database query.  ``last_used_at``
    is updated on successful verification.
    """
    if not isinstance(token, str) or len(token) < 13:
        return None
    credential = McpCredential.lookup(token[:12])
    if credential is None or not credential.verify(token):
        return None
    now = timezone.now()
    updated = McpCredential.objects.filter(pk=credential.pk).update(last_used_at=now)
    if updated:
        credential.last_used_at = now
    return credential


def revoke(credential_id: Any) -> bool:
    """Revoke a credential permanently.  Returns whether a row was updated."""
    return bool(
        McpCredential.objects.filter(pk=credential_id, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
    )
