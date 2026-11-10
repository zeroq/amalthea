"""Case-template defaults and source-alert payload rendering.

Covers the feature plan `PLAN-2026-10-22-case-template-defaults.md`:
- UI create templates prefill a default task/custom field, while API creation stays explicit.
- Applying a template resolves `{{ alert.raw_payload }}` from the earliest linked alert.
- The description excerpt is capped at 64 KiB; the task page links to the complete payload.
- The full-payload modal is lazy-loaded, tenant-scoped, and never cached.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases import template_defaults as td
from cases.models import (
    Case,
    CaseCustomFieldValue,
    CaseStatus,
    CaseTemplate,
    CustomField,
    Share,
    Task,
    TimelineEvent,
)
from identity.models import AuditLog, Organisation, User
from ui.markdown import render_markdown

TOKEN = td.SOURCE_ALERT_PAYLOAD_TOKEN
SENTINEL = td.SOURCE_ALERT_PAYLOAD_SENTINEL
TRUNCATION = td.SOURCE_ALERT_PAYLOAD_TRUNCATION_MARKER


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=login,
        login=login,
        password="hunter2-correct",
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _mk_case(org: Organisation, title: str = "Case") -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_alert(
    org: Organisation,
    *,
    case: Case | None = None,
    ref: str = "ref-1",
    raw_payload: dict | None = None,
    date=None,
) -> Alert:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=ref,
        title=f"Alert {ref}",
        status=status,
        owner_org=org,
        case=case,
        raw_payload=raw_payload or {},
        **({"date": date} if date is not None else {}),
    )


def _mk_task(case: Case, description: str, title: str = "Task") -> Task:
    return Task.objects.create(case=case, title=title, description=description)


def _login(client: Client, user: User) -> None:
    client.force_login(user)


# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------


def test_no_token_returns_description_byte_for_byte(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})

    description = "No token here.\n\nJust prose."
    assert td.render_source_alert_payload(description, case) == description


def test_no_alert_falls_back_without_sentinel(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)

    rendered = td.render_source_alert_payload(f"Payload: {TOKEN}", case)

    assert "No alert linked to this case yet" in rendered
    assert SENTINEL not in rendered
    assert TRUNCATION not in rendered


def test_token_at_end_appends_one_sentinel_on_its_own_line(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"user": "analyst@example.net"})

    rendered = td.render_source_alert_payload(f"Payload: {TOKEN}", case)

    assert '"user": "analyst@example.net"' in rendered
    assert rendered.count(SENTINEL) == 1
    assert rendered.splitlines()[-1] == SENTINEL
    assert td.has_source_payload_sentinel(rendered)


def test_trailing_prose_does_not_render_the_sentinel_visibly(db: None) -> None:
    """Regression for the P0: the sentinel used to be appended inside the token replacement."""
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"user": "analyst@example.net"})

    rendered = td.render_source_alert_payload(f"Payload: {TOKEN} trailing words", case)
    html = render_markdown(rendered)

    assert "trailing words" in rendered
    assert rendered.splitlines()[-1] == SENTINEL
    assert "amalthea:source-alert-payload" not in html
    assert "[//]" not in html


def test_token_followed_by_newline_and_prose_keeps_sentinel_last(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})

    rendered = td.render_source_alert_payload(f"{TOKEN}\n\nAfter the block.", case)

    assert "After the block." in rendered
    assert rendered.splitlines()[-1] == SENTINEL
    assert "amalthea:source-alert-payload" not in render_markdown(rendered)


def test_two_tokens_still_append_only_one_sentinel(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})

    rendered = td.render_source_alert_payload(f"A {TOKEN} B {TOKEN} C", case)

    assert rendered.count(SENTINEL) == 1
    assert rendered.splitlines()[-1] == SENTINEL


def test_sentinel_detection_requires_an_own_line(db: None) -> None:
    assert td.has_source_payload_sentinel(SENTINEL)
    assert td.has_source_payload_sentinel(f"prefix {SENTINEL} suffix") is False
    assert td.has_source_payload_sentinel(f"`{SENTINEL}`") is False
    assert td.has_source_payload_sentinel("") is False


def test_existing_sentinel_is_not_duplicated(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})

    rendered = td.render_source_alert_payload(f"{TOKEN}\n\n{SENTINEL}", case)

    assert rendered.count(SENTINEL) == 1


def test_large_payload_is_capped_fenced_and_marked(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": "a" * 70_000})

    rendered = td.render_source_alert_payload(f"{TOKEN}", case)

    assert TRUNCATION in rendered
    assert rendered.count("```") == 2  # one opening and one closing fence
    assert rendered.splitlines()[-1] == SENTINEL


def test_multibyte_truncation_never_splits_a_character(db: None) -> None:
    text = "abcdé"
    truncated, was_truncated = td._truncate_utf8_bytes(text, 5)

    assert was_truncated is True
    assert truncated == "abcd"
    assert len(truncated.encode("utf-8")) <= 5
    assert "\ufffd" not in truncated


def test_empty_payload_renders_a_closed_fence(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={})

    rendered = td.render_source_alert_payload(f"{TOKEN}", case)

    assert "```json\n{}\n```" in rendered
    assert rendered.count("```") == 2


def test_earliest_alert_ties_break_on_created_at(db: None) -> None:
    org = _mk_org("Org")
    case = _mk_case(org)
    date = timezone.now()
    first = _mk_alert(org, case=case, ref="first", date=date)
    second = _mk_alert(org, case=case, ref="second", date=date)
    Alert.objects.filter(pk=first.pk).update(created_at=date - timedelta(seconds=1))
    Alert.objects.filter(pk=second.pk).update(created_at=date)

    assert td.earliest_alert_for_case(case) == first


def test_earliest_alert_ignores_cross_org_alerts(db: None) -> None:
    org = _mk_org("Org1")
    other = _mk_org("Org2")
    case = _mk_case(org)
    own = _mk_alert(org, case=case, ref="own", date=timezone.now())
    _mk_alert(other, case=case, ref="other", date=timezone.now() - timedelta(days=1))

    assert td.earliest_alert_for_case(case) == own


def test_format_full_payload_compacts_when_pretty_exceeds_cap(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = _mk_org("Org")
    alert = _mk_alert(org, raw_payload={"a": "x" * 10})
    compact = json.dumps(
        alert.raw_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    pretty = json.dumps(alert.raw_payload, ensure_ascii=False, indent=2, sort_keys=True)
    assert len(compact) < len(pretty)
    monkeypatch.setattr(td, "MAX_FULL_PAYLOAD_BYTES", len(compact))

    assert td.format_full_payload(alert) == compact


def test_format_full_payload_refuses_oversize_compact(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = _mk_org("Org")
    alert = _mk_alert(org, raw_payload={"a": "x" * 100})
    monkeypatch.setattr(td, "MAX_FULL_PAYLOAD_BYTES", 10)

    assert "too large to display" in td.format_full_payload(alert)


def test_format_full_payload_skips_pretty_when_estimate_exceeds_cap(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: do not allocate a pretty string that a conservative estimate says is too big."""
    org = _mk_org("Org")
    alert = _mk_alert(org, raw_payload={"a": "x" * 50})
    compact = json.dumps(
        alert.raw_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    pretty = json.dumps(alert.raw_payload, ensure_ascii=False, indent=2, sort_keys=True)
    # The cap admits the pretty form, but the 2x estimate does not.
    assert len(compact) < len(pretty)
    assert len(compact) * 2 > len(pretty)
    monkeypatch.setattr(td, "MAX_FULL_PAYLOAD_BYTES", len(pretty))

    assert td.format_full_payload(alert) == compact


# ---------------------------------------------------------------------------
# Template authoring / application
# ---------------------------------------------------------------------------


def test_ui_create_form_prefills_default_task_and_custom_field(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-case-template-create"))

    assert response.status_code == 200
    assert response.context["form_tasks"] == td.DEFAULT_TEMPLATE_TASKS
    assert response.context["form_custom_fields"] == td.DEFAULT_TEMPLATE_CUSTOM_FIELDS
    assert "Triage/Analysis" in response.content.decode()
    assert "Example Field" in response.content.decode()


def test_ui_create_empty_submission_keeps_defaults(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-template-create"),
        {
            "name": "defaulted",
            "tasks": "",
            "custom_fields": "",
            "severity": "2",
            "tlp": "2",
            "pap": "2",
        },
    )

    assert response.status_code == 302
    template = CaseTemplate.objects.get(name="defaulted")
    assert template.tasks == td.DEFAULT_TEMPLATE_TASKS
    assert template.custom_fields == td.DEFAULT_TEMPLATE_CUSTOM_FIELDS


def test_api_create_template_does_not_inject_defaults(api) -> None:
    response = api.post(
        "/api/v1/case/template", {"name": "api-explicit", "tasks": []}, format="json"
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["tasks"] == []
    assert body["customFields"] == []


def test_ui_apply_template_renders_payload_and_writes_one_ledger_entry(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"user": "analyst@example.net"})
    template = CaseTemplate.objects.create(
        name="payload-template",
        tasks=[{"title": "Triage/Analysis", "description": f"Payload: {TOKEN} trailing"}],
    )
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )

    assert response.status_code == 302
    task = Task.objects.get(case=case)
    assert "analyst@example.net" in task.description
    assert task.description.splitlines()[-1] == SENTINEL
    assert TimelineEvent.objects.filter(case=case, kind="template-applied").count() == 1


def test_ui_apply_template_non_uuid_is_404_not_500(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": "not-a-uuid"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Template provenance + re-apply to seeded cases
# ---------------------------------------------------------------------------


def test_ui_apply_template_records_source_template(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(name="provenance")
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.source_template_id == template.id


def test_edit_apply_to_existing_resyncs_seeded_cases_additively(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    seeded = _mk_case(org, "Seeded")
    unrelated = _mk_case(org, "Unrelated")
    existing_field = CustomField.objects.create(name="existing-field", type="string")
    fresh_field = CustomField.objects.create(name="fresh-field", type="string")
    template = CaseTemplate.objects.create(
        name="resync",
        severity=1,
        tlp=0,
        pap=0,
        summary="old summary",
        description="old description",
        tags=["existing-tag"],
        # Same title in two groups is two distinct template tasks.
        tasks=[
            {"title": "Task A", "group": "G1"},
            {"title": "Task A", "group": "G2"},
        ],
        custom_fields=[{"name": "existing-field", "value": "keep-me"}],
    )
    client = Client()
    _login(client, user)
    assert (
        client.post(
            reverse("ui-case-apply-template", kwargs={"case_id": seeded.number}),
            {"template_id": str(template.id)},
        ).status_code
        == 302
    )

    seeded.refresh_from_db()
    assert seeded.source_template_id == template.id
    assert seeded.summary == "old summary"
    assert seeded.description == "old description"
    assert seeded.tasks.count() == 2
    assert seeded.custom_field_values.get(custom_field=existing_field).value == "keep-me"

    # Move the template forward, then re-apply through the edit form.
    template.severity = 4
    template.tlp = 3
    template.pap = 2
    template.flag = True
    template.scope = "enterprise"
    template.case_type = "incident"
    template.phase = "containment"
    template.tags = ["existing-tag", "new-tag"]
    template.tasks = [
        {"title": "Task A", "group": "G1"},  # present -> skipped
        {"title": "Task A", "group": "G2"},  # present -> skipped
        {"title": "Task A", "group": "G3"},  # new group -> added
        {"title": "Task B", "group": "G1"},  # new title -> added
    ]
    template.custom_fields = [
        {"name": "existing-field", "value": "must-not-overwrite"},
        {"name": "fresh-field", "value": "new-value"},
    ]
    template.save()

    edit_url = reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    page = client.get(edit_url)
    assert page.status_code == 200
    html = page.content.decode()
    # The choice dialog knows how many writable seeded cases a resync would touch.
    assert "template-update-modal" in html
    assert "Update &amp; Apply to Cases" in html
    assert "<strong>1</strong> case" in html

    response = client.post(
        edit_url,
        {
            "name": template.name,
            "summary": "new summary",
            "description": "new description",
            "flag": "on",
            "severity": "4",
            "tlp": "3",
            "pap": "2",
            "tags": "existing-tag, new-tag",
            "tasks": json.dumps(template.tasks),
            "custom_fields": json.dumps(template.custom_fields),
            "apply_to_existing": "1",
        },
    )

    assert response.status_code == 302
    seeded.refresh_from_db()
    assert (seeded.severity, seeded.tlp, seeded.pap, seeded.flag) == (4, 3, 2, True)
    assert (seeded.scope, seeded.case_type, seeded.phase) == (
        "enterprise",
        "incident",
        "containment",
    )
    # The template's summary overwrites; its prose never clobbers a description the case owns.
    assert seeded.summary == "new summary"
    assert seeded.description == "old description"
    # Tags are additive, not replaced.
    assert set(seeded.tags.values_list("name", flat=True)) == {"existing-tag", "new-tag"}
    # Runbook rows are matched, not re-created: existing values win, missing rows are added.
    assert set(seeded.tasks.values_list("title", "group")) == {
        ("Task A", "G1"),
        ("Task A", "G2"),
        ("Task A", "G3"),
        ("Task B", "G1"),
    }
    assert seeded.tasks.count() == 4
    assert seeded.custom_field_values.get(custom_field=existing_field).value == "keep-me"
    assert seeded.custom_field_values.get(custom_field=fresh_field).value == "new-value"
    assert TimelineEvent.objects.filter(case=seeded, kind="template-reapplied").count() == 1

    unrelated.refresh_from_db()
    assert unrelated.severity == 2
    assert unrelated.source_template_id is None
    assert unrelated.tasks.count() == 0
    assert not TimelineEvent.objects.filter(case=unrelated).exists()


def test_edit_apply_to_existing_is_scoped_to_writable_cases(db: None) -> None:
    """A global template must not become a cross-tenant write (ADR-004)."""
    my_org = _mk_org("Mine")
    other_org = _mk_org("Other")
    user = _mk_user("analyst", my_org)
    template = CaseTemplate.objects.create(name="shared-blueprint")
    mine = _mk_case(my_org)
    theirs = _mk_case(other_org)
    Case.objects.filter(pk__in=[mine.pk, theirs.pk]).update(source_template=template)
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)}),
        {"name": template.name, "severity": "4", "apply_to_existing": "1"},
    )

    assert response.status_code == 302
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.severity == 4
    assert theirs.severity == 2


