"""ADR-006 decision 1 — the read-only role is renamed `read-only` -> `viewer`.

Three claims, each *executed* rather than reasoned about (plan §9.7, P5 AC1):

1. the stored value at HEAD is `viewer`, and the enum/model agree;
2. the database **rejects** `read-only` at HEAD (the CHECK was re-added, so the rename cannot
   be silently undone by writing the old value back);
3. `identity/migrations/0008_role_viewer_rename.py` rewrites existing rows **and reverses**,
   so a rollback does not strand a database whose users carry a value the old CHECK forbids.

The migration test follows `test_seed_migrations.py`: it drives `MigrationExecutor` directly and
restores the schema to the graph's leaves on the way out.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from core.enums import USER_ROLE_CHOICES, USER_ROLE_VALUES, VIEWER_ROLE

pytestmark = pytest.mark.django_db

RENAME = ("identity", "0008_role_viewer_rename")
BEFORE = ("identity", "0007_add_timestamps_to_organisation")


def _leaves() -> list[tuple[str, str]]:
    return MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes()


def _migrate(targets: list[tuple[str, str]]) -> None:
    """A fresh executor per call — a reused one replans from stale applied-state."""
    MigrationExecutor(connection).migrate(targets)


def test_the_enum_agrees_with_the_database_at_head() -> None:
    assert VIEWER_ROLE == "viewer"
    assert "viewer" in USER_ROLE_VALUES
    assert "read-only" not in USER_ROLE_VALUES
    assert USER_ROLE_VALUES == tuple(value for value, _ in USER_ROLE_CHOICES)
    # The model's `choices` are built from the enum, so this is the same list by construction.
    field = get_user_model()._meta.get_field("role")
    assert [value for value, _ in field.choices] == list(USER_ROLE_VALUES)


def test_the_database_rejects_the_old_role_value(make_user) -> None:
    """The CHECK is the reason a rename cannot half-happen (ADR-006 §5)."""
    user = make_user(role=VIEWER_ROLE, username="rename-ok")
    assert user.role == VIEWER_ROLE

    # Through the ORM so the id is bound in the backend's own representation; raw SQL with
    # `str(uuid)` silently matches 0 rows on SQLite (UUIDs are stored dashless) and would
    # "pass" without ever reaching the CHECK.
    with pytest.raises(IntegrityError):
        get_user_model().objects.filter(pk=user.pk).update(role="read-only")


# `transaction=True` because a schema change cannot run inside pytest-django's wrapping
# transaction; `serialized_rollback` because transactional tests flush every table, and the
# flush would drop the rows the migrations under test just wrote.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_rename_migration_rewrites_and_reverses_existing_rows() -> None:
    """A row created *before* the rename comes out as `viewer`, and goes back on a rollback."""
    _migrate([BEFORE])
    try:
        historical_user = (
            MigrationExecutor(connection)
            .loader.project_state([BEFORE])
            .apps.get_model("identity", "User")
        )
        historical_user.objects.create(
            username="legacy-viewer", login="legacy-viewer", password="!", role="read-only"
        )

        _migrate([RENAME])
        assert _role_of("legacy-viewer") == "viewer", "forward rename did not rewrite the row"

        # The re-added CHECK rejects the old value at the new state.
        with pytest.raises(IntegrityError):
            get_user_model().objects.filter(login="legacy-viewer").update(role="read-only")

        _migrate([BEFORE])
        assert _role_of("legacy-viewer") == "read-only", "rollback did not restore the old value"
    finally:
        _migrate(_leaves())


def _role_of(login: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT role FROM identity_user WHERE login = %s", [login])
        row = cursor.fetchone()
    assert row is not None, f"{login} disappeared across a migration"
    return str(row[0])
