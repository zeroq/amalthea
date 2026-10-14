"""ADR-006 non-goal — read-only means *readable*: a viewer keeps every read surface.

Plan §9.4 / decision 5. The rename is safe to ship only if it removes nothing a viewer needs, and
the risk it guards against is the opposite of a breach: an over-broad gate (a `not_viewer` on a
read view, or a `write=True` resolve reached from a GET) silently turns a viewer's account into a
404 machine. The page inventory therefore asserts the status; the cases where a 200 could still be
an empty shell — the case export, the attachment download, the API documents, the WebSocket replay —
assert the *content* (or an analyst parity check) as well.

Covered: the page inventory, the case export, attachment list + download, the API GETs, and the case
WebSocket handshake + `sync` replay (the consumer resolves with `write=False`, so a viewer must be
able to subscribe and stream).
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import Client
from django.urls import reverse

from amalthea.asgi import application
from core.enums import VIEWER_ROLE
from identity.models import Organisation, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.get_or_create(name="Default")[0]


def _viewer(org: Organisation, username: str = "read-viewer") -> User:
    return User.objects.create(username=username, org=org, role=VIEWER_ROLE)


@pytest.fixture
def viewer_browser(org: Organisation) -> Client:
    client = Client()
    client.force_login(_viewer(org))
    return client


@pytest.fixture
def analyst_browser(org: Organisation) -> Client:
    client = Client()
    client.force_login(User.objects.create(username="read-analyst", org=org, role="analyst"))
    return client


def _case(org: Organisation, **kw: Any):
    from cases.models import Case, CaseStatus

    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(
        title="Beaconing host", owner_org=org, status=status, description="as reported", **kw
    )


def _attachment(case: Any) -> Any:
    from cases.models import Attachment

    return Attachment.objects.create(
        case=case,
        name="capture.pcap",
        content_type="application/octet-stream",
        size=3,
        sha256="0" * 64,
        path="2026/01/00000000000000000000000000000000.pcap",
    )


# --- the page inventory --------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kwargs_from"),
    [
        ("ui-dashboard", None),
        ("ui-case-list", None),
        ("ui-alert-list", None),
        ("ui-observable-list", None),
        ("ui-taxonomy", None),
        ("ui-automation-list", None),
        ("ui-playbook-list", None),
        ("ui-sources-list", None),
        ("ui-tag-list", None),
        ("ui-case-template-list", None),
        ("ui-case-detail", "case"),
        ("ui-alert-detail", "alert"),
        ("ui-observable-detail", "observable"),
    ],
)
def test_a_viewer_can_still_open_every_read_page(
    viewer_browser: Client, org: Organisation, name: str, kwargs_from: str | None
) -> None:
    from alerts.models import Alert, AlertStatus
    from observables.models import Observable, ObservableType

    kwargs: dict[str, str] = {}
    if kwargs_from == "case":
        kwargs["case_id"] = _case(org).number
    elif kwargs_from == "alert":
        status = AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
        kwargs["alert_id"] = str(
            Alert.objects.create(
                title="Phish", owner_org=org, status=status, source="o365", source_ref="e1"
            ).id
        )
    elif kwargs_from == "observable":
        otype = ObservableType.objects.get_or_create(
            name="ip", defaults={"is_case_sensitive": False}
        )[0]
        kwargs["observable_id"] = str(
            Observable.objects.create(data_type=otype, data="10.0.0.1", owner_org=org).id
        )

    response = viewer_browser.get(reverse(name, kwargs=kwargs))
    assert response.status_code == 200, f"{name} must stay readable for a viewer"


# --- export, attachments -------------------------------------------------------


def test_a_viewer_can_export_a_case(viewer_browser: Client, org: Organisation) -> None:
    case = _case(org)
    response = viewer_browser.get(reverse("ui-case-export", kwargs={"case_id": case.number}))
    assert response.status_code == 200
    assert b"Beaconing host" in response.content, "the export is a real serialisation"


def test_a_viewer_can_list_attachments_and_reach_the_download(
    viewer_browser: Client, analyst_browser: Client, org: Organisation
) -> None:
    """The attachment list and download are reads the viewer keeps.

    The download route carries a `<uuid:attachment_id>` converter, so this also pins the delegation
    to the API view: asserting only on the status *class* would hide the 500 that a `UUID` reaching
    the API's `_as_uuid` helper used to produce (fixed alongside this plan).
    """
    case = _case(org)
    attachment = _attachment(case)

    listing = viewer_browser.get(
        reverse("ui-case-attachment-list", kwargs={"case_id": case.number})
    )
    assert listing.status_code == 200, "the attachment list is a read"
    assert "capture.pcap" in listing.content.decode()

    url = reverse(
        "ui-case-attachment-download",
        kwargs={"case_id": case.number, "attachment_id": attachment.id},
    )
    response = viewer_browser.get(url)
    assert response.status_code == 404, (
        f"the blob is absent on disk, so an honest 404 — got {response.status_code}"
    )
    # Parity: the analyst sees the same 404, so the viewer's answer came from the view's own lookup
    # rather than from a gate refusing the viewer before it.
    assert analyst_browser.get(url).status_code == 404


# --- the API GETs --------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/v1/case", "/api/v1/case/", "/api/v1/alert", "/api/v1/alert/"],
)
def test_a_viewer_can_still_read_the_api_collections(viewer_browser: Client, path: str) -> None:
    response = viewer_browser.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code} for a viewer"
    assert "application/json" in response["Content-Type"]


def test_a_viewer_can_still_read_a_case_by_id(viewer_browser: Client, org: Organisation) -> None:
    case = _case(org)
    response = viewer_browser.get(f"/api/v1/case/{case.id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Beaconing host"


def test_a_viewer_can_still_read_an_alert_and_an_observable(
    viewer_browser: Client, org: Organisation
) -> None:
    from alerts.models import Alert, AlertStatus
    from observables.models import Observable, ObservableType

    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    alert = Alert.objects.create(
        title="Phish", owner_org=org, status=status, source="o365", source_ref="e2"
    )
    otype = ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})[
        0
    ]
    observable = Observable.objects.create(data_type=otype, data="10.0.0.1", owner_org=org)

    assert viewer_browser.get(f"/api/v1/alert/{alert.id}").status_code == 200
    assert viewer_browser.get(f"/api/v1/observable/{observable.id}").status_code == 200


# --- the case room -------------------------------------------------------------


def test_a_viewer_can_subscribe_to_the_case_room_and_sync(
    org: Organisation,
) -> None:
    """The consumer resolves visibility with `write=False`, so a viewer must get in and replay."""
    from cases.ledger import append_timeline_event

    user = _viewer(org, "read-ws")
    case = _case(org)
    append_timeline_event(case, title="First analyst note", kind="comment")

    browser = Client()
    browser.force_login(user)
    cookie = browser.cookies["sessionid"].OutputString()

    async def scenario() -> None:
        communicator = WebsocketCommunicator(
            application, f"/ws/case/{case.id}/", headers=[(b"cookie", cookie.encode("ascii"))]
        )
        connected, close_code = await communicator.connect(timeout=5)
        assert connected is True, f"a viewer was refused the room with {close_code}"

        await communicator.send_json_to({"type": "sync"})
        payload = await communicator.receive_json_from(timeout=5)
        assert payload["type"] == "timeline"
        assert [event["title"] for event in payload["events"]] == ["First analyst note"]

        await communicator.disconnect()

    async_to_sync(scenario)()
