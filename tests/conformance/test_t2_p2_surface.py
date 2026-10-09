"""T2 Phase P2 surface — collaboration: comments, pages, shares and flow (plan §6-P2).

The minimal product-sized guard for the surface added by this phase. The three acceptance
criteria are the behaviours that fail *silently* if the routes, the publisher or the share guard
drift; the exhaustive contract suite is a later wave's work.

=====  =====================================================================
AC-a   a page/comment create reaches the case's WebSocket room, and an
       unauthenticated subscriber cannot join that room.
AC-b   shares are default-deny: a read-only share reads but cannot write,
       and a write is not silently dropped — the row is proven absent.
AC-c   `comment`/`page`/`flow` shapes match the pinned keys, and the whole
       surface is mounted (no route swallowed by `case/<id>` or `<alert_id>`).
=====  =====================================================================

Tests are grouped by acceptance criterion. Every assertion is a status code or a row read back
from the database, so a passing stub cannot pass by returning a shape.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client
from rest_framework.test import APIClient

from alerts.models import Alert
from amalthea.asgi import application
from cases.models import Case, CaseObservable, CaseStatus, Comment, Page, Share
from identity.models import Organisation, User
from realtime.consumers import UNAUTHENTICATED

from ._t1_seed import _alert, _observable

# --- fixtures --------------------------------------------------------------
#
# The conftest `analyst` has no usable password, so it cannot drive the session cookie the
# WebSocket handshake needs. This module defines its own fixtures (module beats conftest) with a
# real password, exactly as `test_realtime.py` does.


@pytest.fixture(autouse=True)
def _no_connection_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the consumer's per-dispatch connection cleanup from tearing down the test database."""
    import channels.db

    monkeypatch.setattr(channels.db, "close_old_connections", lambda: None)


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Test Org")


@pytest.fixture
def analyst(org: Organisation) -> User:
    return User.objects.create_user(
        username="analyst", login="analyst", password="hunter2", email="a@example.net", org=org
    )


@pytest.fixture
def api(analyst: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=analyst)
    return client


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="analyst", password="hunter2")
    return client


