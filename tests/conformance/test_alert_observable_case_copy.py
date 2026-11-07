"""P5 — escalation copies alert observables into the case and reconciles suggestions."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied

from alerts.escalation import import_alert_to_case, merge_alert_into_case
from alerts.models import AlertObservable
from cases.models import CaseObservable, TimelineEvent
from identity.models import Organisation
from observables.alert_sync import (
    copy_alert_observables_to_case,
    reconcile_suggestions_with_copied_observables,
)
from observables.models import ObservableSuggestion, ObservableType

pytestmark = pytest.mark.django_db


def _alert_with_observable(make_alert, make_observable, *, value: str = "copy@example.com"):
    alert = make_alert(
        title="Copy alert",
        type="phishing",
        source="mock",
        source_ref=f"ref-{value}",
        raw_payload={},
    )
    observable = make_observable(data_type="mail", data=value, normalized_data=value)
    AlertObservable.objects.create(alert=alert, observable=observable)
    return alert, observable


def _suggestion(alert, case, status: str, *, value: str = "copy@example.com"):
    return ObservableSuggestion.objects.create(
        owner_org=alert.owner_org,
        data_type=ObservableType.objects.get(name="mail"),
        normalized_data=value,
        raw_data=value,
        case=case,
        source_alert=alert,
        status=status,
    )


def test_merge_copies_links_dispatches_once_and_records_counts(
    make_alert, make_observable, make_case, make_user
) -> None:
    alert, observable = _alert_with_observable(make_alert, make_observable)
    case = make_case(title="Target case")
    user = make_user(role="analyst")

    with patch("automation.registry.dispatch_observable_linked") as dispatch:
        event = merge_alert_into_case(alert, case, actor=user)

    assert CaseObservable.objects.filter(case=case, observable=observable).exists()
    dispatch.assert_called_once_with(observable, case.pk)
    assert event.kind == "alert-merged"
    assert event.metadata["observables_copied"] == 1
    assert event.metadata["observables_already_present"] == 0
    assert event.metadata["suggestions_reconciled"] == 0


def test_merge_is_idempotent_for_copy_and_dispatch(
    make_alert, make_observable, make_case, make_user
) -> None:
    alert, observable = _alert_with_observable(make_alert, make_observable)
    case = make_case(title="Target case")
    user = make_user(role="analyst")

    with patch("automation.registry.dispatch_observable_linked") as dispatch:
        merge_alert_into_case(alert, case, actor=user)
        event = merge_alert_into_case(alert, case, actor=user)

    assert dispatch.call_count == 1
    assert CaseObservable.objects.filter(case=case, observable=observable).count() == 1
    assert event.metadata["observables_copied"] == 0
    assert event.metadata["observables_already_present"] == 1
    assert event.metadata["already_linked"] is True


def test_import_copies_links_and_records_counts(make_alert, make_observable, make_user) -> None:
    alert, observable = _alert_with_observable(make_alert, make_observable)
    user = make_user(role="analyst")

    with patch("automation.registry.dispatch_observable_linked") as dispatch:
        case, _warnings = import_alert_to_case(alert, actor=user)

    assert CaseObservable.objects.filter(case=case, observable=observable).exists()
    dispatch.assert_called_once_with(observable, case.pk)
    event = TimelineEvent.objects.get(case=case, kind="alert-imported")
    assert event.metadata["observables_copied"] == 1
    assert event.metadata["observables_already_present"] == 0


def test_copy_uses_the_alert_links_author_when_no_actor_is_given(
    make_alert, make_observable, make_case, make_user
) -> None:
    alert, observable = _alert_with_observable(make_alert, make_observable)
    case = make_case(title="Target case")
    author = make_user(role="analyst")
    AlertObservable.objects.filter(alert=alert, observable=observable).update(added_by=author)

    copy_alert_observables_to_case(alert, case)

    assert CaseObservable.objects.get(case=case, observable=observable).added_by == author


def test_copy_refuses_cross_org_links(make_alert, make_observable, make_case) -> None:
    alert, _observable = _alert_with_observable(make_alert, make_observable)
    other = Organisation.objects.create(name="Other org")
    case = make_case(title="Other case", org=other)

    with pytest.raises(PermissionDenied):
        copy_alert_observables_to_case(alert, case)


def test_reconcile_accepts_a_proposed_suggestion_for_a_copied_value(
    make_alert, make_observable, make_case, make_user
) -> None:
    alert, observable = _alert_with_observable(make_alert, make_observable)
    case = make_case(title="Target case")
    user = make_user(role="analyst")
    suggestion = _suggestion(alert, case, "proposed")
    copy_alert_observables_to_case(alert, case)

    reconciled = reconcile_suggestions_with_copied_observables(alert, case, actor=user)

    suggestion.refresh_from_db()
    assert reconciled == 1
    assert suggestion.status == "accepted"
    assert suggestion.observable == observable
    assert suggestion.reviewed_by == user
    assert suggestion.reviewed_at is not None


@pytest.mark.parametrize("status", ["rejected", "ignored"])
def test_reconcile_never_overrides_a_reviewer_decision(
    make_alert, make_observable, make_case, status: str
) -> None:
    alert, _observable = _alert_with_observable(make_alert, make_observable)
    case = make_case(title="Target case")
    suggestion = _suggestion(alert, case, status)
    copy_alert_observables_to_case(alert, case)

    reconciled = reconcile_suggestions_with_copied_observables(alert, case)

    suggestion.refresh_from_db()
    assert reconciled == 0
    assert suggestion.status == status
    assert suggestion.observable is None


def test_reconcile_requires_the_case_link_to_exist(make_alert, make_observable, make_case) -> None:
    alert, _observable = _alert_with_observable(make_alert, make_observable)
    case = make_case(title="Target case")
    suggestion = _suggestion(alert, case, "proposed")

    reconciled = reconcile_suggestions_with_copied_observables(alert, case)

    suggestion.refresh_from_db()
    assert reconciled == 0
    assert suggestion.status == "proposed"
