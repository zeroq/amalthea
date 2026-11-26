"""§6.16 P7 — case-room presence is counts-only and deduplicates users."""

from __future__ import annotations

from typing import Any

import channels.db
import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import Client

from amalthea.asgi import application
from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from realtime.presence import InMemoryPresenceStore, _presence_member


@pytest.fixture(autouse=True)
def _no_connection_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.db, "close_old_connections", lambda: None)


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="presence-org")


def _user(username: str, org: Organisation) -> User:
    return User.objects.create_user(username=username, password="presence-password", org=org)


def _browser(user: User) -> Client:
    client = Client()
    assert client.login(username=user.username, password="presence-password")
    return client


def _case(org: Organisation) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(title="Presence case", status=status, owner_org=org)


def _socket(case: Case, browser: Client) -> WebsocketCommunicator:
    cookie = browser.cookies["sessionid"].OutputString()
    return WebsocketCommunicator(
        application,
        f"/ws/case/{case.id}/",
        headers=[(b"cookie", cookie.encode("ascii"))],
    )


def test_store_counts_unique_users_without_broadcasting_identity(
    org: Organisation,
) -> None:
    first = _user("presence-first", org)
    second = _user("presence-second", org)
    store = InMemoryPresenceStore()
    case_id = "00000000-0000-4000-8000-000000000001"

    assert store.join(case_id, first) == 1
    assert store.join(case_id, first) == 1
    assert store.join(case_id, second) == 2
    assert store.touch(case_id, first) == 2
    assert store.leave(case_id, second) == 1
    assert store.leave(case_id, first) == 0

    members = list(store._store.get(case_id, {}))
    assert members == []
    opaque = _presence_member(first)
    assert len(opaque) == 16
    assert str(first.pk) not in opaque
    assert first.username not in opaque


async def _presence_payload(socket: WebsocketCommunicator) -> dict[str, Any]:
    payload = await socket.receive_json_from(timeout=5)
    assert set(payload) == {"type", "count"}
    assert payload["type"] == "presence"
    assert isinstance(payload["count"], int)
    return payload


def test_case_socket_broadcasts_counts_only(org: Organisation) -> None:
    case = _case(org)
    first = _socket(case, _browser(_user("socket-first", org)))
    second = _socket(case, _browser(_user("socket-second", org)))

    async def scenario() -> None:
        connected, code = await first.connect(timeout=5)
        assert connected is True, f"first socket refused with {code}"
        assert await first.receive_nothing(timeout=0.2), "joiner received its own presence frame"

        connected, code = await second.connect(timeout=5)
        assert connected is True, f"second socket refused with {code}"
        assert await second.receive_nothing(timeout=0.2), "joiner received its own presence frame"
        assert await first.receive_nothing(timeout=0.2), (
            "join broadcasts would precede ledger events"
        )

        await second.send_to(text_data='{"type":"presence_ping"}')
        assert (await _presence_payload(second))["count"] == 2
        assert (await _presence_payload(first))["count"] == 2

        await second.disconnect()
        await second.wait(timeout=5)
        assert (await _presence_payload(first))["count"] == 1

        await first.disconnect()
        await first.wait(timeout=5)

    async_to_sync(scenario)()
