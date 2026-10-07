"""AC-B3 — contract tests for every §5 route of the T1 closure (plan §5, §12).

Where `test_authz.py` proves *who may call*, this file proves *what comes back*. Each test is
one acceptance criterion read literally:

=====  =====================================================================
AC-A1  `DELETE alert` → 204 empty, then 404
AC-A2  `DELETE case` → 204 with the alert standing (P10-c); un-linking an
       alert → 204, alert survives, ledger records the removal
AC-A3  task `GET` → `OutputTask`; `PATCH` touches only the keys present and
       400s on an unknown status; `DELETE` → 204 then 404; create defaults
       to `Waiting`
AC-A4  `POST customEvent` → 201 + a WebSocket publish; `PATCH`/`DELETE` →
       204; a system ledger kind → 400
AC-A5  observable `PATCH` → 204, fields applied, `dataType` re-hashes;
       `DELETE` → 204, links gone, `GET` → 404
AC-A6  login → 200 + `OutputUser` + session cookie; bad credentials and a
       foreign organisation → 400 (never 401); logout → 200 and the session
       it held no longer authenticates
AC-A7  the four new serializers emit exactly the key set the golden corpus
       records — asserted against `PINNED_KEYS`, so the fixture and the
       live contract cannot drift apart in silence
=====  =====================================================================

Three sweeps sit alongside them, because these are the claims that are easy to make endpoint
by endpoint and impossible to keep true:

* **ids** — every `_id` on the new surface is a plain UUID. A single `~123` row id would be
  enough to break a client that parses ids by `uuid.UUID(...)`, so the sweep walks the whole
  surface rather than spot-checking one serializer;
* **timestamps** — the new entity serializers are ISO-8601 (deviation **P10-y**), never the
  millisecond integers the recorded OpenAPI shows, and never a bare epoch that a client cannot
  tell from a `date` it must interpret as seconds or milliseconds;
* **errors** — unknown ids are 404 with the envelope, empty patches are 400 *and write
  nothing*, and neither ever answers 500.

The WebSocket test is the only one that needs a socket: `django_capture_on_commit_callbacks`
stands in for the commit the API view's `transaction.on_commit` is waiting on, and the room is
joined with the real session cookie that `POST /api/v1/login` just handed out — so the same
test also proves the cookie the login endpoint sets is the one the realtime stack accepts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import channels.db
import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from alerts.models import Alert
from amalthea.asgi import application
from amalthea.settings.base import REST_FRAMEWORK as _BASE_REST_FRAMEWORK
from cases.ledger import append_timeline_event
from cases.models import Case, CustomField, Task, TimelineEvent
from core.serializers import custom_event_json, custom_field_json, task_json, user_json
from identity.models import Organisation, User
from observables.hashing import data_hash
from observables.models import Observable, ObservableType

from ._t1_seed import _alert, _api_key_client, _call, _case, _event, _observable, _task
from .test_thehive_fixtures import PINNED_KEYS

# S108: observable *data* values used as digest inputs. `observables/hashing.py` is string-only,
# so nothing here is opened, read or written as a path.
SENSITIVE_PATH = "/Tmp/Report.PDF"
FOLDED_PATH = "/tmp/report.pdf"  # noqa: S108


@pytest.fixture(autouse=True)
def _no_connection_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the consumer's per-dispatch connection cleanup from tearing down the test database.

    `AsyncConsumer.dispatch` closes "old" connections around every message, which is right for a
    request/response app and wrong for a test holding a single uncommitted connection — on
    Postgres it would close it mid-test. Channels patches the same function for the same reason
    (`test_realtime.py` carries the longer explanation).
    """
    monkeypatch.setattr(channels.db, "close_old_connections", lambda: None)


@pytest.fixture
def ids(db: None) -> dict[str, str]:
    case = _case("Contract case")
    return {
        "case": str(case.id),
        "alert": str(_alert("Contract alert", ref="contract-ref", case=case).id),
        "task": str(_task(case).id),
        "event": str(_event(case).id),
        "observable": str(_observable("10.0.0.9").id),
    }


@pytest.fixture
def writer(db: None) -> tuple[APIClient, User]:
    """A client that may write, keyed off a real bearer token (not `force_authenticate`).

    `force_authenticate` bypasses the authentication layer entirely, which is fine for shape
    tests but wrong here: these assertions are the ones a real client's `Authorization` header
    would produce, and `ScopePermission` reads `request.auth`.
    """
    org = Organisation.objects.create(name="Contract Org")
    user = User.objects.create(
        login="contract", username="contract", email="contract@example.net", org=org, is_active=True
    )
    client, _token = _api_key_client(user, scope="readwrite")
    return client, user


