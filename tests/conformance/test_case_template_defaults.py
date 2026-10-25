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

from alerts.models import Alert, AlertStatus
from cases import template_defaults as td
from cases.models import (
    Case,
    CaseStatus,
    CaseTemplate,
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
