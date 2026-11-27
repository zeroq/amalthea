"""§6.16 P7 — organisation-scoped realtime rooms.

The org room is deliberately push-only: it carries refresh *signals* (surface + reason +
object id), never payload bodies or cross-tenant room names. These tests cover the socket
handshake, the close codes, the superAdmin audit path, and the publisher's room derivation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import channels.db
import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from amalthea.asgi import application
from cases.ledger import append_timeline_event
from cases.models import Case, CaseStatus
from identity.models import AuditLog, Organisation, User
from realtime.consumers import FORBIDDEN, PROTOCOL_ERROR, UNAUTHENTICATED
from realtime.publisher import publish_org_event


@pytest.fixture(autouse=True)
def _no_connection_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pytest-django's single connection alive inside consumer dispatches."""
    monkeypatch.setattr(channels.db, "close_old_connections", lambda: None)


@pytest.fixture
def org_a(db: None) -> Organisation:
    return Organisation.objects.create(name="org-realtime-a")


@pytest.fixture
def org_b(db: None) -> Organisation:
    return Organisation.objects.create(name="org-realtime-b")


def _user(username: str, org: Organisation, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=username, password="correct-horse-battery", org=org, role=role
    )


def _browser(user: User) -> Client:
    client = Client()
    assert client.login(username=user.username, password="correct-horse-battery")
    return client


def _socket(browser: Client | None, org_id: str) -> WebsocketCommunicator:
    headers: list[tuple[bytes, bytes]] = []
    if browser is not None:
        cookie = browser.cookies["sessionid"].OutputString()
        headers.append((b"cookie", cookie.encode("ascii")))
    return WebsocketCommunicator(application, f"/ws/org/{org_id}/", headers=headers or None)


def test_org_room_refuses_anonymous_foreign_invalid_and_client_frames(
    org_a: Organisation, org_b: Organisation
) -> None:
    own = _browser(_user("org-owner", org_a))
    foreign = _browser(_user("org-foreign", org_b))

    async def scenario() -> None:
        anonymous = _socket(None, str(org_a.id))
        connected, code = await anonymous.connect(timeout=5)
        assert connected is False
        assert code == UNAUTHENTICATED
        await anonymous.disconnect()
        await anonymous.wait(timeout=5)

        cross = _socket(foreign, str(org_a.id))
        connected, code = await cross.connect(timeout=5)
        assert connected is False
        assert code == FORBIDDEN
        await cross.disconnect()
        await cross.wait(timeout=5)

        invalid = _socket(own, "not-a-uuid")
        connected, code = await invalid.connect(timeout=5)
        assert connected is False
        assert code == PROTOCOL_ERROR
        await invalid.disconnect()
        await invalid.wait(timeout=5)

        accepted, code = await _socket(own, str(org_a.id)).connect(timeout=5)
        assert accepted is True, f"own org refused with {code}"

        chatty = _socket(own, str(org_a.id))
        accepted, code = await chatty.connect(timeout=5)
        assert accepted is True, f"own org refused with {code}"
        await chatty.send_to(text_data='{"type": "anything"}')
        frame = await chatty.receive_output(timeout=5)
        assert frame["type"] == "websocket.close"
        assert frame["code"] == PROTOCOL_ERROR
        await chatty.disconnect()
        await chatty.wait(timeout=5)

    async_to_sync(scenario)()


def test_org_room_receives_publisher_refresh_signal(org_a: Organisation) -> None:
    owner = _browser(_user("org-listener", org_a))

    async def scenario() -> None:
        communicator = _socket(owner, str(org_a.id))
        connected, code = await communicator.connect(timeout=5)
        assert connected is True, f"own org refused with {code}"

        event = {
            "surface": "cases",
            "reason": "case-updated",
            "object_id": str(uuid4()),
            "occurred_at": "2026-11-26T12:00:00+00:00",
        }
        await sync_to_async(publish_org_event)(str(org_a.id), "refresh", event)
        payload = await communicator.receive_json_from(timeout=5)
        assert payload == {
            "type": "event",
            "event_type": "refresh",
            "payload": event,
        }
        assert "description" not in payload
        assert "message" not in payload
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


