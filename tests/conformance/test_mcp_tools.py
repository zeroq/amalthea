"""MCP read-tool tests: org scoping, caps and raw-payload exclusion."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest

from alerts.models import Alert, AlertStatus
from automation.models import AutomationRun, Playbook
from cases.models import Case, CaseObservable, CaseStatus, Share, Task, TimelineEvent
from identity.models import Organisation, User
from mcp_server.auth import Principal
from mcp_server.tools.alerts import get_alert, get_alert_triage_context, list_alerts
from mcp_server.tools.base import ToolFailureError
from mcp_server.tools.cases import get_case, get_case_digest, get_case_timeline, list_cases
from mcp_server.tools.observables import get_observable, list_observables
from mcp_server.tools.playbooks import get_playbook_meta, list_playbooks
from mcp_server.tools.search import search
from mcp_server.tools.tasks import list_tasks
from observables.models import Observable, ObservableType


@pytest.fixture
def org(db) -> Organisation:
    return Organisation.objects.create(name="Tool Org")


@pytest.fixture
def other_org(db) -> Organisation:
    return Organisation.objects.create(name="Other Tool Org")


@pytest.fixture
def user(db, org) -> User:
    return User.objects.create(
        login="tool-user",
        username="tool-user",
        email="tool@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


@pytest.fixture
def principal(org) -> Principal:
    return Principal(
        credential_id=uuid4(),
        credential_prefix="abcdefghijkl",
        actor_id=uuid4(),
        organisation_id=org.id,
        role="analyst",
        scopes=("mcp:read",),
    )


@pytest.fixture
def case_status(db) -> CaseStatus:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return status


@pytest.fixture
def alert_status(db) -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return status


@pytest.fixture
def case(org, case_status, user) -> Case:
    return Case.objects.create(
        owner_org=org,
        status=case_status,
        title="Case with raw details",
        description="x" * 5000,
        summary="y" * 5000,
    )


@pytest.fixture
def alert(org, alert_status, user) -> Alert:
    return Alert.objects.create(
        owner_org=org,
        status=alert_status,
        type="phishing",
        source="mailbox",
        source_ref="ref-1",
        title="Phishing alert",
        severity=3,
        raw_payload={"password": "super-secret", "nested": {"token": "abc"}},
        ingestion_warnings={"raw": "secret"},
    )


@pytest.fixture
def observable(org) -> Observable:
    data_type, _ = ObservableType.objects.get_or_create(
        name="mail", defaults={"is_case_sensitive": False}
    )
    return Observable.objects.create(
        owner_org=org,
        data_type=data_type,
        data="evil@example.net",
        normalized_data="evil@example.net",
        enrichment_data={"score": 90, "secret": "nope"},
    )


def _data(result):
    assert result.structured_content is not None
    return result.structured_content["data"]


def test_list_and_get_alerts_are_org_scoped_and_hide_raw_payload(
    org, other_org, alert_status, alert, principal
):
    other = Alert.objects.create(
        owner_org=other_org,
        status=alert_status,
        type="phishing",
        source="mailbox",
        source_ref="ref-2",
        title="Other org alert",
        severity=1,
        raw_payload={"cross": "org"},
    )
    result = list_alerts(principal, limit=50)
    rows = _data(result)
    assert [row["id"] for row in rows] == [str(alert.id)]
    assert "super-secret" not in json.dumps(rows)
    assert "ingestionWarnings" not in rows[0]
    assert "raw_payload" not in json.dumps(rows)
    with pytest.raises(ToolFailureError):
        get_alert(principal, str(other.id))


def test_get_alert_triage_context_hides_raw_payload(alert, principal):
    result = get_alert_triage_context(principal, str(alert.id))
    payload = json.dumps(_data(result))
    assert "super-secret" not in payload
    assert "ingestionWarnings" not in payload


def test_list_and_get_cases_are_org_scoped_and_bound_text(org, other_org, case, principal):
    other_status, _ = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    other = Case.objects.create(owner_org=other_org, status=other_status, title="Other")
    result = list_cases(principal)
    rows = _data(result)
    assert [row["id"] for row in rows] == [str(case.id)]
    assert len(rows[0]["description"]) <= 2000
    assert len(rows[0]["summary"]) <= 2000
    with pytest.raises(ToolFailureError):
        get_case(principal, str(other.id))


def test_shared_case_is_visible_but_foreign_is_not(org, other_org, case_status, principal):
    shared = Case.objects.create(owner_org=other_org, status=case_status, title="Shared")
    Share.objects.create(
        case=shared, organisation_id=principal.organisation_id, permissions={"read": True}
    )
    result = get_case(principal, str(shared.id))
    assert _data(result)["id"] == str(shared.id)


def test_case_digest_excludes_automation_output_and_timeline_metadata(org, case, user, principal):
    TimelineEvent.objects.create(
        case=case,
        date=case.start_date,
        title="comment",
        description="hello",
        metadata={"secret": "metadata-secret"},
    )
    Task.objects.create(case=case, title="Check host")
    playbook = Playbook.objects.create(
        owner_org=org,
        name="enrich",
        trigger_event="case.created",
        config={"api_key": "playbook-secret"},
    )
    AutomationRun.objects.create(
        case=case,
        playbook=playbook,
        playbook_name="enrich",
        status="Running",
        output_log="log-secret",
        idempotency_key="digest-run",
    )
    result = get_case_digest(principal, str(case.id))
    payload = json.dumps(_data(result))
    assert "log-secret" not in payload
    assert "playbook-secret" not in payload
    assert "metadata-secret" not in payload
    assert _data(result)["counts"]["pendingRuns"] == 1
    assert _data(result)["tasks"][0]["title"] == "Check host"


def test_case_timeline_has_no_metadata_and_caps_rows(org, case, principal):
    for index in range(5):
        TimelineEvent.objects.create(
            case=case,
            date=case.start_date,
            title=f"event-{index}",
            description="x",
            metadata={"raw": "secret"},
        )
    result = get_case_timeline(principal, str(case.id), limit=2)
    data = _data(result)
    assert len(data) == 2
    assert result.structured_content["meta"]["truncated"] is True
    assert "nextCursor" in result.structured_content["meta"]
    assert "metadata" not in json.dumps(data)


def test_observables_are_org_scoped_and_safe(org, other_org, case, observable, principal):
    CaseObservable.objects.create(case=case, observable=observable)
    other_type, _ = ObservableType.objects.get_or_create(name="ip")
    other = Observable.objects.create(
        owner_org=other_org,
        data_type=other_type,
        data="10.0.0.9",
        normalized_data="10.0.0.9",
    )
    rows = _data(list_observables(principal))
    assert [row["id"] for row in rows] == [str(observable.id)]
    case_rows = _data(list_observables(principal, case_id=str(case.id)))
    assert [row["id"] for row in case_rows] == [str(observable.id)]
    result = get_observable(principal, str(observable.id))
    assert _data(result)["caseCount"] == 1
    assert "nope" not in json.dumps(_data(result))
    with pytest.raises(ToolFailureError):
        get_observable(principal, str(other.id))


def test_tasks_and_playbooks_are_scoped_and_safe(org, other_org, case, principal):
    Task.objects.create(case=case, title="Contain")
    other_status, _ = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    other_case = Case.objects.create(owner_org=other_org, status=other_status, title="Other")
    Task.objects.create(case=other_case, title="Other task")
    Playbook.objects.create(
        owner_org=org,
        name="enrich",
        description="d",
        trigger_event="case.created",
        config={"api_key": "secret"},
    )
    Playbook.objects.create(
        owner_org=other_org,
        name="foreign",
        trigger_event="case.created",
        config={"api_key": "foreign-secret"},
    )

    tasks = _data(list_tasks(principal))
    assert [task["title"] for task in tasks] == ["Contain"]
    playbooks = _data(list_playbooks(principal))
    assert [item["name"] for item in playbooks] == ["enrich"]
    assert "secret" not in json.dumps(playbooks)
    meta = _data(get_playbook_meta(principal, "enrich"))
    assert meta["name"] == "enrich"
    assert "config" not in meta
    with pytest.raises(ToolFailureError):
        get_playbook_meta(principal, "foreign")


def test_search_is_org_scoped(org, other_org, case, case_status, principal):
    Case.objects.create(owner_org=other_org, status=case_status, title="Ransomware other")
    result = search(principal, "Case with raw", kind="case")
    data = _data(result)
    assert data["sections"][0]["total"] == 1
    assert data["sections"][0]["results"][0]["id"] == str(case.id)
    assert "Ransomware other" not in json.dumps(data)


def test_case_digest_returns_the_most_recent_timeline_events(org, case, principal):
    for index in range(5):
        TimelineEvent.objects.create(
            case=case,
            date=case.start_date + timedelta(minutes=index),
            title=f"event-{index}",
            description="x",
        )

    result = get_case_digest(principal, str(case.id), timeline_limit=2)
    timeline = _data(result)["timeline"]
    assert [event["title"] for event in timeline] == ["event-3", "event-4"]
    assert result.structured_content["meta"]["truncated"] is True


def test_case_timeline_rejects_a_foreign_case_cursor(org, case, case_status, principal):
    TimelineEvent.objects.create(case=case, date=case.start_date, title="ours", description="x")
    other = Case.objects.create(owner_org=org, status=case_status, title="other")
    foreign = TimelineEvent.objects.create(
        case=other, date=other.start_date, title="theirs", description="x"
    )

    with pytest.raises(ToolFailureError) as exc:
        get_case_timeline(principal, str(case.id), cursor=str(foreign.id))
    assert exc.value.status == "not_found"


def test_case_observable_filters_are_applied_before_the_row_cap(org, case, principal):
    mail = ObservableType.objects.get(name="mail")
    ip_type, _ = ObservableType.objects.get_or_create(name="ip")
    email = Observable.objects.create(
        owner_org=org, data_type=mail, data="a@example.net", normalized_data="a@example.net"
    )
    address = Observable.objects.create(
        owner_org=org, data_type=ip_type, data="10.0.0.7", normalized_data="10.0.0.7"
    )
    CaseObservable.objects.create(case=case, observable=email)
    CaseObservable.objects.create(case=case, observable=address)

    result = list_observables(principal, case_id=str(case.id), data_type="ip", limit=1)
    assert [row["id"] for row in _data(result)] == [str(address.id)]
    assert result.structured_content["meta"]["total"] == 1