def test_delete_template_modal_ids_match_openers(db: None) -> None:
    """Regression: `"prefix-"|add:uuid` yields `""`, so the modal never opened.

    Both the list trash button and the detail "Delete template" button must point at a
    dialog whose id is the fully-interpolated UUID.
    """
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    template = CaseTemplate.objects.create(name="modal-id")
    client = Client()
    _login(client, user)
    expected = f"delete-template-{template.id}"

    for url in (
        reverse("ui-case-template-list"),
        reverse("ui-case-template-detail", kwargs={"template_id": str(template.id)}),
    ):
        body = client.get(url).content.decode()
        assert f'data-modal-open="{expected}"' in body, url
        assert f'id="{expected}"' in body, url


def test_delete_template_detaches_seeded_cases(db: None) -> None:
    """Deleting a template must succeed and null the provenance on the cases it seeded.

    `Case.source_template` is SET_NULL: the cases keep the defaults already applied to them, but
    the pointer to a now-deleted template is cleared instead of blocking the delete (PROTECT).
    """
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(name="deletable")
    Case.objects.filter(pk=case.pk).update(source_template=template)
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-template-delete", kwargs={"template_id": str(template.id)})
    )

    assert response.status_code == 302
    assert not CaseTemplate.objects.filter(pk=template.pk).exists()
    case.refresh_from_db()
    assert case.source_template_id is None


