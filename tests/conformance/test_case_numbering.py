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

REVIEW-2026-10-04 round-2 **H-6** (TODO 1.16) is the other half: `nextval` and `INSERT` are
independent, so an explicitly supplied number (TheHive's migration path replays the
original) left the sequence behind and the next auto-numbered case died on
``duplicate key ... case_record_number_uniq``. **SQLite cannot express that failure** — its
allocator is ``MAX(number) + 1``, which reads the row just written — so the end-to-end proof
is Postgres-only and everything else here asserts the *logic* on every engine.
"""

from __future__ import annotations

import inspect
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection, router
from django.utils import timezone

from cases.models import Case, CaseStatus
from cases.numbering import (
    SEQUENCE_NAME,
    SEQUENCE_SYNC_SQL,
    AllocatedNumberField,
    allocate_case_number,
    sync_case_number_sequence,
)


@pytest.fixture
def new_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.mark.django_db
def test_a_case_is_numbered_on_save(new_status: CaseStatus) -> None:
    """The INSERT path fills `number` — and a *fresh* sequence hands out 1.

    Freshness has to be established, not assumed. `case_number_seq` is non-transactional, so
    every `nextval` an earlier test spent and then rolled back is still spent and the sequence's
    start point is suite-order dependent — which is precisely the reason the test below is
    deliberately *relative*. This is where the absolute claim lives, so this is where the
    sequence is rewound.
    """
    _rewind_case_number_sequence()
    case = Case.objects.create(title="Phishing wave", status=new_status)
    assert case.number is not None, "the allocator must fill the number"
    assert case.number == 1, "the first allocated number of a fresh sequence is 1"


def _rewind_case_number_sequence() -> None:
    """Point `case_number_seq` back at its own first value so the next `nextval` returns 1.

    `1` and `is_called = false` rather than `0`: the sequence was created `START 1`, so `0` is
    outside its bounds (`setval: value 0 is out of bounds`), and `is_called = false` is what
    makes the next call return `last_value` itself instead of `last_value + 1`.

    A no-op off Postgres: SQLite's allocator is `MAX(number) + 1` over the rows this test's
    transaction can see, and an empty table is already the fresh state — there is nothing to
    rewind. `SEQUENCE_NAME` is a module constant, so the interpolation is the same one
    `allocate_case_number` and `SEQUENCE_SYNC_SQL` already do.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT setval('{SEQUENCE_NAME}', 1, false)")


@pytest.mark.django_db
def test_numbers_increase_and_are_unique(new_status: CaseStatus) -> None:
    numbers = [Case.objects.create(title=f"case {i}", status=new_status).number for i in range(5)]
    # Relative, not absolute, and that matters on Postgres: a sequence is non-transactional,
    # so an earlier test's rolled-back `nextval` is still consumed and the start point is
    # suite-order dependent. What the allocator promises is monotonicity and uniqueness; the
    # "the first allocation of a fresh sequence is 1" absolute is asserted by the test above.
    assert numbers == sorted(numbers), "allocation must be monotonic"
    assert len(set(numbers)) == 5, "allocation must be unique"
    # Consecutive within this INSERT batch: 2,3,4,5,6 (or 1,2,3,4,5 on a fresh sequence).
    assert numbers[-1] - numbers[0] == 4, "allocation must be gapless per insert"


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


# ---------------------------------------------------------------------------------------
# Round-2 H-6 (TODO 1.16) — the sequence must advance past a caller-supplied number.
#
# `MAX(number) + 1` on SQLite heals itself, so *every* behavioural assertion about this
# passes vacuously on the dev engine. That is precisely why the suite found nothing for two
# rounds. The four tests below are therefore split: the three that run on every engine
# assert the mechanism (which statement, which hook, which engine), and the one that
# asserts the *consequence* is deliberately Postgres-only and says so out loud.
# ---------------------------------------------------------------------------------------


def test_h6_the_sync_statement_advances_the_sequence_monotonically() -> None:
    """The fix is `setval(..., GREATEST(last_value, n), true)` and nothing weaker.

    Asserted on the statement text, so this engine-independent test is what actually holds
    the fix in place when the suite runs on SQLite. `setval` alone would let a re-import of
    an *older* number walk the sequence backwards; `is_called = false` would hand the
    explicit number itself out again on the very next insert.

    REVIEW-2026-10-05 §6(b): the read of `last_value` and the `setval` write must be
    serialized. Postgres forbids locking a sequence directly (`FOR UPDATE` and `LOCK TABLE`
    both raise), so the lock is the transaction-scoped advisory lock inside the statement's
    own subquery — asserted here so the pin can never be dropped back to the racy
    GREATEST-with-stale-read form that round 3 flagged.
    """
    assert "setval" in SEQUENCE_SYNC_SQL
    assert "GREATEST" in SEQUENCE_SYNC_SQL, "the sync must be monotonic, not an assignment"
    assert "last_value" in SEQUENCE_SYNC_SQL, "it must read the sequence's current position"
    assert SEQUENCE_SYNC_SQL.rstrip().endswith("true)"), "is_called must be true"
    assert SEQUENCE_NAME in SEQUENCE_SYNC_SQL
    assert "%(number)s" in SEQUENCE_SYNC_SQL, "the number must be a bound parameter"
    assert "CREATE SEQUENCE" not in SEQUENCE_SYNC_SQL, "C1: no DDL from the write path"
    assert "pg_advisory_xact_lock" in SEQUENCE_SYNC_SQL, (
        "§6(b): the read-modify-write must take the advisory lock before reading last_value"
    )
    assert "pg_advisory_xact_lock" in SEQUENCE_SYNC_SQL.split("GREATEST")[1], (
        "§6(b): the advisory lock must live inside the GREATEST argument subquery, which "
        "evaluates to completion before setval can run"
    )


