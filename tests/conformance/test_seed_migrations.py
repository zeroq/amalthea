"""REVIEW-2026-10-03 **M10/M11/H4/M12** — seed migrations are safe to reverse.

Three related defects, one file of tests:

**M10 — the reverse deleted domain data by value.** Every seed migration's `reverse_code`
did `.filter(value__in=[...]).delete()`. ADR-002 §D4 makes statuses and observable types
**user-extensible** precisely so a SOC can add `Contained` without a schema migration — so
`migrate cases 0002` would have deleted an analyst-created `Closed` that merely shared a
name with the seeded one. All three reversals are now `migrations.RunPython.noop`, and this
file proves it by actually running the reversal against seeded-then-extended data.

**M11 — `sqlmigrate` silently produces nothing for `RunPython`.** It renders SQL
operations only, so every seed migration in this project used to print nothing at all while
looking like it had been verified. `sqlmigrate`'s output for each seed migration is
asserted to be empty *and* the data itself is asserted to be present, so neither half can
be mistaken for the other.

**H4 — `hash` was seeded `is_case_sensitive=True`,** inverting AC5.4.

**M12 — the `Imported` alert status was seeded with a legal stage** but the compat mapper
could not emit it (see `tests/unit` / `compat.enums.stage_from_alert_status`).

Review items: M10 (destructive reverse), M11 (unverifiable seeds), H4, M12.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader


def migrate(targets: list[tuple[str, str]]) -> None:
    """Migrate to `targets` with a **fresh** executor.

    `MigrationExecutor` snapshots the applied migrations when it is constructed, so
    reusing one across two calls replans from stale state and tries to unapply a migration
    that is already unapplied (`OperationalError: no such index: playbook_trigger_idx`).
    """
    MigrationExecutor(connection).migrate(targets)


SEED_MIGRATIONS = (
    ("cases", "0003_seed"),
    ("alerts", "0003_seed"),
    ("observables", "0002_seed"),
)


@pytest.mark.django_db
def test_the_seed_rows_are_present_after_a_normal_migrate() -> None:
    from alerts.models import AlertStatus
    from cases.models import CaseStatus
    from observables.models import ObservableType

    assert set(AlertStatus.objects.values_list("value", flat=True)) >= {
        "New",
        "Triaged",
        "Dismissed",
        "Imported",
    }
    assert set(CaseStatus.objects.values_list("value", flat=True)) >= {
        "New",
        "InProgress",
        "Contained",
        "Closed",
    }
    assert set(ObservableType.objects.values_list("name", flat=True)) >= {
        "ip",
        "domain",
        "fqdn",
        "url",
        "mail",
        "file",
        "hash",
        "user",
        "other",
    }


# `transaction=True` because a schema change cannot run inside pytest-django's wrapping
# transaction; `serialized_rollback` because transactional tests flush every table, and the
# flush would remove the rows the seed migrations inserted — the very rows under test.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize(("app_label", "name"), SEED_MIGRATIONS)
def test_m10_rolling_back_a_seed_migration_deletes_no_rows(app_label: str, name: str) -> None:
    """A schema rollback must never delete domain data.

    The old `reverse_code` deleted by value — `.filter(value__in=[...])`. ADR-002 §D4 makes
    statuses and observable types user-extensible precisely so a SOC can add one without a
    schema migration, so that reverse destroyed rows Amalthea did not create (and, when the
    user row happened to share a name with a seeded one, destroyed it *instead* of the seed).
    """
    from alerts.models import AlertStatus
    from cases.models import CaseStatus
    from observables.models import ObservableType

    model, parent = {
        ("cases", "0003_seed"): (CaseStatus, "0002_initial"),
        ("alerts", "0003_seed"): (AlertStatus, "0002_initial"),
        ("observables", "0002_seed"): (ObservableType, "0001_initial"),
    }[(app_label, name)]

    before = set(model.objects.values_list("id", flat=True))
    assert before, "the seed must have inserted something for this test to mean anything"

    migrate([(app_label, parent)])

    assert set(model.objects.values_list("id", flat=True)) == before, (
        f"M10: rolling back {app_label}/{name} deleted rows"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_m10_a_seed_neither_overwrites_nor_deletes_a_pre_existing_row() -> None:
    """The exact scenario the old reverse destroyed.

    The vocabulary entry already exists (created by an analyst or an import) when the seed
    runs, so `get_or_create` must change nothing — and rolling the seed back afterwards
    must not remove it. The old `filter(name__in=[...]).delete()` reverse did.
    """
    from observables.models import ObservableType

    migrate([("observables", "0001_initial")])
    ObservableType.objects.filter(name="hash").delete()
    analyst_row = ObservableType.objects.create(name="hash", is_case_sensitive=True)

    migrate([("observables", "0002_seed")])
    assert ObservableType.objects.get(pk=analyst_row.pk).is_case_sensitive is True, (
        "the seed must not overwrite a row it did not create"
    )
    assert ObservableType.objects.filter(name="hash").count() == 1

    migrate([("observables", "0001_initial")])
    assert ObservableType.objects.filter(pk=analyst_row.pk).exists(), (
        "M10: rolling back the seed deleted a row Amalthea did not create"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_user_defined_vocabulary_entry_survives_a_rollback() -> None:
    from cases.models import CaseStatus

    migrate([("cases", "0002_initial")])
    analyst_row = CaseStatus.objects.create(value="Awaiting-Legal", stage="InProgress", order=42)
    migrate([("cases", "0003_seed")])
    migrate([("cases", "0002_initial")])
    assert CaseStatus.objects.filter(pk=analyst_row.pk).exists(), (
        "ADR-002 D4: a status an analyst added must survive a schema rollback"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_m10_a_seed_is_idempotent_when_re_applied() -> None:
    from observables.models import ObservableType

    before = dict(ObservableType.objects.values_list("name", "is_case_sensitive"))
    migrate([("observables", "0001_initial")])
    migrate([("observables", "0002_seed")])
    after = dict(ObservableType.objects.values_list("name", "is_case_sensitive"))
    assert after == before, "re-running a seed must be a no-op, not a duplication"
    assert ObservableType.objects.filter(name="ip").count() == 1


@pytest.mark.django_db
def test_h4_the_seeded_vocabulary_matches_ac5_4() -> None:
    from observables.models import ObservableType

    flags = dict(ObservableType.objects.values_list("name", "is_case_sensitive"))
    assert flags["hash"] is False, "H4: hex hashes are compared case-insensitively"
    assert flags["file"] is True, "H4: a file path is the one case-sensitive seeded type"


@pytest.mark.django_db
def test_m12_the_imported_stage_is_a_legal_alert_stage() -> None:
    from alerts.models import AlertStatus

    stages = dict(AlertStatus.objects.values_list("value", "stage"))
    assert stages["Imported"] == "Imported"
    assert set(stages.values()) <= {"New", "InProgress", "Closed", "Imported"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("app_label", "name"), SEED_MIGRATIONS)
def test_m11_sqlmigrate_prints_no_ddl_for_a_seed_migration(
    app_label: str, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`sqlmigrate` cannot render `RunPython`, so "no output" is the expected result.

    Asserting it explicitly is what stops an empty transcript from being read as "the
    migration was verified". The data assertions above are the real check.
    """
    from django.core.management import call_command

    call_command("sqlmigrate", app_label, name, verbosity=1)
    out = capsys.readouterr().out
    statements = [
        line
        for line in out.splitlines()
        # Strip the BEGIN/COMMIT wrapper and the `sqlmigrate cannot do this` comments.
        if line.strip() and not line.strip().startswith("--") and ";" in line
    ]
    assert statements == ["BEGIN;", "COMMIT;"], (
        f"sqlmigrate renders SQL operations only, so a RunPython seed produces no DDL. "
        f"Got: {statements}"
    )


@pytest.mark.django_db
def test_every_seed_migration_has_a_safe_reverse() -> None:
    """Structural guarantee for M10, across every seed in the project."""
    from django.db.migrations import RunPython

    loader = MigrationLoader(connection)
    for app_label, name in SEED_MIGRATIONS:
        migration = loader.disk_migrations[(app_label, name)]
        run_python = [op for op in migration.operations if isinstance(op, RunPython)]
        assert run_python, f"{app_label}/{name} has no RunPython seeding step"
        for op in run_python:
            assert op.reverse_code is RunPython.noop or (
                getattr(op.reverse_code, "__name__", "") == "noop"
            ), (
                f"{app_label}/{name}: the reverse deletes rows, which erases user-created "
                "vocabulary (REVIEW M10)"
            )