# --- AC-A7 — the serializers against the golden corpus ---------------------


@pytest.mark.django_db
def test_task_serializer_emits_exactly_the_recorded_key_set() -> None:
    task = _task(_case("Serializer case"))
    assert set(task_json(task)) == PINNED_KEYS["task_example.json"]


@pytest.mark.django_db
def test_custom_event_serializer_emits_exactly_the_recorded_key_set() -> None:
    event = _event(_case("Serializer case"))
    assert set(custom_event_json(event)) == PINNED_KEYS["custom_event_example.json"]


@pytest.mark.django_db
def test_custom_field_serializer_emits_exactly_the_recorded_key_set() -> None:
    field = CustomField.objects.create(name="threat-type", group="Classification", type="string")
    assert set(custom_field_json(field)) == PINNED_KEYS["custom_field_example.json"]


@pytest.mark.django_db
def test_user_serializer_emits_exactly_the_recorded_key_set() -> None:
    org = Organisation.objects.create(name="Serializer Org")
    user = User.objects.create(login="lucas", username="lucas", org=org, is_active=True)
    assert set(user_json(user)) == PINNED_KEYS["user_example.json"]


# --- AC-A3 — tasks ---------------------------------------------------------


def test_task_get_returns_the_output_task_shape(writer: tuple[APIClient, User]) -> None:
    client, _ = writer
    task = _task(_case("Task shape"), title="Isolate affected workstation")
    response = client.get(f"/api/v1/task/{task.id}")
    assert response.status_code == 200, response.content

    body = response.json()
    assert set(body) == PINNED_KEYS["task_example.json"]
    assert body["_type"] == "Task"
    assert body["title"] == "Isolate affected workstation"
    # The model's default, which is what the create endpoint relies on too (plan §13, A3).
    assert body["status"] == "Waiting"
    assert body["id"] == body["_id"]
    assert body["assignee"] is None and body["_createdBy"] is None


def test_task_patch_updates_only_the_keys_that_are_present(
    writer: tuple[APIClient, User],
) -> None:
    client, user = writer
    task = _task(_case("Task patch"), title="Before", description="untouched", status="Waiting")

    response = client.patch(f"/api/v1/task/{task.id}", {"title": "After"}, format="json")
    assert response.status_code == 204, response.content
    assert response.content == b"", "a 204 must carry no body"

    task.refresh_from_db()
    assert task.title == "After"
    # Only-present (ADR D11): an absent key is not a write, not even to its default.
    assert task.description == "untouched"
    assert task.status == "Waiting"

    assigned = client.patch(f"/api/v1/task/{task.id}", {"assignee": "contract"}, format="json")
    assert assigned.status_code == 204, assigned.content
    task.refresh_from_db()
    assert task.assignee_id == user.pk


def test_task_patch_refuses_a_status_the_vocabulary_does_not_have(
    writer: tuple[APIClient, User],
) -> None:
    """`Todo` is TheHive's word, not ours — 400 here, not a CHECK violation's 500 on Postgres."""
    client, _ = writer
    task = _task(_case("Task status"))
    response = client.patch(f"/api/v1/task/{task.id}", {"status": "Todo"}, format="json")
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["type"] == "BadRequest"
    assert "fields" in body

    task.refresh_from_db()
    assert task.status == "Waiting", "a refused patch must not have written"


def test_task_delete_is_204_then_404(writer: tuple[APIClient, User]) -> None:
    client, _ = writer
    task = _task(_case("Task delete"))
    deleted = client.delete(f"/api/v1/task/{task.id}")
    assert deleted.status_code == 204, deleted.content
    assert deleted.content == b""

    again = client.get(f"/api/v1/task/{task.id}")
    assert again.status_code == 404, again.content
    assert again.json()["type"] == "NotFoundError"


def test_case_task_create_defaults_to_waiting(api: APIClient, ids: dict[str, str]) -> None:
    """The `"Todo"` default violated `task_status_valid` on Postgres (plan §13, A3)."""
    response = api.post(f"/api/v1/case/{ids['case']}/task", {"title": "New task"}, format="json")
    assert response.status_code == 201, response.content

    body = response.json()
    assert body["status"] == "Waiting"
    assert body["title"] == "New task"
    assert body["_id"] == body["id"]
    assert str(Task.objects.get(pk=body["_id"]).case_id) == ids["case"]


