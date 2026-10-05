"""Permanent regression guard against vacuous conformance tests (plan risk **R11**).

Each test here applies a real schema/model mutation, calls the *real* assertion from the
conformance module that replaced the corresponding vacuous one, and requires it to raise.
Every mutation is undone in a `finally`.

Why this file exists, permanently
--------------------------------
REVIEW-2026-10-03 finding **H1** established that four of six Phase 3 schema acceptance
criteria were "met" by tests that could not fail:

* ``test_introspection.py`` — a tautology (``assert f in fields or any(...)``);
* ``test_fk_audit.py`` — ``rf.on_delete is not None`` is always true, and
  ``get_accessor_name()`` silently falls back to the model name, so the exact AC3.4
  violation it claimed to detect still passed;
* ``test_indexes.py`` — ``assert len(tables) > 0`` passes with 32 tables and zero of the
  plan's §6.3 indexes;
* ``test_phase3_schema.py::test_all_pks_are_uuid`` — a literal ``pass`` body.

The replacement assertions were accepted only because their **failure modes were
demonstrated**. This file is that demonstration, and it is the only artifact in the
repository that proves the replacements are not vacuous in the same way. Risk R11 makes
"a passing AC is only evidence once it can be shown to fail" part of the Definition of
Done, so deleting this file would delete the evidence and leave the next reviewer unable
to distinguish a real contract from another tautology. It is a guard, not a scaffold:
keep it, extend it whenever a conformance assertion is added or changed.

Planner ruling, 2026-10-03 (TODO §1.9): **keep, permanently** — the framing that this was a
throwaway probe to be deleted "after the evidence is captured" was wrong; the evidence is
only durable while the mechanism that produced it runs on every `pytest` invocation.
"""

from contextlib import contextmanager

import pytest
from django.db import IntegrityError, connection, models, transaction

from observables.models import Observable, ObservableType
from tests.conformance import (
    test_enum_contracts,
    test_fk_audit,
    test_indexes,
    test_integrity,
    test_introspection,
    test_phase3_schema,
)
from tests.conformance._mutation import schema_mutation
from tests.conformance._schema import partial_index_predicate

Skipped = pytest.skip.Exception


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_1_introspection_detects_a_renamed_column():
    """Break `alert.ingestion_source_id` (REVIEW M2's column) and expect a failure."""
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE alert RENAME COLUMN ingestion_source_id TO mutated_source_id")
    try:
        with pytest.raises(AssertionError) as excinfo:
            test_introspection.test_required_columns_are_in_the_database(
                "alert", test_introspection.REQUIRED_COLUMNS["alert"]
            )
        print("MUTATION-1 ASSERTION:", str(excinfo.value))
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE alert RENAME COLUMN mutated_source_id TO ingestion_source_id"
            )


@pytest.mark.django_db
def test_mutation_2_fk_audit_detects_a_missing_related_name():
    """Drop the explicit `related_name` — the defect the old test could not see."""
    from automation.models import AutomationRun

    field = AutomationRun._meta.get_field("playbook")
    original = field.remote_field.related_name
    field.remote_field.related_name = None
    try:
        # R11: prove the mutation landed before asserting what it broke. `field` is the same
        # object the audit re-fetches from `_meta`, so reading it back is a real check — without
        # it this guard could be assigning to a detached object and reporting a failure mode for
        # a schema it never touched (the round-3 H3-3 loophole, caught in the guards themselves).
        assert field.remote_field.related_name is None, "the mutation did not land"
        with pytest.raises(AssertionError) as excinfo:
            test_fk_audit.test_every_fk_declares_a_related_name()
        print("MUTATION-2 ASSERTION:", str(excinfo.value))
    finally:
        field.remote_field.related_name = original


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_3_indexes_detects_a_redundant_index():
    """Re-create the REVIEW M1 redundancy: `(status_id)` beside `(status_id, start_date)`."""
    from django.db import models

    from cases.models import Case

    redundant = models.Index(fields=["status_id"], name="case_status_idx_mutated")
    # add_index() verifies the index reached sqlite_master; the block removes it again on exit.
    with schema_mutation("case_record", label="add redundant (status_id) index") as m:
        m.add_index(Case, redundant)
        with pytest.raises(AssertionError) as excinfo:
            test_indexes.test_no_index_is_left_prefix_covered_by_a_wider_one()
        print("MUTATION-3 ASSERTION:", str(excinfo.value))