def test_h6_the_sequence_sync_is_vendor_guarded() -> None:
    """The guard must stay, and must be the vendor — not "does the sequence exist?".

    Asserted against the function's own source so it cannot rot into an unconditional
    `setval`, which would raise `ProgrammingError` on SQLite for every imported case.
    """
    source = inspect.getsource(sync_case_number_sequence)
    assert "postgresql" in source, "the sync is a Postgres-only concept"
    assert source.index("postgresql") < source.index("cursor.execute"), (
        "the vendor check must come before the statement is issued"
    )
    assert "CREATE SEQUENCE" not in source, "the sequence is created by migration 0005"


def test_h6_the_sync_is_a_noop_off_postgres() -> None:
    """On this engine the call must do nothing at all — and must not raise."""
    if connection.vendor == "postgresql":  # pragma: no cover - dev/CI run SQLite
        pytest.skip("this assertion is about the SQLite no-op path")
    sync_case_number_sequence(4242)  # must be silent


@pytest.mark.django_db
def test_h6_an_explicit_number_syncs_the_sequence_on_insert(
    new_status: CaseStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync is driven from `Field.pre_save`, so no INSERT path can skip it.

    Three behaviours in one test, because they are three ways to get it wrong: an explicit
    number must sync; an auto-allocated number must *not* (the sequence already moved, and a
    redundant `setval` on every case write is write amplification); an UPDATE must not sync
    either, because the number cannot have changed.
    """
    import cases.numbering as numbering

    calls: list[int] = []
    monkeypatch.setattr(
        numbering, "sync_case_number_sequence", lambda number, using=None: calls.append(number)
    )

    imported = Case.objects.create(title="Imported", status=new_status, number=4242)
    assert calls == [4242], f"an explicit number must advance the sequence, got {calls}"

    calls.clear()
    Case.objects.create(title="auto", status=new_status)
    assert calls == [], "an auto-allocated number came from the sequence; re-syncing is waste"

    calls.clear()
    imported.title = "renamed"
    imported.save()
    assert calls == [], "an UPDATE cannot change the number; the sync would be pure overhead"


@pytest.mark.django_db
def test_h6_bulk_create_with_explicit_numbers_syncs_too(
    new_status: CaseStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bulk_create()` is a different INSERT path; it must not be a hole in the fix."""
    import cases.numbering as numbering

    calls: list[int] = []
    monkeypatch.setattr(
        numbering, "sync_case_number_sequence", lambda number, using=None: calls.append(number)
    )
    Case.objects.bulk_create(
        [
            Case(title="imp a", status=new_status, number=900),
            Case(title="imp b", status=new_status, number=901),
        ]
    )
    assert sorted(calls) == [900, 901], calls

    calls.clear()
    Case.objects.bulk_create([Case(title="auto", status=new_status)])
    assert calls == [], "an auto-allocated batch must not re-sync"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="H-6 is Postgres-only by construction: SQLite allocates MAX(number)+1, which reads "
    "the row just written and therefore cannot reproduce the collision. Run this test on "
    "Postgres (TODO 3.1) — deleting sync_case_number_sequence makes it fail there.",
)
def test_h6_on_postgres_the_next_auto_number_clears_an_imported_number(
    new_status: CaseStatus,
) -> None:
    """The end-to-end consequence, on the only engine that can exhibit it.

    Without the fix this raises ``IntegrityError: duplicate key value violates unique
    constraint "case_record_number_uniq"`` at the ``Case.objects.create`` below.
    """
    Case.objects.create(title="imported at 500", status=new_status, number=500)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT last_value FROM {SEQUENCE_NAME}")  # noqa: S608 (module constant)
        assert cursor.fetchone()[0] >= 500, "setval did not advance the sequence"

    following = Case.objects.create(title="auto after import", status=new_status)
    assert following.number > 500, f"nextval re-issued an imported number: {following.number}"
    assert Case.objects.filter(number=500).count() == 1, (
        "the imported number must belong to exactly one case: the imported row"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="the read-modify-write race (§6b) exists only where a sequence is a real object; "
    "SQLite has no sequence to race on",
)
def test_h6_concurrent_imports_cannot_walk_the_sequence_backwards(
    new_status: CaseStatus,
) -> None:
    """REVIEW-2026-10-05 §6(b): two concurrent imports must serialize on the sync.

    Without the advisory lock, T1 and T2 both read the stale ``last_value`` (e.g. 100), then
    T1's ``setval`` to 500 lands and T2's ``setval`` to 300 overwrites it — the sequence
    moves **backwards** and later ``nextval`` re-issues numbers already in the table. With
    the lock, T2 blocks until T1 commits, reads the fresh 500, and ``GREATEST(500, 300)``
    keeps 500. This test needs real concurrency, so it commits and uses two connections.
    """
    import threading

    from django.db import connections as dbs

    # Anchor the sequence so earlier (non-transactional) sequence consumers in this file
    # cannot move the goalposts: explicit imports in prior tests leave last_value at 4242/901,
    # which would turn this race test into a GREATEST test instead.
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT setval('{SEQUENCE_NAME}', 100, true)")
    Case.objects.create(title="seed at 100", status=new_status, number=100)

    start = threading.Barrier(2)  # both imports must be ready before either begins, to
    # widen the window in which both can read the stale last_value before either setval.

    def sync(number: int, results: list[tuple[int, int]]) -> None:
        conn = dbs["default"]
        with conn.cursor() as cursor:
            start.wait()  # line up both racers
            # Each job opens its own transaction so the advisory xact lock is held
            # until *after* the read; the race window is the gap between read and setval.
            cursor.execute("BEGIN")
            cursor.execute(SEQUENCE_SYNC_SQL, {"number": number})
            cursor.fetchone()  # the setval result — consumed so the cursor stays clean
            cursor.execute(f"SELECT last_value FROM {SEQUENCE_NAME}")  # noqa: S608
            last = cursor.fetchone()[0]
            cursor.execute("COMMIT")
            results.append((number, last))

    results: list[tuple[int, int]] = []
    threads = [
        threading.Thread(target=sync, args=(500, results)),
        threading.Thread(target=sync, args=(300, results)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    by_import = dict(results)
    assert by_import[500] == 500, f"the higher import must land at 500, got {by_import}"
    # Final position is 500 no matter which thread ran last: the lower import's setval(300)
    # cannot overwrite 500 after 500 was set.
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT last_value FROM {SEQUENCE_NAME}")  # noqa: S608
        assert cursor.fetchone()[0] == 500, "a lower import walked the sequence backwards"


# --------------------------------------------------------------------------------------
# REVIEW-2026-10-04 round-2 **M-2** (TODO 1.19): `Case.stamp_closed_date` was never tested.
#
# Deleting the `self.stamp_closed_date()` call from `Case.save` left the whole suite green,
# so TheHive's `closedDate` ledger field was written by an unverified branch. The two
# properties that matter are "stamped exactly on entry to Closed" and "never clobbered
# afterwards" — a naive `if stage == "Closed"` would pass the first and fail the second.
# --------------------------------------------------------------------------------------


@pytest.fixture
def closed_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="Closed", defaults={"stage": "Closed", "order": 9}
    )[0]


@pytest.fixture
def investigating_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="Investigating", defaults={"stage": "InProgress", "order": 2}
    )[0]


