"""Quick test for P5 suggestion API endpoints."""

import pytest
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from observables.models import ObservableSuggestion, ObservableType


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Test Org")


@pytest.fixture
def user(db, org):
    return User.objects.create(
        login="analyst", username="analyst", email="a@b.com", org=org, is_active=True
    )


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def case_status(db):
    return CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 0}
    )[0]


@pytest.fixture
def case(db, org, case_status):
    return Case.objects.create(title="Test Case", status=case_status, owner_org=org)


@pytest.fixture
def mail_type(db):
    return ObservableType.objects.get(name="mail")


def test_suggestion_accept_creates_observable_and_link(db, api_client, case, mail_type):
    """Accept a suggestion creates Observable + CaseObservable and fires trigger."""
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="test@example.com",
        raw_data="test@example.com",
        case=case,
        source_paths=["description"],
        reason="Test suggestion",
        status="proposed",
    )

    url = f"/api/v1/suggestions/{suggestion.id}/accept/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 200, response.content

    suggestion.refresh_from_db()
    assert suggestion.status == "accepted"
    assert suggestion.observable is not None
    assert suggestion.observable.normalized_data == "test@example.com"

    # Check CaseObservable link was created
    from cases.models import CaseObservable

    link = CaseObservable.objects.filter(case=case, observable=suggestion.observable).first()
    assert link is not None


def test_suggestion_reject_no_link(db, api_client, case, mail_type):
    """Reject a suggestion marks reviewed but creates no link."""
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="test2@example.com",
        raw_data="test2@example.com",
        case=case,
        source_paths=["description"],
        reason="Test suggestion",
        status="proposed",
    )

    url = f"/api/v1/suggestions/{suggestion.id}/reject/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 200, response.content

    suggestion.refresh_from_db()
    assert suggestion.status == "rejected"
    assert suggestion.observable is None
    assert suggestion.reviewed_by is not None

    # No CaseObservable link should exist
    from cases.models import CaseObservable

    links = CaseObservable.objects.filter(case=case)
    assert links.count() == 0


def test_suggestion_ignore_no_link(db, api_client, case, mail_type):
    """Ignore a suggestion marks reviewed but creates no link."""
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="test3@example.com",
        raw_data="test3@example.com",
        case=case,
        source_paths=["description"],
        reason="Test suggestion",
        status="proposed",
    )

    url = f"/api/v1/suggestions/{suggestion.id}/ignore/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 200, response.content

    suggestion.refresh_from_db()
    assert suggestion.status == "ignored"
    assert suggestion.observable is None


def test_bulk_accept(db, api_client, case, mail_type):
    """Bulk accept multiple suggestions."""
    sugg1 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="bulk1@example.com",
        raw_data="bulk1@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )
    sugg2 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="bulk2@example.com",
        raw_data="bulk2@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )

    url = "/api/v1/suggestions/bulk-accept/"
    response = api_client.post(url, {"ids": [str(sugg1.id), str(sugg2.id)]}, format="json")
    assert response.status_code == 200, response.content
    results = response.json()
    assert len(results) == 2

    sugg1.refresh_from_db()
    sugg2.refresh_from_db()
    assert sugg1.status == "accepted"
    assert sugg2.status == "accepted"
    assert sugg1.observable is not None
    assert sugg2.observable is not None


def test_bulk_reject(db, api_client, case, mail_type):
    """Bulk reject multiple suggestions."""
    sugg1 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="bulk3@example.com",
        raw_data="bulk3@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )
    sugg2 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="bulk4@example.com",
        raw_data="bulk4@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )

    url = "/api/v1/suggestions/bulk-reject/"
    response = api_client.post(url, {"ids": [str(sugg1.id), str(sugg2.id)]}, format="json")
    assert response.status_code == 200, response.content
    results = response.json()
    assert len(results) == 2

    sugg1.refresh_from_db()
    sugg2.refresh_from_db()
    assert sugg1.status == "rejected"
    assert sugg2.status == "rejected"
    assert sugg1.observable is None
    assert sugg2.observable is None


def test_case_suggestions_list(db, api_client, case, mail_type):
    """List suggestions for a case."""
    ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="list1@example.com",
        raw_data="list1@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )
    ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="list2@example.com",
        raw_data="list2@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="accepted",
    )

    url = f"/api/v1/case/{case.id}/suggestions/"
    response = api_client.get(url)
    assert response.status_code == 200, response.content
    results = response.json()
    assert len(results) == 2

    # Filter by status
    response = api_client.get(url, {"status": "proposed"})
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["status"] == "proposed"


def test_alert_suggestions_list(db, api_client, org, mail_type):
    """List suggestions for an alert."""
    alert_status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 0}
    )
    alert = Alert.objects.create(
        title="Test Alert",
        source="test",
        type="test",
        source_ref="ref1",
        severity=2,
        status=alert_status,
        owner_org=org,
    )

    ObservableSuggestion.objects.create(
        owner_org=org,
        data_type=mail_type,
        normalized_data="alert1@example.com",
        raw_data="alert1@example.com",
        source_alert=alert,
        source_paths=["actor.email"],
        reason="Test",
        status="proposed",
    )

    url = f"/api/v1/alert/{alert.id}/suggestions/"
    response = api_client.get(url)
    assert response.status_code == 200, response.content
    results = response.json()
    assert len(results) == 1
    assert results[0]["sourceAlertId"] == str(alert.id)


def test_accept_idempotent(db, api_client, case, mail_type):
    """Accepting an already-accepted suggestion is idempotent."""
    from observables.models import Observable

    obs = Observable.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="idem@example.com",
        data="idem@example.com",
    )
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="idem@example.com",
        raw_data="idem@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="accepted",
        observable=obs,
    )

    url = f"/api/v1/suggestions/{suggestion.id}/accept/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 200, response.content


