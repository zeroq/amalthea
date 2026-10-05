"""Uniqueness guarantees, exercised through the real write paths.

The review noted that `test_duplicate_source_ref_raises` built its `Case` with an explicit
`number=1`. That made the test pass while the *only* production path for numbering —
auto-allocation — was never executed anywhere in the suite, which is why REVIEW **C1**
(`CREATE SEQUENCE` executed from `Case.save()`, invalid on SQLite) survived a green run.
The case is now created the way the application creates it.

The observable case is the one REVIEW **H3** rewrote: uniqueness moved from the unbounded
`normalized_data` text to the 64-character `data_hash` digest.

REVIEW-2026-10-04 round-2 **H-3**/**H-4** are the two UNIQUE keys nobody exercised:
`AutomationRun.idempotency_key` and `IngestionSource.slug`. Deleting either `unique=True`
and regenerating the migration left the suite green.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from observables.models import Observable, ObservableType


@pytest.fixture
def case() -> Case:
    """A case created the way the application creates it: no explicit number."""
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title="duplicate-ref fixture", status=status)


@pytest.fixture
def alert_status() -> AlertStatus:
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.mark.django_db
def test_duplicate_source_ref_raises(alert_status: AlertStatus) -> None:
    Alert.objects.create(type="t", source="s", source_ref="ref1", title="a1", status=alert_status)
    with pytest.raises(IntegrityError), transaction.atomic():
        Alert.objects.create(
            type="t", source="s", source_ref="ref1", title="a2", status=alert_status
        )


@pytest.mark.django_db
def test_the_same_ref_from_another_source_type_is_allowed(alert_status: AlertStatus) -> None:
    """The unique key is the triple `(source, type, source_ref)` — REVIEW C3."""
    Alert.objects.create(type="t", source="s", source_ref="ref1", title="a1", status=alert_status)
    other_source = Alert.objects.create(
        type="t", source="s2", source_ref="ref1", title="a2", status=alert_status
    )
    other_type = Alert.objects.create(
        type="t2", source="s", source_ref="ref1", title="a3", status=alert_status
    )
    assert other_source.pk and other_type.pk


@pytest.mark.django_db
def test_duplicate_observable_raises() -> None:
    observable_type = ObservableType.objects.get_or_create(
        name="ip", defaults={"is_case_sensitive": False}
    )[0]
    Observable.objects.create(data_type=observable_type, data="1.1.1.1", normalized_data="1.1.1.1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Observable.objects.create(
            data_type=observable_type, data="1.1.1.1", normalized_data="1.1.1.1"
        )


@pytest.mark.django_db
def test_duplicate_custom_field_value_raises(case: Case) -> None:
    """REVIEW M4: one value per (case, custom_field)."""
    from cases.models import CaseCustomFieldValue, CustomField

    custom_field = CustomField.objects.create(name="Impact", type="string")
    CaseCustomFieldValue.objects.create(case=case, custom_field=custom_field, value="High")
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseCustomFieldValue.objects.create(case=case, custom_field=custom_field, value="High")


@pytest.mark.django_db
def test_duplicate_case_observable_link_raises(case: Case) -> None:
    """AGENTS.md Module C: an observable is sighted once per case."""
    from cases.models import CaseObservable

    observable_type = ObservableType.objects.get_or_create(name="ip")[0]
    observable = Observable.objects.create(
        data_type=observable_type, data="1.1.1.1", normalized_data="1.1.1.1"
    )
    CaseObservable.objects.create(case=case, observable=observable)
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseObservable.objects.create(case=case, observable=observable)


@pytest.mark.django_db
def test_h3_automation_run_idempotency_key_is_unique(case: Case) -> None:
    """REVIEW-2026-10-04 round-2 **H-3**: the Module D redelivery guarantee.

    `idempotency_key` unique=True removed + migration regenerated: the whole suite passed.
    That column is the *only* thing standing between a Celery redelivery and a second
    execution of the same playbook — AGENTS.md Module D's feedback loop is idempotent
    because of this constraint, not because of application code.

    The DDL is asserted as well as the behaviour, so a migration that dropped the UNIQUE
    without a model change is still caught.
    """
    from automation.models import AutomationRun, Playbook

    from ._schema import unique_keys

    playbook = Playbook.objects.create(name="h3-enrich", trigger_event="alert.created")
    # Rolled back unconditionally: when the database *fails* to refuse the duplicate the second
    # INSERT succeeds, and a guard that detaches the UNIQUE must still be able to rebuild the
    # table afterwards. A committed duplicate would turn the guard's own restore into an
    # unrelated `UNIQUE constraint failed` and hide the defect it was meant to show.
    with transaction.atomic():
        AutomationRun.objects.create(
            case=case, playbook=playbook, playbook_name="h3-enrich", idempotency_key="case42:vt"
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            AutomationRun.objects.create(
                case=case,
                playbook=playbook,
                playbook_name="h3-enrich",
                idempotency_key="case42:vt",
            )
        transaction.set_rollback(True)
    assert ("idempotency_key",) in unique_keys("automation_run"), (
        "the behaviour above passed only because the ORM sent the same key twice — the "
        "database must reject it independently"
    )


@pytest.mark.django_db
def test_h4_ingestion_source_slug_is_unique() -> None:
    """REVIEW-2026-10-04 round-2 **H-4**: `slug` is the webhook's route key.

    `POST /api/v1/alerts/webhook/{source_id}` resolves the source by slug, so two sources
    sharing one would make the route non-deterministic: two different mapping configs
    reachable at the same URL. The endpoint itself is Phase 4 work, but the key it will
    look up is enforced now.
    """
    from ingest.models import IngestionSource

    from ._schema import unique_keys

    with transaction.atomic():
        IngestionSource.objects.create(slug="splunk", name="Splunk")
        with pytest.raises(IntegrityError), transaction.atomic():
            IngestionSource.objects.create(slug="splunk", name="Splunk (copy)")
        transaction.set_rollback(True)
    assert ("slug",) in unique_keys("ingestion_source")
    assert not IngestionSource.objects.filter(slug="splunk").exists(), (
        "the rolled-back block left a row behind"
    )