# --- AC-A1 / AC-A2 — deletions and un-linking ------------------------------


def test_alert_delete_is_204_empty_and_then_404(api: APIClient, ids: dict[str, str]) -> None:
    deleted = api.delete(f"/api/v1/alert/{ids['alert']}")
    assert deleted.status_code == 204, deleted.content
    assert deleted.content == b"", "a 204 must carry no body"
    assert not Alert.objects.filter(pk=ids["alert"]).exists()

    again = api.get(f"/api/v1/alert/{ids['alert']}")
    assert again.status_code == 404, again.content
    assert again.json()["type"] == "NotFoundError"


def test_alert_import_into_a_named_case_is_the_merge_spelling(
    api: APIClient, ids: dict[str, str]
) -> None:
    """`POST /api/v1/alert/{alertId}/import/{caseId}` — the verifier's untested §7.2 row.

    The route is `alert-import-into` and aliases the merge handler (`alerts/urls.py`); the
    no-case `/import` spelling (promote into a *new* case) is covered by `test_mvp_loop.py`.
    This pins the fully-qualified spelling a client uses when the target case already exists:
    200 with the `OutputCase`, and the alert's `case` FK now points at it.
    """
    case = Case.objects.get(pk=ids["case"])
    fresh = _alert("Import-target alert", ref="import-into-ref")
    response = api.post(f"/api/v1/alert/{fresh.id}/import/{case.id}", {}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["_id"] == str(case.id), "the response is the case, not the alert"

    fresh.refresh_from_db()
    assert fresh.case_id == case.id, "the import did not link the alert to the named case"


def test_case_delete_is_204_and_leaves_the_alert_standing(
    api: APIClient, ids: dict[str, str]
) -> None:
    """Deviation **P10-c**: alerts `SET_NULL` and survive, because a deleted case's alert is
    exactly the record an analyst still needs to re-open the investigation.

    Asserting the alert row *exists* after the case is gone is the claim; asserting only the
    204 would pass even if the cascade had taken the alert with it.
    """
    alert = Alert.objects.get(pk=ids["alert"])
    assert alert.case_id is not None, "precondition: the alert is linked to the case"

    deleted = api.delete(f"/api/v1/case/{ids['case']}")
    assert deleted.status_code == 204, deleted.content
    assert deleted.content == b""

    alert.refresh_from_db()
    assert alert.case_id is None, "the alert was destroyed with the case instead of standing"
    assert api.get(f"/api/v1/case/{ids['case']}").status_code == 404


def test_unlinking_an_alert_survives_and_is_written_to_the_ledger(
    api: APIClient, ids: dict[str, str]
) -> None:
    """AC-A2: 204, the alert lives on unlinked, and the case timeline records who did it."""
    response = api.delete(f"/api/v1/case/{ids['case']}/alert/{ids['alert']}")
    assert response.status_code == 204, response.content
    assert response.content == b""

    alert = Alert.objects.get(pk=ids["alert"])
    assert alert.case_id is None, "un-link must not destroy"

    removal = TimelineEvent.objects.filter(case_id=ids["case"], kind="alert-removed").first()
    assert removal is not None, "the ledger never recorded the removal"
    assert removal.metadata.get("alert_id") == ids["alert"]


# --- AC-A4 — custom events -------------------------------------------------


def test_custom_event_post_is_201_and_records_the_callers_clock(
    api: APIClient, ids: dict[str, str]
) -> None:
    """`date` is the row's `date` — an event back-dated to when it happened, not to now."""
    when = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp() * 1000)
    response = api.post(
        f"/api/v1/case/{ids['case']}/customEvent",
        {"title": "Ransom note received", "date": when, "description": "by email"},
        format="json",
    )
    assert response.status_code == 201, response.content

    body = response.json()
    assert set(body) == PINNED_KEYS["custom_event_example.json"]
    assert body["_type"] == "CustomEvent"
    assert body["caseId"] == ids["case"]
    assert body["title"] == "Ransom note received"
    assert datetime.fromisoformat(body["date"]) == datetime.fromtimestamp(when / 1000, UTC)


def test_custom_event_post_rejects_a_payload_without_a_date(
    api: APIClient, ids: dict[str, str]
) -> None:
    response = api.post(
        f"/api/v1/case/{ids['case']}/customEvent", {"title": "No clock"}, format="json"
    )
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["type"] == "BadRequest"
    assert "date" in body["fields"]


