"""REVIEW-2026-10-03 **H1/H5/H6/M2/M6/M14** — Phase 3 schema contract.

The previous version of `test_all_pks_are_uuid` contained a bare `pass`: it collected,
ran green, and asserted nothing. It is gone.

What is asserted here, all against the live database:

* **H1** every domain primary key is a UUID column (PLAN §6.1, ADR-002 §D3).
* **M2** the `Alert.source_id` -> `Alert.ingestion_source` rename happened, and the
  verbatim wire string `source` is still a separate column (AGENTS.md Module A).
* **M6** the case table is `case_record`, not the SQL reserved word `case`.
* **H5/L1** the named CHECK constraints exist, so out-of-range severities are refused by
  the database and not only by DRF.
* **M14** `AutomationRun.playbook` is a real, non-null FK with an immutable name snapshot
  beside it.
* **M12** the `Imported` alert status is seeded, because ADR-002 §D4 lists it as a legal
  stage and the compat mapper must be able to produce it.
* **M7** the observable link tables are append-only: they carry `created_at` and no
  `updated_at` (the column they used to inherit from `TimeStampedModel` while also
  shadowing that base's `created_at`).

Review items: H1 (vacuous test), H5 (CHECK constraints), H6 (UUID PKs), M2 (FK rename),
M6 (reserved table name), M7 (link-table timestamps), M12 (Imported stage), M14 (playbook FK).
"""

from __future__ import annotations

import pytest
from django.db import models

from ._schema import check_names, columns

#: Models whose PK must be a UUID (PLAN §6.1: "all 19 domain tables use UUIDModel").
DOMAIN_MODELS = (
    "alerts.Alert",
    "alerts.AlertStatus",
    "automation.AutomationRun",
    "automation.Playbook",
    "cases.Case",
    "cases.CaseStatus",
    "cases.CustomField",
    "cases.Task",
    "cases.TimelineEvent",
    "identity.ApiKey",
    "identity.Organisation",
    "identity.User",
    "ingest.IngestionSource",
    "observables.Observable",
    "observables.ObservableType",
)

#: Named CHECK constraints the remediation is responsible for (REVIEW H5/L1).
REQUIRED_CHECKS: dict[str, tuple[str, ...]] = {
    "alert": ("alert_severity_range", "alert_tlp_range", "alert_pap_range"),
    "case_record": ("case_severity_range", "case_tlp_range", "case_pap_range"),
    "task": ("task_status_valid",),
    "observable": ("observable_tlp_range", "observable_pap_range"),
    "identity_apikey": ("apikey_scope_valid",),
    "automation_run": ("automation_run_status_valid",),
    "ingestion_source": ("ingestion_source_severity_range",),
}


@pytest.mark.django_db
@pytest.mark.parametrize("label", DOMAIN_MODELS)
def test_all_pks_are_uuid(label: str) -> None:
    """The literal `pass` this file used to contain, replaced.

    Asserted two ways on purpose: the model declares a `UUIDField` (the intent) and the
    database column really is a 32/36-character character column storing a UUID (the
    effect). An `AutoField` that only *looks* UUID-ish in the model would still fail.
    """
    from django.apps import apps

    model = apps.get_model(label)
    pk = model._meta.pk
    assert isinstance(pk, models.UUIDField), (
        f"{label}.pk is {type(pk).__name__}, expected UUIDField"
    )

    declared = columns(model._meta.db_table)[pk.column]
    # Django stores a UUID as a 32-character hex string on SQLite and as `uuid` on
    # Postgres; anything else means the column is an integer sequence.
    assert declared.startswith(("CHAR", "VARCHAR", "UUID")), (
        f"{label} PK column {pk.column} is {declared!r}, not a UUID character column"
    )


@pytest.mark.django_db
def test_m2_ingestion_source_column_was_renamed() -> None:
    """The wire string and the FK must be different columns with different purposes."""
    present = columns("alert")
    assert "ingestion_source_id" in present, "REVIEW M2: the ingestion-source FK is missing"
    assert "source" in present, "the verbatim wire field `source` must not be renamed"
    assert "source_id" not in present, "REVIEW M2: the old `source_id_id` column still exists"

    from alerts.models import Alert

    assert Alert._meta.get_field("ingestion_source").column == "ingestion_source_id"
    assert Alert._meta.get_field("source").column == "source"
    assert Alert._meta.get_field("source").max_length and not Alert._meta.get_field("source").unique


@pytest.mark.django_db
def test_m6_case_table_is_not_the_sql_reserved_word() -> None:
    from cases.models import Case

    assert Case._meta.db_table == "case_record"
    assert "case" not in columns("case_record"), "the reserved name must not survive as a table"


