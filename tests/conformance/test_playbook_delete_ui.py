"""Playbook deletion from the UI, including the run-history warning (BRIEF 2026-10-22).

The API contract is covered in `test_playbook_authoring.py`; this file pins the server-rendered
affordance: the trash icon exists for a playbook with runs, the modal names the consequence, an
active run disables confirmation, and a confirmed delete removes the playbook plus its run rows.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from automation.models import AutomationRun, Playbook
from core.enums import VIEWER_ROLE
from identity.models import User

pytestmark = pytest.mark.django_db


def _playbook(name: str) -> Playbook:
    return Playbook.objects.create(
        name=name,
        trigger_event="observable.created",
        config={"action": "python", "action_path": "enrichment_probe"},
    )


def _run(playbook: Playbook, *, status: str, key: str) -> AutomationRun:
    return AutomationRun.objects.create(
        playbook=playbook,
        playbook_name=playbook.name,
        trigger_event="observable.created",
        status=status,
        idempotency_key=key,
    )


def _browser(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_list_shows_delete_for_a_playbook_with_terminal_runs(analyst: User) -> None:
    playbook = _playbook("ui-terminal-runs")
    _run(playbook, status="Success", key="ui-terminal-1")
    _run(playbook, status="Failed", key="ui-terminal-2")

    html = _browser(analyst).get(reverse("ui-playbook-list")).content.decode()

    assert f'data-modal-open="delete-playbook-{playbook.id}"' in html
    assert "fa-trash-can" in html
    assert "2 past runs" in html
    assert "run history" in html
    assert "Case timeline entries are kept" in html


def test_detail_warns_and_disables_delete_while_a_run_is_active(analyst: User) -> None:
    playbook = _playbook("ui-active-runs")
    _run(playbook, status="Running", key="ui-active-1")

    html = (
        _browser(analyst)
        .get(reverse("ui-playbook-detail", kwargs={"playbook_id": str(playbook.id)}))
        .content.decode()
    )

    assert "Delete playbook" in html
    assert "Cannot delete playbook yet" in html
    assert "1 run still pending or running" in html
    assert "deactivate the playbook instead" in html
    assert "disabled" in html


def test_delete_with_terminal_runs_removes_the_playbook_and_history(analyst: User) -> None:
    playbook = _playbook("ui-delete-terminal")
    first = _run(playbook, status="Success", key="ui-delete-terminal-1")
    second = _run(playbook, status="Failed", key="ui-delete-terminal-2")
    client = _browser(analyst)

    response = client.post(
        reverse("ui-playbook-delete", kwargs={"playbook_id": str(playbook.id)}), follow=True
    )

    assert response.redirect_chain[-1][0] == reverse("ui-playbook-list")
    assert not Playbook.objects.filter(pk=playbook.pk).exists()
    assert not AutomationRun.objects.filter(pk__in=[first.pk, second.pk]).exists()
    assert "2 runs of history were also removed" in response.content.decode()


def test_delete_with_an_active_run_is_refused_and_keeps_the_rows(analyst: User) -> None:
    playbook = _playbook("ui-delete-blocked")
    run = _run(playbook, status="Pending", key="ui-delete-blocked-1")
    client = _browser(analyst)

    response = client.post(
        reverse("ui-playbook-delete", kwargs={"playbook_id": str(playbook.id)}), follow=True
    )

    assert response.redirect_chain[-1][0] == reverse(
        "ui-playbook-detail", kwargs={"playbook_id": str(playbook.id)}
    )
    assert Playbook.objects.filter(pk=playbook.pk).exists()
    assert AutomationRun.objects.filter(pk=run.pk).exists()
    body = response.content.decode()
    assert "still pending or running" in body
    assert "deactivate the playbook" in body


def test_viewer_is_not_offered_a_playbook_delete_control(analyst: User) -> None:
    playbook = _playbook("ui-viewer-no-delete")
    _run(playbook, status="Success", key="ui-viewer-no-delete-1")
    viewer = User.objects.create(username="playbook-del-viewer", org=analyst.org, role=VIEWER_ROLE)
    client = _browser(viewer)

    list_html = client.get(reverse("ui-playbook-list")).content.decode()
    assert reverse("ui-playbook-delete", kwargs={"playbook_id": str(playbook.id)}) not in list_html
    assert f'data-modal-open="delete-playbook-{playbook.id}"' not in list_html

    detail_html = client.get(
        reverse("ui-playbook-detail", kwargs={"playbook_id": str(playbook.id)})
    ).content.decode()
    assert "Delete playbook" not in detail_html
    assert f'id="delete-playbook-{playbook.id}"' not in detail_html