def test_a_hostile_date_string_is_a_clean_400_and_never_a_500(
    api: APIClient, ids: dict[str, str]
) -> None:
    """ "Infinity" took `parse_timestamp` into `fromtimestamp`, which raises on it.

    The contract for anything unparseable is the same 400 `invalid timestamp` envelope a
    malformed ISO string gets (auditor F5), never a 500 out of the datetime library.
    """
    response = api.post(
        f"/api/v1/case/{ids['case']}/customEvent",
        {"title": "Doomed", "date": "Infinity"},
        format="json",
    )
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["type"] == "BadRequest"
    assert body["fields"] == {"date": ["invalid timestamp"]}
    assert not TimelineEvent.objects.filter(title="Doomed").exists(), "a refused POST wrote"


def test_an_unbounded_custom_event_title_is_truncated_not_rejected(
    api: APIClient, ids: dict[str, str]
) -> None:
    """`TimelineEvent.title` is `CharField(max_length=500)` — Postgres enforces it, SQLite does not.

    Without the bound at both write sites (create *and* patch), the same body is a 201 on
    SQLite and a `DataError` 500 on Postgres (auditor F4); the contract is one answer on both.
    """
    long_title = "R" * 1200
    date_ms = int(timezone.now().timestamp() * 1000)

    created = api.post(
        f"/api/v1/case/{ids['case']}/customEvent",
        {"title": long_title, "date": date_ms},
        format="json",
    )
    assert created.status_code == 201, created.content
    event_id = created.json()["_id"]
    assert created.json()["title"] == "R" * 500, "the title was stored past the column limit"

    patched = api.patch(f"/api/v1/customEvent/{event_id}", {"title": long_title}, format="json")
    assert patched.status_code == 204, patched.content
    assert TimelineEvent.objects.get(pk=event_id).title == "R" * 500


def test_custom_event_patch_and_delete_are_204(api: APIClient, ids: dict[str, str]) -> None:
    """204/204, read back off the row.

    There is no `GET /api/v1/customEvent/{id}` (plan §5 lists PATCH/DELETE only), and the
    timeline wire is the P8-1 envelope — ms `date`, mapped `kind`, no title and no event id —
    so the row itself is the only place a title can be observed. The *status* is the contract;
    the row check is what proves the 204 was earned rather than returned by an empty branch.
    """
    event_id = ids["event"]

    patched = api.patch(f"/api/v1/customEvent/{event_id}", {"title": "Edited note"}, format="json")
    assert patched.status_code == 204, patched.content
    assert patched.content == b""
    assert TimelineEvent.objects.get(pk=event_id).title == "Edited note"

    deleted = api.delete(f"/api/v1/customEvent/{event_id}")
    assert deleted.status_code == 204, deleted.content
    assert deleted.content == b""
    assert not TimelineEvent.objects.filter(pk=event_id).exists()


def test_custom_event_patch_refuses_a_system_ledger_kind(
    api: APIClient, ids: dict[str, str]
) -> None:
    """An analyst edits their own note, never the machine's audit trail (AC-A4)."""
    case = Case.objects.get(pk=ids["case"])
    system = append_timeline_event(case, title="Case created", kind="case-created")

    response = api.patch(
        f"/api/v1/customEvent/{system.id}", {"title": "Rewrite history"}, format="json"
    )
    assert response.status_code == 400, response.content
    assert response.json()["type"] == "BadRequest"
    system.refresh_from_db()
    assert system.title != "Rewrite history"


def test_a_custom_event_reaches_a_joined_case_room(
    db: None, django_capture_on_commit_callbacks: Any
) -> None:
    """AC-A4's second half: the 201 is followed by a WebSocket publish to `case_{id}`.

    The room is joined with the session cookie `POST /api/v1/login` issued, which makes this
    one test carry two claims — that the cookie exists, and that `AuthMiddlewareStack` accepts
    it. Publishing has to happen inside `sync_to_async`, because `publish_case_event` drives
    `group_send` with `async_to_sync` and asgiref routes that to the loop the consumers live
    on (see `test_realtime.py` for the long version of why).
    """
    org = Organisation.objects.create(name="Realtime Org")
    User.objects.create_user(
        username="realtime", password="hunter2-correct", email="rt@example.net", org=org
    )
    case = _case("Published case")
    login_client = APIClient()
    login = login_client.post(
        "/api/v1/login", {"user": "realtime", "password": "hunter2-correct"}, format="json"
    )
    assert login.status_code == 200, login.content
    cookie = login_client.cookies["sessionid"].OutputString()

    def post_event() -> str:
        with django_capture_on_commit_callbacks(execute=True):
            created = login_client.post(
                f"/api/v1/case/{case.id}/customEvent",
                {"title": "Indicator found", "date": int(timezone.now().timestamp() * 1000)},
                format="json",
            )
        assert created.status_code == 201, created.content
        return str(created.json()["_id"])

    async def scenario() -> None:
        room = WebsocketCommunicator(
            application,
            f"/ws/case/{case.id}/",
            headers=[(b"cookie", cookie.encode("ascii"))],
        )
        connected, _ = await room.connect(timeout=5)
        assert connected is True, "the login cookie was refused by the realtime stack"

        event_id = await sync_to_async(post_event)()

        message = await room.receive_json_from(timeout=5)
        assert message["type"] == "event"
        assert message["payload"]["event"]["id"] == event_id
        assert message["payload"]["event"]["title"] == "Indicator found"

        await room.disconnect()
        await room.wait(timeout=5)

    async_to_sync(scenario)()


