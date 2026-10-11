from __future__ import annotations

from typing import TYPE_CHECKING, Any

from argon2 import PasswordHasher
from django.contrib.auth.models import AbstractUser
from django.db import models

from core.enums import API_KEY_SCOPES, USER_ROLE_CHOICES, USER_ROLE_VALUES, in_values
from core.models import TimeStampedModel, UUIDModel

if TYPE_CHECKING:
    import ingest.models

DEFAULT_USER_ROLE = "analyst"


class Organisation(UUIDModel, TimeStampedModel):
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
    role = models.CharField(
        max_length=20,
        choices=USER_ROLE_CHOICES,
        default=DEFAULT_USER_ROLE,
        blank=False,
    )
    org = models.ForeignKey(
        Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )

    class Meta:
        # Declared explicitly so the concrete model matches the option set recorded in
        # identity/0001_initial (a subclass Meta does not inherit AbstractUser.Meta's verbose
        # names); without this, makemigrations emits a cosmetic AlterModelOptions on every run.
        verbose_name = "user"
        verbose_name_plural = "users"
        constraints = [
            in_values("identity_user_role_valid", "role", USER_ROLE_VALUES),
        ]

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


# Module-level hasher instance (mirrors compat/auth.py pattern)
_ph = PasswordHasher()


def _hash_secret(plaintext: str) -> str:
    return _ph.hash(plaintext)


def _verify_secret(hash_: str, plaintext: str) -> bool:
    try:
        _ph.verify(hash_, plaintext)
        return True
    except Exception:
        # Legacy plaintext secret — constant-time compare
        from django.utils.crypto import constant_time_compare

        return constant_time_compare(plaintext.encode("utf-8"), hash_.encode("utf-8"))


class IngestionToken(UUIDModel, TimeStampedModel):
    """A per-org webhook ingestion token (ADR-004 Q6).

    Binds an `IngestionSource` to exactly one `Organisation`. The same source can
    route to different tenants by issuing distinct tokens. The secret is stored
    as an Argon2 hash; plaintext is shown once at creation.
    """

    source = models.ForeignKey(
        "ingest.IngestionSource",
        on_delete=models.CASCADE,
        related_name="tokens",
        # `ingest_token_src_revoked_idx` leads with `source`, so the implicit FK index would be
        # redundant (repo idiom: db_index=False + composite — see Share.case).
        db_index=False,
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="ingestion_tokens",
    )
    name = models.CharField(max_length=200)
    # First 12 chars of the raw token — unique index for O(1) lookup by prefix.
    prefix = models.CharField(max_length=12, unique=True, db_index=True)
    secret_hash = models.CharField(max_length=255)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_ingestion_token"
        indexes = [
            models.Index(fields=["source", "revoked_at"], name="ingest_token_src_revoked_idx"),
        ]

    def __str__(self) -> str:
        return f"IngestionToken({self.name}) for {self.organisation}"

    @classmethod
    def create_token(
        cls,
        *,
        source: ingest.models.IngestionSource,
        organisation: Organisation,
        name: str,
    ) -> tuple[IngestionToken, str]:
        """
        Create a new token, returning `(token, plaintext)`.

        Plaintext is returned **once** — the caller must display/save it immediately.
        """
        import secrets

        plaintext = secrets.token_urlsafe(32)
        prefix = plaintext[:12]
        token = cls.objects.create(
            source=source,
            organisation=organisation,
            name=name,
            prefix=prefix,
            secret_hash=_hash_secret(plaintext),
        )
        return token, plaintext

    def verify(self, presented: str) -> bool:
        """Constant-time verification of a presented secret."""
        if self.revoked_at is not None:
            return False
        return _verify_secret(self.secret_hash, presented)


class AuditLog(UUIDModel):
    """Immutable audit trail for cross-tenant and administrative actions (ADR-004 Q5).

    Records break-glass superAdmin reads, role/org/share/token changes, and other
    security-relevant events. `actor`/`actor_org`/`target_org` are SET_NULL so the
    log survives account/org deletion.
    """

    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        # covered by auditlog_actor_created_idx (actor, -created_at).
        db_index=False,
    )
    actor_org = models.ForeignKey(
        Organisation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    target_org = models.ForeignKey(
        Organisation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        # covered by auditlog_tgt_org_created_idx (target_org, -created_at).
        db_index=False,
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "identity_auditlog"
        indexes = [
            models.Index(fields=["actor", "-created_at"], name="auditlog_actor_created_idx"),
            models.Index(fields=["target_org", "-created_at"], name="auditlog_tgt_org_created_idx"),
            models.Index(fields=["action", "-created_at"], name="auditlog_action_created_idx"),
        ]

    def __str__(self) -> str:
        return f"AuditLog({self.action} on {self.target_type}:{self.target_id})"


__all__ = [
    "DEFAULT_USER_ROLE",
    "USER_ROLE_CHOICES",
    "USER_ROLE_VALUES",
    "ApiKey",
    "AuditLog",
    "IngestionToken",
    "Organisation",
    "User",
]
