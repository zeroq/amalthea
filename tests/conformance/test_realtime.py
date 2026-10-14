"""Phase 7 — the case room, the sync protocol, and the ledger choke point.

Ref: PLAN §Phase 7 (AC7.1-AC7.4) and BRIEF-2026-10-06-realtime-ledger T5.

What each test is actually claiming, beyond its marker:

* **AC7.1** is the whole chain: an HTML form POST → `append_timeline_event` → `transaction.on_commit`
  → publisher → group → two independent sockets, with no reload in between. The POST is made the way
  a browser makes it (including `HX-Request`, so the append-one-`<li>` path is covered).
* **AC7.2** a handshake without a session is refused with 4401 *and never joined the room*: a
  publish afterwards delivers nothing through that socket, which is the half of the claim a bare
  close-code assertion would not catch.
* **AC7.3** a publish to case A reaches A and reaches nothing on a socket joined to case B.
* **AC7.4** sync returns the keyset strictly after the anchor, a reconnect resumes from the new
  anchor, and an event that arrives both live and in a later sync is served once — the server never
  re-sends an id it already served for that anchor (the client's `data-event-id` dedupe is the
  second, client-side half).

## Why the tests are shaped this way

`async_to_sync(scenario)()` runs the sockets on one event loop in a worker thread, while the Django
side of the test (`Client.post`, `record_result`) runs on the test thread inside `sync_to_async`.
Two consequences are load-bearing and easy to break:

1. **Publishing must happen inside that sync context.** `publish_case_event` drives `group_send`
   with `async_to_sync`, which has to target the loop the consumers live on; asgiref routes it
   there because the enclosing `sync_to_async` recorded that loop as the current one. Publishing
   from the bare test thread would open a fresh loop, and the InMemory layer's queue would be
   written by a thread that never wakes it — the assertion would fail as a timeout, not a diff.
2. **`channels.db.close_old_connections` is patched to a no-op.** It runs around every consumer
   dispatch and, inside pytest-django's never-committing transaction, closes the connection the test
   is using on Postgres (SQLite's in-memory `close` is a no-op, which is why this looked fine
   before). Channels' own `ApplicationCommunicator` patches it for exactly this reason.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import channels.db
import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from amalthea.asgi import application
from automation.dispatcher import record_result
from automation.models import AutomationRun, Playbook
from cases.ledger import append_timeline_event
from cases.models import Case, CaseStatus, Share, TimelineEvent
from identity.models import Organisation, User
from realtime.consumers import FORBIDDEN, PROTOCOL_ERROR, UNAUTHENTICATED
from realtime.publisher import publish_case_event

#: The literal the choke-point scan forbids outside `cases/ledger.py`. It lives here as one name so
#: the scan and its self-test cannot drift apart.
DIRECT_CREATE = "TimelineEvent.objects.create("

#: Apps whose ledger writes are in scope. `cases/`, `ui/` and `automation/` are brief T1; `alerts/`
#: was added when its two escalation sites (`alert-imported`, `alert-merged`) were migrated to the
#: choke point, so the audit now covers every app that writes ledger rows.
SCANNED_APPS = ("alerts", "cases", "ui", "automation")

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER = REPO_ROOT / "cases" / "ledger.py"


@pytest.fixture(autouse=True)
def _no_connection_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the consumer's per-dispatch connection cleanup from tearing down the test's database.

    `AsyncConsumer.dispatch` closes and re-opens "old" connections around every message, which is
    right for a request/response app and wrong for one test that holds a single connection inside an
    uncommitted transaction: on Postgres it closes that connection mid-test and the next query dies
    with "the connection is closed". Channels patches the same function in its own test
    communicator, for the same reason.
    """
    monkeypatch.setattr(channels.db, "close_old_connections", lambda: None)


@pytest.fixture
def default_org(db: None) -> Organisation:
    """Reuse the default org for tenant isolation."""
    name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
    return Organisation.objects.get_or_create(name=name)[0]