# --- AC-A5 — observables ---------------------------------------------------


def test_observable_get_returns_the_artifact_and_the_cases_that_carry_it(
    api: APIClient, ids: dict[str, str]
) -> None:
    response = api.get(f"/api/v1/observable/{ids['observable']}")
    assert response.status_code == 200, response.content

    body = response.json()
    assert body["_type"] == "observable"
    assert body["dataType"] == "ip"
    assert body["data"] == "10.0.0.9"
    assert [entry["_id"] for entry in body["cases"]] == []  # not linked to a case in this seed


def test_observable_patch_applies_present_fields_and_rejects_empty_ones(
    api: APIClient, ids: dict[str, str]
) -> None:
    response = api.patch(
        f"/api/v1/observable/{ids['observable']}",
        {"tlp": 1, "ioc": True, "message": "Seen in netflow"},
        format="json",
    )
    assert response.status_code == 204, response.content
    assert response.content == b""

    observable = Observable.objects.get(pk=ids["observable"])
    assert (observable.tlp, observable.ioc, observable.message) == (1, True, "Seen in netflow")

    empty = api.patch(f"/api/v1/observable/{ids['observable']}", {}, format="json")
    assert empty.status_code == 400, empty.content
    assert empty.json()["type"] == "BadRequest"


def test_observable_patch_that_retypes_the_artifact_rehashes_it(api: APIClient) -> None:
    """`(data_type, data_hash)` is the uniqueness key; a type change must move both halves.

    The two types differ only in `is_case_sensitive`, so re-typing is the one way the digest
    can change with `normalized_data` untouched — which is exactly the path a stale hash
    would leave the unique constraint blind to.
    """
    sensitive = ObservableType.objects.create(name="t1-sensitive", is_case_sensitive=True)
    ObservableType.objects.create(name="t1-folded", is_case_sensitive=False)
    observable = Observable.objects.create(
        data_type=sensitive, data=SENSITIVE_PATH, normalized_data=SENSITIVE_PATH
    )
    before = observable.data_hash
    assert before == data_hash(SENSITIVE_PATH), "precondition: hashed case-sensitively"

    response = api.patch(
        f"/api/v1/observable/{observable.id}", {"dataType": "t1-folded"}, format="json"
    )
    assert response.status_code == 204, response.content

    observable.refresh_from_db()
    assert observable.data_hash == data_hash(FOLDED_PATH), "the digest still describes the old type"
    assert observable.data_hash != before, "re-typing produced an identical digest — a no-op"


def test_observable_delete_is_204_drops_the_links_and_then_404s(
    api: APIClient, ids: dict[str, str]
) -> None:
    from cases.models import CaseObservable

    case = Case.objects.get(pk=ids["case"])
    observable = Observable.objects.get(pk=ids["observable"])
    CaseObservable.objects.create(case=case, observable=observable)

    response = api.delete(f"/api/v1/observable/{ids['observable']}")
    assert response.status_code == 204, response.content
    assert response.content == b""
    assert not CaseObservable.objects.filter(observable=observable).exists()

    again = api.get(f"/api/v1/observable/{ids['observable']}")
    assert again.status_code == 404, again.content
    assert again.json()["type"] == "NotFoundError"