# ---------------------------------------------------------------------------
# Task detail + full-payload modal
# ---------------------------------------------------------------------------


def test_task_detail_shows_exactly_one_trigger_when_rendered(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"user": "analyst@example.net"})
    task = _mk_task(case, td.render_source_alert_payload(f"Payload: {TOKEN}", case))
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-task-detail", kwargs={"task_id": task.id}))

    assert response.status_code == 200
    assert response.context["show_source_payload"] is True
    assert response.context["source_payload_url"] == reverse(
        "ui-task-source-payload", kwargs={"task_id": task.id}
    )
    assert response.content.decode().count(f'data-modal-open="task-source-payload-{task.id}"') == 1


def test_task_detail_hides_trigger_without_sentinel(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})
    task = _mk_task(case, "Plain description")
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-task-detail", kwargs={"task_id": task.id}))

    assert response.status_code == 200
    assert response.context["show_source_payload"] is False
    assert "View full alert payload" not in response.content.decode()


def test_task_edit_removes_sentinel_and_hides_trigger(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-task-edit", kwargs={"task_id": task.id}),
        {"title": task.title, "status": "Waiting", "description": "clean"},
    )
    assert response.status_code == 302

    task.refresh_from_db()
    assert SENTINEL not in task.description
    assert (
        "View full alert payload"
        not in client.get(reverse("ui-task-detail", kwargs={"task_id": task.id})).content.decode()
    )


