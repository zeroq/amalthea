"""MCP credentials and invocation audit rows (Phase 1).

Phase 1 is read-only: a credential can carry only ``mcp:read``.  The plaintext
token is returned exactly once by :func:`mcp_server.credentials.mint`; only the
Argon2 hash is persisted.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.enums import in_values
from identity.models import Organisation, User

PHASE1_SCOPES: tuple[str, ...] = ("mcp:read",)
RESERVED_SCOPES: tuple[str, ...] = ("mcp:propose", "mcp:summary", "mcp:write", "mcp:admin")

_PASSWORD_HASHER = PasswordHasher()

INVOCATION_STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    ("ok", "ok"),
    ("denied", "denied"),
    ("not_found", "not_found"),
    ("invalid", "invalid"),
    ("rate_limited", "rate_limited"),
    ("error", "error"),
)


def hash_secret(plaintext: str) -> str:
    """Return an Argon2 hash.  Plaintext never leaves this module."""
    return _PASSWORD_HASHER.hash(plaintext)


def verify_secret(secret_hash: str, plaintext: str) -> bool:
    """Constant-time verification against the stored Argon2 hash.

    There is deliberately no plaintext/constant-time fallback: a value that is
    not a valid Argon2 hash is a data-integrity error, not a valid credential.
    """
    try:
        return _PASSWORD_HASHER.verify(secret_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError, ValueError, TypeError):
        return False


class McpCredential(models.Model):
    """An organisation-scoped static bearer credential for one service user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="mcp_credentials",
        help_text="Tenant this credential is permanently bound to.",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="mcp_credentials",
        help_text="Effective service user; must belong to the credential organisation.",
    )
    name = models.CharField(max_length=100)
    prefix = models.CharField(
        max_length=12,
        unique=True,
        help_text="First 12 characters of the token, used for O(1) lookup.",
    )
    secret_hash = models.CharField(max_length=255)
    scopes = models.JSONField(
        default=list,
        help_text="Granted scopes. Phase 1 accepts only ['mcp:read'].",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_credential"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organisation", "-created_at"], name="mcp_org_cre_idx"),
        ]

    def __str__(self) -> str:
        return f"McpCredential({self.name})"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and timezone.now() >= self.expires_at

    @property
    def is_active(self) -> bool:
        return not (self.is_revoked or self.is_expired)

    def clean(self) -> None:
        super().clean()
        if self.user_id and self.organisation_id and self.user.org_id != self.organisation_id:
            raise ValidationError(
                {"user": "The service user must belong to the credential organisation."}
            )
        if not self.user_id or not self.user.is_active:
            raise ValidationError({"user": "The service user must be active."})
        scopes = self.scopes or []
        if not isinstance(scopes, list) or any(scope not in PHASE1_SCOPES for scope in scopes):
            raise ValidationError({"scopes": "Phase 1 credentials may only grant mcp:read."})
        if not scopes:
            raise ValidationError({"scopes": "At least one scope is required."})

    def verify(self, presented: str) -> bool:
        """True only for an active credential whose Argon2 hash matches."""
        if not self.is_active:
            return False
        return verify_secret(self.secret_hash, presented)

    @classmethod
    def mint(
        cls,
        *,
        organisation: Organisation,
        user: User,
        name: str,
        created_by: User | None = None,
        expires_at: Any = None,
    ) -> tuple[McpCredential, str]:
        """Create a credential and return ``(credential, plaintext)`` exactly once.

        A 32-byte URL-safe token is generated; the first 12 characters are the
        lookup prefix.  Prefix collisions are retried with a fresh token.
        """
        if user.org_id != organisation.id:
            raise ValidationError(
                {"user": "The service user must belong to the credential organisation."}
            )
        if not user.is_active:
            raise ValidationError({"user": "The service user must be active."})
        if not name.strip():
            raise ValidationError({"name": "A credential name is required."})

        plaintext = ""
        prefix = ""
        for _ in range(8):
            plaintext = secrets.token_urlsafe(32)
            prefix = plaintext[:12]
            if not cls.objects.filter(prefix=prefix).exists():
                break
        else:  # pragma: no cover - astronomically unlikely
            raise RuntimeError("Could not allocate a unique MCP credential prefix.")

        credential = cls(
            organisation=organisation,
            user=user,
            name=name.strip(),
            prefix=prefix,
            secret_hash=hash_secret(plaintext),
            scopes=list(PHASE1_SCOPES),
            expires_at=expires_at,
            created_by=created_by,
        )
        credential.full_clean()
        credential.save()
        return credential, plaintext

    @classmethod
    def lookup(cls, prefix: str) -> McpCredential | None:
        return cls.objects.select_related("user", "organisation").filter(prefix=prefix).first()


class McpInvocation(models.Model):
    """One audit row per authenticated MCP tool call."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.CharField(max_length=100, db_index=True)
    credential = models.ForeignKey(
        McpCredential,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invocations",
    )
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    organisation = models.ForeignKey(
        Organisation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    tool_name = models.CharField(max_length=100, db_index=True)
    arguments_hash = models.CharField(max_length=64)
    arguments_redacted = models.JSONField(default=dict)
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=list(INVOCATION_STATUS_CHOICES))
    error_code = models.CharField(max_length=100, blank=True, default="")
    duration_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mcp_invocation"
        ordering = ["-created_at"]
        constraints = [
            in_values(
                "mcp_invocation_status_valid",
                "status",
                tuple(value for value, _ in INVOCATION_STATUS_CHOICES),
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "-created_at"], name="mcp_inv_org_cre_idx"),
            models.Index(fields=["actor", "-created_at"], name="mcp_inv_actor_cre_idx"),
            models.Index(fields=["tool_name", "-created_at"], name="mcp_inv_tool_cre_idx"),
            models.Index(fields=["status", "-created_at"], name="mcp_inv_status_cre_idx"),
        ]

    def __str__(self) -> str:
        return f"McpInvocation({self.tool_name}) status={self.status}"