@pytest.mark.django_db
def test_mutation_4_phase3_schema_detects_a_nullable_login():
    """Make `User.login` nullable again — the defect REVIEW H6 removed.

    A model mutation rather than a schema one: SQLite cannot drop a CHECK constraint
    (`remove_constraint` is a deliberate no-op there), and `login.null` is the property
    the audit reads. Reversible, so the suite stays green afterwards.
    """
    from identity.models import User

    login = User._meta.get_field("login")
    original_null, original_blank = login.null, login.blank
    login.null, login.blank = True, True
    try:
        # R11 read-back, same reasoning as guard 2: `login` is the object the audit reads.
        assert login.null is True and login.blank is True, "the mutation did not land"
        with pytest.raises(AssertionError) as excinfo:
            test_phase3_schema.test_h6_user_requires_a_distinct_login()
        print("MUTATION-4 ASSERTION:", str(excinfo.value))
    finally:
        login.null, login.blank = original_null, original_blank


def _duplicate_insert_raises(marker: str) -> bool:
    """Try to store two identical observables. Return True if the second was rejected.

    Self-contained and rolled back: it must not depend on another test's fixtures, and it must not
    leave rows behind, or they collide with the *next* guard's remade table.
    """
    other = ObservableType.objects.get(name="other")
    try:
        with transaction.atomic():
            Observable.objects.create(data_type=other, data=marker, normalized_data=marker)
            Observable.objects.create(data_type=other, data=marker, normalized_data=marker)
            transaction.set_rollback(True)
        return False
    except IntegrityError:
        return True


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_5_integrity_detects_a_dropped_unique_constraint():
    """Drop `uniq_obs_dtype_hash` for real, and prove both that it went and that it mattered.

    Guards the REVIEW **H3** fix — moving observable uniqueness off the unbounded `normalized_data`
    onto `data_hash`, which bounds the btree index tuple and removes the collation dependence that
    made case-sensitive dedupe wrong on a non-`C` collation. That constraint is the entire safety net
    of the change, and it was previously unguarded.

    **This guard was itself vacuous the first time it was written** (round-2 finding C-1): it called
    `schema_editor.remove_constraint()`, which on SQLite is `_remake_table(model)` — a rebuild from
    *current model state*. The constraint was still in `_meta.constraints`, so it was written straight
    back out; duplicates were rejected throughout while the test printed `duplicate insert accepted`
    and reported a pass. `schema_mutation` makes that failure structurally impossible now, and
    `detach_constraint` supplies the technique SQLite actually requires.
    """
    with schema_mutation("observable", label="drop uniq_obs_dtype_hash") as m:
        m.detach_constraint(Observable, "uniq_obs_dtype_hash")
        m.assert_ddl_delta(missing=["uniq_obs_dtype_hash"])
        assert not _duplicate_insert_raises("mut5"), (
            "duplicate was still rejected — the constraint is somehow still enforced"
        )

    assert _duplicate_insert_raises("mut5-restored"), (
        "constraint was restored but duplicates are accepted again — restore did not take effect"
    )
    print("MUTATION-5: uniq_obs_dtype_hash absent from DDL, duplicate accepted, then restored")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_6_enum_contract_detects_a_dropped_check_constraint():
    """Drop a CHECK constraint and prove the enum guard stops rejecting out-of-range values.

    Closes round-2 finding **H-2**: the six CHECK constraints behind `H5`/`L1` were name-only.
    Mutation 4's own docstring conceded that `remove_constraint` is "a deliberate no-op" for CHECK on
    SQLite — so the database-level half of the enum validation could have been deleted with no test
    failing. These are the last line of defence for `severity`/`tlp`/`pap`, making this the guard that
    matters most for data integrity.
    """
    other = ObservableType.objects.get(name="other")

    def out_of_range_tlp(marker: str) -> bool:
        try:
            with transaction.atomic():
                Observable.objects.create(
                    data_type=other, data=marker, normalized_data=marker, tlp=99
                )
                transaction.set_rollback(True)
            return False
        except IntegrityError:
            return True

    with schema_mutation("observable", label="drop observable_tlp_range") as m:
        m.detach_constraint(Observable, "observable_tlp_range")
        m.assert_ddl_delta(missing=["observable_tlp_range"])
        assert not out_of_range_tlp("mut6"), "tlp=99 still rejected — the CHECK is somehow enforced"

    assert out_of_range_tlp("mut6-restored"), "CHECK restored but tlp=99 is accepted again"
    print("MUTATION-6: observable_tlp_range absent from DDL, tlp=99 accepted, then restored")


# ---------------------------------------------------------------------------------------
# Round-2 C-2 / H-1 / H-2 / H-3 / H-4 — TODO 1.14, 1.19 and §8.5.
#
# Each guard below applies the exact mutation REVIEW-2026-10-04 recorded, confirms it landed,
# and requires the *real* assertion to raise. Every one of these mutations passed the whole
# suite when it was applied by hand, which is precisely why the assertions are only worth
# something now.
# ---------------------------------------------------------------------------------------