def test_task_source_payload_returns_tail_truncated_from_the_excerpt(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(
        org,
        case=case,
        raw_payload={"padding": "x" * 70_000, "tail": "TAIL_MARKER"},
    )
    description = td.render_source_alert_payload(f"{TOKEN}", case)
    task = _mk_task(case, description)
    client = Client()
    _login(client, user)

    assert "TAIL_MARKER" not in description
    response = client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id}))

    assert response.status_code == 200
    assert b"TAIL_MARKER" in response.content


def test_task_source_payload_is_never_cached(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id}))

    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert "max-age=0" in response["Cache-Control"]


def test_task_source_payload_hides_raw_alert_from_case_sharee(db: None) -> None:
    owner_org = _mk_org("Owner")
    sharee_org = _mk_org("Sharee")
    sharee = _mk_user("sharee", sharee_org)
    case = _mk_case(owner_org)
    _mk_alert(owner_org, case=case, raw_payload={"secret": "alert-only"})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))
    Share.objects.create(case=case, organisation=sharee_org, permissions={"read": True})
    client = Client()
    _login(client, sharee)

    detail = client.get(reverse("ui-task-detail", kwargs={"task_id": task.id}))
    fragment = client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id}))

    assert detail.status_code == 200
    assert detail.context["show_source_payload"] is False
    assert "View full alert payload" not in detail.content.decode()
    assert fragment.status_code == 404


def test_task_source_payload_allows_owner_and_superadmin(db: None) -> None:
    owner_org = _mk_org("Owner")
    other_org = _mk_org("Other")
    owner = _mk_user("owner", owner_org)
    superadmin = _mk_user("super", other_org, role="superAdmin")
    case = _mk_case(owner_org)
    _mk_alert(owner_org, case=case, raw_payload={"x": 1})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))

    owner_client = Client()
    _login(owner_client, owner)
    assert (
        owner_client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id})).status_code
        == 200
    )

    super_client = Client()
    _login(super_client, superadmin)
    assert (
        super_client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id})).status_code
        == 200
    )


def test_superadmin_task_reads_write_cross_org_audit_log(db: None) -> None:
    owner_org = _mk_org("Owner")
    other_org = _mk_org("Other")
    owner = _mk_user("owner", owner_org)
    superadmin = _mk_user("super", other_org, role="superAdmin")
    case = _mk_case(owner_org)
    _mk_alert(owner_org, case=case, raw_payload={"x": 1})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))
    AuditLog.objects.all().delete()

    super_client = Client()
    _login(super_client, superadmin)
    assert (
        super_client.get(reverse("ui-task-detail", kwargs={"task_id": task.id})).status_code == 200
    )

    logs = AuditLog.objects.filter(
        action="cross_org_read", target_type="Task", target_id=str(task.id)
    )
    assert logs.count() == 1
    log = logs.get()
    assert log.actor_id == superadmin.id
    assert log.actor_org_id == other_org.id
    assert log.target_org_id == owner_org.id

    # Same-org reads by the owner must not add audit rows.
    owner_client = Client()
    _login(owner_client, owner)
    assert (
        owner_client.get(reverse("ui-task-detail", kwargs={"task_id": task.id})).status_code == 200
    )
    assert (
        owner_client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id})).status_code
        == 200
    )
    assert logs.count() == 1

    # The full-payload fragment is a second cross-org read and is audited too.
    assert (
        super_client.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id})).status_code
        == 200
    )
    assert logs.count() == 2