def test_case_observable_post_slashless_is_the_spelling_thehive4py_uses(
    api: APIClient, ids: dict[str, str]
) -> None:
    """The interop route (verifier V1): `POST /api/v1/case/{id}/observable` (no trailing slash).

    thehive4py 2.1.0 posts the slashless spelling
    (`thehive4py/endpoints/observable.py:38` — `"POST", path=f"/api/v1/case/{case_id}/observable"`).
    Before this wave's route merge, the slashless path resolved to the GET-only view and POST
    answered 405 through the untyped `GenericError` branch — so the pinned interop client could
    not attach an artifact at all. Both spellings now serve GET and POST from one dispatcher
    (`case_observable_list`, the `case_task_list` pattern).
    """
    case = Case.objects.get(pk=ids["case"])
    assert api.get(f"/api/v1/case/{case.id}/observable").status_code == 200

    created = api.post(
        f"/api/v1/case/{case.id}/observable",  # slashless, exactly what thehive4py sends
        {"dataType": "ip", "data": "203.0.113.9"},
        format="json",
    )
    assert created.status_code == 200, created.content
    assert any(entry["data"] == "203.0.113.9" for entry in created.json()), (
        "the attached artifact is not in the response list"
    )

    # And the slashed spelling is not a separate route: same dispatcher, same shape.
    again = api.post(
        f"/api/v1/case/{case.id}/observable/",
        {"dataType": "ip", "data": "203.0.113.10"},
        format="json",
    )
    assert again.status_code == 200, again.content
    assert {entry["data"] for entry in again.json()} >= {"203.0.113.9", "203.0.113.10"}