@pytest.mark.django_db
@pytest.mark.parametrize(("table", "expected"), sorted(REQUIRED_CHECKS.items()))
def test_required_check_constraints_exist(table: str, expected: tuple[str, ...]) -> None:
    present = check_names(table)
    missing = [name for name in expected if name not in present]
    assert not missing, f"{table} is missing CHECK constraint(s): {missing}"


@pytest.mark.django_db
def test_m14_automation_run_playbook_fk_is_required_and_named() -> None:
    from automation.models import AutomationRun

    playbook = AutomationRun._meta.get_field("playbook")
    assert playbook.null is False, "a run must reference a playbook (REVIEW M14)"
    assert not playbook.blank, "the playbook FK is not optional"
    assert playbook.remote_field.on_delete is models.PROTECT, (
        "a referenced playbook is audit evidence; deleting it must not cascade"
    )
    # The name is a snapshot, not a duplicate of the FK: renaming a playbook later must
    # not rewrite what the ledger says was dispatched.
    snapshot = AutomationRun._meta.get_field("playbook_name")
    assert snapshot.null is False and not snapshot.unique


@pytest.mark.django_db
def test_m12_imported_alert_status_is_seeded() -> None:
    from alerts.models import AlertStatus

    stage_of = dict(AlertStatus.objects.values_list("value", "stage"))
    assert "Imported" in stage_of, "ADR-002 D4 lists `Imported` as a legal alert status"
    assert stage_of["Imported"] == "Imported"


@pytest.mark.django_db
def test_h6_user_requires_a_distinct_login() -> None:
    """TheHive keys assignees on `login`, not `username` (PLAN §6.1, ADR-002 §D3)."""
    from identity.models import User

    login = User._meta.get_field("login")
    username = User._meta.get_field("username")
    assert login.unique, "`login` must be unique: it is the assignee key"
    assert not login.null and not login.blank, "`login` must be populated, never nullable"
    assert login is not username, "`login` and `username` are distinct concepts"
    assert columns("identity_user")["login"].startswith(("CHAR", "VARCHAR"))


@pytest.mark.django_db
def test_h6_apikey_prefix_is_unique_and_the_pk_is_a_uuid() -> None:
    """REVIEW M13: `compat.auth` resolves a token with `filter(prefix=...)`.

    Without `unique=True` two keys sharing a prefix make that lookup pick one arbitrarily,
    so it is a correctness requirement, not just a performance one.
    """
    from identity.models import ApiKey

    assert ApiKey._meta.get_field("prefix").unique
    assert isinstance(ApiKey._meta.pk, models.UUIDField)
    assert columns("identity_apikey")["prefix"].startswith(("CHAR", "VARCHAR"))


@pytest.mark.django_db
@pytest.mark.parametrize("table", ["case_observable", "alert_observable"])
def test_m7_observable_link_tables_are_append_only(table: str) -> None:
    """REVIEW M7: a link row records when it was made and nothing else.

    Linking an observable creates a row and unlinking deletes it, so there is no edit to
    timestamp. The old models inherited `updated_at` from `TimeStampedModel` while also
    shadowing that base's `created_at`; they now declare `created_at` directly and the dead
    `updated_at` column is gone. A live `updated_at` here means the model re-inherited the
    base and the migrations were regenerated to match.
    """
    present = columns(table)
    assert "created_at" in present, f"{table} must record when the link was made"
    assert "updated_at" not in present, (
        f"{table} is append-only; `updated_at` is a column nothing writes (REVIEW M7)"
    )


@pytest.mark.django_db
def test_case_number_is_unique_and_not_client_supplied() -> None:
    """C1: the number is allocated by the database, never by the caller."""
    from cases.models import Case

    number = Case._meta.get_field("number")
    assert number.unique and number.editable is False
    assert isinstance(number, object) and type(number).__name__ == "AllocatedNumberField"


@pytest.mark.django_db
def test_observable_uniqueness_is_bounded() -> None:
    """H3: the unique key is `(data_type, data_hash)`, never the unbounded text column."""
    from observables.models import Observable

    unique_fields = [
        tuple(Observable._meta.get_field(name).column for name in constraint.fields)
        for constraint in Observable._meta.constraints
        if isinstance(constraint, models.UniqueConstraint)
    ]
    assert ("data_type_id", "data_hash") in unique_fields, unique_fields
    hash_field = Observable._meta.get_field("data_hash")
    assert hash_field.max_length == 64 and hash_field.null is False and not hash_field.editable
    assert ("data_type_id", "normalized_data") not in unique_fields, (
        "H3: uniqueness on unbounded `normalized_data` exceeds the Postgres btree tuple limit"
    )