def test_task_source_payload_anonymous_and_post_guards(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": 1})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))

    anonymous = Client()
    assert (
        anonymous.get(reverse("ui-task-source-payload", kwargs={"task_id": task.id})).status_code
        == 302
    )

    client = Client()
    _login(client, user)
    assert (
        client.post(reverse("ui-task-source-payload", kwargs={"task_id": task.id})).status_code
        == 405
    )


def test_payload_script_is_escaped_in_markdown(db: None) -> None:
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    _mk_alert(org, case=case, raw_payload={"x": "<script>alert(1)</script>"})
    task = _mk_task(case, td.render_source_alert_payload(f"{TOKEN}", case))
    client = Client()
    _login(client, user)

    content = client.get(reverse("ui-task-detail", kwargs={"task_id": task.id})).content.decode()

    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content


# ---------------------------------------------------------------------------
# Regression: template custom-field materialization
# ---------------------------------------------------------------------------


def test_template_missing_field_creates_custom_field_and_value(db: None) -> None:
    """When a template references a CustomField that does not yet exist, apply-time
    materialization creates the global CustomField and the CaseCustomFieldValue,
    making the case-detail Custom tab appear."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org, "Missing field case")
    template = CaseTemplate.objects.create(
        name="tmpl-with-missing-field",
        custom_fields=[
            {"name": "Example Field", "type": "string", "value": "example"},
        ],
    )
    client = Client()
    _login(client, user)
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302, response.content

    case.refresh_from_db()
    # The global CustomField should have been created.
    field = CustomField.objects.get(name="Example Field")
    assert field.type == "string"
    # The case value should have been materialized.
    value = case.custom_field_values.get(custom_field=field)
    assert value.value == "example"
    # The Custom tab should now be visible.
    detail = client.get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    html = detail.content.decode()
    assert 'id="tab-custom"' in html
    assert 'id="panel-custom"' in html


def test_template_existing_field_reused_type_not_changed(db: None) -> None:
    """When a template references an existing CustomField, the global definition wins;
    the template's declared type does NOT overwrite it."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org, "Existing field case")
    # Pre-existing field with type 'integer'.
    CustomField.objects.create(name="Existing Field", type="integer")
    template = CaseTemplate.objects.create(
        name="tmpl-existing-field",
        custom_fields=[
            {"name": "Existing Field", "type": "string", "value": 7},
        ],
    )
    client = Client()
    _login(client, user)
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302, response.content

    case.refresh_from_db()
    # The existing field must be reused (not duplicated).
    assert CustomField.objects.filter(name="Existing Field").count() == 1
    # Its type must remain "integer" — the template's "string" must NOT change it.
    field = CustomField.objects.get(name="Existing Field")
    assert field.type == "integer"
    assert case.custom_field_values.get(custom_field=field).value == 7


def test_template_missing_invalid_type_defaults_to_string(db: None) -> None:
    """When a template's custom field has a missing or invalid type, it defaults to "string"."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org, "Invalid type case")
    template = CaseTemplate.objects.create(
        name="tmpl-invalid-type",
        custom_fields=[
            {"name": "Bad Type Field", "value": "fallback"},  # no type declared
            {"name": "Also Bad", "type": "colour", "value": "red"},  # 'colour' is not valid
        ],
    )
    client = Client()
    _login(client, user)
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302, response.content

    case.refresh_from_db()
    # Both fields should have been created with type 'string'.
    for name, expected_value in [("Bad Type Field", "fallback"), ("Also Bad", "red")]:
        field = CustomField.objects.get(name=name)
        assert field.type == "string", f"Expected string, got {field.type} for {name}"
        assert case.custom_field_values.get(custom_field=field).value == expected_value


def test_template_resync_does_not_overwrite_existing_value(db: None) -> None:
    """Resync-ing a template must not overwrite an existing CaseCustomFieldValue."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org, "Resync case")
    field = CustomField.objects.create(name="Persistent Field", type="string")
    CaseCustomFieldValue.objects.create(case=case, custom_field=field, value="original")
    template = CaseTemplate.objects.create(
        name="resync-template",
        custom_fields=[
            {"name": "Persistent Field", "value": "should-not-change"},
        ],
    )
    client = Client()
    _login(client, user)
    # First apply.
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302, response.content
    # Re-apply through the API path too: get_or_create must keep the original value.
    api_client = APIClient()
    api_client.force_login(user)
    resp = api_client.post(
        "/api/v1/case/_bulk/caseTemplate",
        {"ids": [str(case.id)], "caseTemplate": template.name, "updateTitlePrefix": False},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    case.refresh_from_db()
    value = case.custom_field_values.get(custom_field=field)
    assert value.value == "original", f"Expected 'original', got '{value.value}'"


# ---------------------------------------------------------------------------
# Regression: idempotent task materialization on template re-apply
# ---------------------------------------------------------------------------


def test_template_idempotent_ui_double_apply(db: None) -> None:
    """UI double-apply: task count stays 1; one template-applied then one template-reapplied."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(
        name="idempotent-template",
        tasks=[{"title": "Triage task", "group": ""}],
    )
    client = Client()
    _login(client, user)

    # First apply
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.tasks.count() == 1
    assert TimelineEvent.objects.filter(case=case, kind="template-applied").count() == 1

    # Second apply (re-apply)
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.tasks.count() == 1, "Second UI apply should not create a duplicate task"
    assert TimelineEvent.objects.filter(case=case, kind="template-reapplied").count() == 1, (
        "Second UI apply should write one template-reapplied event"
    )
    assert TimelineEvent.objects.filter(case=case, kind="template-applied").count() == 1, (
        "Should still have exactly one template-applied event from first apply"
    )


def test_template_idempotent_api_double_apply(db: None) -> None:
    """API double-apply via POST /api/v1/case/_bulk/caseTemplate: task count stays 1."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(
        name="api-idempotent",
        tasks=[{"title": "API task", "group": ""}],
    )
    client = Client()
    _login(client, user)
    api_client = APIClient()
    api_client.force_login(user)

    # First apply via API
    resp = api_client.post(
        "/api/v1/case/_bulk/caseTemplate",
        {"ids": [str(case.id)], "caseTemplate": template.name, "updateTitlePrefix": False},
        format="json",
    )
    assert resp.status_code == 200
    case.refresh_from_db()
    assert case.tasks.count() == 1
    assert TimelineEvent.objects.filter(case=case, kind="template-applied").count() == 1

    # Second apply via API (re-apply)
    resp = api_client.post(
        "/api/v1/case/_bulk/caseTemplate",
        {"ids": [str(case.id)], "caseTemplate": template.name, "updateTitlePrefix": False},
        format="json",
    )
    assert resp.status_code == 200
    case.refresh_from_db()
    assert case.tasks.count() == 1, "Second API apply should not create a duplicate task"
    assert TimelineEvent.objects.filter(case=case, kind="template-reapplied").count() == 1, (
        "Second API apply should write one template-reapplied event"
    )
    assert TimelineEvent.objects.filter(case=case, kind="template-applied").count() == 1, (
        "Should still have exactly one template-applied event from first apply"
    )


def test_template_same_title_different_groups_not_deduplicated(db: None) -> None:
    """Same title in different groups: both are created first time, neither duplicated second time."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(
        name="multi-group",
        tasks=[
            {"title": "Task A", "group": "G1"},
            {"title": "Task A", "group": "G2"},
        ],
    )
    client = Client()
    _login(client, user)

    # First apply
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.tasks.count() == 2

    # Second apply (re-apply)
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.tasks.count() == 2, (
        "Second apply should not duplicate tasks with same title in different groups"
    )


def test_template_manual_task_skipped_same_title_different_group(db: None) -> None:
    """Existing manual task with same (title, group) is skipped; same title in another group is added."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    # Pre-create a manual task with same title and empty group
    Task.objects.create(case=case, title="Existing Task", group="")
    template = CaseTemplate.objects.create(
        name="manual-skip",
        tasks=[{"title": "Existing Task", "group": ""}, {"title": "Existing Task", "group": "G1"}],
    )
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    # The manual task is NOT duplicated, but still exists; a same-title task in another group IS added
    assert case.tasks.count() == 2, "Manual task kept, same title in another group added"
    task_titles = list(case.tasks.values_list("title", "group"))
    assert task_titles.count(("Existing Task", "")) == 1, "Manual task should not be duplicated"
    assert ("Existing Task", "G1") in task_titles, "Same title in another group should be added"


def test_template_no_field_overwrite_on_re_apply(db: None) -> None:
    """Existing task fields (status/assignee/description) are not overwritten on re-apply."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(
        name="no-overwrite",
        tasks=[{"title": "Persistent Task", "group": "", "mandatory": True}],
    )
    # Create a task with different status initially
    task = Task.objects.create(
        case=case,
        title="Persistent Task",
        group="",
        status="InProgress",
        description="Original description",
        assignee=user,
    )
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    task.refresh_from_db()
    # Task fields must NOT be overwritten
    assert task.status == "InProgress", f"Status should remain InProgress, got {task.status}"
    assert task.description == "Original description", (
        f"Description should remain unchanged, got {task.description}"
    )


def test_template_re_apply_adds_genuinely_new_task(db: None) -> None:
    """Edited template that adds a genuinely new task still adds it on re-apply."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org)
    template = CaseTemplate.objects.create(
        name="add-new-task",
        tasks=[{"title": "Original Task", "group": ""}],
    )
    client = Client()
    _login(client, user)

    # First apply
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.tasks.count() == 1

    # Update template to add a new task
    template.tasks = [
        {"title": "Original Task", "group": ""},
        {"title": "New Task", "group": "G1"},
    ]
    template.save()

    # Re-apply the edited template
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.tasks.count() == 2, "Re-apply should add the genuinely new task"


# --------------------------------------------------------------------------
# New: Case-template severity "None" tests
# --------------------------------------------------------------------------


def test_ui_create_form_defaults_to_none(db: None) -> None:
    """Create form default severity is None; the "None (keep case severity)" option is selected."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-case-template-create"))
    assert response.status_code == 200
    content = response.content.decode()
    severity_select = content.split('id="id_severity"', 1)[1].split("</select>", 1)[0]
    # The "none" option should be selected by default
    assert 'value="none" selected' in severity_select
    # Help text present
    assert "None leaves the case's existing severity unchanged when applied." in content


def test_ui_create_form_submission_defaults_to_none(db: None) -> None:
    """UI create form submission without severity sets severity to None."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-template-create"),
        {
            "name": "none-default",
            "display_name": "None Default",
            "tasks": "",
            "custom_fields": "",
            # severity omitted -> defaults to None
            "tlp": "2",
            "pap": "2",
        },
    )

    assert response.status_code == 302, response.content
    template = CaseTemplate.objects.get(name="none-default")
    assert template.severity is None, f"Expected severity=None, got {template.severity}"