def _detected(func: object, *args: object) -> str:
    """Run a real conformance assertion and **require it to fail**. Returns the failure text.

    Not `pytest.raises(AssertionError)`: several of these contracts are enforced with
    ``pytest.raises(IntegrityError)``, and when the database stops enforcing them that helper
    raises ``Failed`` — which subclasses ``BaseException``, not ``Exception`` or
    ``AssertionError``. Catching only ``AssertionError`` would report a *pass* for a mutation
    that was detected perfectly well.

    The exclusions matter too: ``Skipped`` is also an ``OutcomeException``, so a guard that
    accepted any ``BaseException`` would count "this assertion did not run" as "this assertion
    caught the mutation".
    """
    try:
        func(*args)  # type: ignore[operator]
    except (Skipped, KeyboardInterrupt, SystemExit) as exc:
        raise AssertionError(
            f"the guard proved nothing: {getattr(func, '__name__', func)} was SKIPPED ({exc!r}) "
            f"rather than failing — the contract is untested, not detected"
        ) from exc
    except BaseException as exc:
        return f"{type(exc).__name__}: {exc}"
    raise AssertionError(
        f"MUTATION NOT DETECTED: {getattr(func, '__name__', func)} passed while the schema it "
        f"asserts was broken"
    )


@contextmanager
def _swapped_on_delete(model: type[models.Model], field_name: str, replacement: object) -> object:
    """Temporarily set ``field.remote_field.on_delete``. Self-verifying; caller asserts.

    Not a metadata edit with no consequences: Django's deletion collector reads
    ``remote_field.on_delete`` at delete time, so flipping it genuinely changes what
    ``delete()`` does — which is why the behavioural assertions below break too.
    """
    field = model._meta.get_field(field_name)
    original = field.remote_field.on_delete
    assert original is not replacement, "the mutation would be a no-op"
    field.remote_field.on_delete = replacement
    try:
        yield field
    finally:
        field.remote_field.on_delete = original


@pytest.mark.django_db
def test_mutation_7_on_delete_policy_detects_a_set_null_to_cascade_swap() -> None:
    """`Alert.case` ``SET_NULL`` → ``CASCADE``: deleting a Case destroys its alerts.

    Round-2 **C-2**. The old audit only rejected ``on_delete=None``, so this and the
    `Case.assignee` twin both passed the entire suite. `EXPECTED_ON_DELETE` is now a full
    29-entry table, so leaving the `PROTECT` subset is no longer enough to miss them.
    """
    from alerts.models import Alert

    with _swapped_on_delete(Alert, "case", models.CASCADE):
        assert Alert._meta.get_field("case").remote_field.on_delete is models.CASCADE
        print(
            "MUTATION-7 ASSERTION:",
            _detected(test_fk_audit.test_every_fk_matches_the_declared_on_delete_policy),
        )


@pytest.mark.django_db
def test_mutation_8_on_delete_policy_detects_a_cascade_to_set_null_swap() -> None:
    """`Task.case` ``CASCADE`` → ``SET_NULL``: deleting a Case orphans its tasks.

    The opposite direction — one a "reject every CASCADE" assertion alone would miss. AGENTS.md
    §3 declares ``Case 1 —— 0..* Task``; a task with ``case_id IS NULL`` is not a lower
    cardinality, it is simply unreachable from the case ledger.
    """
    from cases.models import Task

    with _swapped_on_delete(Task, "case", models.SET_NULL):
        assert Task._meta.get_field("case").remote_field.on_delete is models.SET_NULL
        print(
            "MUTATION-8 ASSERTION:",
            _detected(test_fk_audit.test_every_fk_matches_the_declared_on_delete_policy),
        )


@pytest.mark.django_db
def test_mutation_9_on_delete_policy_detects_a_protect_to_cascade_swap() -> None:
    """`Observable.data_type` ``PROTECT`` → ``CASCADE``: deleting a type re-files artifacts.

    The ``PROTECT`` subset the old audit *did* check — proving the replacement catches the
    case the old one handled as well as the two it did not.
    """
    from observables.models import Observable as ObservableModel

    with _swapped_on_delete(ObservableModel, "data_type", models.CASCADE):
        assert ObservableModel._meta.get_field("data_type").remote_field.on_delete is (
            models.CASCADE
        )
        print(
            "MUTATION-9 ASSERTION:",
            _detected(test_fk_audit.test_every_fk_matches_the_declared_on_delete_policy),
        )


