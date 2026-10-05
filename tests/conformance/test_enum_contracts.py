"""REVIEW-2026-10-03 **H5/L1** — enum domains are enforced by the database, not just DRF.

`choices=` is a *validation* aid: it populates the DRF serializer's allow-list and
`full_clean`, so an out-of-range severity returns 400 through the API. It is **not**
enforced by the database, so a raw write, a bulk update, a fixture load or any future
service that skips the serializer can store `severity = 99`. PLAN §6.1 says the domain is
1..4; these tests prove the database refuses anything else.

Both halves are tested for every field: `full_clean` raising `ValidationError` (the API
path) *and* the INSERT raising `IntegrityError` (the truth), because either alone leaves a
hole.

REVIEW-2026-10-04 round-2 **H-2** is why this file is parametrised over *every*
`CHOICE_FIELDS` entry rather than a hand-picked few. Six constraints —
`case_status_stage_valid`, `alert_status_stage_valid`, `alert_tlp_range`, `alert_pap_range`,
`observable_pap_range`, `ingestion_source_severity_range` — were name-only: replacing each
with a vacuous same-named CHECK (`stage <> ''`) and regenerating the migration left the
suite green, because no test ever wrote an out-of-range value through *that* constraint.
`GRADED_DOMAIN` below closes it: every constraint in `CHOICE_FIELDS` gets a row to mutate
and a value that must be refused.

Review items: H5 (CHECK constraints), L1 (choices without constraints), round-2 H-2
(name-only CHECKs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from alerts.models import Alert, AlertStatus
from automation.models import AutomationRun, Playbook
from cases.models import Case, CaseStatus, CustomField, Task
from ingest.models import IngestionSource
from observables.models import Observable, ObservableType

#: The domain of every graded enum, as PLAN §6.1 defines it. `TLP` carries an extra
#: `Unknown` step (0..4); `PAP` is a plain 0..3 four-step scale; severity is 1..4.
DOMAINS: dict[str, tuple[int, ...]] = {
    "severity": (1, 2, 3, 4),
    "tlp": (0, 1, 2, 3, 4),
    "pap": (0, 1, 2, 3),
}
#: Values every one of those domains must refuse.
OUT_OF_RANGE = (-1, 5, 99)

#: Every `choices` field in the schema, with the CHECK constraint that backs it. Adding a
#: graded field without a constraint therefore fails this test rather than shipping a hole.
CHOICE_FIELDS: dict[tuple[str, str], str] = {
    ("alert", "severity"): "alert_severity_range",
    ("alert", "tlp"): "alert_tlp_range",
    ("alert", "pap"): "alert_pap_range",
    ("alert_status", "stage"): "alert_status_stage_valid",
    ("automation_run", "status"): "automation_run_status_valid",
    ("case_record", "severity"): "case_severity_range",
    ("case_record", "tlp"): "case_tlp_range",
    ("case_record", "pap"): "case_pap_range",
    ("case_status", "stage"): "case_status_stage_valid",
    ("custom_field", "type"): "custom_field_type_valid",
    ("identity_apikey", "scope"): "apikey_scope_valid",
    ("ingestion_source", "default_severity"): "ingestion_source_severity_range",
    ("observable", "tlp"): "observable_tlp_range",
    ("observable", "pap"): "observable_pap_range",
    ("task", "status"): "task_status_valid",
}


@pytest.fixture
def case(new_case_status: CaseStatus) -> Case:
    return Case.objects.create(title="enum probe", status=new_case_status)


@pytest.fixture
def new_case_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def alert_status() -> AlertStatus:
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def hash_type() -> ObservableType:
    return ObservableType.objects.get_or_create(name="hash", defaults={"is_case_sensitive": False})[
        0
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(("field", "value"), [(f, v) for f, vs in DOMAINS.items() for v in vs])
def test_valid_enum_values_are_accepted(case: Case, field: str, value: int) -> None:
    setattr(case, field, value)
    case.full_clean(exclude=["case", "start_date", "end_date", "closed_date", "number"])
    case.save()
    assert getattr(case, field) == value


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["severity", "tlp", "pap"])
@pytest.mark.parametrize("value", OUT_OF_RANGE)
def test_out_of_range_enum_values_fail_validation(case: Case, field: str, value: int) -> None:
    setattr(case, field, value)
    with pytest.raises(ValidationError):
        case.full_clean(exclude=["case", "start_date", "end_date", "closed_date", "number"])


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["severity", "tlp", "pap"])
@pytest.mark.parametrize("value", OUT_OF_RANGE)
def test_out_of_range_enum_values_fail_in_the_database(case: Case, field: str, value: int) -> None:
    """The half `choices=` cannot give us: `choices` alone would let this through."""
    with pytest.raises(IntegrityError), transaction.atomic():
        type(case).objects.filter(pk=case.pk).update(**{field: value})


@pytest.mark.django_db
@pytest.mark.parametrize("value", OUT_OF_RANGE)
def test_alert_severity_range_is_enforced(alert_status: AlertStatus, value: int) -> None:
    alert = Alert.objects.create(
        type="t", source="s", source_ref=f"ref-{value}", title="a", status=alert_status
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        type(alert).objects.filter(pk=alert.pk).update(severity=value)


@pytest.mark.django_db
@pytest.mark.parametrize("value", OUT_OF_RANGE)
def test_observable_tlp_range_is_enforced(hash_type: ObservableType, value: int) -> None:
    observable = Observable.objects.create(
        data_type=hash_type, data="d41d8cd98f00b204", normalized_data="d41d8cd98f00b204"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        type(observable).objects.filter(pk=observable.pk).update(tlp=value)


@pytest.mark.django_db
def test_task_status_domain_is_enforced(case: Case) -> None:
    task = Task.objects.create(case=case, title="t")
    with pytest.raises(IntegrityError), transaction.atomic():
        type(task).objects.filter(pk=task.pk).update(status="NotAStatus")


@pytest.mark.django_db
def test_automation_run_status_domain_is_enforced(case: Case) -> None:
    playbook = Playbook.objects.create(name="enrich", trigger_event="alert.created")
    run = AutomationRun.objects.create(
        case=case, playbook=playbook, playbook_name="enrich", trigger_event="alert.created"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        type(run).objects.filter(pk=run.pk).update(status="AlmostDone")


@pytest.mark.django_db
def test_apikey_scope_domain_is_enforced() -> None:
    from identity.models import ApiKey, Organisation, User

    org = Organisation.objects.create(name="acme")
    user = User.objects.create(username="alice", org=org)
    key = ApiKey.objects.create(user=user, prefix="abcd1234", key_hash="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        type(key).objects.filter(pk=key.pk).update(scope="root")


@pytest.mark.django_db
def test_custom_field_type_domain_is_enforced(case: Case) -> None:
    field = CustomField.objects.create(name="Impact", type="string")
    with pytest.raises(ValidationError):
        CustomField(name="Bad", type="enum").full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        type(field).objects.filter(pk=field.pk).update(type="not-a-type")


@pytest.mark.django_db
def test_every_choices_field_also_has_a_check_constraint() -> None:
    """H5's structural guarantee: a `choices` field with no CHECK is a hole waiting.

    The comparison is against the schema as it actually is, so adding a graded field
    without a constraint fails here — the test cannot be satisfied by editing a list.
    """
    from django.db import models

    from ._schema import check_names, own_models

    actual = {
        (model._meta.db_table, field.name)
        for _, model in own_models()
        for field in model._meta.fields
        if getattr(field, "choices", None) and not isinstance(field, models.ForeignKey)
    }
    assert actual == set(CHOICE_FIELDS), (
        "the set of `choices` fields changed; every one needs a CHECK constraint and an "
        f"entry in CHOICE_FIELDS. Missing: {sorted(actual - set(CHOICE_FIELDS))}, "
        f"stale: {sorted(set(CHOICE_FIELDS) - actual)}"
    )
    for (table, _field), constraint in CHOICE_FIELDS.items():
        assert constraint in check_names(table), (
            f"{table}: the CHECK constraint backing the choices field is called "
            f"{constraint!r} but the database reports {sorted(check_names(table))}"
        )


# ---------------------------------------------------------------------------------------
# Round-2 H-2 — every CHECK-backed field gets a value the database must actually refuse.
#
# One entry per `CHOICE_FIELDS` constraint, and the test below iterates the table rather than
# a subset, so "replace this CHECK with a vacuous same-named one" cannot survive. The value
# has to be impossible under the real domain: for a smallint range that is far out of it,
# and for a `stage` vocabulary it is a name outside `CASE_STAGES`/`ALERT_STAGES`.
# ---------------------------------------------------------------------------------------

#: `db_table -> builder producing one saved row with in-range values`. Declared after the
#: builders so the `task` row can reuse the `case_record` one.
_SAMPLE_ROW: dict[str, Callable[[], Any]]


def _case_status() -> Any:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


def _alert_status() -> Any:
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


def _alert() -> Alert:
    return Alert.objects.create(
        type="t", source="s", source_ref="enum-probe", title="a", status=_alert_status()
    )


_SAMPLE_ROW = {
    "case_record": lambda: Case.objects.create(title="enum probe", status=_case_status()),
    "case_status": _case_status,
    "alert": _alert,
    "alert_status": _alert_status,
    "observable": lambda: Observable.objects.create(
        data_type=ObservableType.objects.get_or_create(
            name="other", defaults={"is_case_sensitive": False}
        )[0],
        data="enum-probe",
        normalized_data="enum-probe",
    ),
    "task": lambda: Task.objects.create(case=_SAMPLE_ROW["case_record"](), title="t"),
    "automation_run": lambda: AutomationRun.objects.create(
        playbook=Playbook.objects.create(name="enum-probe", trigger_event="alert.created"),
        playbook_name="enum-probe",
        idempotency_key="enum-probe",
    ),
    "ingestion_source": lambda: IngestionSource.objects.create(
        slug="enum-probe", name="enum probe"
    ),
    "custom_field": lambda: CustomField.objects.create(name="enum-probe", type="string"),
    "identity_apikey": lambda: _apikey(),
}


def _apikey() -> Any:
    from identity.models import ApiKey, User

    return ApiKey.objects.create(
        user=User.objects.create(username="enum-probe"), prefix="enumprobe", key_hash="x"
    )


#: `constraint name -> (row factory, field name, values the domain must refuse)`.
GRADED_DOMAIN: dict[str, tuple[str, str, tuple[Any, ...]]] = {
    "alert_severity_range": ("alert", "severity", (-1, 5, 99)),
    "alert_tlp_range": ("alert", "tlp", (-1, 5, 99)),
    "alert_pap_range": ("alert", "pap", (-1, 4, 99)),
    "alert_status_stage_valid": ("alert_status", "stage", ("inprogres", "")),
    "case_severity_range": ("case_record", "severity", (-1, 5, 99)),
    "case_tlp_range": ("case_record", "tlp", (-1, 5, 99)),
    "case_pap_range": ("case_record", "pap", (-1, 4, 99)),
    "case_status_stage_valid": ("case_status", "stage", ("inprogres", "")),
    "custom_field_type_valid": ("custom_field", "type", ("enum", "")),
    "apikey_scope_valid": ("identity_apikey", "scope", ("root", "")),
    "ingestion_source_severity_range": ("ingestion_source", "default_severity", (-1, 5, 99)),
    "observable_tlp_range": ("observable", "tlp", (-1, 5, 99)),
    "observable_pap_range": ("observable", "pap", (-1, 4, 99)),
    "task_status_valid": ("task", "status", ("AlmostDone", "")),
    "automation_run_status_valid": ("automation_run", "status", ("AlmostDone", "")),
}


@pytest.mark.django_db
@pytest.mark.parametrize(("constraint", "spec"), sorted(GRADED_DOMAIN.items()))
def test_h2_every_checked_enum_domain_is_refused_by_the_database(
    constraint: str, spec: tuple[str, str, tuple[Any, ...]]
) -> None:
    """Round-2 H-2: no CHECK may be name-only.

    Parametrised over all fifteen, so neutering any one of them — including the six the
    review found were name-only — fails here rather than in production.
    """
    table, field, bad_values = spec
    row = _SAMPLE_ROW[table]()
    # Everything in the block is provisional. If the database *fails* to refuse a value the
    # write succeeds, and an out-of-range row must not survive into the next test — or into
    # a mutation guard's schema restore, which would then fail with an unrelated
    # `CHECK constraint failed` and mask the defect the guard is demonstrating.
    with transaction.atomic():
        for bad in bad_values:
            with pytest.raises(IntegrityError), transaction.atomic():
                type(row).objects.filter(pk=row.pk).update(**{field: bad})
        transaction.set_rollback(True)
    row.refresh_from_db()
    assert getattr(row, field) not in bad_values, (
        f"{constraint}: an out-of-range {table}.{field} was accepted and stored"
    )


def test_h2_the_domain_table_covers_every_check_constraint() -> None:
    """Guards the guard: a new CHECK must arrive with a behavioural test, or not at all."""
    assert set(GRADED_DOMAIN) == set(CHOICE_FIELDS.values()), (
        "every CHECK-backed choices field needs a GRADED_DOMAIN entry: "
        f"missing={sorted(set(CHOICE_FIELDS.values()) - set(GRADED_DOMAIN))}, "
        f"stale={sorted(set(GRADED_DOMAIN) - set(CHOICE_FIELDS.values()))}"
    )