def _owned_case(org: Organisation, title: str = "Owned") -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _foreign_client(org: Organisation) -> APIClient:
    """A client in a *second* organisation — the tenant a case must not leak to without a share."""
    user = User.objects.create_user(
        username=f"foreign-{org.id.hex[:6]}",
        login=f"foreign-{org.id.hex[:6]}",
        password="hunter2",
        email="f@example.net",
        org=org,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _room(case: Case, browser: Client) -> WebsocketCommunicator:
    """A socket for `case` carrying the browser's real session cookie (see `test_realtime`)."""
    cookie = browser.cookies["sessionid"].OutputString()
    return WebsocketCommunicator(
        application,
        f"/ws/case/{case.id}/",
        headers=[(b"cookie", cookie.encode("ascii"))],
    )


# --- AC-a — a create is published; an anonymous socket is refused ----------


def test_a_case_comment_create_publishes_to_the_case_room(
    api: APIClient,
    analyst: User,
    org: Organisation,
    browser: Client,
    django_capture_on_commit_callbacks: object,
) -> None:
    """POST /case/{id}/comment must reach a second tab without a reload (Module B).

    The comment row and the ledger event are written in one transaction and the WebSocket event
    is published on commit, so this proves the publish is wired to the commit rather than to the
    row write alone.
    """
    case = _owned_case(org)

    def post_comment() -> None:
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            response = api.post(
                f"/api/v1/case/{case.id}/comment", {"message": "live comment"}, format="json"
            )
        assert response.status_code == 201, response.content

    async def scenario() -> None:
        communicator = _room(case, browser)
        assert (await communicator.connect(timeout=5))[0] is True
        await sync_to_async(post_comment)()
        message = await communicator.receive_json_from(timeout=5)
        assert message["type"] == "event"
        assert message["event_type"] == "comment"
        assert message["payload"]["event"]["description"] == "live comment"
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


def test_a_case_page_create_publishes_a_page_event(
    api: APIClient,
    analyst: User,
    org: Organisation,
    browser: Client,
    django_capture_on_commit_callbacks: object,
) -> None:
    """A page is mutable state but still announces itself, so an open case view refreshes it."""
    case = _owned_case(org)

    def post_page() -> None:
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            response = api.post(
                f"/api/v1/case/{case.id}/page",
                {"title": "Notes", "content": "# heading", "order": 2, "category": "analysis"},
                format="json",
            )
        assert response.status_code == 201, response.content

    async def scenario() -> None:
        communicator = _room(case, browser)
        assert (await communicator.connect(timeout=5))[0] is True
        await sync_to_async(post_page)()
        message = await communicator.receive_json_from(timeout=5)
        assert message["event_type"] == "page"
        event = message["payload"]["event"]
        assert event["_type"] == "page"
        assert event["title"] == "Notes"
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


def test_an_anonymous_socket_cannot_join_a_case_room(db: None) -> None:
    """PLAN AC6.1-P2-a: no session, no room — and the refused socket stays out of the group."""
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    case = Case.objects.create(title="Unlisted", status=status)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(application, f"/ws/case/{case.id}/")
        connected, close_code = await communicator.connect(timeout=5)
        assert connected is False, "an anonymous socket was accepted"
        assert close_code == UNAUTHENTICATED, f"refused with {close_code}, expected 4401"
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


# --- AC-b — shares are default-deny ---------------------------------------


def test_a_read_only_share_can_read_but_cannot_write(api: APIClient, org: Organisation) -> None:
    """PLAN AC6.1-P2-b: a read-only share reads; every write is refused and changes nothing."""
    case = _owned_case(org)
    other = Organisation.objects.create(name="Other Org")
    other_api = _foreign_client(other)

    # No share yet: the foreign tenant cannot even see the case.
    assert other_api.get(f"/api/v1/case/{case.id}/comment").status_code == 404

    # Owner grants read-only.
    granted = api.post(
        f"/api/v1/case/{case.id}/shares",
        {"shares": [{"organisation": str(other.id), "permissions": {"write": False}}]},
        format="json",
    )
    assert granted.status_code == 200, granted.content
    body = granted.json()
    assert any(
        entry["organisation"] == str(other.id) and entry["canWrite"] is False for entry in body
    ), body

    # Read paths open up.
    assert other_api.get(f"/api/v1/case/{case.id}/comment").status_code == 200
    assert other_api.get(f"/api/v1/case/{case.id}/flow").status_code == 200
    assert other_api.get(f"/api/v1/case/{case.id}/shares").status_code == 200

    # Write paths stay closed — each verb that could mutate.
    assert (
        other_api.post(
            f"/api/v1/case/{case.id}/comment", {"message": "nope"}, format="json"
        ).status_code
        == 404
    )
    assert (
        other_api.post(f"/api/v1/case/{case.id}/page", {"title": "nope"}, format="json").status_code
        == 404
    )
    assert (
        other_api.post(
            f"/api/v1/case/{case.id}/shares",
            {"shares": [{"organisation": str(other.id), "permissions": {"write": True}}]},
            format="json",
        ).status_code
        == 404
    )
    assert not Comment.objects.filter(case=case).exists(), "a refused write landed a row"
    assert not Page.objects.filter(case=case).exists(), "a refused write landed a row"
    assert Share.objects.get(case=case, organisation=other).can_write is False


def test_a_write_share_unlocks_writes_and_revoke_relocks(api: APIClient, org: Organisation) -> None:
    """The failure mode's mirror: write is granted only by a write share, and revoking closes again."""
    case = _owned_case(org)
    other = Organisation.objects.create(name="Other Org")
    other_api = _foreign_client(other)

    replaced = api.put(
        f"/api/v1/case/{case.id}/shares",
        {"shares": [{"organisation": str(other.id), "permissions": {"write": True}}]},
        format="json",
    )
    assert replaced.status_code == 200, replaced.content
    assert (
        other_api.post(
            f"/api/v1/case/{case.id}/comment", {"message": "allowed"}, format="json"
        ).status_code
        == 201
    )

    share = Share.objects.get(case=case, organisation=other)
    revoked = api.delete(f"/api/v1/case/{case.id}/share/{share.id}")
    assert revoked.status_code == 204, revoked.content
    assert other_api.get(f"/api/v1/case/{case.id}/comment").status_code == 404


# --- AC-c — shapes and mounting -------------------------------------------


def test_case_comment_crud_shapes(api: APIClient, org: Organisation) -> None:
    case = _owned_case(org)
    created = api.post(f"/api/v1/case/{case.id}/comment", {"message": "first"}, format="json")
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["_type"] == "comment"
    assert set(body) == {
        "_id",
        "id",
        "_type",
        "_createdBy",
        "_createdAt",
        "_updatedBy",
        "_updatedAt",
        "message",
        "isEdited",
        "extraData",
        "external",
    }
    assert body["message"] == "first"
    assert body["isEdited"] is False

    listed = api.get(f"/api/v1/case/{case.id}/comment")
    assert listed.status_code == 200, listed.content
    assert [entry["message"] for entry in listed.json()] == ["first"]

    edited = api.patch(f"/api/v1/comment/{body['_id']}", {"message": "edited"}, format="json")
    assert edited.status_code == 200, edited.content
    reread = api.get(f"/api/v1/case/{case.id}/comment").json()
    assert reread[0]["message"] == "edited"
    assert reread[0]["isEdited"] is True

    assert api.delete(f"/api/v1/comment/{body['_id']}").status_code == 204
    assert not Comment.objects.filter(case=case).exists()


def test_alert_comment_create_and_list(api: APIClient, org: Organisation) -> None:
    alert: Alert = _alert("Phish", ref="p2-alert", case=None)
    created = api.post(f"/api/v1/alert/{alert.id}/comment", {"message": "reported"}, format="json")
    assert created.status_code == 201, created.content
    listed = api.get(f"/api/v1/alert/{alert.id}/comment")
    assert listed.status_code == 200, listed.content
    assert [entry["message"] for entry in listed.json()] == ["reported"]


def test_case_page_crud_and_shape(api: APIClient, org: Organisation) -> None:
    case = _owned_case(org)
    other_case = _owned_case(org, title="Other")
    created = api.post(
        f"/api/v1/case/{case.id}/page",
        {"title": "Runbook", "content": "# step", "order": 1, "category": "ir"},
        format="json",
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["_type"] == "page"
    assert set(body) == {
        "_id",
        "id",
        "_type",
        "_createdBy",
        "_createdAt",
        "_updatedBy",
        "_updatedAt",
        "caseId",
        "title",
        "content",
        "order",
        "category",
        "extraData",
    }
    assert body["caseId"] == str(case.id)

    # A page reached through a *different* case is a 404 even though its id exists.
    assert api.get(f"/api/v1/case/{other_case.id}/page/{body['_id']}").status_code == 404

    patched = api.patch(
        f"/api/v1/case/{case.id}/page/{body['_id']}",
        {"content": "updated", "order": 5},
        format="json",
    )
    assert patched.status_code == 200, patched.content
    assert Page.objects.get(pk=body["_id"]).content == "updated"

    deleted = api.delete(f"/api/v1/case/{case.id}/page/{body['_id']}")
    assert deleted.status_code == 204, deleted.content
    assert not Page.objects.filter(case=case).exists()


def test_case_flow_links_every_entity_without_raw_payload(
    api: APIClient, org: Organisation
) -> None:
    case = _owned_case(org)
    alert = _alert("Linked", ref="flow-1", case=case)
    observable = _observable("198.51.100.7")
    CaseObservable.objects.create(case=case, observable=observable)

    response = api.get(f"/api/v1/case/{case.id}/flow")
    assert response.status_code == 200, response.content
    body: dict[str, Any] = response.json()
    assert body["_type"] == "flow"
    assert set(body) == {"_type", "case", "alerts", "observables", "tasks"}
    assert body["case"]["_id"] == str(case.id)
    assert [entry["_id"] for entry in body["alerts"]] == [str(alert.id)]
    assert [entry["data"] for entry in body["observables"]] == ["198.51.100.7"]
    assert "raw_payload" not in body["alerts"][0], "flow inlined the raw ingestion payload"


def test_comment_and_page_routes_are_not_swallowed_by_case_detail(
    api: APIClient, org: Organisation
) -> None:
    """Mount order: `case/<id>/comment` must hit the comment view, not `case_detail`.

    If the bare `case/<case_id>` route were registered first, the POST below would create or read
    a *case* named after the id rather than a comment — the request would still be a 2xx, so only
    the row read back proves which view ran.
    """
    case = _owned_case(org)
    assert (
        api.post(
            f"/api/v1/case/{case.id}/comment", {"message": "route check"}, format="json"
        ).status_code
        == 201
    )
    assert Comment.objects.filter(case=case, message="route check").count() == 1
    assert (
        api.post(
            f"/api/v1/case/{case.id}/page", {"title": "route check"}, format="json"
        ).status_code
        == 201
    )
    assert Page.objects.filter(case=case, title="route check").count() == 1


def test_the_whole_surface_is_401_without_credentials(anonymous_api: APIClient, db: None) -> None:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    case = Case.objects.create(title="Private", status=status)
    for path, method in (
        (f"/api/v1/case/{case.id}/comment", "post"),
        (f"/api/v1/case/{case.id}/page", "post"),
        (f"/api/v1/case/{case.id}/shares", "get"),
        (f"/api/v1/case/{case.id}/flow", "get"),
    ):
        response = getattr(anonymous_api, method)(path, {}, format="json")
        assert response.status_code == 401, f"{method.upper()} {path} -> {response.status_code}"


def test_alert_comment_route_is_not_swallowed_by_alert_detail(api: APIClient) -> None:
    alert = _alert("Route", ref="route-1")
    assert (
        api.post(
            f"/api/v1/alert/{alert.id}/comment", {"message": "route check"}, format="json"
        ).status_code
        == 201
    )
    assert Alert.objects.get(pk=alert.id).comments.count() == 1