def test_reject_idempotent(db, api_client, case, mail_type):
    """Rejecting an already-rejected suggestion is idempotent."""
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="idem2@example.com",
        raw_data="idem2@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="rejected",
    )

    url = f"/api/v1/suggestions/{suggestion.id}/reject/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 200, response.content


def test_cannot_accept_rejected(db, api_client, case, mail_type):
    """Cannot accept a rejected suggestion."""
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="noaccept@example.com",
        raw_data="noaccept@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="rejected",
    )

    url = f"/api/v1/suggestions/{suggestion.id}/accept/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 400, response.content


def test_cannot_reject_accepted(db, api_client, case, mail_type):
    """Cannot reject an accepted suggestion."""
    from observables.models import Observable

    obs = Observable.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="noreject@example.com",
        data="noreject@example.com",
    )
    suggestion = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="noreject@example.com",
        raw_data="noreject@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="accepted",
        observable=obs,
    )

    url = f"/api/v1/suggestions/{suggestion.id}/reject/"
    response = api_client.post(url, {}, format="json")
    assert response.status_code == 400, response.content


def test_bulk_accept_dispatches_once_per_new_link(db, api_client, case, mail_type):
    """Bulk accept only dispatches trigger for new observables/links."""
    from unittest.mock import patch

    from observables.models import Observable

    # Create an existing observable
    existing_obs = Observable.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="existing@example.com",
        data="existing@example.com",
    )
    from cases.models import CaseObservable

    CaseObservable.objects.create(case=case, observable=existing_obs)

    # Suggestion for existing observable
    sugg1 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="existing@example.com",
        raw_data="existing@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )
    # Suggestion for new observable
    sugg2 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="new@example.com",
        raw_data="new@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )

    with patch("automation.registry.dispatch_observable_linked") as mock_dispatch:
        url = "/api/v1/suggestions/bulk-accept/"
        response = api_client.post(url, {"ids": [str(sugg1.id), str(sugg2.id)]}, format="json")
        assert response.status_code == 200

        # Should only dispatch once (for the new observable/link)
        assert mock_dispatch.call_count == 1


def test_bulk_reject_no_dispatch(db, api_client, case, mail_type):
    """Bulk reject does not dispatch any triggers."""
    from unittest.mock import patch

    sugg1 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="br1@example.com",
        raw_data="br1@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )
    sugg2 = ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data="br2@example.com",
        raw_data="br2@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )

    with patch("automation.registry.dispatch_observable_linked") as mock_dispatch:
        url = "/api/v1/suggestions/bulk-reject/"
        response = api_client.post(url, {"ids": [str(sugg1.id), str(sugg2.id)]}, format="json")
        assert response.status_code == 200

        # Should not dispatch any triggers
        assert mock_dispatch.call_count == 0


def test_permissions_case_scope(db, org, mail_type):
    """Suggestions are scoped to case's org - other org cannot access."""
    from rest_framework.test import APIClient

    from identity.models import User

    # Create two orgs and users
    org1 = org
    org2 = Organisation.objects.create(name="Other Org")
    user1 = User.objects.create(
        login="u1", username="u1", email="u1@test.com", org=org1, is_active=True
    )
    user2 = User.objects.create(
        login="u2", username="u2", email="u2@test.com", org=org2, is_active=True
    )

    status, _ = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 0}
    )
    case = Case.objects.create(title="Test Case", status=status, owner_org=org1)

    suggestion = ObservableSuggestion.objects.create(
        owner_org=org1,
        data_type=mail_type,
        normalized_data="perm@example.com",
        raw_data="perm@example.com",
        case=case,
        source_paths=["description"],
        reason="Test",
        status="proposed",
    )

    # User from org1 can access
    client1 = APIClient()
    client1.force_authenticate(user=user1)
    response = client1.post(f"/api/v1/suggestions/{suggestion.id}/accept/", {}, format="json")
    assert response.status_code == 200

    # Reset suggestion
    suggestion.refresh_from_db()
    suggestion.status = "proposed"
    suggestion.observable = None
    suggestion.reviewed_by = None
    suggestion.reviewed_at = None
    suggestion.save()

    # User from org2 cannot access (404, not 403 - no existence oracle)
    client2 = APIClient()
    client2.force_authenticate(user=user2)
    response = client2.post(f"/api/v1/suggestions/{suggestion.id}/accept/", {}, format="json")
    assert response.status_code == 404


def test_find_observables_maps_provenance_when_only_payload_is_supplied(db, mail_type):
    """Payload-only extraction must still attribute the right JSON path (offset regression)."""
    from observables.extractor import find_observables, json_paths_and_values

    paths = json_paths_and_values({"user": {"email": "payloadonly@example.com"}})
    findings = find_observables(types={"mail": mail_type}, path_values=paths)

    finding = next(f for f in findings if f.normalized == "payloadonly@example.com")
    assert finding.source_paths == ["user.email"]


def test_find_observables_maps_provenance_after_free_text(db, mail_type):
    from observables.extractor import find_observables, json_paths_and_values

    paths = json_paths_and_values({"device": {"external_ip": "203.0.113.9"}})
    # `types` keys are type names; look up the real vocabulary row for `ip`.
    ip_type = ObservableType.objects.get(name="ip")
    findings = find_observables("Case narrative", types={"ip": ip_type}, path_values=paths)

    finding = next(f for f in findings if f.normalized == "203.0.113.9")
    assert finding.source_paths == ["device.external_ip"]
