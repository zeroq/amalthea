from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from argon2 import PasswordHasher
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models

from core.enums import API_KEY_SCOPES, USER_ROLE_CHOICES, USER_ROLE_VALUES, in_values
from core.models import TimeStampedModel, UUIDModel

if TYPE_CHECKING:
    import ingest.models

DEFAULT_USER_ROLE = "analyst"


class UserManager(DjangoUserManager["User"]):
    """Custom manager that ensures every created user has an organisation.

    When `org` is omitted or explicitly None, the user is assigned to the
    organisation named by `settings.AMALTHEA_DEFAULT_ORG_NAME` (default "Default").
    This keeps `createsuperuser` and `createsuperuser --noinput` working with
    zero new required flags. `org` is NOT added to REQUIRED_FIELDS.
    """

    def _get_default_org(self) -> Organisation:
        # Import here to avoid circular imports at module load time
        from identity.models import Organisation

        default_org_name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
        org, _ = Organisation.objects.get_or_create(name=default_org_name)
        return org

    def create_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> User:
        if "org" not in extra_fields or extra_fields["org"] is None:
            extra_fields["org"] = self._get_default_org()
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> User:
        if "org" not in extra_fields or extra_fields["org"] is None:
            extra_fields["org"] = self._get_default_org()
        return super().create_superuser(username, email, password, **extra_fields)


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

    Every user belongs to exactly one organisation (ADR-007). The FK is non-null with
    PROTECT so an organisation with members cannot be deleted; the UI already forces
    an explicit reassign-or-delete choice.
    """

    login = models.CharField(max_length=150, unique=True)
    role = models.CharField(
        max_length=20,
        choices=USER_ROLE_CHOICES,
        default=DEFAULT_USER_ROLE,
        blank=False,
    )
    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="users")

    objects: ClassVar[UserManager] = UserManager()

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


def _source_org_id(source_id: UUID | str | None) -> UUID | None:
    """The organisation PK of the source `source_id` names, or `None` if it is gone.

    Imported lazily: `ingest.models` carries the FK back to this app, so a module-level import here
    would make the two apps import each other (ADR-009 records the migration-ordering consequence).
    """
    from ingest.models import IngestionSource

    if source_id is None:  # no source named (unsaved token) — nothing to compare
        return None
    return (
        IngestionSource.objects.filter(pk=source_id)
        .values_list("organisation_id", flat=True)
        .first()
    )


class IngestionToken(UUIDModel, TimeStampedModel):
    """A per-org webhook ingestion token (ADR-004 Q6, ADR-009).

    Binds an `IngestionSource` to the `Organisation` that owns it. Since ADR-009 the source is itself
    org-owned, so there is exactly one organisation a token for a given source can name — the
    receiver's "token identifies the feed *and* the tenant" premise depends on it. The secret is
    stored as an Argon2 hash; plaintext is shown once at creation.
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
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "identity_ingestion_token"
        indexes = [
            models.Index(fields=["source", "revoked_at"], name="ingest_token_src_revoked_idx"),
        ]

    def __str__(self) -> str:
        return f"IngestionToken({self.name}) for {self.organisation}"

    def org_mismatch_message(self) -> str | None:
        """The refusal message if this token's org is not its source's org, else `None`.

        A cross-table CHECK constraint cannot express this pair, so the check lives here (and in
        `create_token`, and in the Settings form — one test per path, per plan AC9). It is worth the
        extra read: a token that names the wrong tenant is a **cross-tenant ingest**, which writes
        another org's alerts and rules with this org's credential.
        """
        if not self.source_id or not self.organisation_id:
            return None  # nothing to compare yet; required fields are validated elsewhere
        source_org_id = (
            self.source.organisation_id
            if "source" in self._state.fields_cache
            else _source_org_id(self.source_id)
        )
        if source_org_id is None or source_org_id == self.organisation_id:
            return None
        return (
            f"Ingestion token belongs to organisation {self.organisation_id} but its source "
            f"belongs to {source_org_id} — a token must name the organisation that owns its source "
            "(ADR-009)"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refuse to persist a token whose organisation is not the source's (ADR-009).

        Enforced here rather than only in `create_token` because every writer must be covered: the
        form, `create_token`, a fixture, a shell, a future data migration. `full_clean()` is not
        called by `save()`, so this is the only place a raw write can be caught.
        """
        if self.source_id and self.organisation_id:
            source_org_id = _source_org_id(self.source_id)
            if source_org_id is not None and source_org_id != self.organisation_id:
                raise ValidationError(
                    "An ingestion token must belong to the same organisation as its source "
                    f"(source: {source_org_id}, token: {self.organisation_id}). See ADR-009."
                )
        super().save(*args, **kwargs)

    @classmethod
    def create_token(
        cls,
        *,
        source: ingest.models.IngestionSource,
        organisation: Organisation,
        name: str,
        created_by: User | None = None,
    ) -> tuple[IngestionToken, str]:
        """
        Create a new token, returning `(token, plaintext)`.

        Plaintext is returned **once** — the caller must display/save it immediately.

        Refuses a pair whose organisations disagree (ADR-009) *before* hashing, so a caller that
        catches the error has not written a row or burned a prefix.
        """
        if source.organisation_id != organisation.id:
            raise ValidationError(
                f"{source.slug!r} belongs to {source.organisation.name!r}, so it cannot have a "
                f"token for {organisation.name!r} (ADR-009)."
            )

        import secrets

        plaintext = secrets.token_urlsafe(32)
        prefix = plaintext[:12]
        token = cls.objects.create(
            source=source,
            organisation=organisation,
            name=name,
            prefix=prefix,
            secret_hash=_hash_secret(plaintext),
            created_by=created_by,
        )
        return token, plaintext

    def verify(self, presented: str) -> bool:
        """Constant-time verification of a presented secret."""
        if self.revoked_at is not None:
            return False
        return _verify_secret(self.secret_hash, presented)


class AuditLog(UUIDModel):
    """Immutable audit trail for cross-tenant and administrative actions (ADR-004 Q5).

    Records break-glass superAdmin reads, role/org/share/token changes, mapping edits and other
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
    details = models.JSONField(
        default=dict,
        blank=True,
        # The columns above answer "who did what, to which object, when". They cannot answer *what
        # changed* — the first question asked when a feed's mapping moved under an analyst. An
        # additive, non-secret bag is deliberately preferred to a column per audit kind: the audit
        # table stays one shape, and a new audited action needs no migration.
        # **Never** put a secret here: `AuditLog` rows outlive the thing they describe.
        help_text="Non-secret structured detail, e.g. {'changed': ['fields.title']}.",
    )
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