def test_superadmin_cross_org_subscription_is_audited(
    org_a: Organisation, org_b: Organisation
) -> None:
    superadmin = _browser(_user("org-breakglass", org_a, role="superAdmin"))
    before = AuditLog.objects.filter(action="realtime_org_join").count()

    async def scenario() -> None:
        communicator = _socket(superadmin, str(org_b.id))
        connected, code = await communicator.connect(timeout=5)
        assert connected is True, f"superAdmin cross-org refused with {code}"
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()

    audit = AuditLog.objects.filter(action="realtime_org_join")
    assert audit.count() == before + 1
    entry = audit.order_by("-created_at").first()
    assert entry is not None
    assert entry.target_org_id == org_b.id
    assert entry.actor_org_id == org_a.id
    assert entry.action == "realtime_org_join"
    assert entry.target_type == "Organisation"
    assert entry.target_id == str(org_b.id)


def test_superadmin_same_org_subscription_is_not_audited(org_a: Organisation) -> None:
    superadmin = _browser(_user("org-super-local", org_a, role="superAdmin"))
    before = AuditLog.objects.filter(action="realtime_org_join").count()

    async def scenario() -> None:
        communicator = _socket(superadmin, str(org_a.id))
        connected, code = await communicator.connect(timeout=5)
        assert connected is True, f"superAdmin own org refused with {code}"
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()
    assert AuditLog.objects.filter(action="realtime_org_join").count() == before


def test_ledger_emits_owner_org_refresh_after_commit(
    db: None, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any
) -> None:
    org = Organisation.objects.create(name="ledger-refresh-org")
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    case = Case.objects.create(title="Ledger refresh", status=status, owner_org=org)
    publisher = MagicMock()
    monkeypatch.setattr("cases.ledger.event_log.publish_org_event", publisher)

    with django_capture_on_commit_callbacks(execute=True):
        append_timeline_event(case, title="Status changed", kind="status-changed")

    publisher.assert_called_once()
    args = publisher.call_args.args
    assert args[0] == str(case.owner_org_id)
    assert args[1] == "refresh"
    payload = args[2]
    assert set(payload) == {"surface", "reason", "object_id", "occurred_at"}
    assert payload["surface"] == "cases"
    assert payload["reason"] == "case-updated"
    assert payload["object_id"] == str(case.id)
    assert "description" not in payload
    assert "title" not in payload


def test_alert_comment_emits_owner_org_refresh_after_commit(
    db: None, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any
) -> None:
    org = Organisation.objects.create(name="alert-refresh-org")
    user = _user("alert-commenter", org)
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    case_status, _ = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    case = Case.objects.create(title="Linked case", status=case_status, owner_org=org)
    alert = Alert.objects.create(
        type="test",
        source="test",
        source_ref="org-refresh-comment",
        title="Alert refresh",
        status=status,
        owner_org=org,
        case=case,
    )
    publisher = MagicMock()
    monkeypatch.setattr("alerts.views.publish_org_event", publisher)
    api = APIClient()
    api.force_authenticate(user=user)

    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            reverse("alert-comment-list", args=[str(alert.id)]),
            {"message": "Investigating"},
            format="json",
        )

    assert response.status_code == 201
    publisher.assert_called_once()
    args = publisher.call_args.args
    assert args[0] == str(alert.owner_org_id)
    assert args[1] == "refresh"
    assert args[2]["surface"] == "cases"
    assert args[2]["reason"] == "case-updated"
    assert args[2]["object_id"] == str(alert.case_id)
    assert set(args[2]) == {"surface", "reason", "object_id", "occurred_at"}


def test_alert_create_path_has_org_refresh_signal() -> None:
    source = (Path(__file__).resolve().parents[2] / "alerts" / "views.py").read_text(
        encoding="utf-8"
    )
    assert '"reason": "alert-created"' in source
    assert "publish_org_event(" in source


def test_publisher_derives_room_from_org_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLayer:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def group_send(self, group: str, message: dict[str, Any]) -> None:
            self.calls.append((group, message))

    layer = FakeLayer()
    monkeypatch.setattr("realtime.publisher.get_channel_layer", lambda: layer)
    org_id = str(uuid4())
    publish_org_event(org_id, "refresh", {"surface": "alerts"})
    assert layer.calls == [
        (
            f"org_{org_id}",
            {
                "type": "event",
                "event_type": "refresh",
                "payload": {"surface": "alerts"},
            },
        )
    ]