@pytest.mark.django_db
def test_mutation_10_on_delete_swap_breaks_the_delete_semantics_test() -> None:
    """The same class of swap, caught *behaviourally* — by counting rows, not reading a string.

    A table of intent is documentation until something is deleted against it.
    """
    from alerts.models import Alert, AlertStatus
    from cases.models import CaseStatus

    vocab = {
        "case_status": CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0],
        "alert_status": AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0],
    }

    with _swapped_on_delete(Alert, "case", models.CASCADE):
        assert Alert._meta.get_field("case").remote_field.on_delete is models.CASCADE
        print(
            "MUTATION-10 ASSERTION:",
            _detected(
                test_fk_audit.test_delete_semantics_deleting_a_case_unlinks_alerts_and_automation_runs,
                vocab,
            ),
        )
    assert not Alert.objects.filter(source_ref="r1").exists(), (
        "the CASCADE swap must actually have destroyed the alert"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_11_severity_check_detects_a_neutered_range() -> None:
    """Round-2 **H-2**, first half: a graded ``severity`` CHECK must be behavioural.

    Detaching ``case_severity_range`` is the exact mutation the review ran against six
    name-only constraints; every one of the six passed.
    """
    from cases.models import Case

    with schema_mutation("case_record", label="drop case_severity_range") as m:
        m.detach_constraint(Case, "case_severity_range")
        m.assert_ddl_delta(missing=["case_severity_range"])
        print(
            "MUTATION-11 ASSERTION:",
            _detected(
                test_enum_contracts.test_h2_every_checked_enum_domain_is_refused_by_the_database,
                "case_severity_range",
                ("case_record", "severity", (-1, 5, 99)),
            ),
        )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_12_pap_check_detects_a_neutered_range() -> None:
    """Round-2 **H-2**, second half, and on the *narrowest* domain in the schema.

    `pap` is 1..4, not 1..5 like `severity`/`tlp`, so an off-by-one in its bounds is the
    defect most likely to be invisible — hence a separate guard rather than trusting the
    parametrised sweep to have been run over it.
    """
    from alerts.models import Alert

    with schema_mutation("alert", label="drop alert_pap_range") as m:
        m.detach_constraint(Alert, "alert_pap_range")
        m.assert_ddl_delta(missing=["alert_pap_range"])
        print(
            "MUTATION-12 ASSERTION:",
            _detected(
                test_enum_contracts.test_h2_every_checked_enum_domain_is_refused_by_the_database,
                "alert_pap_range",
                ("alert", "pap", (-1, 4, 99)),
            ),
        )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_13_idempotency_key_unique_detects_a_deletion() -> None:
    """Round-2 **H-3**: `AutomationRun.idempotency_key` unique=True removed + migrate.

    AGENTS.md Module D's redelivery guarantee is this constraint, not application code: a
    Celery task delivered twice must find its key taken and skip. Nothing else enforces it.
    """
    from automation.models import AutomationRun
    from cases.models import Case, CaseStatus

    case = Case.objects.create(
        title="guard-13",
        status=CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
            0
        ],
    )
    with schema_mutation("automation_run", label="drop idempotency_key UNIQUE") as m:
        m.detach_field_unique(AutomationRun, "idempotency_key")
        m.assert_ddl_delta(missing=["UNIQUE"])
        print(
            "MUTATION-13 ASSERTION:",
            _detected(test_integrity.test_h3_automation_run_idempotency_key_is_unique, case),
        )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_14_ingestion_source_slug_unique_detects_a_deletion() -> None:
    """Round-2 **H-4**: `IngestionSource.slug` unique=True removed + migrate.

    `POST /api/v1/alerts/webhook/{source_id}` resolves the source by slug, so two sources
    sharing one makes the route non-deterministic — two mapping configs at one URL. The
    endpoint is Phase 4 work; the key it will look up is Phase 3's to enforce.
    """
    from ingest.models import IngestionSource

    with schema_mutation("ingestion_source", label="drop slug UNIQUE") as m:
        m.detach_field_unique(IngestionSource, "slug")
        m.assert_ddl_delta(missing=["UNIQUE"])
        print(
            "MUTATION-14 ASSERTION:",
            _detected(test_integrity.test_h4_ingestion_source_slug_is_unique),
        )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mutation_15_partial_index_predicate_detects_a_removal() -> None:
    """Round-2 **H-1**: `ar_pending_idx` re-created without `condition=Q(status="Pending")`.

    Django reports an index's *columns* and stops, so the predicate was never visible to any
    assertion in the suite — yet it is the entire point of C2's index: without it the dispatch
    beat scans and sorts every run instead of the pending ones.
    """
    from automation.models import AutomationRun

    original = next(i for i in AutomationRun._meta.indexes if i.name == "ar_pending_idx")
    mutated = models.Index(fields=["created_at"], name="ar_pending_idx")
    with schema_mutation("automation_run", label="widen ar_pending_idx") as m:
        m.replace_index(AutomationRun, original, mutated)
        m.assert_ddl_delta(missing=["WHERE"])
        print(
            "MUTATION-15 ASSERTION:",
            _detected(test_indexes.test_the_pending_run_index_predicate_is_still_partial),
        )
    assert partial_index_predicate("automation_run", "ar_pending_idx"), (
        "the partial predicate must be back after the guard restores the index"
    )
