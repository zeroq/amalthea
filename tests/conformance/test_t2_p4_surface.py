"""T2 Phase P4 surface — bulk edits, case merge and case templates (plan §6-P4).

Three acceptance criteria, each one a behaviour that fails *dangerously* if it drifts:

=====  =====================================================================
AC-a   `_bulk` is per-item transactional: one unknown id or one bad value
       fails only its own item and the rest still commit.
AC-b   `case/_merge` moves every child onto the target, records provenance on
       the target ledger, and is idempotent on replay.
AC-c   applying a case template copies its task/custom-field/tag defaults.
=====  =====================================================================

The matrix-wide method/scope coverage for the new routes lives in `test_authz.py`; this file proves
the semantics.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases.ledger import events_after
from cases.models import (
    Case,
    CaseObservable,
    CaseStatus,
    CaseTemplate,
    CustomField,
    Task,
)
from identity.models import User
from observables.models import Observable, ObservableType


@pytest.fixture
def case_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


def _case(status: CaseStatus, title: str, org: Any = None) -> Case:
    return Case.objects.create(title=title, status=status, owner_org=org)


def _observable(value: str) -> Observable:
    obs_type = ObservableType.objects.get_or_create(
        name="ip", defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(data_type=obs_type, data=value, normalized_data=value)


# --- AC6.1-P4-a — the per-item failure boundary ----------------------------


def test_bulk_case_patch_commits_the_others_when_one_id_is_unknown(
    api: APIClient, analyst: User, case_status: CaseStatus
) -> None:
    first = _case(case_status, "Bulk A")
    second = _case(case_status, "Bulk B")

    response = api.patch(
        "/api/v1/case/_bulk",
        {"ids": [str(first.id), "not-a-uuid", str(second.id)], "severity": 3},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["updated"] == 2
    assert body["failed"] == 1
    statuses = {item["id"]: item["status"] for item in body["results"]}
    assert statuses[str(first.id)] == 200
    assert statuses[str(second.id)] == 200
    assert statuses["not-a-uuid"] == 404

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.severity, second.severity) == (3, 3), "a bad id rolled back its neighbours"


def test_bulk_case_patch_reports_an_out_of_range_value_without_rolling_back_the_batch(
    api: APIClient, case_status: CaseStatus
) -> None:
    good = _case(case_status, "Bulk C")

    response = api.patch(
        "/api/v1/case/_bulk",
        {"ids": [str(good.id)], "severity": 99},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["updated"] == 0 and body["failed"] == 1
    assert body["results"][0]["status"] == 400
    good.refresh_from_db()
    assert good.severity != 99, "an out-of-range value was stored"


def test_bulk_alert_patch_updates_severity(api: APIClient, case_status: CaseStatus) -> None:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New"})[0]
    alert = Alert.objects.create(
        type="t", source="s", source_ref="bulk-alert", title="a", status=status
    )

    response = api.patch(
        "/api/v1/alert/_bulk", {"ids": [str(alert.id)], "severity": 4}, format="json"
    )

    assert response.status_code == 200, response.content
    alert.refresh_from_db()
    assert alert.severity == 4


def test_bulk_task_patch_updates_status(api: APIClient, case_status: CaseStatus) -> None:
    case = _case(case_status, "Bulk task case")
    task = Task.objects.create(case=case, title="Do the thing")

    response = api.patch(
        "/api/v1/task/_bulk", {"ids": [str(task.id)], "status": "InProgress"}, format="json"
    )

    assert response.status_code == 200, response.content
    task.refresh_from_db()
    assert task.status == "InProgress"


def test_bulk_observable_patch_updates_flags(api: APIClient) -> None:
    observable = _observable("192.0.2.44")

    response = api.patch(
        "/api/v1/observable/_bulk",
        {"ids": [str(observable.id)], "ioc": True, "sighted": True},
        format="json",
    )

    assert response.status_code == 200, response.content
    observable.refresh_from_db()
    assert observable.ioc is True and observable.sighted is True


# --- AC6.1-P4-b — merge moves children and is idempotent -------------------


def test_case_merge_reparents_children_and_records_provenance(
    api: APIClient, analyst: User, case_status: CaseStatus
) -> None:
    target = _case(case_status, "Target")
    source = _case(case_status, "Source")
    Task.objects.create(case=source, title="From the source")
    link = CaseObservable.objects.create(case=source, observable=_observable("192.0.2.9"))
    assert link.pk is not None

    response = api.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")

    assert response.status_code == 200, response.content
    assert response.json()["_id"] == str(target.id)

    assert not Case.objects.filter(pk=source.pk).exists()
    assert Task.objects.filter(case=target, title="From the source").exists()
    assert CaseObservable.objects.filter(case=target, observable=link.observable).exists()

    provenance = [event for event in events_after(target, None) if event.kind == "case-merged"]
    assert provenance, "the merge wrote no provenance to the target ledger"
    assert str(source.id) in str(provenance[-1].metadata)


def test_case_merge_is_idempotent_on_replay(api: APIClient, case_status: CaseStatus) -> None:
    target = _case(case_status, "Target")
    source = _case(case_status, "Source")

    first = api.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
    assert first.status_code == 200, first.content
    merged_events = [event for event in events_after(target, None) if event.kind == "case-merged"]
    assert len(merged_events) == 1

    # The source is gone; replaying the same URL resolves only the target, so it is a no-op that
    # still returns the target rather than a 404 or a second provenance entry.
    replay = api.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
    assert replay.status_code == 200, replay.content
    assert replay.json()["_id"] == str(target.id)
    assert len([e for e in events_after(target, None) if e.kind == "case-merged"]) == 1, (
        "a replay wrote a second provenance entry"
    )


# --- AC6.1-P4-c — templates apply their defaults ---------------------------


def test_applying_a_template_copies_tasks_tags_and_scalars(
    api: APIClient, case_status: CaseStatus
) -> None:
    CustomField.objects.create(name="phish-campaign", type="string")
    template = CaseTemplate.objects.create(
        name="phishing",
        title_prefix="[PHISH] ",
        severity=3,
        tlp=1,
        tags=["phishing"],
        tasks=[{"title": "Contain mailbox"}, {"title": "Notify user"}],
        custom_fields=[{"name": "phish-campaign", "value": "wave-7"}],
    )
    case = _case(case_status, "Reported mail")

    response = api.post(
        "/api/v1/case/_bulk/caseTemplate",
        {"ids": [str(case.id)], "caseTemplate": template.name, "updateTitlePrefix": True},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["updated"] == 1

    case.refresh_from_db()
    assert case.severity == 3 and case.tlp == 1
    assert case.title.startswith("[PHISH] ")
    assert sorted(Task.objects.filter(case=case).values_list("title", flat=True)) == [
        "Contain mailbox",
        "Notify user",
    ]
    assert case.custom_field_values.filter(
        custom_field__name="phish-campaign", value="wave-7"
    ).exists()
    assert case.tag_links.filter(tag__name="phishing").exists()


def test_case_template_crud_round_trips(api: APIClient) -> None:
    created = api.post(
        "/api/v1/case/template",
        {"name": "ransomware", "severity": 4, "tags": ["ransomware"]},
        format="json",
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["name"] == "ransomware" and body["severity"] == 4

    fetched = api.get(f"/api/v1/case/template/{body['id']}")
    assert fetched.status_code == 200, fetched.content

    patched = api.patch(
        f"/api/v1/case/template/{body['id']}", {"displayName": "Ransomware"}, format="json"
    )
    assert patched.status_code == 204, patched.content

    deleted = api.delete(f"/api/v1/case/template/{body['id']}")
    assert deleted.status_code == 204, deleted.content
    assert not CaseTemplate.objects.filter(pk=body["id"]).exists()


def test_taxonomy_aggregates_the_pickable_vocabularies(api: APIClient) -> None:
    response = api.get("/api/v1/taxonomy")
    assert response.status_code == 200, response.content
    body = response.json()
    assert {
        "observableTypes",
        "caseStatuses",
        "alertStatuses",
        "caseTemplates",
        "tags",
    } <= set(body)