def test_alert_observable_accepts_an_array_of_artifacts(
    api: APIClient, ids: dict[str, str]
) -> None:
    """The array-`data` branch of `POST /alert/{id}/observable` (verifier V4).

    `InputCreateObservable.data` is a string or an array of strings; both spellings produce the
    same array-shaped 201 (`OutputObservable[]`).
    """
    response = api.post(
        f"/api/v1/alert/{ids['alert']}/observable",
        {"dataType": "ip", "data": ["198.51.100.7", "198.51.100.8"]},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert {entry["data"] for entry in body} >= {"198.51.100.7", "198.51.100.8"}
    assert all(isinstance(entry, dict) and "_id" in entry for entry in body)


# --- AC-A4/B1 — custom fields ---------------------------------------------


def test_custom_field_list_returns_the_recorded_shape(api: APIClient, db: None) -> None:
    CustomField.objects.create(name="threat-type", group="Classification", type="string")
    response = api.get("/api/v1/customField")
    assert response.status_code == 200, response.content

    body = response.json()
    assert isinstance(body, list) and len(body) == 1
    assert set(body[0]) == PINNED_KEYS["custom_field_example.json"]
    assert body[0]["_type"] == "customField"
    assert body[0]["displayName"] == body[0]["name"], "P10-d: displayName mirrors name"


# --- AC-A6 — login and logout ----------------------------------------------


def _passworded_user() -> User:
    org = Organisation.objects.create(name="Session Org")
    return User.objects.create_user(
        login="analyst@example.net",
        username="analyst",
        password="hunter2-correct",
        email="analyst@example.net",
        org=org,
    )


def test_login_returns_output_user_and_a_cookie_that_authenticates(db: None) -> None:
    _passworded_user()
    client = APIClient()

    response = client.post(
        "/api/v1/login",
        {"user": "analyst@example.net", "password": "hunter2-correct"},
        format="json",
    )
    assert response.status_code == 200, response.content

    body = response.json()
    assert set(body) == PINNED_KEYS["user_example.json"]
    assert body["login"] == "analyst@example.net"
    assert body["org"] == "Session Org"
    assert body["hasPassword"] is True
    assert body["hasKey"] is False, "no key was issued to this account"
    assert "sessionid" in response.cookies, "the endpoint's contract includes a session cookie"

    # The cookie is worth nothing if the rest of the surface does not honour it.
    authenticated = client.get("/api/v1/case")
    assert authenticated.status_code == 200, authenticated.content


def test_login_refuses_bad_credentials_and_a_foreign_organisation_with_400(db: None) -> None:
    """400, never 401: a 401 is what an attacker enumerates accounts against (plan §12, AC-A6)."""
    _passworded_user()
    client = APIClient()

    wrong_password = client.post(
        "/api/v1/login", {"user": "analyst@example.net", "password": "nope"}, format="json"
    )
    assert wrong_password.status_code == 400, wrong_password.content
    wrong_body = wrong_password.json()
    assert wrong_body["type"] == "BadRequest"
    # The envelope must not say *which* half was wrong.
    assert "fields" not in wrong_body or not wrong_body["fields"]

    unknown = client.post(
        "/api/v1/login", {"user": "ghost@example.net", "password": "anything"}, format="json"
    )
    assert unknown.status_code == 400, unknown.content

    wrong_org = client.post(
        "/api/v1/login",
        {"user": "analyst@example.net", "password": "hunter2-correct", "organisation": "Elsewhere"},
        format="json",
    )
    assert wrong_org.status_code == 400, wrong_org.content
    assert wrong_org.json()["fields"] == {"organisation": ["does not belong to this user"]}
    assert "sessionid" not in wrong_org.cookies, "a refused login must not open a session"


@pytest.mark.parametrize(
    "body",
    [
        {"user": "analyst@example.net", "password": ["x"]},
        {"user": "analyst@example.net", "password": 123},
        {"user": "analyst@example.net", "password": []},
        {"user": ["analyst@example.net"], "password": "hunter2-correct"},
    ],
    ids=["password-list", "password-int", "password-empty-list", "user-list"],
)
def test_non_string_credentials_are_the_same_400_never_a_500(
    body: dict[str, Any], db: None
) -> None:
    """A hostile body type used to reach `authenticate()` and raise out of the hasher (F1).

    Every row gets the *identical* envelope a wrong password gets — message, type, empty
    `fields`, no session — so the body type cannot become a status-code oracle either.
    """
    _passworded_user()
    client = APIClient()

    response = client.post("/api/v1/login", body, format="json")
    assert response.status_code == 400, response.content
    assert response.json() == {
        "type": "BadRequest",
        "message": "Invalid credentials",
        "fields": {},
    }
    assert "sessionid" not in response.cookies, "a refused login must not open a session"


@pytest.mark.django_db
@override_settings(
    REST_FRAMEWORK={
        **_BASE_REST_FRAMEWORK,
        # Spelled out rather than inherited: `override_settings` replaces the whole
        # `REST_FRAMEWORK` dict, and pinning the rate here makes "101st request throttled" a
        # property of this test instead of of whatever `settings/base.py` currently says.
        "DEFAULT_THROTTLE_RATES": {"anon": "100/min", "user": "1000/min"},
    }
)
def test_the_101st_anonymous_login_from_one_client_is_throttled() -> None:
    """The `anon` 100/min `AnonRateThrottle` plan §8 asks for on login, pinned to request 101.

    The throttle runs in DRF's `initial()`, before the view, so every attempt counts however
    it ends; the rows below are failed guesses against a non-existent account, which is the
    traffic the limit exists for. One client throughout because `AnonRateThrottle` keys on
    IP. (No `DEFAULT_THROTTLE_RATES` override pattern existed to copy — the webhook suite
    overrides its own view-level `WEBHOOK_RATE_*` settings — so this uses `@override_settings`
    on `REST_FRAMEWORK`, the key DRF's `api_settings` reloads on `setting_changed`.)
    """
    client = APIClient()
    codes = [
        client.post(
            "/api/v1/login",
            {"user": "ghost@example.net", "password": "guess"},
            format="json",
        ).status_code
        for _ in range(101)
    ]
    assert codes[:100] == [400] * 100, "the rate budget did not last exactly 100 attempts"
    assert codes[100] == 429, f"the 101st attempt was not throttled: {codes[100]}"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_logout_is_200_and_takes_the_session_with_it(method: str, db: None) -> None:
    _passworded_user()
    client = APIClient()
    assert (
        client.post(
            "/api/v1/login",
            {"user": "analyst@example.net", "password": "hunter2-correct"},
            format="json",
        ).status_code
        == 200
    )
    assert client.get("/api/v1/case").status_code == 200, "precondition: the session works"

    logged_out = _call(client, method, "/api/v1/logout")
    assert logged_out.status_code == 200, logged_out.content
    assert logged_out.content == b"", "the OpenAPI records no body for logout"

    # Django rotates the session key on logout, so the cookie the client still holds is dead.
    after = client.get("/api/v1/case")
    assert after.status_code == 401, after.content
    assert after.json()["type"] == "AuthenticationError"


# --- the sweeps ------------------------------------------------------------


UNKNOWN_ID = "00000000-0000-4000-8000-0000000000ff"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/alert/{id}"),
        ("DELETE", "/api/v1/alert/{id}"),
        ("GET", "/api/v1/case/{id}"),
        ("DELETE", "/api/v1/case/{id}"),
        ("GET", "/api/v1/task/{id}"),
        ("PATCH", "/api/v1/task/{id}"),
        ("DELETE", "/api/v1/task/{id}"),
        ("PATCH", "/api/v1/customEvent/{id}"),
        ("DELETE", "/api/v1/customEvent/{id}"),
        ("GET", "/api/v1/observable/{id}"),
        ("PATCH", "/api/v1/observable/{id}"),
        ("DELETE", "/api/v1/observable/{id}"),
    ],
)
def test_unknown_ids_answer_404_and_never_500(method: str, path: str, api: APIClient) -> None:
    """The envelope is the contract: `NotFoundError` for every route, never a stack trace."""
    response = _call(api, method, path.format(id=UNKNOWN_ID))
    assert response.status_code == 404, (path, response.status_code, response.content)
    body = response.json()
    assert set(body) == {"type", "message"}, body
    assert body["type"] == "NotFoundError"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/api/v1/task/{task}", "task"),
        ("/api/v1/customEvent/{event}", "event"),
        ("/api/v1/observable/{observable}", "observable"),
    ],
)
def test_an_empty_patch_is_a_400_that_writes_nothing(
    path: str, key: str, api: APIClient, ids: dict[str, str]
) -> None:
    """No field, no write (ADR D11) — a 200 here would mean an empty body silently reset rows."""
    response = api.patch(path.format(**ids), {}, format="json")
    assert response.status_code == 400, response.content
    assert response.json()["type"] == "BadRequest"

    if key == "task":
        assert Task.objects.get(pk=ids["task"]).title == "Isolate the host"
    elif key == "event":
        assert TimelineEvent.objects.get(pk=ids["event"]).title == "Analyst note"
    else:
        assert Observable.objects.get(pk=ids["observable"]).message == ""