def test_ui_create_form_explicit_severity_persists(db: None) -> None:
    """UI create form with explicit numeric severity persists it."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    client = Client()
    _login(client, user)

    response = client.post(
        reverse("ui-case-template-create"),
        {
            "name": "explicit-sev",
            "display_name": "Explicit Severity",
            "severity": "3",
            "tasks": "",
            "custom_fields": "",
            "tlp": "2",
            "pap": "2",
        },
    )

    assert response.status_code == 302, response.content
    template = CaseTemplate.objects.get(name="explicit-sev")
    assert template.severity == 3, f"Expected severity=3, got {template.severity}"


def test_ui_edit_form_null_severity_selects_none_not_medium(db: None) -> None:
    """Edit form with null severity selects None and NOT Medium (regression for |default:2)."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    template = CaseTemplate.objects.create(
        name="null-sev-template",
        display_name="Null Sev Template",
        severity=None,  # explicitly None
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    client = Client()
    _login(client, user)

    response = client.get(
        reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    )
    assert response.status_code == 200
    content = response.content.decode()
    severity_select = content.split('id="id_severity"', 1)[1].split("</select>", 1)[0]
    # The "none" option should be selected
    assert 'value="none" selected' in severity_select, (
        "None option should be selected for null template"
    )
    # Medium should NOT be selected (regression guard: |default:2 would select Medium for null)
    assert 'value="2" selected' not in severity_select, (
        "Medium should NOT be selected when template severity is None"
    )


