"""Conformance tests for PLAN-2026-10-17 §12.8 — case verdict + close flow.

Covers the five parts of §12.8 and the two prerequisite defect fixes (decision D4):

- Close rule (decision D1): a case may not enter Closed without a non-`undefined` verdict
  and a non-empty closing comment, enforced on the single PATCH, on `PATCH /case/_bulk/`,
  and on create-to-Closed.
- Verdict fields (decision D3): `Case.verdict` / `Alert.verdict` default to `undefined`,
  reject out-of-vocabulary at the DB, and are exposed in `case_json`/`alert_json`.
- Propagation (decision D2/D5): closing a case closes every linked alert (including
  `Imported` ones), copies the verdict, and writes one audit row + one timeline event.
- Defect D4a: `closed_date` is persisted on close and cleared on reopen.
- Defect D4b: classification/verdict/severity audit rows use `target_type="Case"` so the
  case Audit tab can see them.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from cases.update import CaseUpdateError, apply_case_update
from core.enums import ADMIN_ROLE, VIEWER_ROLE
from identity.models import AuditLog, Organisation, User

# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture
def default_org() -> Organisation:
    return Organisation.objects.get_or_create(name="default")[0]


@pytest.fixture
def admin_user(default_org: Organisation) -> User:
    return User.objects.get_or_create(
        login="admin",
        defaults={"org": default_org, "role": ADMIN_ROLE, "is_active": True},
    )[0]


@pytest.fixture
def viewer_user(default_org: Organisation) -> User:
    return User.objects.get_or_create(
        login="viewer",
        defaults={"org": default_org, "role": VIEWER_ROLE, "is_active": True},
    )[0]


@pytest.fixture
def open_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]


@pytest.fixture
def closed_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="Closed", defaults={"stage": "Closed", "order": 2}
    )[0]


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _auth(api_client: APIClient, user: User) -> None:
    api_client.force_authenticate(user=user)


def _case(org: Organisation, status_obj: CaseStatus, **kwargs: Any) -> Case:
    return Case.objects.create(title="Verdict probe", status=status_obj, owner_org=org, **kwargs)


def _alert(
    org: Organisation, *, ref: str, case: Case | None = None, value: str = "New", stage: str = "New"
) -> Alert:
    alert_status = AlertStatus.objects.get_or_create(
        value=value, defaults={"stage": stage, "order": 1}
    )[0]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=ref,
        title="Linked alert",
        status=alert_status,
        case=case,
        owner_org=org,
    )


# ---------------------------------------------------------------------------------------
# Verdict defaults + vocabulary + API exposure (G1)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_case_verdict_defaults_to_undefined(
    default_org: Organisation, open_status: CaseStatus
) -> None:
    case = _case(default_org, open_status)
    assert case.verdict == "undefined"


@pytest.mark.django_db
def test_alert_verdict_defaults_to_undefined(default_org: Organisation) -> None:
    alert = _alert(default_org, ref="v-default")
    assert alert.verdict == "undefined"


@pytest.mark.django_db
def test_case_json_and_alert_json_expose_verdict(
    default_org: Organisation, open_status: CaseStatus
) -> None:
    from core.serializers import alert_json, case_json

    case = _case(default_org, open_status, verdict="true_positive")
    assert case_json(case)["verdict"] == "true_positive"

    alert = _alert(default_org, ref="v-json")
    alert.verdict = "false_positive"
    alert.save(update_fields=["verdict"])
    assert alert_json(alert)["verdict"] == "false_positive"


@pytest.mark.django_db
def test_api_create_rejects_out_of_vocabulary_verdict(
    api_client: APIClient, admin_user: User
) -> None:
    _auth(api_client, admin_user)
    resp = api_client.post(
        "/api/v1/case/", {"title": "Bad verdict", "verdict": "bogus"}, format="json"
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "verdict" in resp.data.get("fields", {})


# ---------------------------------------------------------------------------------------
# Close rule on the single PATCH (decision D1)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_close_without_verdict_is_refused(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        f"/api/v1/case/{case.id}/", {"status": "Closed", "comment": "done"}, format="json"
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "verdict" in resp.data.get("fields", {})
    case.refresh_from_db()
    assert case.status.stage == "InProgress"
    assert case.closed_date is None


@pytest.mark.django_db
def test_api_close_with_undefined_verdict_is_refused(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "undefined", "comment": "done"},
        format="json",
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "verdict" in resp.data.get("fields", {})


@pytest.mark.django_db
def test_api_close_without_comment_is_refused(
    api_client: APIClient,
    admin_user: User,
    default_org: Organisation,
    open_status: CaseStatus,
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        f"/api/v1/case/{case.id}/", {"status": "Closed", "verdict": "true_positive"}, format="json"
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "comment" in resp.data.get("fields", {})


@pytest.mark.django_db
def test_api_close_with_blank_comment_is_refused(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "true_positive", "comment": "   "},
        format="json",
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "comment" in resp.data.get("fields", {})


# ---------------------------------------------------------------------------------------
# Successful close + closed_date/end_date (G7, defect D4a)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_close_with_verdict_and_comment_succeeds_and_stamps_dates(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "true_positive", "comment": "Contained and eradicated."},
        format="json",
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["verdict"] == "true_positive"
    case.refresh_from_db()
    assert case.status.stage == "Closed"
    assert case.closed_date is not None, "closed_date must persist (defect D4a)"
    assert case.end_date is not None


@pytest.mark.django_db
def test_reopening_clears_closed_date(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)
    api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "false_positive", "comment": "Benign."},
        format="json",
    )
    case.refresh_from_db()
    assert case.closed_date is not None

    resp = api_client.patch(f"/api/v1/case/{case.id}/", {"status": "InProgress"}, format="json")

    assert resp.status_code == status.HTTP_200_OK
    case.refresh_from_db()
    assert case.status.stage == "InProgress"
    assert case.closed_date is None, "reopening must clear closed_date (defect D4a)"


# ---------------------------------------------------------------------------------------
# Propagation to linked alerts (G6, decision D2)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_close_propagates_to_all_linked_alerts(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)
    triaged = _alert(default_org, ref="a-1", case=case, value="Triaged", stage="InProgress")
    imported = _alert(default_org, ref="a-2", case=case, value="Imported", stage="Imported")
    unrelated = _alert(default_org, ref="a-3")

    resp = api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "true_positive", "comment": "Case closed."},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK

    triaged.refresh_from_db()
    imported.refresh_from_db()
    unrelated.refresh_from_db()
    assert triaged.status.stage == "Closed"
    assert imported.status.stage == "Closed", "an Imported alert must also leave triage"
    assert triaged.verdict == "true_positive"
    assert imported.verdict == "true_positive"
    # An alert with no case must be untouched.
    assert unrelated.status.stage == "New"
    assert unrelated.verdict == "undefined"


@pytest.mark.django_db
def test_close_prefers_seeded_closed_alert_status(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)
    alert = _alert(default_org, ref="a-status", case=case, value="Triaged", stage="InProgress")

    api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "true_positive", "comment": "Closed."},
        format="json",
    )

    alert.refresh_from_db()
    # `Closed` is seeded by alerts/0016_seed_closed_alert_status; it must win over `Dismissed`.
    assert alert.status.value == "Closed"


@pytest.mark.django_db
def test_close_records_one_audit_row_and_one_timeline_event(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)
    _alert(default_org, ref="a-1", case=case)
    _alert(default_org, ref="a-2", case=case)

    api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "true_positive", "comment": "Closed."},
        format="json",
    )

    audit = AuditLog.objects.filter(action="case.alerts_closed", target_id=str(case.id))
    assert audit.count() == 1
    assert audit.first().details["count"] == 2
    assert audit.first().target_type == "Case"

    from cases.models import TimelineEvent

    events = TimelineEvent.objects.filter(case=case, kind="alerts-closed")
    assert events.count() == 1
    assert events.first().metadata["count"] == 2


# ---------------------------------------------------------------------------------------
# Audit visibility (defect D4b)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_verdict_change_writes_case_typed_audit_row(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "false_positive", "comment": "Benign."},
        format="json",
    )

    audit = AuditLog.objects.filter(
        action="case.verdict", target_type="Case", target_id=str(case.id)
    )
    assert audit.count() == 1


@pytest.mark.django_db
def test_classification_audit_row_uses_case_target_type(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    """Defect D4b: the applier wrote lowercase "case"; the Audit tab filters "Case"."""
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    api_client.patch(f"/api/v1/case/{case.id}/", {"scope": "single_asset_user"}, format="json")

    assert (
        AuditLog.objects.filter(
            action="case.scope", target_type="Case", target_id=str(case.id)
        ).count()
        == 1
    )
    assert not AuditLog.objects.filter(
        action="case.scope", target_type="case", target_id=str(case.id)
    ).exists()


# ---------------------------------------------------------------------------------------
# Bulk path (decision D1)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_close_without_verdict_is_refused(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        "/api/v1/case/_bulk/",
        {"ids": [str(case.id)], "status": "Closed", "comment": "done"},
        format="json",
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["results"][0]["status"] == status.HTTP_400_BAD_REQUEST
    case.refresh_from_db()
    assert case.status.stage == "InProgress"


@pytest.mark.django_db
def test_bulk_close_with_verdict_succeeds(
    api_client: APIClient, admin_user: User, default_org: Organisation, open_status: CaseStatus
) -> None:
    _auth(api_client, admin_user)
    case = _case(default_org, open_status)

    resp = api_client.patch(
        "/api/v1/case/_bulk/",
        {
            "ids": [str(case.id)],
            "status": "Closed",
            "verdict": "true_positive",
            "comment": "Closed via bulk.",
        },
        format="json",
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["results"][0]["status"] == status.HTTP_200_OK
    case.refresh_from_db()
    assert case.status.stage == "Closed"
    assert case.closed_date is not None


# ---------------------------------------------------------------------------------------
# Create-to-Closed (decision D1)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_in_closed_stage_without_verdict_is_refused(
    api_client: APIClient, admin_user: User
) -> None:
    _auth(api_client, admin_user)
    resp = api_client.post(
        "/api/v1/case/",
        {"title": "Born closed", "status": "Closed", "comment": "done"},
        format="json",
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "verdict" in resp.data.get("fields", {})


@pytest.mark.django_db
def test_create_in_closed_stage_with_verdict_succeeds(
    api_client: APIClient, admin_user: User
) -> None:
    _auth(api_client, admin_user)
    resp = api_client.post(
        "/api/v1/case/",
        {
            "title": "Born closed",
            "status": "Closed",
            "verdict": "false_positive",
            "comment": "Known benign.",
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.data["verdict"] == "false_positive"


# ---------------------------------------------------------------------------------------
# Service-layer direct (no HTTP) — the rule is not a view concern
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_service_layer_close_rule_raises(
    default_org: Organisation, open_status: CaseStatus, admin_user: User
) -> None:
    case = _case(default_org, open_status)
    with pytest.raises(CaseUpdateError) as excinfo:
        apply_case_update(case, {"status": "Closed"}, actor=admin_user)
    assert excinfo.value.error_type == "ValidationError"
    assert "verdict" in excinfo.value.fields


@pytest.mark.django_db
def test_reclosing_an_already_closed_case_is_allowed_without_new_comment(
    default_org: Organisation,
    open_status: CaseStatus,
    admin_user: User,
) -> None:
    """`previous_stage == "Closed"` short-circuits the rule: there is nothing left to decide."""
    case = _case(default_org, open_status)
    apply_case_update(
        case,
        {"status": "Closed", "verdict": "true_positive", "comment": "Closed."},
        actor=admin_user,
    )
    # A second close that only restates the verdict must not demand a fresh comment.
    apply_case_update(case, {"status": "Closed", "verdict": "true_positive"}, actor=admin_user)
    case.refresh_from_db()
    assert case.status.stage == "Closed"


@pytest.mark.django_db
def test_viewer_cannot_close(
    api_client: APIClient,
    viewer_user: User,
    default_org: Organisation,
    open_status: CaseStatus,
) -> None:
    _auth(api_client, viewer_user)
    case = _case(default_org, open_status)
    resp = api_client.patch(
        f"/api/v1/case/{case.id}/",
        {"status": "Closed", "verdict": "true_positive", "comment": "nope"},
        format="json",
    )
    assert resp.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
    case.refresh_from_db()
    assert case.status.stage == "InProgress"
