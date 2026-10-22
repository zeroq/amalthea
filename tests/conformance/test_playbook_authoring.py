"""AC6.12-P2 - playbook authoring surface (plan §6.1, T2.1-T2.6).

Covers: CRUD, _meta non-drift, config validation matrix, run-now -> run + timeline,
DELETE with runs (active refused, terminal history removed), P12-1 (TaskCompleted emission on
completion only).

L3: role gating — read-only users denied on mutating endpoints.
"""

from __future__ import annotations

import pytest

from automation.models import AutomationRun, Playbook
from cases.models import Task, TimelineEvent
from core.enums import VIEWER_ROLE
from identity.models import Organisation, User
from tests.conformance._t1_seed import _api_key_client, _call, _case, _observable

pytestmark = pytest.mark.django_db


@pytest.fixture
def readonly_user(db: None) -> User:
    """A read-only user in the same org as the analyst fixture."""
    org = Organisation.objects.create(name="ReadOnly Org")
    return User.objects.create(
        login="readonly",
        username="readonly",
        email="readonly@example.net",
        org=org,
        is_active=True,
        role=VIEWER_ROLE,
    )


@pytest.fixture
def readonly_client(readonly_user: User):
    """API client authenticated as read-only user."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=readonly_user)
    return client


def _post(client, path: str, data: dict):
    resp = client.post(path, data, format="json")
    return resp.status_code, resp.json()


def _get(client, path: str):
    resp = client.get(path)
    return resp.status_code, resp.json()


def _patch(client, path: str, data: dict):
    resp = client.patch(path, data, format="json")
    if resp.status_code == 204:
        return 204, {}
    return resp.status_code, resp.json()


def _delete(client, path: str) -> int:
    return client.delete(path).status_code


# --- _meta non-drift (AC6.12-P2-c) ------------------------------------------


def test_playbook_meta_trigger_list_matches_dispatcher(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="read")
    code, body = _get(c, "/api/v1/playbook/_meta")
    assert code == 200
    assert body["triggerEvents"] == list(
        __import__("automation.dispatcher").dispatcher.TRIGGER_EVENTS
    )


def test_playbook_meta_scripts_match_loader(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="read")
    code, body = _get(c, "/api/v1/playbook/_meta")
    assert code == 200
    actions = {a["action"]: a for a in body["actions"]}
    assert set(actions["http"]["methods"]) == {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    }
    # The python action now returns "scripts" instead of "registeredPaths"
    assert "scripts" in actions["python"]
    script_names_from_meta = {s["name"] for s in actions["python"]["scripts"]}
    # Should match the discovered script names
    from automation.scripts_loader import script_names

    assert script_names_from_meta == set(script_names())


# --- config validation matrix (AC6.12-P2-b) ---------------------------------


def test_config_unknown_action_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "bad-action",
            "triggerEvent": "observable.created",
            "config": {"action": "foo", "url": "http://example.com"},
        },
    )
    assert code == 400
    assert "action" in body["fields"]


def test_config_http_missing_url_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "bad-url",
            "triggerEvent": "observable.created",
            "config": {"action": "http"},
        },
    )
    assert code == 400
    assert "url" in body["fields"]


def test_config_http_bad_method_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "bad-method",
            "triggerEvent": "observable.created",
            "config": {"action": "http", "url": "http://example.com", "method": "CONNECT"},
        },
    )
    assert code == 400
    assert "method" in body["fields"]


def test_config_http_bad_timeout_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "bad-timeout",
            "triggerEvent": "observable.created",
            "config": {
                "action": "http",
                "url": "http://example.com",
                "timeoutSeconds": -5,
            },
        },
    )
    assert code == 400
    assert "timeoutSeconds" in body["fields"]


def test_config_python_missing_path_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "bad-path",
            "triggerEvent": "observable.created",
            "config": {"action": "python"},
        },
    )
    assert code == 400
    assert "action_path" in body["fields"]


def test_config_python_unregistered_path_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "unreg-path",
            "triggerEvent": "observable.created",
            "config": {"action": "python", "action_path": "no.such.module"},
        },
    )
    assert code == 400
    assert "action_path" in body["fields"]


def test_config_good_http_accepted(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "good-http",
            "triggerEvent": "observable.created",
            "config": {
                "action": "http",
                "url": "https://example.com/{observable}",
                "method": "GET",
                "timeoutSeconds": 5,
            },
        },
    )
    assert code == 201, body


def test_config_good_python_accepted(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "good-python",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201, body


# --- CRUD -------------------------------------------------------------------


def test_playbook_crud(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    # create
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "crud-playbook",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    # list
    resp = _call(c, "GET", "/api/v1/playbook")
    assert resp.status_code == 200
    assert any(p["_id"] == pb_id for p in resp.json())

    # detail by id
    resp = _call(c, "GET", f"/api/v1/playbook/{pb_id}")
    assert resp.status_code == 200
    assert resp.json()["_id"] == pb_id

    # detail by name
    resp = _call(c, "GET", "/api/v1/playbook/crud-playbook")
    assert resp.status_code == 200
    assert resp.json()["_id"] == pb_id

    # patch (PATCH is 204, no body)
    code, _ = _patch(c, f"/api/v1/playbook/{pb_id}", {"description": "updated"})
    assert code == 204
    resp = _call(c, "GET", f"/api/v1/playbook/{pb_id}")
    assert resp.json()["description"] == "updated"

    # delete
    code = _delete(c, f"/api/v1/playbook/{pb_id}")
    assert code == 204
    resp = _call(c, "GET", f"/api/v1/playbook/{pb_id}")
    assert resp.status_code == 404


def _post(client, path: str, data: dict):
    resp = client.post(path, data, format="json")
    return resp.status_code, resp.json()


def _get(client, path: str):
    resp = client.get(path)
    return resp.status_code, resp.json()


def _patch(client, path: str, data: dict):
    resp = client.patch(path, data, format="json")
    if resp.status_code == 204:
        return 204, {}
    return resp.status_code, resp.json()


def _delete(client, path: str) -> int:
    return client.delete(path).status_code


def test_delete_playbook_with_runs_refused(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    # create a playbook
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "has-runs",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    # create a run directly
    AutomationRun.objects.create(
        playbook_id=body["_id"],
        playbook_name="has-runs",
        trigger_event="observable.created",
        status="Pending",
        idempotency_key="test-delete-with-run",
    )

    # delete should be refused
    code = _delete(c, f"/api/v1/playbook/{pb_id}")
    assert code == 400


def test_delete_playbook_with_terminal_runs_removes_playbook_and_runs(analyst: User) -> None:
    """Terminal runs are history, not a guard: deleting the playbook removes them too (AC3)."""
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "terminal-runs",
            "triggerEvent": "observable.created",
            "config": {"action": "python", "action_path": "enrichment_probe"},
        },
    )
    assert code == 201
    pb_id = body["_id"]

    run = AutomationRun.objects.create(
        playbook_id=pb_id,
        playbook_name="terminal-runs",
        trigger_event="observable.created",
        status="Success",
        idempotency_key="delete-terminal-run",
    )

    assert _delete(c, f"/api/v1/playbook/{pb_id}") == 204
    assert not Playbook.objects.filter(pk=pb_id).exists()
    assert not AutomationRun.objects.filter(pk=run.pk).exists()


def test_delete_playbook_with_an_active_run_keeps_all_history(analyst: User) -> None:
    """A live run blocks the delete before any terminal row is removed (AC4)."""
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "mixed-runs",
            "triggerEvent": "observable.created",
            "config": {"action": "python", "action_path": "enrichment_probe"},
        },
    )
    assert code == 201
    pb_id = body["_id"]

    success_run = AutomationRun.objects.create(
        playbook_id=pb_id,
        playbook_name="mixed-runs",
        trigger_event="observable.created",
        status="Success",
        idempotency_key="delete-mixed-success",
    )
    pending_run = AutomationRun.objects.create(
        playbook_id=pb_id,
        playbook_name="mixed-runs",
        trigger_event="observable.created",
        status="Running",
        idempotency_key="delete-mixed-running",
    )

    assert _delete(c, f"/api/v1/playbook/{pb_id}") == 400
    assert Playbook.objects.filter(pk=pb_id).exists()
    assert AutomationRun.objects.filter(pk__in=[success_run.pk, pending_run.pk]).count() == 2


# --- run-now → run + timeline (AC6.12-P2-a) ---------------------------------


def test_run_now_creates_run_and_timeline(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "run-now-pb",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    case = _case("Run-now case", org=analyst.org)
    obs = _observable("10.0.0.1", org=analyst.org)
    # link observable to case
    from cases.models import CaseObservable

    CaseObservable.objects.create(case=case, observable=obs)

    code, body = _post(
        c,
        f"/api/v1/playbook/{pb_id}/run",
        {"case": str(case.id)},
    )
    assert code == 202, body
    run_id = body["_id"]
    assert body["triggeredBy"] == "manual"

    # run row exists with triggered_by=manual
    run = AutomationRun.objects.get(pk=run_id)
    assert run.triggered_by == "manual"
    assert run.idempotency_key.startswith("manual:")

    # Run the task synchronously (bypassing transaction.on_commit which doesn't fire in test transactions)
    from automation.tasks import execute_run

    execute_run(str(run.id))

    # timeline entry exists
    te = TimelineEvent.objects.filter(case_id=case.id, kind="automation-run").first()
    assert te is not None
    assert "automation" in te.title.lower()


# --- P12-1: TaskCompleted emission on completion only (AC6.12-P2-d) --------


def test_task_completed_emits_on_transition_into_completed_only(analyst: User) -> None:
    """Completing a task emits TaskCompleted; re-saving an already-complete task emits nothing."""

    from tests.conformance._t1_seed import _case

    case = _case("P12-1 test", org=analyst.org)
    task = Task.objects.create(case=case, title="p12-1 task", status="Waiting")

    # Mock dispatch to capture calls
    captured = []

    def capture_dispatch(event):
        captured.append(event)

    import automation.dispatcher

    original = automation.dispatcher.dispatch
    automation.dispatcher.dispatch = capture_dispatch

    try:
        # 1. Transition Todo -> Completed → should emit
        task.status = "Completed"
        task.save()

        # 2. Re-save Completed -> Completed → should NOT emit
        task.title = "updated"
        task.save()

        # 3. Transition Completed -> InProgress -> Completed → should emit again
        task.status = "InProgress"
        task.save()
        task.status = "Completed"
        task.save()

        from core.events import TaskCompleted

        task_completeds = [e for e in captured if isinstance(e, TaskCompleted)]
        assert len(task_completeds) == 2, f"expected 2 TaskCompleted, got {len(task_completeds)}"
        assert task_completeds[0].task_id == str(task.id)
        assert task_completeds[1].task_id == str(task.id)
    finally:
        import automation.dispatcher

        automation.dispatcher.dispatch = original


# --- DELETE with runs refused -----------------------------------------------


def test_delete_playbook_with_runs_refused_400(analyst: User) -> None:
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "has-runs-pb",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    # Create a run referencing this playbook
    AutomationRun.objects.create(
        playbook_id=pb_id,
        playbook_name="has-runs-pb",
        trigger_event="observable.created",
        status="Pending",
        idempotency_key="test-delete-with-run",
    )

    code = _delete(c, f"/api/v1/playbook/{pb_id}")
    assert code == 400


# --- L3: Role gating for playbook authoring/run --------------------------------


def test_readonly_user_cannot_create_playbook(readonly_client) -> None:
    """read-only (viewer) user is denied creating a playbook (403).

    The denial now comes from the global `ViewerReadOnly` permission (ADR-006) rather
    than the view's inline `IsAnalystOrAbove` check, and `compat.errors` normalizes
    every permission-layer 403 to its fixed envelope — hence the generic message. The
    status and the envelope type are the contract.
    """
    code, body = _post(
        readonly_client,
        "/api/v1/playbook",
        {
            "name": "readonly-create",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 403, body
    assert body["type"] == "AuthorizationError"


def test_readonly_user_cannot_update_playbook(readonly_client, analyst: User) -> None:
    """read-only user is denied updating a playbook (403)."""
    # Create a playbook as analyst first
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "readonly-update-target",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    # Try to update as read-only
    code, body = _patch(readonly_client, f"/api/v1/playbook/{pb_id}", {"description": "hacked"})
    assert code == 403, body
    assert body["type"] == "AuthorizationError"


def test_readonly_user_cannot_delete_playbook(readonly_client, analyst: User) -> None:
    """read-only user is denied deleting a playbook (403)."""
    # Create a playbook as analyst first
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "readonly-delete-target",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    # Try to delete as read-only
    code = _delete(readonly_client, f"/api/v1/playbook/{pb_id}")
    assert code == 403


def test_readonly_user_cannot_run_playbook(readonly_client, analyst: User) -> None:
    """read-only user is denied manually running a playbook (403)."""
    # Create a playbook as analyst first
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "readonly-run-target",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    case = _case("Run-now case", org=analyst.org)
    obs = _observable("10.0.0.1", org=analyst.org)
    from cases.models import CaseObservable

    CaseObservable.objects.create(case=case, observable=obs)

    # Try to run as read-only
    code, body = _post(
        readonly_client,
        f"/api/v1/playbook/{pb_id}/run",
        {"case": str(case.id)},
    )
    assert code == 403, body
    assert body["type"] == "AuthorizationError"


def test_analyst_user_can_create_playbook(analyst: User) -> None:
    """analyst user can create a playbook (201)."""
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "analyst-create",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201, body


def test_analyst_user_can_update_playbook(analyst: User) -> None:
    """analyst user can update a playbook (204)."""
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "analyst-update",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    code, _ = _patch(c, f"/api/v1/playbook/{pb_id}", {"description": "updated by analyst"})
    assert code == 204


def test_analyst_user_can_delete_playbook(analyst: User) -> None:
    """analyst user can delete a playbook (204)."""
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "analyst-delete",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    code = _delete(c, f"/api/v1/playbook/{pb_id}")
    assert code == 204


def test_analyst_user_can_run_playbook(analyst: User) -> None:
    """analyst user can manually run a playbook (202)."""
    c, _ = _api_key_client(analyst, scope="readwrite")
    code, body = _post(
        c,
        "/api/v1/playbook",
        {
            "name": "analyst-run",
            "triggerEvent": "observable.created",
            "config": {
                "action": "python",
                "action_path": "enrichment_probe",
            },
        },
    )
    assert code == 201
    pb_id = body["_id"]

    case = _case("Analyst run case", org=analyst.org)
    obs = _observable("10.0.0.2", org=analyst.org)
    from cases.models import CaseObservable

    CaseObservable.objects.create(case=case, observable=obs)

    code, body = _post(c, f"/api/v1/playbook/{pb_id}/run", {"case": str(case.id)})
    assert code == 202, body
    assert body["triggeredBy"] == "manual"
