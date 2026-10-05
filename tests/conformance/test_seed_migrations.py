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
could not emit it (see `compat.enums.stage_from_alert_status`, covered by
`test_seed_migrations.py::test_m12_the_imported_stage_is_a_legal_alert_stage`).

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


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("app_label", "name", "parent"),
    [
        ("cases", "0003_seed", "0002_initial"),
        ("observables", "0002_seed", "0001_initial"),
    ],
)
def test_h3_2_rolling_back_a_seed_unapplies_nothing_in_another_app(
    app_label: str, name: str, parent: str
) -> None:
    """Rolling back a seed must not unapply another app's migrations.

    Round-3 **H3-2**. `test_m10_rolling_back_a_seed_migration_deletes_no_rows` above passed while
    `migrate observables 0001_initial` **dropped the entire `case_record` table**. It passed because
    it only read `ObservableType` rows and asserted nothing about the schema those rows sat in --
    the same shape of gap as the round-1 and round-2 guard failures this project has hit twice.

    The cause was four dependency edges that were never needed:

    * `cases/0004_case_hardening`, `alerts/0007` and `alerts/0008` each declared a dependency on
      `observables/0002_seed`. They only reference the `observable` *table* (via
      `case_observable.observable` and `alert_observable.observable`), which `0001_initial` creates.
      Depending on a *seed* is what chained the rollback across apps.
    * `observables/0003_observable_data_hash` declared a dependency on `cases/0004_case_hardening`.
      It alters only `Observable` fields and constraints; it never touches `cases`.

    With the seed mid-chain, unapplying it forced Django to unapply everything depending on it,
    transitively -- 13 migrations across three apps, including the ones that create `case_record`.
    Pinning the edges lower cannot help; the edges have to *mean* something.

    `alerts/0003_seed` is deliberately *not* listed. Its rollback still unapplies
    `automation/0003_automation_hardening`, but through the legitimate `automation:0003 ->
    alerts/0004_alert_hardening` edge -- automation's FKs genuinely point at `alerts.alert`, so
    hardening built on top of alerts hardening has to come down with it. That is Django's model
    working correctly, not a seed being dragged across apps, and `test_every_seed_migration_has_a
    _safe_reverse` plus the graph test below cover the property that does matter. Recorded rather
    than silently widened.

    This asserts the **plan**, not the executed schema, and that is deliberate. Executing the
    rollback is order-fragile in this file: `migrate(cases, 0002)` renames `case_record` back to
    `case`, and re-applying forward leaves `automation_run`'s SQLite foreign key pointing at the
    old name, so a restore-forward breaks the *next* test's teardown flush with "no such table:
    main.case". A plan is deterministic, needs no DB mutation, and states the invariant directly. The
    executed behaviour was confirmed by hand: `case_record` survives once the edges are corrected.
    """
    plan = MigrationExecutor(connection).migration_plan([(app_label, parent)])

    unapplied = sorted({f"{app}.{mig}" for app, mig in plan})
    foreign = [entry for entry in unapplied if not entry.startswith(f"{app_label}.")]

    assert not foreign, (
        f"H3-2: rolling back {app_label}/{name} would unapply {foreign} from another app. A seed "
        f"rollback must stay inside its own app; full plan was {unapplied}."
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize(
    ("app_label", "child"),
    [
        # Every cross-app dependency that targets a seed must be a leaf-safe one, or rolling the
        # seed back reaches into another app's migrations.
        ("observables", "0003_observable_data_hash"),
        ("cases", "0004_case_hardening"),
        ("alerts", "0007_alter_alertcustomfieldvalue_alert_and_more"),
        ("alerts", "0008_alter_alertobservable_alert_and_more"),
        ("alerts", "0004_alert_hardening"),
        ("automation", "0003_automation_hardening"),
    ],
)
def test_h3_2_no_migration_depends_on_another_apps_seed(app_label: str, child: str) -> None:
    """No migration may depend on a *seed* in another app; that is what makes rollback cascade.

    Depending on your own app's preceding migration is normal — migrations are sequential within an
    app, so `observables/0003` depending on `observables/0002_seed` is expected and harmless. The
    defect is reaching across an app boundary to a seed, which puts unrelated schema between the
    operator and the thing they asked to reverse.

    A seed is data, and every migration above it in the chain becomes un-unapplyable by accident:
    Django must unapply dependents before the seed, so a request to reverse one seed quietly
    reverses real schema work in another app. Depending on the table-creating migration
    (`0001_initial`) is both sufficient and safe.
    """
    seed_apps = {app for app, name in SEED_MIGRATIONS}
    graph = MigrationLoader(connection).graph

    node = None
    for candidate in graph.nodes:
        if candidate[0] == app_label and candidate[1] == child:
            node = candidate
            break
    assert node is not None, f"{app_label}/{child} not found in the migration graph"

    for dependency in graph.node_map[node].parents:
        cross_app_seed = (
            dependency[0] in seed_apps
            and dependency[0] != app_label
            and dependency[1] in _seed_names(dependency[0])
        )
        assert not cross_app_seed, (
            f"H3-2: {app_label}/{child} depends on seed {dependency[0]}/{dependency[1]} in another "
            "app; depend on the table-creating migration instead"
        )


def _seed_names(app_label: str) -> set[str]:
    return {name for seed_app, name in SEED_MIGRATIONS if seed_app == app_label}


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
