"""ADR-007 — every user belongs to exactly one organisation (`User.org` NOT NULL).

The plan `PLAN-2026-10-13-non-null-user-org.md` makes organisation membership a
structural invariant. These tests pin the four database-level consequences:

1. the ORM cannot write an org-less row (`NOT NULL` at the database, not just a form rule);
2. the supported creation paths (`create_user`/`create_superuser`) default to
   `AMALTHEA_DEFAULT_ORG_NAME`, so `createsuperuser` keeps working with no new flags;
3. `identity/migrations/0009_user_org_required.py::backfill_user_org` repairs any
   pre-existing org-less row by moving it to the default org;
4. `on_delete=PROTECT` makes deleting an organisation that still has members impossible
   at the database level (the UI already forces an explicit reassign/delete choice).

The migration test follows the established fixture style of
`test_viewer_role_rename.py` / `test_seed_migrations.py`: it drives `MigrationExecutor`
directly and restores the schema to the graph's leaves on the way out.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import ProtectedError

from identity.models import Organisation, User

pytestmark = pytest.mark.django_db

# The last migration before org became NOT NULL: at this state `identity_user.org_id` is
# still nullable, so a raw `UPDATE ... org_id = NULL` is legal and can reproduce the
# pre-0009 state the backfill exists to repair.
BEFORE_NOT_NULL = ("identity", "0008_role_viewer_rename")


def _leaves() -> list[tuple[str, str]]:
    # Disk-only loader: the graph's leaves are what the rest of the session is migrated to.
    return MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes()


def _migrate(targets: list[tuple[str, str]]) -> None:
    """A fresh executor per call — a reused one replans from stale applied-state."""
    MigrationExecutor(connection).migrate(targets)


# ---------------------------------------------------------------------------------------
# 1. The database, not a form, is what refuses an org-less user
# ---------------------------------------------------------------------------------------


def test_objects_create_with_org_none_raises_integrity_error(default_org: Organisation) -> None:
    """`User.objects.create(org=None)` is a raw ORM write: the DB's NOT NULL rejects it.

    This is the root cause of the wave's failures: the low-level manager-less constructor
    has no defaulting, so the constraint is the only thing standing between a caller and
    an org-less row. `transaction.atomic()` keeps the failed INSERT from poisoning the
    surrounding test transaction.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(
            username="orgless-by-create",
            login="orgless-by-create",
            email="orgless-by-create@example.net",
            org=None,
        )


