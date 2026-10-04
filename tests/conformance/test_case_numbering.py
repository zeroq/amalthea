"""REVIEW-2026-10-03 **C1** — `Case.number` is allocated, never client-supplied.

PLAN §6.1 makes `number` a unique case number and §6.3 specifies a Postgres sequence. The
original implementation ran ``CREATE SEQUENCE IF NOT EXISTS case_number_seq`` from
`Case.save()`, which is wrong three ways:

1. it is not valid SQLite, so **every** auto-allocated case raised
   `OperationalError: near "SEQUENCE": syntax error` in dev;
2. DDL issued from a request path is not a migration — it does not roll back, it does not
   run on a replica, and it cannot be reviewed;
3. `bulk_create()` never calls `save()`, so any allocation done in `save()` is skipped
   wholesale — exactly the failure mode the `number=1` in the old `test_integrity.py`
   masked.

Allocation now lives in the field's `pre_save`, which every INSERT path runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from django.db import connection, router

from cases.models import Case, CaseStatus
from cases.numbering import SEQUENCE_NAME, AllocatedNumberField, allocate_case_number


@pytest.fixture
def new_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.mark.django_db
def test_a_case_is_numbered_on_save(new_status: CaseStatus) -> None:
    case = Case.objects.create(title="Phishing wave", status=new_status)
    assert case.number is not None, "the allocator must fill the number"
    assert case.number == 1, "the first allocated number of a fresh sequence is 1"


@pytest.mark.django_db
def test_numbers_increase_and_are_unique(new_status: CaseStatus) -> None:
    numbers = [Case.objects.create(title=f"case {i}", status=new_status).number for i in range(5)]
    assert numbers == [1, 2, 3, 4, 5], "allocation must be monotonic and gapless per insert"
    assert len(set(numbers)) == 5


@pytest.mark.django_db
def test_an_explicit_number_is_preserved(new_status: CaseStatus) -> None:
    """Migrating a historical case file must be able to keep its original number."""
    case = Case.objects.create(title="Imported", status=new_status, number=4242)
    assert case.number == 4242
    # SQLite allocates MAX(number)+1, so an explicitly imported number moves the sequence
    # on. On Postgres the sequence is independent and would still hand out 1.
    assert Case.objects.create(title="Next", status=new_status).number == 4243


@pytest.mark.django_db
def test_bulk_create_is_numbered_too(new_status: CaseStatus) -> None:
    """`bulk_create()` bypasses `save()`; the field's `pre_save` is what covers it."""
    cases = Case.objects.bulk_create(
        [
            Case(title="bulk 1", status=new_status),
            Case(title="bulk 2", status=new_status),
            Case(title="bulk 3", status=new_status),
        ]
    )
    allocated = sorted(case.number for case in cases)
    assert all(number is not None for number in allocated), "bulk_create left a number unset"
    assert len(set(allocated)) == 3, (
        f"bulk_create produced duplicate numbers {allocated}: every row's field pre_save "
        "read the same MAX(number) because the batch is rendered before it is inserted"
    )
    assert sorted(Case.objects.values_list("number", flat=True)) == allocated


@pytest.mark.django_db
def test_bulk_create_numbering_does_not_collide_with_save(new_status: CaseStatus) -> None:
    Case.objects.create(title="saved first", status=new_status)
    Case.objects.bulk_create([Case(title="bulk", status=new_status)])
    numbers = sorted(Case.objects.values_list("number", flat=True))
    assert len(set(numbers)) == len(numbers), numbers


@pytest.mark.django_db
def test_the_number_is_unique_in_the_database(new_status: CaseStatus) -> None:
    from django.db import IntegrityError

    Case.objects.create(title="one", status=new_status, number=7)
    with pytest.raises(IntegrityError):
        Case.objects.create(title="two", status=new_status, number=7)


@pytest.mark.django_db
def test_number_is_read_only_for_api_consumers(new_status: CaseStatus) -> None:
    """`editable=False` keeps DRF from accepting a caller-chosen number."""
    assert Case._meta.get_field("number").editable is False
    assert isinstance(Case._meta.get_field("number"), AllocatedNumberField)


@pytest.mark.django_db
def test_full_clean_still_works_with_an_unset_number(new_status: CaseStatus) -> None:
    """The runtime `if number is None` guard must survive the field change."""
    case = Case(title="validates", status=new_status)
    case.full_clean(exclude=["case", "start_date", "end_date", "closed_date"])
    assert case.number is None, "full_clean must not allocate; only the write path does"


@pytest.mark.django_db
def test_the_postgres_sequence_is_created_by_a_migration_not_by_save() -> None:
    """The DDL belongs in a migration, and the allocator must not issue DDL.

    A migration that runs the statement proves the schema change is reviewed and
    reversible; `allocate_case_number` containing no DDL proves the request path cannot be
    the thing that creates it.
    """
    from django.db.migrations import RunPython as RunPythonOperation
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    assert ("cases", "0005_case_number_sequence") in loader.disk_migrations, sorted(
        name for app_label, name in loader.disk_migrations if app_label == "cases"
    )
    migration = loader.disk_migrations[("cases", "0005_case_number_sequence")]
    operations = [op for op in migration.operations if isinstance(op, RunPythonOperation)]
    assert len(operations) == 1, "exactly one forward/backward RunPython pair is expected"
    forward = operations[0].code
    # The migration reads the *name* of the module-level constant the allocator uses
    # rather than hard-coding a second copy of it, so the two cannot drift apart.
    assert "SEQUENCE_NAME" in forward.__code__.co_names, forward.__code__.co_names
    migration_module = sys.modules[forward.__module__]
    assert migration_module.SEQUENCE_NAME == SEQUENCE_NAME

    source = Path(forward.__code__.co_filename).read_text()
    assert "postgresql" in source, "the DDL must be guarded on the engine that has sequences"

    allocator_source = Path(allocate_case_number.__code__.co_filename).read_text()
    allocator_body = allocator_source[allocator_source.index("def allocate_case_number") :]
    assert "CREATE SEQUENCE" not in allocator_body, (
        "C1: DDL executed from the allocation path is not a migration"
    )


@pytest.mark.django_db
def test_allocate_case_number_uses_the_default_database(new_status: CaseStatus) -> None:
    assert Case._meta.db_table == "case_record"
    number = allocate_case_number(using=router.db_for_write(Case))
    assert isinstance(number, int) and number >= 1
