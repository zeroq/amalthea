"""§11.4 I1 — internal exception text must never reach a flash message."""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus, Task
from identity.models import Organisation, User
from mcp_server.models import McpOrgPolicy

pytestmark = pytest.mark.django_db

SECRET = "SECRET_INTERNAL_EXCEPTION_8f3c"


def _flash_text(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _user(org: Organisation, *, name: str, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=name,
        login=name,
        password="hunter2-correct",
        email=f"{name}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _case_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


def test_case_bulk_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    org = Organisation.objects.create(name="Flash hygiene case org")
    user = _user(org, name="case-flash")
    case = Case.objects.create(title="Case", status=_case_status(), owner_org=org)
    client = Client()
    client.force_login(user)

    def boom(self, *args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(Case, "save", boom)
    response = client.post("/cases/bulk", {"ids": [str(case.id)], "status": "New"})

    assert response.status_code == 302
    text = " ".join(_flash_text(response))
    assert SECRET not in text
    assert "Could not update this case." in text


def test_task_bulk_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    org = Organisation.objects.create(name="Flash hygiene task org")
    user = _user(org, name="task-flash")
    case = Case.objects.create(title="Case", status=_case_status(), owner_org=org)
    task = Task.objects.create(case=case, title="Task", status="Waiting")
    client = Client()
    client.force_login(user)

    def boom(self, *args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(Task, "save", boom)
    response = client.post("/tasks/bulk", {"ids": [str(task.id)], "status": "InProgress"})

    assert response.status_code == 302
    text = " ".join(_flash_text(response))
    assert SECRET not in text
    assert "Could not update this task." in text


def test_alert_bulk_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    org = Organisation.objects.create(name="Flash hygiene alert org")
    user = _user(org, name="alert-flash")
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    alert = Alert.objects.create(
        type="test",
        source="test",
        source_ref="flash-1",
        title="Alert",
        status=status,
        owner_org=org,
    )
    client = Client()
    client.force_login(user)

    def boom(self, *args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(Alert, "save", boom)
    response = client.post("/alerts/bulk", {"ids": [str(alert.id)], "status": "New"})

    assert response.status_code == 302
    text = " ".join(_flash_text(response))
    assert SECRET not in text
    assert "Could not update this alert." in text


def test_autonomy_validation_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    org = Organisation.objects.create(name="Flash hygiene autonomy org")
    user = _user(org, name="autonomy-flash", role="admin")
    McpOrgPolicy.objects.create(organisation=org)
    client = Client()
    client.force_login(user)

    def boom(self, *args, **kwargs):
        raise ValidationError(SECRET)

    monkeypatch.setattr(McpOrgPolicy, "full_clean", boom)
    response = client.post(
        f"/settings/agent-access/autonomy/{org.id}/update",
        {"max_auto_severity": "1", "daily_action_cap": "20"},
    )

    assert response.status_code == 200
    text = response.content.decode()
    assert SECRET not in text
    assert "Invalid autonomy policy" in text