@pytest.fixture
def analyst(db: None, default_org: Organisation) -> User:
    return User.objects.create_user(
        username="analyst", password="hunter2-correct", email="a@example.net", org=default_org
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="analyst", password="hunter2-correct")
    return client


def _case(title: str, org: Organisation = None) -> Case:
    """A case with a valid status, as in `test_ui_loop` — `Case.status` is a required FK."""
    if org is None:
        org, _ = Organisation.objects.get_or_create(name="Test Org")
    status = (
        CaseStatus.objects.filter(value="New").first()
        or CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    )
    return Case.objects.create(title=title, status=status, owner_org=org)


def _room(case: Case, browser: Client) -> WebsocketCommunicator:
    """A socket for `case`, carrying the browser's session cookie.

    The cookie is how `AuthMiddlewareStack` sees the login: the test client's session is real, so
    this exercises the same handshake a browser performs rather than a stubbed scope.
    """
    cookie = browser.cookies["sessionid"].OutputString()
    return WebsocketCommunicator(
        application,
        f"/ws/case/{case.id}/",
        headers=[(b"cookie", cookie.encode("ascii"))],
    )


async def _publish(case_id: str, event: dict[str, Any], event_type: str = "comment") -> None:
    """Publish through the real choke point from the scenario's sync context (see module docstring)."""
    await sync_to_async(publish_case_event)(case_id, event_type, {"event": event})


def _seed_events(case: Case) -> list[TimelineEvent]:
    """Three ledger rows, the last two sharing a timestamp and ordered by id.

    Written directly rather than through the service: these rows exist to be *read* by a sync, and
    both deliberate choices are the point. Random ids would make the tie between rows 2 and 3 a coin
    flip (UUID order is not creation order), so the ids are fixed — and with them, a `date`-only
    filter provably skips or repeats row 3, which is what AC7.4's "no duplicates" is really about.
    """
    now = timezone.now()
    stamps = [now - timedelta(seconds=2), now - timedelta(seconds=1), now - timedelta(seconds=1)]
    ids = [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
    ]
    return [
        TimelineEvent.objects.create(
            case=case, id=event_id, date=stamp, title=f"seeded {index + 1}", kind="comment"
        )
        for index, (event_id, stamp) in enumerate(zip(ids, stamps, strict=True))
    ]


def _ids(payload: dict[str, Any]) -> list[str]:
    return [entry["id"] for entry in payload["events"]]


# --- the choke-point scan (mutation guard) -------------------------------