def test_ui_edit_form_numeric_severity_selects_correctly(db: None) -> None:
    """Edit form with numeric severity selects that option correctly."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    template = CaseTemplate.objects.create(
        name="num-sev-template",
        display_name="Num Sev Template",
        severity=2,  # Medium
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    client = Client()
    _login(client, user)

    response = client.get(
        reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    )
    assert response.status_code == 200
    content = response.content.decode()
    severity_select = content.split('id="id_severity"', 1)[1].split("</select>", 1)[0]
    # Medium (value="2") should be selected
    assert 'value="2" selected' in severity_select, "Medium should be selected for severity=2"
    # None option should NOT be selected
    assert 'value="none" selected' not in severity_select, (
        "None should not be selected for numeric severity"
    )


def test_list_renders_none_with_dim_class(db: None) -> None:
    """List row for null severity renders <span class="dim">None</span> with no sev- class."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    CaseTemplate.objects.create(
        name="list-none-template",
        display_name="List None Template",
        severity=None,
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-case-template-list"))
    assert response.status_code == 200
    content = response.content.decode()
    # Should render "None" with class "dim" and NO "sev-" class
    assert 'class="dim"' in content, "List should have dim class for None severity"
    assert "None" in content, "List should show None for null severity"
    # Should NOT have sev- class
    assert "sev-sev-" not in content, "List should not have sev- class for None"


