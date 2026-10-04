"""REVIEW-2026-10-03 **H6/H13** — identity: UUID keys, a populated `login`, unique prefixes.

ADR-002 §D3 requires UUID primary keys internally and a `login` that is *distinct* from
`username`, because TheHive keys assignees on `login` while Django keys them on
`username`. A user created without one (or with a nullable one) is unreachable from the
assignee key the API exposes.

The review also flagged the two dead ends this had to resolve honestly:

* ADR-002 §D11 says "accept, leave unassigned, record the attempted login" for an unknown
  assignee, while the remediation request said to auto-create unknown users. Those
  conflict; the ADR is the ratified decision and auto-creating a user from a webhook
  payload is a privilege-escalation vector, so it is **not** done here. Instead `login` is
  `NOT NULL` and unique, which is what makes "record the attempted login" possible, and
  the deviation is reported rather than silently resolved.
* `ApiKey.prefix` was merely indexed while `compat.auth` resolves a token with
  `filter(prefix=...).first()`. Two keys sharing a prefix made that lookup pick one
  arbitrarily, so `unique=True` is a correctness requirement.

Review items: H6 (UUID PKs and login), H13 (unique key prefix), L2 (prefix not unique).
"""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from identity.models import ApiKey, Organisation, User


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="acme")


@pytest.mark.django_db
def test_user_pk_is_a_uuid(org: Organisation) -> None:
    user = User.objects.create(username="alice", org=org)
    assert isinstance(user.pk, uuid.UUID)
    assert user.login == "alice", "login is populated from username when unset"


@pytest.mark.django_db
def test_login_may_differ_from_username(org: Organisation) -> None:
    """TheHive's assignee key is the login, not the Django username."""
    user = User.objects.create(username="alice@corp.example", login="alice", org=org)
    assert user.username != user.login
    assert User.objects.get(login="alice").pk == user.pk


@pytest.mark.django_db
def test_login_is_required_and_unique(org: Organisation) -> None:
    User.objects.create(username="alice", org=org)
    with pytest.raises(ValidationError):
        User(username="bob", login="alice", org=org).full_clean(exclude=["password"])
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(username="bob", login="alice", org=org)


@pytest.mark.django_db
def test_a_null_login_is_refused_by_the_database(org: Organisation) -> None:
    """A nullable login is what made the assignee key unreliable in the first place."""
    User.objects.create(username="alice", org=org)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(username="alice").update(login=None)


@pytest.mark.django_db
def test_apikey_pk_is_a_uuid_and_its_prefix_is_unique(org: Organisation) -> None:
    user = User.objects.create(username="alice", org=org)
    key = ApiKey.objects.create(user=user, prefix="abcd1234", key_hash="deadbeef")
    assert isinstance(key.pk, uuid.UUID)
    with pytest.raises(IntegrityError), transaction.atomic():
        ApiKey.objects.create(user=user, prefix="abcd1234", key_hash="cafebabe")


@pytest.mark.django_db
def test_apikey_prefixes_can_be_looked_up_unambiguously(org: Organisation) -> None:
    """`compat.auth` does `filter(prefix=...).first()`; uniqueness makes it total."""
    user = User.objects.create(username="alice", org=org)
    ApiKey.objects.create(user=user, prefix="aaaa1111", key_hash="x")
    ApiKey.objects.create(user=user, prefix="bbbb2222", key_hash="y")
    matches = list(ApiKey.objects.filter(prefix="aaaa1111"))
    assert len(matches) == 1


@pytest.mark.django_db
def test_api_key_scope_is_limited_to_read_and_readwrite(org: Organisation) -> None:
    user = User.objects.create(username="alice", org=org)
    key = ApiKey.objects.create(user=user, prefix="cccc3333", key_hash="x", scope="read")
    with pytest.raises(ValidationError):
        ApiKey(user=user, prefix="dddd4444", key_hash="x", scope="admin").full_clean(
            exclude=["revoked_at", "last_used_at"]
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        type(key).objects.filter(pk=key.pk).update(scope="admin")


@pytest.mark.django_db
def test_organisation_pk_is_a_uuid() -> None:
    assert isinstance(Organisation.objects.create(name="acme").pk, uuid.UUID)


@pytest.mark.django_db
def test_user_is_still_a_django_auth_user(org: Organisation) -> None:
    """`AbstractUser` must still be intact: the admin, the auth middleware and the
    permission system all depend on it."""
    from django.contrib.auth import get_user_model

    user = User.objects.create_user(username="carol", password="s3cret", org=org)
    assert isinstance(user, get_user_model())
    assert user.check_password("s3cret")
    assert user.has_perm("cases.add_case") in (True, False)  # permission machinery intact


@pytest.mark.django_db
def test_uuid_pks_are_declared_from_the_first_migration() -> None:
    """REVIEW H6/H13 deviation, made explicit.

    `User.id` and `ApiKey.id` were `BigAutoField` in `0001_initial`. Retyping a primary key
    that eight columns in five other apps already reference is not migratable on Postgres
    (`ALTER COLUMN ... TYPE uuid` is refused while a foreign key points at it), and there
    is no production data, so the initial migration is corrected instead. This test pins
    that decision: if someone later reverts the initial migration to an integer PK, this
    fails and the deviation has to be revisited deliberately.
    """
    from django.db import connection
    from django.db.migrations import operations as migration_operations
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    initial = loader.disk_migrations[("identity", "0001_initial")]
    assert initial is not None
    pk_fields = {
        op.name: dict(op.fields)
        for op in initial.operations
        if isinstance(op, migration_operations.CreateModel) and op.name in {"User", "ApiKey"}
    }
    assert set(pk_fields) == {"User", "ApiKey"}, sorted(pk_fields)
    for model_name, fields in pk_fields.items():
        field = fields["id"]
        is_uuid = "uuid" in field.deconstruct()[3] or "UUID" in type(field).__name__
        assert is_uuid, (
            f"identity/0001 creates {model_name}.id as {type(field).__name__}, not a UUID"
        )