def test_objects_update_org_to_none_raises_integrity_error(default_org: Organisation) -> None:
    """The invariant also holds on update: an existing member cannot be orphaned."""
    user = User.objects.create(
        username="unorphanable",
        login="unorphanable",
        email="unorphanable@example.net",
        org=default_org,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(org=None)


# ---------------------------------------------------------------------------------------
# 2. Every supported creation path defaults to the default organisation
# ---------------------------------------------------------------------------------------


def test_create_user_without_org_lands_in_the_default_org(default_org: Organisation) -> None:
    """`User.objects.create_user` must keep working with no `org=` argument (ADR-007 §14.2)."""
    user = User.objects.create_user(username="defaulted-user", password="hunter2-correct")
    assert user.org_id is not None
    assert user.org_id == default_org.id
    assert User.objects.get(pk=user.pk).org_id == default_org.id


def test_create_superuser_without_org_lands_in_the_default_org(default_org: Organisation) -> None:
    """`createsuperuser --noinput` funnels through `create_superuser`; it must not prompt."""
    user = User.objects.create_superuser(
        username="defaulted-super",
        email="defaulted-super@example.net",
        password="hunter2-correct",
    )
    assert user.org_id == default_org.id
    assert user.is_superuser is True
    assert user.is_staff is True


def test_create_user_with_explicit_org_is_not_overridden(default_org: Organisation) -> None:
    """The defaulting only fills a gap; an explicit organisation is respected."""
    other = Organisation.objects.create(name="Explicit Org")
    user = User.objects.create_user(
        username="explicit-org-user", password="hunter2-correct", org=other
    )
    assert user.org_id == other.id != default_org.id


def test_createsuperuser_command_succeeds_without_an_org_flag() -> None:
    """The operator-facing wrapper: the management command itself still works."""
    from django.core.management import call_command

    call_command(
        "createsuperuser",
        interactive=False,
        username="cmd-super",
        email="cmd-super@example.net",
        verbosity=0,
    )
    user = User.objects.get(username="cmd-super")
    assert user.org_id is not None


# ---------------------------------------------------------------------------------------
# 3. `0009`'s backfill repairs a pre-NOT-NULL org-less row
# ---------------------------------------------------------------------------------------


# `transaction=True` because a schema change cannot run inside pytest-django's wrapping
# transaction; `serialized_rollback` because transactional tests flush every table, and the
# flush would drop the default organisation the migration reads.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_0009_backfill_moves_an_orgless_row_into_the_default_org(
    default_org: Organisation,
) -> None:
    """A row NULLed at the pre-0009 schema comes out of `backfill_user_org` in the default org.

    The row is created through the live ORM (which needs an org) and then orphaned with
    **raw SQL** while the schema is rolled back to 0008, where `org_id` is still nullable.
    That bypasses the live model's `null=False` and reproduces exactly the state the
    migration targets.
    """
    import importlib

    # The module name begins with a digit, so it cannot be imported with `from ... import`.
    backfill_user_org = importlib.import_module(
        "identity.migrations.0009_user_org_required"
    ).backfill_user_org

    _migrate([BEFORE_NOT_NULL])
    try:
        user = User.objects.create(
            username="pre-migration-orgless",
            login="pre-migration-orgless",
            email="pre-migration-orgless@example.net",
            org=default_org,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE identity_user SET org_id = NULL WHERE username = %s",
                ["pre-migration-orgless"],
            )
            cursor.execute(
                "SELECT org_id FROM identity_user WHERE username = %s",
                ["pre-migration-orgless"],
            )
            nulled = cursor.fetchone()

        assert nulled is not None and nulled[0] is None, (
            "the probe row was not actually org-less — the test proves nothing"
        )

        # The `apps` registry from the 0008 state, so the function sees the model shape the
        # migration's predecessor produced (org nullable), not the live NOT NULL model.
        historical_apps = MigrationExecutor(connection).loader.project_state([BEFORE_NOT_NULL]).apps
        backfill_user_org(historical_apps, None)

        user.refresh_from_db()
        assert user.org_id == default_org.id, "backfill left the row org-less"
    finally:
        _migrate(_leaves())


# ---------------------------------------------------------------------------------------
# 4. PROTECT: an organisation with members cannot be deleted
# ---------------------------------------------------------------------------------------


def test_deleting_an_organisation_with_members_raises_protected_error(
    default_org: Organisation,
) -> None:
    """`on_delete=PROTECT` (ADR-007 §14.1) makes orphaning members impossible at the DB.

    A dedicated case with *only* a member (no cases/alerts) so the failure can only come
    from `User.org`'s PROTECT, not from another tenant-data guard.
    """
    org = Organisation.objects.create(name="Staffed Org")
    member = User.objects.create(
        username="staffed-member",
        login="staffed-member",
        email="staffed-member@example.net",
        org=org,
    )

    with pytest.raises(ProtectedError):
        org.delete()

    member.refresh_from_db()
    assert member.org_id == org.id
    assert Organisation.objects.filter(pk=org.pk).exists(), "the protected org survived"


# ---------------------------------------------------------------------------------------
# 5. The Django admin add-user form requires an organisation
# ---------------------------------------------------------------------------------------


def test_admin_add_user_form_requires_an_org() -> None:
    """ADR-007: the admin add-form cannot save an org-less user.

    `identity/admin.py` marks `org` required on the *add* form (the change form relies on
    the model's own `blank=False`). The add-form check is what an operator hits.
    """
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from identity.admin import UserAdmin

    admin = UserAdmin(get_user_model(), AdminSite())
    request = RequestFactory().get("/admin/identity/user/add/")

    add_form = admin.get_form(request, change=False)
    assert add_form.base_fields["org"].required is True, (
        "the admin add-user form still accepts a blank organisation"
    )
    # `login` is derived in User.save(), so the admin deliberately does not require it.
    assert add_form.base_fields["login"].required is False
