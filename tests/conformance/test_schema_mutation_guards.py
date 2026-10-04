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

import pytest
from django.db import connection

from tests.conformance import test_fk_audit, test_indexes, test_introspection, test_phase3_schema


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
    with connection.schema_editor() as editor:
        editor.add_index(Case, redundant)
    try:
        with pytest.raises(AssertionError) as excinfo:
            test_indexes.test_no_index_is_left_prefix_covered_by_a_wider_one()
        print("MUTATION-3 ASSERTION:", str(excinfo.value))
    finally:
        with connection.schema_editor() as editor:
            editor.remove_index(Case, redundant)


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
        with pytest.raises(AssertionError) as excinfo:
            test_phase3_schema.test_h6_user_requires_a_distinct_login()
        print("MUTATION-4 ASSERTION:", str(excinfo.value))
    finally:
        login.null, login.blank = original_null, original_blank
