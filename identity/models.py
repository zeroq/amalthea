from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.enums import API_KEY_SCOPES, in_values
from core.models import TimeStampedModel, UUIDModel


class Organisation(UUIDModel):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "identity_organisation"

    def __str__(self) -> str:
        return self.name


class User(AbstractUser, UUIDModel):
    """Django's user model, keyed by UUID internally (ADR-002 §D3).

    `login` is the key TheHive clients use for `assignee` (ADR-002 §D11), so it is
    **unique** — without uniqueness an assignee string could match two users and the
    resolver would be ambiguous by construction (REVIEW H6) — and it is populated from
    `username` on save instead of being left blank forever.

    Note the D11 tolerance: an *unknown* assignee login is **not** auto-created. D11
    says "accept, leave unassigned, record the attempted login"; auto-creating a user
    account per unrecognised wire value would be a privilege-escalation footgun. The
    review's suggestion to auto-create is rejected on those grounds.
    """

    login = models.CharField(max_length=150, unique=True)
    role = models.CharField(max_length=100, blank=True)
    org = models.ForeignKey(
        Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.login:
            self.login = self.username
        super().save(*args, **kwargs)


class ApiKey(UUIDModel, TimeStampedModel):
    """A hashed bearer credential. Plaintext is shown once; only `key_hash` is stored."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="api_keys"
    )
    name = models.CharField(max_length=100)
    # unique, not merely indexed: `compat.auth` looks a key up by prefix and `.first()`
    # would silently pick one of two rows if a duplicate prefix were possible (REVIEW H6/L2).
    # `unique=True` implies the index the `db_index=True` lookup wanted.
    prefix = models.CharField(max_length=32, unique=True)
    key_hash = models.CharField(max_length=255)
    scope = models.CharField(
        max_length=20,
        choices=[(s, s) for s in API_KEY_SCOPES],
        default="readwrite",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_apikey"
        constraints = [
            in_values("apikey_scope_valid", "scope", API_KEY_SCOPES),
        ]

    def __str__(self) -> str:
        return f"ApiKey({self.name})"