def _direct_creates(root: Path, allowed: set[Path]) -> list[Path]:
    """Every scanned file that writes a `TimelineEvent` row itself instead of via the ledger."""
    found: list[Path] = []
    for app in SCANNED_APPS:
        for path in sorted((root / app).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if path in allowed:
                continue
            if DIRECT_CREATE in path.read_text(encoding="utf-8"):
                found.append(path.relative_to(root))
    return found


def test_the_ledger_service_is_the_only_writer_of_timeline_rows() -> None:
    """A row written anywhere else skips the commit hook and is therefore invisible live (brief T1).

    The audit reads every app that writes ledger rows — `cases/`, `ui/` and `automation/` from brief
    T1, plus `alerts/`, whose two escalation sites were the last direct writers left in the product.
    Widening the scope is what makes "every" true rather than hopeful: a new writer in any of them
    fails the build instead of quietly going unpublished.

    The positive half matters as much as the negative: if the literal vanished from `ledger.py` the
    scan would pass against a service that writes nothing, and every "both tabs saw it" assertion
    above would fail for a reason unrelated to realtime.
    """
    assert not _direct_creates(REPO_ROOT, allowed={LEDGER}), (
        "create a TimelineEvent through cases.ledger.append_timeline_event instead"
    )
    assert DIRECT_CREATE in LEDGER.read_text(encoding="utf-8"), (
        "cases/ledger.py no longer writes TimelineEvent rows"
    )


def test_the_scan_would_fail_a_call_site_reverted_to_a_direct_create(tmp_path: Path) -> None:
    """The guard has to be able to fail, or it is a comment.

    Reconstructs the mutation it exists to catch — a call site put back to
    `TimelineEvent.objects.create` — against a synthetic tree that mirrors the widened scope (an
    `alerts/escalation.py`, a `ui/views.py`, an empty `automation/`), plus the two shapes it must
    not mistake for one: the ledger itself and bytecode caches. Each violation is then repaired in
    turn, so a scanner that only ever found the first app would fail here too.
    """
    for app in SCANNED_APPS:
        (tmp_path / app).mkdir()
    ledger = tmp_path / "cases" / "ledger.py"
    ledger.write_text("event = TimelineEvent.objects.create(case=case)\n", encoding="utf-8")

    escalation = tmp_path / "alerts" / "escalation.py"
    escalation.write_text("TimelineEvent.objects.create(case=case)\n", encoding="utf-8")

    views = tmp_path / "ui" / "views.py"
    views.write_text("TimelineEvent.objects.create(case=case)\n", encoding="utf-8")

    cache = tmp_path / "ui" / "__pycache__"
    cache.mkdir()
    (cache / "models.py").write_text(DIRECT_CREATE, encoding="utf-8")

    assert _direct_creates(tmp_path, allowed={ledger}) == [
        Path("alerts/escalation.py"),
        Path("ui/views.py"),
    ]

    escalation.write_text("append_timeline_event(case, title='ok', kind='alert-merged')\n")
    assert _direct_creates(tmp_path, allowed={ledger}) == [Path("ui/views.py")]

    views.write_text("event = append_timeline_event(case, title='ok', kind='comment')\n")
    assert _direct_creates(tmp_path, allowed={ledger}) == []


# --- AC7.2 — the handshake ------------------------------------------------


def test_an_anonymous_handshake_is_refused_with_4401(default_org: Organisation) -> None:
    """No session, no room (PLAN AC7.2 / brief 4).

    The publish afterwards is the real claim: a consumer that refused the handshake must also have
    stayed out of the group, or a bug that joins before authenticating would still look correct
    from the close code alone.
    """
    case = _case("Unlisted", org=default_org)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(application, f"/ws/case/{case.id}/")
        connected, close_code = await communicator.connect(timeout=5)
        assert connected is False, "an anonymous socket was accepted"
        assert close_code == UNAUTHENTICATED, f"refused with {close_code}, expected 4401"

        await _publish(
            str(case.id), {"id": "0f2f1c4a-4c1a-4a0f-8f2f-0f2f1c4a4c1a", "title": "unreachable"}
        )
        assert await communicator.receive_nothing(timeout=0.5), (
            "the refused socket was still subscribed to the room"
        )

        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


# --- AC7.1 — the publish chain -------------------------------------------


def test_a_comment_reaches_two_tabs_and_the_htmx_append(
    browser: Client, analyst: User, django_capture_on_commit_callbacks: object
) -> None:
    """Two concurrent clients see a new comment without reloading (PLAN AC7.1 / brief T3).

    The POST is made with `HX-Request`, so the response is the single `<li>` the browser appends;
    that `data-event-id` is then asserted to be the same id both sockets deliver, which ties the
    HTTP append and the WebSocket append to one event rather than two similar ones.
    """
    case = _case("Two tabs", org=analyst.org)

    def post_note() -> str:
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            response = browser.post(
                reverse("ui-case-comment", kwargs={"case_id": case.number}),
                {"body": "Both tabs should see this."},
                headers={"HX-Request": "true"},
            )
        assert response.status_code == 200, response.content
        return response.content.decode()

    async def scenario() -> None:
        first, second = _room(case, browser), _room(case, browser)
        assert (await first.connect(timeout=5))[0] is True
        assert (await second.connect(timeout=5))[0] is True

        html = await sync_to_async(post_note)()
        assert html.count("<li") == 1, f"the HX response must be one entry, got: {html}"
        appended = re.search(r'data-event-id="([^"]+)"', html)
        assert appended is not None, "the appended <li> carries no data-event-id"
        event_id = appended.group(1)

        for communicator in (first, second):
            message = await communicator.receive_json_from(timeout=5)
            assert message["type"] == "event"
            assert message["event_type"] == "comment"
            assert message["payload"]["event"]["id"] == event_id
            assert message["payload"]["event"]["description"] == "Both tabs should see this."

        for communicator in (first, second):
            await communicator.disconnect()
            await communicator.wait(timeout=5)

    async_to_sync(scenario)()


def test_status_changes_and_automation_results_are_published(
    browser: Client, analyst: User, django_capture_on_commit_callbacks: object
) -> None:
    """The other two emitters of brief T3: the UI status form and the worker's `record_result`.

    Both matter because neither is a comment: one is an analyst clicking a control, the other runs
    in the Celery worker after the request that triggered the automation has gone away. If either
    bypassed the choke point the case would look idle while it was changing.
    """
    case = _case("Changing", org=analyst.org)
    playbook = Playbook.objects.create(name="enrich", trigger_event="observable.created")

    def post_status() -> None:
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            response = browser.post(
                reverse("ui-case-status", kwargs={"case_id": case.number}),
                {"stage": "Closed"},
            )
        assert response.status_code == 302, response.content

    def finish_run() -> None:
        run = AutomationRun.objects.create(
            case=case, playbook=playbook, playbook_name="enrich", idempotency_key="realtime:1"
        )
        result = type("Result", (), {"ok": True, "output_log": "2 lookups", "error": ""})()
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            record_result(run, result, subject_label="enrich")

    async def scenario() -> None:
        communicator = _room(case, browser)
        assert (await communicator.connect(timeout=5))[0] is True

        await sync_to_async(post_status)()
        status_message = await communicator.receive_json_from(timeout=5)
        assert status_message["event_type"] == "status-change"
        assert status_message["payload"]["event"]["metadata"]["stage"] == "Closed"

        await sync_to_async(finish_run)()
        automation_message = await communicator.receive_json_from(timeout=5)
        assert automation_message["event_type"] == "automation-run"
        assert automation_message["payload"]["event"]["metadata"]["playbook"] == "enrich"
        assert automation_message["payload"]["event"]["title"].startswith("Automation success")

        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


# --- AC7.3 — room isolation ----------------------------------------------


def test_a_publish_never_reaches_another_cases_room(
    default_org: Organisation, browser: Client
) -> None:
    """Case-scoped events never leak (PLAN AC7.3).

    Both sockets are authenticated and connected before the publish, so the only variable is which
    room the event was addressed to.
    """
    case_a, case_b = _case("Alpha", org=default_org), _case("Beta", org=default_org)
    event_id = "6b3a2a8e-6d1f-4d0a-9a4a-0d5f6b7c8d9e"

    async def scenario() -> None:
        room_a, room_b = _room(case_a, browser), _room(case_b, browser)
        assert (await room_a.connect(timeout=5))[0] is True
        assert (await room_b.connect(timeout=5))[0] is True

        await _publish(str(case_a.id), {"id": event_id, "title": "only alpha", "kind": "comment"})

        received = await room_a.receive_json_from(timeout=5)
        assert received["type"] == "event"
        assert received["payload"]["event"]["id"] == event_id
        assert await room_b.receive_nothing(timeout=0.5), "case B saw case A's event"

        for communicator in (room_a, room_b):
            await communicator.disconnect()
            await communicator.wait(timeout=5)

    async_to_sync(scenario)()


# --- AC7.4 — sync, reconnect, no duplicates -------------------------------


def test_sync_resumes_from_the_anchor_after_a_reconnect_without_duplicates(
    default_org: Organisation, browser: Client, django_capture_on_commit_callbacks: object
) -> None:
    """Reconnect + re-sync from the timeline, once, without repeats (PLAN AC7.4 / brief 5).

    The seeded rows deliberately share a timestamp on their last two entries, so the "exactly the
    third event" assertion is also the assertion that the keyset tie-breaks on `id`.
    """
    case = _case("Resync", org=default_org)
    seeded = _seed_events(case)

    def add_live_note() -> str:
        """A real row, published through the service.

        Sync reads the database, so an event that existed only on the wire could never come back in
        a later sync — and the "was it served twice?" assertion below would prove nothing.
        """
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            event = append_timeline_event(case, title="live", kind="comment")
        return str(event.id)

    async def scenario() -> None:
        first = _room(case, browser)
        assert (await first.connect(timeout=5))[0] is True

        # Whole ledger when there is no anchor.
        await first.send_json_to({"type": "sync", "after": None})
        assert _ids(await first.receive_json_from(timeout=5)) == [str(e.id) for e in seeded]

        # Exactly the third, despite sharing a timestamp with the second.
        await first.send_json_to({"type": "sync", "after": str(seeded[1].id)})
        assert _ids(await first.receive_json_from(timeout=5)) == [str(seeded[2].id)]

        # Drop the connection, then rejoin and sync from the last id we rendered.
        await first.disconnect()
        await first.wait(timeout=5)

        rejoin = _room(case, browser)
        assert (await rejoin.connect(timeout=5))[0] is True
        await rejoin.send_json_to({"type": "sync", "after": str(seeded[2].id)})
        assert await rejoin.receive_json_from(timeout=5) == {"type": "timeline", "events": []}

        # A live event while we are connected…
        live_id = await sync_to_async(add_live_note)()
        live = await rejoin.receive_json_from(timeout=5)
        assert live["type"] == "event"
        assert live["payload"]["event"]["id"] == live_id

        # …must not be served a second time when the same client syncs from its old anchor.
        await rejoin.send_json_to({"type": "sync", "after": str(seeded[1].id)})
        served = _ids(await rejoin.receive_json_from(timeout=5))
        assert served == [str(seeded[2].id), live_id], "sync re-sent an id the client already has"
        assert len(served) == len(set(served)), "sync served a duplicate"

        await rejoin.disconnect()
        await rejoin.wait(timeout=5)

    async_to_sync(scenario)()


def test_client_messages_outside_the_protocol_are_refused_with_4000(
    default_org: Organisation, browser: Client
) -> None:
    """Unknown shapes are closed, not echoed (brief T2).

    The echo this consumer used to reply with would make a client bug look like a working protocol,
    so every refusal is asserted as a close code rather than as "the wrong thing came back".
    """
    case = _case("Strict room", org=default_org)

    async def expect_refusal(payload: dict[str, Any] | str) -> None:
        communicator = _room(case, browser)
        assert (await communicator.connect(timeout=5))[0] is True
        if isinstance(payload, str):
            await communicator.send_input({"type": "websocket.receive", "text": payload})
        else:
            await communicator.send_json_to(payload)
        message = await communicator.receive_output(timeout=5)
        assert message["type"] == "websocket.close", f"unexpected frame: {message}"
        assert message.get("code") == PROTOCOL_ERROR, f"closed with {message.get('code')}"
        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async def scenario() -> None:
        await expect_refusal({"type": "wibble"})
        await expect_refusal({"type": "sync", "after": "not-a-uuid"})
        await expect_refusal({"type": "sync", "after": 7})
        await expect_refusal("{not json")

    async_to_sync(scenario)()


# --- T4 — the page wires the client --------------------------------------


def test_the_case_page_wires_the_live_client_and_the_htmx_append(
    browser: Client, analyst: User
) -> None:
    """The page the two tabs load: script, socket URL and the HTMX append attributes (brief T4).

    The URL is asserted as a full `ws://…/ws/case/<uuid>/` rather than a substring: a template that
    dropped the scheme or the case id would still contain `ws/case/` and pass.
    """
    case = _case("Wired", org=analyst.org)
    response = browser.get(reverse("ui-case-detail", kwargs={"case_id": case.id}))
    assert response.status_code == 200
    html = response.content.decode()

    assert '<script src="/static/ui/live.js" defer></script>' in html, (
        "the live client is not loaded on the case page"
    )
    assert f'data-ws-url="ws://testserver/ws/case/{case.id}/"' in html
    assert 'id="timeline-events"' in html

    assert 'hx-post="' in html and 'hx-target="#timeline-events"' in html
    assert 'hx-swap="beforeend"' in html and 'hx-disabled-elt="this"' in html
    # Without htmx the form is still a plain POST to the same action — the fallback is the design,
    # not an accident, so the two URLs must agree. Both attributes must be read from the *same*
    # tag: a page-wide `hx-post` search finds the first one anywhere, which is now the tasks-tab
    # modal form that precedes the comment form, and would compare two unrelated endpoints.
    comment_form = re.search(r'<form [^>]*class="comment"[^>]*>', html)
    assert comment_form is not None, "the comment form is missing from the case page"
    action = re.search(r'action="([^"]+)"', comment_form.group(0))
    hx_post = re.search(r'hx-post="([^"]+)"', comment_form.group(0))
    assert action is not None and hx_post is not None
    assert action.group(1) == hx_post.group(1)


def test_a_plain_browser_still_gets_the_redirect_fallback(browser: Client, analyst: User) -> None:
    """No `HX-Request` → the pre-HTMX behaviour, byte for byte (brief 6)."""
    case = _case("No JS", org=analyst.org)
    response = browser.post(
        reverse("ui-case-comment", kwargs={"case_id": case.number}), {"body": "from a form"}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("ui-case-detail", kwargs={"case_id": case.number})
    assert case.timeline_events.filter(kind="comment").count() == 1


# --- T5 — tenant isolation on the case room (PLAN §9 P5) ------------------
#
# The HTTP boundary is scoped by `identity.scoping.resolve_case`; the WebSocket handshake must
# consult *the same* helper before `accept()`, or a tenant could read another tenant's live ledger
# by guessing a UUID. `4404` is deliberately the same close code for a cross-org case and for a
# non-existent one: a distinct code would turn the handshake into an existence oracle.


def _org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _ws_browser(username: str, org: Organisation, role: str = "analyst") -> Client:
    """A logged-in client whose session cookie the ASGI stack can read (see `_room`)."""
    User.objects.create_user(
        username=username,
        password="hunter2-correct",
        email=f"{username}@example.net",
        org=org,
        role=role,
    )
    client = Client()
    assert client.login(username=username, password="hunter2-correct")
    return client


def _room_for(case_id: object, browser: Client) -> WebsocketCommunicator:
    """A socket for an arbitrary case id, carrying `browser`'s real session cookie."""
    cookie = browser.cookies["sessionid"].OutputString()
    return WebsocketCommunicator(
        application,
        f"/ws/case/{case_id}/",
        headers=[(b"cookie", cookie.encode("ascii"))],
    )


def test_t5a_cross_org_socket_is_refused_4404_and_receives_no_events(
    default_org: Organisation,
) -> None:
    """A user in org B cannot join org A's room, and learns nothing after the refusal.

    The `receive_nothing` after publishing is the load-bearing half: a consumer that closed the
    handshake but still called `group_add` would pass a close-code-only assertion while leaking
    every subsequent timeline event to the refused tenant.
    """
    org_b = _org("Org B")
    case_a = _case("Org A secret", org=default_org)
    foreign = _ws_browser("org-b-analyst", org_b)
    event_id = "11111111-1111-4111-8111-111111111111"

    async def scenario() -> None:
        cross = _room_for(case_a.id, foreign)
        connected, close_code = await cross.connect(timeout=5)
        assert connected is False, "a cross-org socket was accepted"
        assert close_code == FORBIDDEN, f"refused with {close_code}, expected 4404"

        await _publish(str(case_a.id), {"id": event_id, "title": "leaked"})
        assert await cross.receive_nothing(timeout=0.5), (
            "the refused cross-org socket was still subscribed to the room"
        )

        await cross.disconnect()
        await cross.wait(timeout=5)

    async_to_sync(scenario)()


def test_t5a_non_existent_case_uses_the_same_4404_no_existence_oracle(
    default_org: Organisation,
) -> None:
    """Cross-org and non-existent cases are indistinguishable at the handshake (no oracle)."""
    org_b = _org("Org B")
    case_a = _case("Org A secret", org=default_org)
    foreign = _ws_browser("org-b-prober", org_b)

    async def scenario() -> None:
        missing = _room_for(uuid4(), foreign)
        missing_connected, missing_code = await missing.connect(timeout=5)

        cross = _room_for(case_a.id, foreign)
        cross_connected, cross_code = await cross.connect(timeout=5)

        assert missing_connected is False and cross_connected is False
        assert missing_code == cross_code == FORBIDDEN, (
            f"existence oracle: missing={missing_code}, cross={cross_code}"
        )

        for communicator in (missing, cross):
            await communicator.disconnect()
            await communicator.wait(timeout=5)

    async_to_sync(scenario)()


def test_t5b_owner_org_stream_still_handshakes_syncs_and_relays(
    browser: Client, analyst: User
) -> None:
    """The owner-org path is unchanged: accept, sync the ledger, relay a live event (T5-b)."""
    case = _case("Owner stream", org=analyst.org)
    seeded = _seed_events(case)
    event_id = "22222222-2222-4222-8222-222222222222"

    async def scenario() -> None:
        communicator = _room(case, browser)
        connected, close_code = await communicator.connect(timeout=5)
        assert connected is True, f"owner-org socket refused with {close_code}"

        await communicator.send_json_to({"type": "sync", "after": None})
        payload = await communicator.receive_json_from(timeout=5)
        assert payload["type"] == "timeline"
        assert _ids(payload) == [str(event.id) for event in seeded]

        await _publish(str(case.id), {"id": event_id, "title": "live from owner"})
        live = await communicator.receive_json_from(timeout=5)
        assert live["type"] == "event"
        assert live["payload"]["event"]["id"] == event_id

        await communicator.disconnect()
        await communicator.wait(timeout=5)

    async_to_sync(scenario)()


def test_t5c_anonymous_4401_while_read_share_and_superadmin_may_subscribe(
    default_org: Organisation,
) -> None:
    """Anonymous stays 4401; a read-share holder and a superAdmin are accepted (T5-c)."""
    case = _case("Shared room", org=default_org)
    org_b = _org("Org B")
    Share.objects.create(case=case, organisation=org_b, permissions={"read": True})
    reader = _ws_browser("org-b-reader", org_b)
    superadmin = _ws_browser("org-b-super", org_b, role="superAdmin")

    async def scenario() -> None:
        anonymous = WebsocketCommunicator(application, f"/ws/case/{case.id}/")
        connected, close_code = await anonymous.connect(timeout=5)
        assert connected is False, "an anonymous socket was accepted"
        assert close_code == UNAUTHENTICATED, f"refused with {close_code}, expected 4401"
        await anonymous.disconnect()
        await anonymous.wait(timeout=5)

        for socket_browser in (reader, superadmin):
            communicator = _room_for(case.id, socket_browser)
            accepted, refused_code = await communicator.connect(timeout=5)
            assert accepted is True, f"expected accept, got {refused_code}"
            await communicator.disconnect()
            await communicator.wait(timeout=5)

    async_to_sync(scenario)()
