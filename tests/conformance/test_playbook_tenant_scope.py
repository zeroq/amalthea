"""§11.2 / M3 — playbooks are tenant-scoped.

These tests pin the four boundaries the plan introduced:

* model/schema: every playbook has an owner, names are unique per organisation;
* API reads/writes: cross-org is uniform 404, viewers never read playbook config, superAdmin
  cross-org detail reads are audited;
* manual runs: a playbook cannot run against a case in another organisation;
* trigger dispatch: the event subject's organisation selects the playbooks, and an event whose
  organisation cannot be resolved creates no runs at all.

The migration test drives `MigrationExecutor` to prove pre-0008 rows are assigned to the default
organisation, following the same pattern as `test_viewer_role_rename.py`.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from rest_framework.test import APIClient

from automation.dispatcher import PlaybookOrgMismatch, dispatch, run_now
from automation.models import AutomationRun, Playbook
from cases.models import CaseObservable
from core.enums import VIEWER_ROLE
from core.events import AlertIngested, CaseStatusChanged, ObservableCreated, TaskCompleted
from identity.models import AuditLog, Organisation, User

pytestmark = pytest.mark.django_db

PLAYBOOK_CONFIG = {"action": "python", "action_path": "enrichment_probe"}


def _user(org: Organisation, login: str, role: str = "analyst") -> User:
    return User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _playbook(
    org: Organisation, name: str = "tenant-playbook", trigger: str = "observable.created"
) -> Playbook:
    return Playbook.objects.create(
        owner_org=org,
        name=name,
        trigger_event=trigger,
        is_active=True,
        config=PLAYBOOK_CONFIG,
    )


@pytest.fixture
def org1(db: None) -> Organisation:
    # A non-default org: the seed migrations install example playbooks in the default org, and
    # these tests assert on exact lists.
    return Organisation.objects.create(name="Playbook Test Org 1")


@pytest.fixture
def org2(db: None) -> Organisation:
    return Organisation.objects.create(name="Playbook Test Org 2")


@pytest.fixture
def user1(org1: Organisation) -> User:
    return _user(org1, "pb-user1")


@pytest.fixture
def user2(org2: Organisation) -> User:
    return _user(org2, "pb-user2")


@pytest.fixture
def viewer(org1: Organisation) -> User:
    return _user(org1, "pb-viewer", role=VIEWER_ROLE)


@pytest.fixture
def superadmin(org1: Organisation) -> User:
    return _user(org1, "pb-super", role="superAdmin")


# ---------------------------------------------------------------------------
# Model / schema
# ---------------------------------------------------------------------------


def test_playbook_requires_an_owner(org1: Organisation) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Playbook.objects.create(name="no-owner", trigger_event="observable.created")


def test_playbook_name_is_unique_per_org_but_reusable_across_orgs(
    org1: Organisation, org2: Organisation
) -> None:
    _playbook(org1, "same-name")
    _playbook(org2, "same-name")
    with pytest.raises(IntegrityError), transaction.atomic():
        _playbook(org1, "same-name")


# ---------------------------------------------------------------------------
# API reads: list/detail scoping, viewer denial, superAdmin audit
# ---------------------------------------------------------------------------


def test_collection_list_is_org_scoped(
    org1: Organisation, org2: Organisation, user1: User, user2: User, superadmin: User
) -> None:
    p1 = _playbook(org1, "org-one")
    p2 = _playbook(org2, "org-two")

    body = _client(user1).get("/api/v1/playbook").json()
    assert [item["_id"] for item in body] == [str(p1.id)]
    assert body[0]["ownerOrg"] == str(org1.id)
    assert body[0]["ownerOrgName"] == org1.name

    body2 = _client(user2).get("/api/v1/playbook").json()
    assert [item["_id"] for item in body2] == [str(p2.id)]

    body_super = _client(superadmin).get("/api/v1/playbook").json()
    super_ids = {item["_id"] for item in body_super}
    # superAdmin also sees the seed playbooks in the default organisation.
    assert {str(p1.id), str(p2.id)} <= super_ids


def test_viewer_cannot_read_playbook_config(org1: Organisation, user1: User, viewer: User) -> None:
    playbook = _playbook(org1)
    viewer_client = _client(viewer)

    assert viewer_client.get("/api/v1/playbook").status_code == 403
    assert viewer_client.get(f"/api/v1/playbook/{playbook.id}").status_code == 403
    assert viewer_client.get("/api/v1/playbook/_meta").status_code == 200

    # The same-org analyst can still read the authoring surface.
    assert _client(user1).get(f"/api/v1/playbook/{playbook.id}").json()["config"] == PLAYBOOK_CONFIG


def test_detail_cross_org_is_a_uniform_404(
    org1: Organisation, org2: Organisation, user1: User
) -> None:
    own = _playbook(org1, "own")
    foreign = _playbook(org2, "foreign")
    client = _client(user1)

    assert client.get(f"/api/v1/playbook/{own.id}").status_code == 200
    assert client.get(f"/api/v1/playbook/{foreign.id}").status_code == 404
    assert client.get(f"/api/v1/playbook/{foreign.id}").json()["type"] == "NotFoundError"
    assert client.get("/api/v1/playbook/00000000-0000-0000-0000-000000000000").status_code == 404


def test_superadmin_cross_org_detail_is_readable_and_audited(
    org1: Organisation, org2: Organisation, superadmin: User
) -> None:
    foreign = _playbook(org2, "audited")
    AuditLog.objects.all().delete()

    resp = _client(superadmin).get(f"/api/v1/playbook/{foreign.id}")

    assert resp.status_code == 200
    log = AuditLog.objects.get(action="cross_org_read")
    assert log.actor_id == superadmin.id
    assert log.actor_org_id == org1.id
    assert log.target_org_id == org2.id
    assert log.target_type == "Playbook"
    assert log.target_id == str(foreign.id)


def test_superadmin_name_lookup_is_ambiguous_across_orgs(
    org1: Organisation, org2: Organisation, superadmin: User
) -> None:
    p1 = _playbook(org1, "duplicate-name")
    p2 = _playbook(org2, "duplicate-name")
    client = _client(superadmin)

    assert client.get("/api/v1/playbook/duplicate-name").status_code == 404
    assert client.get(f"/api/v1/playbook/{p1.id}").status_code == 200
    assert client.get(f"/api/v1/playbook/{p2.id}").status_code == 200


# ---------------------------------------------------------------------------
# API writes: create org binding, per-org names, immutable owner
# ---------------------------------------------------------------------------


def test_create_assigns_the_callers_org_and_allows_the_same_name_elsewhere(
    org1: Organisation, org2: Organisation, user1: User, user2: User
) -> None:
    payload = {
        "name": "shared-name",
        "triggerEvent": "observable.created",
        "config": PLAYBOOK_CONFIG,
    }

    first = _client(user1).post("/api/v1/playbook", payload, format="json")
    second = _client(user2).post("/api/v1/playbook", payload, format="json")
    duplicate = _client(user1).post("/api/v1/playbook", payload, format="json")

    assert first.status_code == 201
    assert first.json()["ownerOrg"] == str(org1.id)
    assert second.status_code == 201
    assert second.json()["ownerOrg"] == str(org2.id)
    assert duplicate.status_code == 400
    assert "already exists" in duplicate.json()["fields"]["name"]


def test_create_ignores_an_attempt_to_choose_owner_org(
    org1: Organisation, org2: Organisation, user1: User
) -> None:
    payload = {
        "name": "owner-injection",
        "triggerEvent": "observable.created",
        "config": PLAYBOOK_CONFIG,
        "ownerOrg": str(org2.id),
        "owner_org": str(org2.id),
    }

    resp = _client(user1).post("/api/v1/playbook", payload, format="json")

    assert resp.status_code == 201
    playbook = Playbook.objects.get(pk=resp.json()["_id"])
    assert playbook.owner_org_id == org1.id


def test_owner_org_is_immutable_on_patch(
    org1: Organisation, org2: Organisation, user1: User
) -> None:
    playbook = _playbook(org1, "immutable")

    resp = _client(user1).patch(
        f"/api/v1/playbook/{playbook.id}",
        {"description": "updated", "ownerOrg": str(org2.id), "owner_org": str(org2.id)},
        format="json",
    )

    assert resp.status_code == 204
    playbook.refresh_from_db()
    assert playbook.owner_org_id == org1.id
    assert playbook.description == "updated"


# ---------------------------------------------------------------------------
# Manual runs
# ---------------------------------------------------------------------------


def test_manual_run_rejects_a_case_from_another_org(
    org1: Organisation, org2: Organisation, user1: User, make_case
) -> None:
    playbook = _playbook(org1, trigger="manual")
    foreign_case = make_case(title="foreign", org=org2)

    resp = _client(user1).post(
        f"/api/v1/playbook/{playbook.id}/run",
        {"case": str(foreign_case.id)},
        format="json",
    )

    assert resp.status_code == 404
    assert AutomationRun.objects.count() == 0


def test_manual_run_own_org_is_queued(org1: Organisation, user1: User, make_case) -> None:
    playbook = _playbook(org1, trigger="manual")
    case = make_case(title="own", org=org1)

    resp = _client(user1).post(
        f"/api/v1/playbook/{playbook.id}/run",
        {"case": str(case.id)},
        format="json",
    )

    assert resp.status_code == 202
    run = AutomationRun.objects.get()
    assert run.playbook_id == playbook.id
    assert run.case_id == case.id
    assert run.triggered_by == "manual"


def test_run_now_raises_on_org_mismatch(org1: Organisation, org2: Organisation, make_case) -> None:
    playbook = _playbook(org1, trigger="manual")
    foreign_case = make_case(title="foreign", org=org2)

    with pytest.raises(PlaybookOrgMismatch):
        run_now(playbook, case=foreign_case)

    assert AutomationRun.objects.count() == 0


# ---------------------------------------------------------------------------
# Dispatch: the subject's org selects the playbooks
# ---------------------------------------------------------------------------


def test_alert_ingested_only_dispatches_the_alert_orgs_playbooks(
    org1: Organisation, org2: Organisation, make_alert
) -> None:
    own = _playbook(org1, "own-alert", trigger="alert.ingested")
    _playbook(org2, "foreign-alert", trigger="alert.ingested")
    alert = make_alert(
        title="org1 alert",
        type="test",
        source="test-source",
        source_ref="tenant-alert-1",
        org=org1,
    )
    # Creating an alert fires `alert.ingested` through the post_save receiver; clear that run so
    # this test exercises the explicit dispatch path (and its idempotency key) on its own.
    AutomationRun.objects.all().delete()

    runs = dispatch(AlertIngested(alert_id=str(alert.id)))

    assert [run.playbook_id for run in runs] == [own.id]


def test_case_status_changed_only_dispatches_the_case_orgs_playbooks(
    org1: Organisation, org2: Organisation, make_case
) -> None:
    own = _playbook(org1, "own-case", trigger="case.status_changed")
    _playbook(org2, "foreign-case", trigger="case.status_changed")
    case = make_case(title="org1 case", org=org1)

    runs = dispatch(
        CaseStatusChanged(case_id=str(case.id), old_status="New", new_status="Investigating")
    )

    assert [run.playbook_id for run in runs] == [own.id]


def test_observable_created_uses_the_linked_case_org(
    org1: Organisation, org2: Organisation, make_case, make_observable
) -> None:
    own = _playbook(org1, "own-observable", trigger="observable.created")
    _playbook(org2, "foreign-observable", trigger="observable.created")
    case = make_case(title="org1 case", org=org1)
    observable = make_observable(data="203.0.113.42", org=org1)
    CaseObservable.objects.create(case=case, observable=observable)

    runs = dispatch(ObservableCreated(observable_id=str(observable.id), case_id=str(case.id)))

    assert [run.playbook_id for run in runs] == [own.id]


def test_task_completed_uses_the_task_case_org(
    org1: Organisation, org2: Organisation, make_case
) -> None:
    from cases.models import Task

    own = _playbook(org1, "own-task", trigger="task.completed")
    _playbook(org2, "foreign-task", trigger="task.completed")
    case = make_case(title="org1 case", org=org1)
    task = Task.objects.create(case=case, title="task")

    runs = dispatch(TaskCompleted(task_id=str(task.id), case_id=str(case.id)))

    assert [run.playbook_id for run in runs] == [own.id]


def test_unresolvable_event_org_creates_no_runs(org1: Organisation) -> None:
    _playbook(org1, "would-match-if-global", trigger="alert.ingested")

    runs = dispatch(AlertIngested(alert_id="00000000-0000-0000-0000-000000000000"))

    assert runs == []
    assert AutomationRun.objects.count() == 0


def test_observable_event_without_a_case_creates_no_runs(
    org1: Organisation, make_observable
) -> None:
    _playbook(org1, "would-match-if-unscoped", trigger="observable.created")
    observable = make_observable(data="203.0.113.43", org=org1)

    runs = dispatch(ObservableCreated(observable_id=str(observable.id)))

    assert runs == []
    assert AutomationRun.objects.count() == 0


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _migrate(targets: list[tuple[str, str]]) -> None:
    MigrationExecutor(connection).migrate(targets)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_0008_backfills_preexisting_playbooks_to_the_default_org(
    default_org: Organisation,
) -> None:
    before = ("automation", "0007_rewrite_legacy_action_path")
    after = ("automation", "0008_playbook_tenant_scope")
    leaves = MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes()

    _migrate([before])
    try:
        old_apps = MigrationExecutor(connection).loader.project_state([before]).apps
        OldPlaybook = old_apps.get_model("automation", "Playbook")  # noqa: N806
        legacy = OldPlaybook.objects.create(
            name="legacy-tenant-backfill",
            trigger_event="observable.created",
            config={},
        )

        _migrate([after])

        current = Playbook.objects.get(pk=legacy.pk)
        assert current.owner_org_id == default_org.id

        # The old global unique name is gone: the same name is legal in another org.
        other = Organisation.objects.create(name="Migration Other Org")
        Playbook.objects.create(
            owner_org=other,
            name="legacy-tenant-backfill",
            trigger_event="observable.created",
            config={},
        )
    finally:
        _migrate(leaves)