@pytest.mark.django_db
def test_m2_a_closed_case_records_its_closed_date(new_status: CaseStatus, closed_status) -> None:
    case = Case.objects.create(title="becomes closed", status=new_status)
    assert case.closed_date is None, "an open case must not carry a closed_date"

    case.status = closed_status
    case.save()
    case.refresh_from_db()
    assert case.closed_date is not None, "entering the Closed stage must stamp closed_date"


@pytest.mark.django_db
def test_m2_closed_date_is_not_clobbered_by_a_later_write(
    new_status: CaseStatus, closed_status: CaseStatus
) -> None:
    case = Case.objects.create(title="closed once", status=closed_status)
    first_stamp = case.closed_date
    assert first_stamp is not None, "creating an already-closed case must stamp it"

    case.title = "closed once, then edited"
    case.save()
    case.refresh_from_db()
    assert case.closed_date == first_stamp, "a later save must not move the original anchor"


@pytest.mark.django_db
def test_m2_a_pre_existing_closed_date_is_never_overwritten(
    new_status: CaseStatus, closed_status
) -> None:
    sentinel = timezone.now() - timedelta(days=7)
    case = Case.objects.create(title="imported with a historic date", status=new_status)
    Case.objects.filter(pk=case.pk).update(closed_date=sentinel)

    case.refresh_from_db()
    case.status = closed_status
    case.save()
    case.refresh_from_db()
    assert case.closed_date == sentinel, (
        "the stamp is 'set once, on entry'. Overwriting a pre-existing value would destroy "
        "the imported close time that `end_date` is reconciled against."
    )