def test_new_entity_timestamps_are_iso8601_not_epoch(api: APIClient, ids: dict[str, str]) -> None:
    """Deviation **P10-y**: entity serializers are ISO-8601 (plan §5.1).

    The recorded OpenAPI shows millisecond integers. Emitting those would be indistinguishable
    from an epoch in *seconds* to a client that guesses, so the format is pinned here for every
    new surface at once rather than one serializer at a time.
    """
    task = _task(
        Case.objects.get(pk=ids["case"]),
        due_date=timezone.now(),
        started_at=timezone.now(),
        ended_at=timezone.now(),
    )
    task_body = api.get(f"/api/v1/task/{task.id}").json()
    event_body = custom_event_json(TimelineEvent.objects.get(pk=ids["event"]))
    field_body = custom_field_json(
        CustomField.objects.create(name="timestamp-probe", type="string")
    )

    for label, value in {
        "_createdAt": task_body["_createdAt"],
        "_updatedAt": task_body["_updatedAt"],
        "dueDate": task_body["dueDate"],
        "event.date": event_body["date"],
        "event._createdAt": event_body["_createdAt"],
        "field._createdAt": field_body["_createdAt"],
    }.items():
        assert isinstance(value, str), f"{label} is {type(value).__name__}, not a string"
        parsed = datetime.fromisoformat(value)  # raises if it is not ISO-8601 at all
        assert parsed.tzinfo is not None, f"{label} is a naive timestamp: {value!r}"


def test_ids_across_the_new_surface_are_plain_uuids(api: APIClient, ids: dict[str, str]) -> None:
    """No `~123` row ids: a client that parses ids with `uuid.UUID(...)` must never meet one."""
    bodies = [
        api.get(f"/api/v1/alert/{ids['alert']}").json(),
        api.get(f"/api/v1/case/{ids['case']}").json(),
        api.get(f"/api/v1/task/{ids['task']}").json(),
        api.get(f"/api/v1/observable/{ids['observable']}").json(),
        custom_event_json(TimelineEvent.objects.get(pk=ids["event"])),
    ]
    for body in bodies:
        assert "_id" in body, body
        assert "~" not in body["_id"], f"a TheHive row id leaked onto the wire: {body['_id']}"
        UUID(body["_id"])  # raises if the id is anything but a plain UUID

    # The one place `~` belongs: the P8-1 timeline wire renders *entity* ids TheHive-style, so
    # `~` + the case UUID must still be there — otherwise this sweep would be rewarding the
    # same mistake it is looking for, applied to a shape that never claimed to be an `_id`.
    wire = api.get(f"/api/v1/case/{ids['case']}/timeline").json()["events"]
    assert wire, "the seed wrote a ledger row"
    assert wire[0]["entityId"] == f"~{ids['case']}"
    UUID(wire[0]["entityId"][1:])
