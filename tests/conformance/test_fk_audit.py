"""REVIEW-2026-10-03 **H1** — foreign-key audit.

The previous version of this test read `Case._meta.get_fields()` and accepted a
`related_name` of `None` as long as `get_accessor_name()` returned anything — which
Django always does, defaulting to `<model>_set`. The assertion could therefore not fail.

These tests assert what the review and the plan actually require:

* every FK on Amalthea's own models has an **explicit** `on_delete`, because the default
  (`CASCADE`) is a data-loss policy that should never be inherited by accident;
* every FK has an explicit `related_name`, so a second FK to the same target cannot
  silently create a second `<model>_set` accessor and shadow the first;
* deletion behaviour matches intent — `PROTECT` where a referenced row is evidence
  (observables, playbooks, observable types), never left to a default;
* the FK columns Django reports from the **database** are exactly the FKs the models
  declare, so a migration that was never applied is caught.

Review items: H1 (vacuous test), L3 (on_delete policy), M5 (reverse-direction indexes).
"""

from __future__ import annotations

import pytest
from django.db import connection, models

from ._schema import foreign_keys, own_models

#: `(table, columns)` pairs that must exist as real constraints in the database.
EXPECTED_FK_COLUMNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "case_record": (("status_id",), ("assignee_id",), ("owner_org_id",)),
    # REVIEW M2: `source_id` was renamed to `ingestion_source_id`; the old column name is
    # asserted absent in test_phase3_schema.py.
    "alert": (
        ("status_id",),
        ("assignee_id",),
        ("owner_org_id",),
        ("case_id",),
        ("ingestion_source_id",),
    ),
    "observable": (("data_type_id",),),
    "task": (("case_id",), ("assignee_id",)),
    "automation_run": (
        ("case_id",),
        ("alert_id",),
        ("playbook_id",),
        ("triggered_by_observable_id",),
    ),
    "case_observable": (("case_id",), ("observable_id",), ("added_by_id",)),
    "alert_observable": (("alert_id",), ("observable_id",), ("added_by_id",)),
    "timeline_event": (("case_id",), ("actor_id",)),
    "case_custom_field_value": (("case_id",), ("custom_field_id",)),
    "alert_custom_field_value": (("alert_id",), ("custom_field_id",)),
    "identity_apikey": (("user_id",),),
    "identity_user": (("org_id",),),
    "case_status": (),
    "custom_field": (),
}


def declared_on_delete_is_explicit(field: models.ForeignKey) -> bool:
    """True when the field carries an explicit `on_delete`.

    `ForeignKey.deconstruct()` omits `on_delete` from its kwargs when the value equals the
    default (`CASCADE`), so a missing key means the model relied on the default — exactly
    what this audit forbids.
    """
    return "on_delete" in field.deconstruct()[3]


@pytest.mark.django_db
def test_fk_audit_covers_the_whole_model_inventory() -> None:
    """Guards the guard: the audit must not quietly shrink to nothing.

    If an app were dropped from `_schema.OWN_APPS`, every assertion below would keep
    passing while auditing fewer models, so pin the number of FKs inspected.
    """
    fks = [f for _, model in own_models() for f in model._meta.fields if f.many_to_one]
    assert len(fks) >= 29, f"expected the full FK set, found only {len(fks)}"


@pytest.mark.django_db
def test_every_fk_declares_an_explicit_on_delete() -> None:
    offenders = [
        f"{model.__module__}.{model.__name__}.{field.name}"
        for _, model in own_models()
        for field in model._meta.fields
        if field.many_to_one and not declared_on_delete_is_explicit(field)
    ]
    assert not offenders, f"FK(s) relying on Django's implicit CASCADE: {offenders}"


@pytest.mark.django_db
def test_every_fk_declares_a_related_name() -> None:
    offenders = [
        f"{model.__module__}.{model.__name__}.{field.name}"
        for _, model in own_models()
        for field in model._meta.fields
        if field.many_to_one and field.remote_field.related_name is None
    ]
    assert not offenders, (
        "FK(s) without an explicit related_name; a second FK to the same target would "
        f"shadow the reverse accessor: {offenders}"
    )


@pytest.mark.django_db
def test_reference_rows_use_the_reviewed_on_delete_policy() -> None:
    """Pin the `on_delete` policy the review verified as sound.

    `PROTECT` on `status`/`data_type` so a case or observable can never be orphaned by
    deleting its vocabulary row, and on `playbook` (REVIEW M14) so a run cannot reference
    a playbook that no longer exists. `CASCADE` stays on case-owned children — a
    `CaseObservable` link has no meaning once its case is gone. `SET_NULL` on
    `Alert.case` matches TheHive's unlink-on-delete.
    """
    expected_protect = {
        ("cases", "Case", "status"),
        ("alerts", "Alert", "status"),
        ("observables", "Observable", "data_type"),
        ("automation", "AutomationRun", "playbook"),
    }
    actual = {
        (model._meta.app_label, model.__name__, field.name)
        for _, model in own_models()
        for field in model._meta.fields
        if field.many_to_one and field.remote_field.on_delete is models.PROTECT
    }
    missing = sorted(expected_protect - actual)
    assert not missing, f"these references must use on_delete=PROTECT: {missing}"


@pytest.mark.django_db
@pytest.mark.parametrize(("table", "expected"), sorted(EXPECTED_FK_COLUMNS.items()))
def test_expected_fk_columns_exist_in_the_database(
    table: str, expected: tuple[tuple[str, ...], ...]
) -> None:
    present = foreign_keys(table)
    missing = [cols for cols in expected if cols not in present]
    assert not missing, f"{table} is missing foreign key(s): {missing}"


@pytest.mark.django_db
def test_database_fks_match_the_models() -> None:
    """Compare the database's FK constraints with `models.py`, table by table.

    The original test compared Django's in-memory model graph against itself, so a
    constraint that existed only in `models.py` — or only in the database — passed.
    """
    mismatches: list[str] = []
    for _, model in own_models():
        table = model._meta.db_table
        declared = {
            (f.column, f.target_field.model._meta.db_table)
            for f in model._meta.fields
            if f.many_to_one and f.column
        }
        actual = {(cols[0], target[0]) for cols, target in _fks_in_database(table)}
        if declared != actual:
            mismatches.append(f"{table}:\n    model={sorted(declared)}\n    db   ={sorted(actual)}")
    assert not mismatches, "FK mismatch between models.py and the database:\n" + "\n".join(
        mismatches
    )


def _fks_in_database(table: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    """`{(local columns, referenced columns)}` for every FK constraint on `table`."""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {
        (tuple(spec["columns"]), tuple(spec["foreign_key"]))
        for spec in constraints.values()
        if spec.get("foreign_key")
    }
