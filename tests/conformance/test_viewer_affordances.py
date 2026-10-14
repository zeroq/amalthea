"""ADR-006 Layer 3 — a viewer is never *offered* a control the server will refuse.

The security boundary is server-side (Layers 1-2, pinned by `test_viewer_readonly_ui.py` and
`test_viewer_readonly_api.py`). These tests pin the UX half of the rule: the same page that renders
mutation controls for an analyst renders none of them for a viewer, and - just as important - the
read affordances survive. A template that forgets the guard shows a dead control, never a hole, so
the failure mode of this file is a bad page, not a breach.

Every "viewer has nothing" assertion is paired with an "an analyst has it" assertion on the same
page, so the file cannot pass because a template or a route stopped rendering altogether.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.enums import VIEWER_ROLE
from identity.models import Organisation, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.get_or_create(name="Default")[0]


def _user(org: Organisation, username: str, role: str = "analyst") -> User:
    return User.objects.create(username=username, org=org, role=role)


@pytest.fixture
def viewer_browser(org: Organisation) -> Client:
    client = Client()
    client.force_login(_user(org, "aff-viewer", VIEWER_ROLE))
    return client


@pytest.fixture
def analyst_browser(org: Organisation) -> Client:
    client = Client()
    client.force_login(_user(org, "aff-analyst"))
    return client


def _case(org: Organisation, **kw):
    from cases.models import Case, CaseStatus

    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(
        title="Suspicious inbox rule", owner_org=org, status=status, description="as reported", **kw
    )


def _present(html: str, paths: list[str]) -> list[str]:
    return [path for path in paths if path in html]


# --- case detail ---------------------------------------------------------------


def test_case_detail_offers_a_viewer_no_mutation_controls(
    viewer_browser: Client, analyst_browser: Client, org: Organisation
) -> None:
    case = _case(org)
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    mutation_paths = [
        reverse("ui-case-status", kwargs={"case_id": case.number}),
        reverse("ui-case-extract", kwargs={"case_id": case.number}),
        reverse("ui-case-comment", kwargs={"case_id": case.number}),
        reverse("ui-case-procedure-create", kwargs={"case_id": case.number}),
        reverse("ui-case-task-create", kwargs={"case_id": case.number}),
        reverse("ui-case-attachment-upload", kwargs={"case_id": case.number}),
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        reverse("ui-case-share-list", kwargs={"case_id": case.number}),
        reverse("ui-case-edit", kwargs={"case_id": case.number}),
        reverse("ui-case-merge", kwargs={"case_id": case.number}),
    ]

    # The analyst is the control: if these paths are missing for them too, the viewer assertion
    # below would be vacuous.
    missing_for_analyst = _present(analyst_browser.get(url).content.decode(), mutation_paths)
    assert missing_for_analyst == mutation_paths, (
        "analyst page no longer carries these controls, so the viewer check is vacuous: "
        f"{[p for p in mutation_paths if p not in missing_for_analyst]}"
    )

    response = viewer_browser.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert _present(html, mutation_paths) == [], "viewer was offered a mutation control"
    assert "Read-only access" in html, "the viewer's note box should say why it is absent"
    # Reads survive: the export link and the case body.
    assert reverse("ui-case-export", kwargs={"case_id": case.number}) in html
    assert "Suspicious inbox rule" in html


def test_case_detail_hides_the_merge_modal_from_a_viewer(
    viewer_browser: Client, analyst_browser: Client, org: Organisation
) -> None:
    case = _case(org)
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    assert 'id="case-merge-modal"' in analyst_browser.get(url).content.decode()
    assert 'id="case-merge-modal"' not in viewer_browser.get(url).content.decode()


def test_case_detail_keeps_download_but_drops_delete_for_a_viewer(
    viewer_browser: Client, analyst_browser: Client, org: Organisation
) -> None:
    from cases.models import Attachment

    case = _case(org)
    attachment = Attachment.objects.create(
        case=case,
        name="evidence.txt",
        content_type="text/plain",
        size=12,
        sha256="a" * 64,
        path="2026/01/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt",
    )
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    download = reverse(
        "ui-case-attachment-download",
        kwargs={"case_id": case.number, "attachment_id": attachment.id},
    )

    assert "attachment-delete-" in analyst_browser.get(url).content.decode()
    viewer_html = viewer_browser.get(url).content.decode()
    assert "attachment-delete-" not in viewer_html
    assert download in viewer_html, "a viewer may still read the evidence"


# --- alert detail --------------------------------------------------------------


def test_alert_detail_offers_a_viewer_no_triage_controls(
    viewer_browser: Client, analyst_browser: Client, org: Organisation
) -> None:
    from alerts.models import Alert, AlertStatus

    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    alert = Alert.objects.create(
        title="Suspicious inbox rule",
        owner_org=org,
        status=status,
        source="o365",
        source_ref="evt-1",
        type="mailbox.rule.created",
        raw_payload={"event": "mailbox.rule.created"},
    )
    url = reverse("ui-alert-detail", kwargs={"alert_id": alert.id})
    paths = [
        reverse("ui-alert-status", kwargs={"alert_id": alert.id}),
        reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}),
        reverse("ui-alert-merge", kwargs={"alert_id": alert.id}),
    ]

    assert _present(analyst_browser.get(url).content.decode(), ["escalate"]) != []
    viewer_html = viewer_browser.get(url).content.decode()
    assert _present(viewer_html, paths) == [], "viewer was offered a triage control"
    assert "Read-only access" in viewer_html
    assert "Raw payload" in viewer_html, "a viewer still reads the payload"
    assert "Escalate" not in viewer_html


# --- observable detail ---------------------------------------------------------


def test_observable_detail_offers_a_viewer_no_edit_or_delete(
    viewer_browser: Client, analyst_browser: Client, make_observable
) -> None:
    observable = make_observable(data="203.0.113.9")
    url = reverse("ui-observable-detail", kwargs={"observable_id": str(observable.id)})
    paths = [
        reverse("ui-observable-edit", kwargs={"observable_id": str(observable.id)}),
        reverse("ui-observable-delete", kwargs={"observable_id": str(observable.id)}),
    ]

    analyst_html = analyst_browser.get(url).content.decode()
    assert _present(analyst_html, paths) == paths, "analyst page lost the edit/delete controls"
    viewer_html = viewer_browser.get(url).content.decode()
    assert _present(viewer_html, paths) == [], "viewer was offered an edit/delete control"
    assert "203.0.113.9" in viewer_html, "a viewer still reads the observable"


# --- authoring lists -----------------------------------------------------------


def test_playbook_list_offers_a_viewer_no_authoring_links(
    viewer_browser: Client, analyst_browser: Client
) -> None:
    from automation.models import Playbook

    playbook = Playbook.objects.create(
        name="aff-ordering-playbook",
        trigger_event="observable.created",
        config={"action": "python"},
    )
    url = reverse("ui-playbook-list")
    detail = reverse("ui-playbook-detail", kwargs={"playbook_id": str(playbook.id)})
    create = reverse("ui-playbook-create")
    delete = reverse("ui-playbook-delete", kwargs={"playbook_id": str(playbook.id)})

    analyst_html = analyst_browser.get(url).content.decode()
    assert create in analyst_html and detail in analyst_html and delete in analyst_html
    viewer_html = viewer_browser.get(url).content.decode()
    assert create not in viewer_html and detail not in viewer_html and delete not in viewer_html
    assert "aff-ordering-playbook" in viewer_html, "a viewer still reads the playbook inventory"


def test_tag_list_offers_a_viewer_no_authoring_links(
    viewer_browser: Client, analyst_browser: Client
) -> None:
    from cases.models import Tag

    tag = Tag.objects.create(name="phishing")
    url = reverse("ui-tag-list")
    detail = reverse("ui-tag-detail", kwargs={"tag_id": str(tag.id)})
    create = reverse("ui-tag-create")
    delete = reverse("ui-tag-delete", kwargs={"tag_id": str(tag.id)})

    analyst_html = analyst_browser.get(url).content.decode()
    assert create in analyst_html and detail in analyst_html and delete in analyst_html
    viewer_html = viewer_browser.get(url).content.decode()
    assert create not in viewer_html and detail not in viewer_html and delete not in viewer_html
    assert "phishing" in viewer_html


def test_case_template_list_offers_a_viewer_no_authoring_links(
    viewer_browser: Client, analyst_browser: Client
) -> None:
    from cases.models import CaseTemplate

    template = CaseTemplate.objects.create(name="phishing-blueprint", display_name="Phishing")
    url = reverse("ui-case-template-list")
    detail = reverse("ui-case-template-detail", kwargs={"template_id": str(template.id)})
    create = reverse("ui-case-template-create")
    delete = reverse("ui-case-template-delete", kwargs={"template_id": str(template.id)})

    analyst_html = analyst_browser.get(url).content.decode()
    assert create in analyst_html and detail in analyst_html and delete in analyst_html
    viewer_html = viewer_browser.get(url).content.decode()
    assert create not in viewer_html and detail not in viewer_html and delete not in viewer_html
    assert "phishing-blueprint" in viewer_html