def test_detail_renders_none_keep_case_severity(db: None) -> None:
    """Detail page for null severity renders 'None — keep case severity' with .dim, no swatch."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    template = CaseTemplate.objects.create(
        name="detail-none-template",
        display_name="Detail None Template",
        severity=None,
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    client = Client()
    _login(client, user)

    response = client.get(
        reverse("ui-case-template-detail", kwargs={"template_id": str(template.id)})
    )
    assert response.status_code == 200
    content = response.content.decode()
    # Should render "None — keep case severity" with class "dim"
    assert "None — keep case severity" in content, "Detail should show 'None — keep case severity'"
    assert 'class="dim"' in content, "Detail should have dim class for None severity"
    # Should NOT have sev- class
    assert "sev-sev-" not in content, "Detail should not have sev- class for None"


def test_apply_picker_hint_for_none_template(db: None) -> None:
    """Apply picker shows '(keeps case severity)' hint for None templates."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)

    # Create a case for the apply page
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    case = Case.objects.create(title="Test case", status=status, owner_org=org, assignee=user)

    template = CaseTemplate.objects.create(
        name="apply-none-template",
        display_name="Apply None Template",
        severity=None,
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    client = Client()
    _login(client, user)

    response = client.get(reverse("ui-case-apply-template", kwargs={"case_id": case.number}))
    assert response.status_code == 200, response.content
    content = response.content.decode()
    # The None template should have the hint
    assert "keeps case severity" in content, "Apply picker should show hint for None template"
    # The template name should appear
    assert template.name in content, "Template name should appear in apply picker"


@pytest.mark.parametrize("severity", [1, 4])
def test_ui_apply_template_none_preserves_case_severity(db: None, severity: int) -> None:
    """UI apply of a None template preserves the case's existing severity."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)

    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    case = Case.objects.create(
        title="Severity case", status=status, owner_org=org, assignee=user, severity=severity
    )
    case.refresh_from_db()
    original_severity = case.severity
    assert original_severity == severity

    # Create a None template
    template = CaseTemplate.objects.create(
        name="none-apply-template",
        display_name="None Apply Template",
        severity=None,
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )

    client = Client()
    _login(client, user)

    # Apply the None template
    response = client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    assert response.status_code == 302, response.content

    case.refresh_from_db()
    # Severity should remain unchanged
    assert case.severity == original_severity, (
        f"Expected severity {original_severity} preserved, got {case.severity}"
    )

    # The template should still be recorded as the source
    assert case.source_template_id == template.id, "Case should have source_template set"


def test_ui_apply_template_none_reapply_preserves_case_severity(db: None) -> None:
    """Re-applying a None template preserves the case's existing severity."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)

    # Create a case with severity 1 (Low), seeded by a template
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    case = Case.objects.create(title="Re-apply case", status=status, owner_org=org, assignee=user)

    # Create and apply a None template first
    template = CaseTemplate.objects.create(
        name="reapply-none-template",
        display_name="Re-apply None Template",
        severity=None,
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )

    client = Client()
    _login(client, user)

    # First apply
    client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    case.refresh_from_db()
    severity_after_first = case.severity

    # Re-apply the same None template
    client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    case.refresh_from_db()

    # Severity should still be preserved (unchanged from first apply result)
    assert case.severity == severity_after_first, (
        f"Re-apply should preserve severity, got {case.severity} vs {severity_after_first}"
    )


def test_edit_apply_to_existing_none_preserves_case_severity(db: None) -> None:
    """Editing a None template and applying to existing cases keeps each case's severity."""
    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    case = _mk_case(org, "Resync None")
    case.severity = 3
    case.save(update_fields=["severity"])
    template = CaseTemplate.objects.create(
        name="resync-none-template",
        display_name="Resync None Template",
        severity=None,
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    client = Client()
    _login(client, user)

    # Seed `source_template` through a normal apply.
    client.post(
        reverse("ui-case-apply-template", kwargs={"case_id": case.number}),
        {"template_id": str(template.id)},
    )
    case.refresh_from_db()
    assert case.severity == 3
    assert case.source_template_id == template.id

    response = client.post(
        reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)}),
        {
            "name": template.name,
            "severity": "none",
            "tlp": "2",
            "pap": "2",
            "tasks": "[]",
            "custom_fields": "[]",
            "apply_to_existing": "1",
        },
    )
    assert response.status_code == 302, response.content
    case.refresh_from_db()
    assert case.severity == 3, (
        "Edit + apply-to-existing must preserve a None template's case severity"
    )


def test_api_create_template_omitted_severity_is_none(db: None) -> None:
    """API create with omitted severity returns severity: null."""
    from rest_framework.test import APIClient

    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    api_client = APIClient()
    api_client.force_login(user)

    response = api_client.post("/api/v1/case/template", {"name": "api-none-omitted"}, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["severity"] is None, f"Expected severity null, got {body['severity']}"
    assert body["severityLabel"] == "", (
        f"Expected severityLabel empty, got {body['severityLabel']!r}"
    )


def test_api_create_template_null_severity_is_none(db: None) -> None:
    """API create with severity: null returns severity: null."""
    from rest_framework.test import APIClient

    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    api_client = APIClient()
    api_client.force_login(user)

    response = api_client.post(
        "/api/v1/case/template", {"name": "api-none-explicit", "severity": None}, format="json"
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["severity"] is None, f"Expected severity null, got {body['severity']}"
    assert body["severityLabel"] == "", (
        f"Expected severityLabel empty, got {body['severityLabel']!r}"
    )


def test_api_create_template_invalid_severity_400(db: None) -> None:
    """API create with invalid severity returns 400."""
    from rest_framework.test import APIClient

    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    api_client = APIClient()
    api_client.force_login(user)

    response = api_client.post(
        "/api/v1/case/template", {"name": "api-bad-sev", "severity": "5"}, format="json"
    )

    assert response.status_code == 400, response.content


def test_api_patch_template_null_severity_sets_none(db: None) -> None:
    """API patch with severity: null sets severity to None."""
    from rest_framework.test import APIClient

    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    template = CaseTemplate.objects.create(
        name="patch-none-template",
        display_name="Patch None Template",
        severity=2,  # Start with Medium
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    api_client = APIClient()
    api_client.force_login(user)

    response = api_client.patch(
        f"/api/v1/case/template/{template.id}", {"severity": None}, format="json"
    )

    assert response.status_code == 204, response.content
    template.refresh_from_db()
    assert template.severity is None, f"Expected severity=None after patch, got {template.severity}"


def test_api_patch_template_absent_severity_unchanged(db: None) -> None:
    """API patch without severity key leaves severity unchanged."""
    from rest_framework.test import APIClient

    org = _mk_org("Org")
    user = _mk_user("analyst", org)
    template = CaseTemplate.objects.create(
        name="patch-unchanged-template",
        display_name="Patch Unchanged Template",
        severity=2,  # Start with Medium
        tlp=2,
        pap=2,
        tasks=[],
        custom_fields=[],
    )
    api_client = APIClient()
    api_client.force_login(user)

    response = api_client.patch(
        f"/api/v1/case/template/{template.id}", {"displayName": "Updated"}, format="json"
    )

    assert response.status_code == 204, response.content
    template.refresh_from_db()
    assert template.severity == 2, "Severity should be unchanged when key absent"


# --------------------------------------------------------------------------
# End new None severity tests
# --------------------------------------------------------------------------
