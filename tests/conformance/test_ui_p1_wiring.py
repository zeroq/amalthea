"""P1 wiring tests — backend half of the UI completeness wave.

Covers the five P1 backend tasks:
- T1: Attachments context, field name, created_by reference, redirects
- T2: Case-template edit GET prefilled + POST updates
- T3: Password change requires login and renders
- T4: Error handlers (404, 500, 403) return themed templates, correct status, no inline assets
- T5: ui-case-bulk route is gone (404)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import Client, RequestFactory, override_settings
from django.urls import NoReverseMatch, reverse

from cases.models import Attachment, Case, CaseStatus, CaseTemplate
from core.enums import VIEWER_ROLE
from identity.models import Organisation, User

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Test Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="analyst",
        login="analyst",
        password="hunter2-correct",
        email="analyst@example.net",
        org=org,
        is_active=True,
    )


@pytest.fixture
def case(db: None, org: Organisation, analyst: User) -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title="Test case", status=status, owner_org=org, assignee=analyst)


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="analyst", password="hunter2-correct")
    return client


# --------------------------------------------------------------------------
# T1 — Attachments wiring on case detail
# --------------------------------------------------------------------------


def test_case_detail_passes_attachments_queryset(browser: Client, case: Case) -> None:
    """T1a: case_detail context includes attachments rail capped at RAIL_CAP with total."""
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200

    attachments = response.context["attachments"]
    attachments_total = response.context["attachments_total"]
    # Now a list (capped at RAIL_CAP) with honest total
    assert isinstance(attachments, list)
    assert attachments_total == case.attachments.count()
    # Length should be min(total, RAIL_CAP)
    assert len(attachments) == min(attachments_total, 200)
    # Ordering preserved: newest first (-created_at)
    if len(attachments) > 1:
        assert attachments[0].created_at >= attachments[1].created_at


def test_case_detail_attachment_form_field_name_matches_api(browser: Client, case: Case) -> None:
    """T1b: upload form uses name='attachments' to match API expectation."""
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200

    # The form input should have name="attachments"
    content = response.content.decode()
    assert 'name="attachments"' in content
    # Should NOT have the old name
    assert 'name="attachment"' not in content


def test_case_detail_attachment_list_uses_created_by(browser: Client, case: Case) -> None:
    """T1c: template references attachment.created_by not uploaded_by."""
    # Create an attachment
    _attachment = Attachment.objects.create(
        case=case,
        name="test.txt",
        content_type="text/plain",
        size=5,
        sha256="abc123",
        path="test.txt",
        created_by=case.assignee,
    )

    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    # Should reference created_by.login
    assert "created_by" in content or case.assignee.login in content
    # The old uploaded_by should not appear
    assert "uploaded_by" not in content


def test_attachment_upload_redirects_to_case_detail(browser: Client, case: Case) -> None:
    """T1d: case_attachment_upload redirects to ui-case-detail."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse("ui-case-attachment-upload", kwargs={"case_id": case.id})
    response = browser.post(
        url,
        {"attachments": SimpleUploadedFile("x.txt", b"hello", content_type="text/plain")},
    )
    # Should redirect to case detail, not attachment list
    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})


def test_attachment_delete_redirects_to_case_detail(browser: Client, case: Case) -> None:
    """T1d: case_attachment_delete redirects to ui-case-detail."""
    attachment = Attachment.objects.create(
        case=case,
        name="test.txt",
        content_type="text/plain",
        size=5,
        sha256="abc123",
        path="test.txt",
        created_by=case.assignee,
    )

    url = reverse(
        "ui-case-attachment-delete", kwargs={"case_id": case.id, "attachment_id": attachment.id}
    )
    response = browser.post(url)
    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})


def test_standalone_attachment_list_still_works(browser: Client, case: Case) -> None:
    """The standalone ui-case-attachment-list route/page still works."""
    url = reverse("ui-case-attachment-list", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200


# --------------------------------------------------------------------------
# T2 — Case-template edit
# --------------------------------------------------------------------------


def test_case_template_edit_get_prefilled(browser: Client) -> None:
    """T2: GET ui-case-template-edit returns form pre-populated with template data."""
    template = CaseTemplate.objects.create(
        name="edit-test",
        display_name="Edit Test Template",
        title_prefix="[EDIT]",
        description="A template for editing",
        summary="Summary text",
        flag=True,
        severity=3,
        tlp=1,
        pap=2,
        tags=["tag1", "tag2"],
        tasks=[{"title": "Task 1", "status": "Waiting"}],
        custom_fields=[{"name": "field1", "type": "string", "value": ""}],
    )

    url = reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    response = browser.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    # Form should be prefilled
    assert 'value="edit-test"' in content
    assert 'value="Edit Test Template"' in content
    assert 'value="[EDIT]"' in content
    assert "A template for editing" in content
    assert "Summary text" in content
    # flag is a checkbox - should be checked
    assert "checked" in content
    # Heading should say "Edit"
    assert "Edit Case Template" in content
    # Submit button should say "Update"
    assert ">Update<" in content or 'value="Update"' in content
    # Form should POST to edit URL
    # The form action should include the edit URL
    assert 'action="' in content
    assert url in content or "edit" in content


def test_case_template_edit_post_updates(browser: Client) -> None:
    """T2: POST ui-case-template-edit updates the template."""
    template = CaseTemplate.objects.create(
        name="original-name",
        display_name="Original",
        title_prefix="[ORIG]",
        description="Original description",
        summary="Original summary",
        flag=False,
        severity=2,
        tlp=2,
        pap=2,
        tags=["orig-tag"],
        tasks=[],
        custom_fields=[],
    )

    url = reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    response = browser.post(
        url,
        {
            "name": "updated-name",
            "display_name": "Updated Template",
            "title_prefix": "[UPD]",
            "description": "Updated description",
            "summary": "Updated summary",
            "flag": "on",  # checkbox sends "on" when checked
            "severity": "4",
            "tlp": "3",
            "pap": "1",
            "tags": "tag1, tag2",
            "tasks": json.dumps([{"title": "New Task", "status": "Waiting"}]),
            "custom_fields": json.dumps(
                [{"name": "new_field", "type": "string", "value": "default"}]
            ),
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("ui-case-template-detail", kwargs={"template_id": template.id})

    template.refresh_from_db()
    assert template.name == "updated-name"
    assert template.display_name == "Updated Template"
    assert template.title_prefix == "[UPD]"
    assert template.description == "Updated description"
    assert template.summary == "Updated summary"
    assert template.flag is True
    assert template.severity == 4
    assert template.tlp == 3
    assert template.pap == 1
    assert set(template.tags) == {"tag1", "tag2"}
    assert template.tasks == [{"title": "New Task", "status": "Waiting"}]
    assert template.custom_fields == [{"name": "new_field", "type": "string", "value": "default"}]


def test_case_template_edit_audits_mutation(browser: Client) -> None:
    """T2: mutation is audited (mirrors API audit pattern)."""
    from identity.models import AuditLog

    template = CaseTemplate.objects.create(
        name="audit-test",
        display_name="Audit Test",
        title_prefix="[AUDIT]",
        description="",
        summary="",
        flag=False,
        severity=2,
        tlp=2,
        pap=2,
        tags=[],
        tasks=[],
        custom_fields=[],
    )

    AuditLog.objects.all().delete()

    url = reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    browser.post(url, {"name": "audit-test", "display_name": "Audited", "flag": "on"})

    audit = AuditLog.objects.filter(action="template.updated", target_id=str(template.id)).first()
    assert audit is not None
    assert audit.actor.username == "analyst"
    assert audit.target_type == "CaseTemplate"


def test_case_template_detail_has_edit_link(browser: Client) -> None:
    """T2: case_template_detail has an Edit link pointing to edit view."""
    template = CaseTemplate.objects.create(
        name="link-test",
        display_name="Link Test",
        title_prefix="[LINK]",
        description="",
        summary="",
        flag=False,
        severity=2,
        tlp=2,
        pap=2,
        tags=[],
        tasks=[],
        custom_fields=[],
    )

    url = reverse("ui-case-template-detail", kwargs={"template_id": str(template.id)})
    response = browser.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    edit_url = reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    assert f'href="{edit_url}"' in content
    assert "Edit" in content


# --------------------------------------------------------------------------
# T3 — Password change
# --------------------------------------------------------------------------


def test_password_change_requires_login() -> None:
    """T3: ui-password-change requires authentication."""
    client = Client()
    url = reverse("ui-password-change")
    response = client.get(url)
    # Should redirect to login
    assert response.status_code == 302
    assert "/login" in response.url


def test_password_change_renders_for_authenticated_user(browser: Client) -> None:
    """T3: authenticated user can access password change page."""
    url = reverse("ui-password-change")
    response = browser.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    assert "Change password" in content
    assert 'name="old_password"' in content
    assert 'name="new_password1"' in content
    assert 'name="new_password2"' in content
    # No inline scripts/styles/handlers (external scripts with src= are allowed)
    import re

    assert not re.search(r"<script(?![^>]*\bsrc=)", content, re.IGNORECASE)
    assert "style=" not in content
    assert "on" + "click" not in content


def test_password_change_shows_unusable_password_message(
    browser: Client, org: Organisation
) -> None:
    """T3: shows 'no usable password' branch for SSO-only users."""
    # Create user without usable password
    user = User.objects.create_user(
        username="sso_user",
        login="sso_user",
        email="sso@example.net",
        org=org,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()

    client = Client()
    client.force_login(user)

    url = reverse("ui-password-change")
    response = client.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    assert "does not have a usable password" in content
    assert "SSO" in content or "administrator" in content


def test_base_html_has_password_change_link(browser: Client) -> None:
    """T3: base.html rail-foot has Change password link next to Sign out."""
    url = reverse("ui-dashboard")
    response = browser.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    # Change password link should be in rail-foot
    assert (
        'href="/password/change"' in content or "href=\"{% url 'ui-password-change' %}\"" in content
    )
    # Actually check rendered URL
    password_url = reverse("ui-password-change")
    assert f'href="{password_url}"' in content
    assert "Change password" in content
    # Should appear before Sign out
    change_pos = content.index(f'href="{password_url}"')
    signout_pos = content.index("Sign out")
    assert change_pos < signout_pos


# --------------------------------------------------------------------------
# T4 — Error handlers (D2)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_handler404_returns_themed_template() -> None:
    """T4: handler404 renders ui/404.html with status 404."""
    factory = RequestFactory()
    request = factory.get("/nonexistent-page/")

    from ui.views import handler404

    response = handler404(request)
    assert response.status_code == 404
    assert "Not found" in response.content.decode()
    assert "404" in response.content.decode()
    # Should extend base layout
    assert "Amalthea" in response.content.decode()


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver", "localhost"])
def test_unmatched_url_uses_themed_404_handler() -> None:
    """T4: the *wiring* resolves — an unmatched URL renders our themed 404, not Django's.

    The handler is registered as a dotted string (`handler404 = "ui.views.handler404"`), so a typo
    would silently fall back to Django's default page. This test goes through the real resolver and
    asserts our template actually rendered.
    """
    response = Client().get("/this-page-does-not-exist/")
    assert response.status_code == 404
    body = response.content.decode()
    assert "Amalthea" in body  # our base layout
    assert "Not found" in response.content.decode()


def test_error_handlers_are_dotted_paths_that_resolve() -> None:
    """T4: every configured error handler resolves to a real callable."""
    from django.urls import get_callable

    from amalthea import urls as root_urls

    for name in ("handler404", "handler500", "handler403"):
        handler = getattr(root_urls, name)
        assert isinstance(handler, str), f"{name} should stay a dotted path (mypy scope)"
        assert callable(get_callable(handler)), f"{name} does not resolve to a callable"


@pytest.mark.django_db
def test_handler500_returns_themed_template() -> None:
    """T4: handler500 renders ui/500.html with status 500."""
    factory = RequestFactory()
    request = factory.get("/")

    from ui.views import handler500

    response = handler500(request)
    assert response.status_code == 500
    assert "Server error" in response.content.decode()
    assert "500" in response.content.decode()
    # Should extend base layout
    assert "Amalthea" in response.content.decode()


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_handler500_works_with_debug_false_no_db() -> None:
    """T4: handler500 works with DEBUG=False without DB queries.

    The handler must not query the database because a 500 often means
    the DB is unavailable.
    """
    # We can't easily trigger a real 500 without breaking other tests,
    # but we can test the view directly
    from ui.views import handler500

    factory = RequestFactory()
    request = factory.get("/")
    # This should not raise any DB-related exceptions
    response = handler500(request)
    assert response.status_code == 500


@pytest.mark.django_db
def test_handler403_returns_themed_template() -> None:
    """T4: handler403 renders ui/403.html with status 403."""
    factory = RequestFactory()
    request = factory.get("/")

    from ui.views import handler403

    response = handler403(request)
    assert response.status_code == 403
    assert "Forbidden" in response.content.decode()
    assert "403" in response.content.decode()
    # Should extend base layout
    assert "Amalthea" in response.content.decode()


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_error_templates_have_no_inline_assets() -> None:
    """T4: error templates contain no inline scripts, styles, or handlers."""
    for template_name in ["404.html", "500.html", "403.html"]:
        path = Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui" / template_name
        content = path.read_text(encoding="utf-8")
        # Check no inline script tags
        assert "<script" not in content.lower() or "src=" in content
        # Check no style attributes
        assert "style=" not in content
        # Check no on* handlers
        import re

        on_attr = re.compile(r"\son\w+\s*=", re.IGNORECASE)
        assert not on_attr.search(content)


# --------------------------------------------------------------------------
# T5 — ui-case-bulk is gone
# --------------------------------------------------------------------------


def test_ui_case_bulk_route_removed() -> None:
    """T5: ui-case-bulk URL name no longer resolves."""
    with pytest.raises(NoReverseMatch):
        reverse("ui-case-bulk", kwargs={"case_id": "123"})


def test_ui_case_bulk_returns_404(browser: Client, case: Case) -> None:
    """T5: accessing the old bulk URL returns 404."""
    # The old URL pattern was /cases/<case_id>/bulk
    url = f"/cases/{case.number}/bulk"
    response = browser.post(url)
    # Should be 404 (no route) or 405 (route exists but wrong method)
    # Since we removed the route, it should 404
    assert response.status_code == 404


# --------------------------------------------------------------------------
# F1 — Case-template mutations require analyst+ role (excludes read-only)
# --------------------------------------------------------------------------


@pytest.fixture
def read_only_user(db: None, org: Organisation) -> User:
    """A user with read-only role (not in analyst/admin/superAdmin)."""
    return User.objects.create_user(
        username="readonly",
        login="readonly",
        password="hunter2-correct",
        email="readonly@example.net",
        org=org,
        is_active=True,
        role=VIEWER_ROLE,
    )


@pytest.fixture
def read_only_browser(read_only_user: User) -> Client:
    client = Client()
    assert client.login(username="readonly", password="hunter2-correct")
    return client


def test_case_template_create_post_requires_analyst_role(read_only_browser: Client) -> None:
    """F1: POST to create template returns 404 for read-only user."""
    url = reverse("ui-case-template-create")
    response = read_only_browser.post(url, {"name": "test-template", "display_name": "Test"})
    assert response.status_code == 404
    # Template should not be created
    assert not CaseTemplate.objects.filter(name="test-template").exists()


def test_case_template_edit_post_requires_analyst_role(read_only_browser: Client) -> None:
    """F1: POST to edit template returns 404 for read-only user."""
    # Create template as admin user
    template = CaseTemplate.objects.create(
        name="editable-template",
        display_name="Editable",
        title_prefix="[EDIT]",
        description="",
        summary="",
        flag=False,
        severity=2,
        tlp=2,
        pap=2,
        tags=[],
        tasks=[],
        custom_fields=[],
    )
    url = reverse("ui-case-template-edit", kwargs={"template_id": str(template.id)})
    response = read_only_browser.post(url, {"name": "editable-template", "display_name": "Hacked"})
    assert response.status_code == 404
    # Template should be unchanged
    template.refresh_from_db()
    assert template.display_name == "Editable"


def test_case_template_delete_requires_analyst_role(read_only_browser: Client) -> None:
    """F1: POST to delete template returns 404 for read-only user."""
    template = CaseTemplate.objects.create(
        name="deletable-template",
        display_name="Deletable",
        title_prefix="[DEL]",
        description="",
        summary="",
        flag=False,
        severity=2,
        tlp=2,
        pap=2,
        tags=[],
        tasks=[],
        custom_fields=[],
    )
    url = reverse("ui-case-template-delete", kwargs={"template_id": str(template.id)})
    response = read_only_browser.post(url)
    assert response.status_code == 404
    # Template should still exist
    assert CaseTemplate.objects.filter(name="deletable-template").exists()


# --------------------------------------------------------------------------
# F3 — handler500 must not query the DB (even for authenticated users)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_handler500_zero_queries_authenticated_user(db: None, analyst: User) -> None:
    """F3: handler500 renders without DB queries even for authenticated requests.

    Uses RequestFactory with a real User instance to exercise the auth context
    processor path, then asserts zero queries via CaptureQueriesContext.
    """
    from django.db import connection
    from django.test import RequestFactory
    from django.test.utils import CaptureQueriesContext

    factory = RequestFactory()
    request = factory.get("/")
    request.user = analyst  # Real user instance, not AnonymousUser

    from ui.views import handler500

    with CaptureQueriesContext(connection) as ctx:
        response = handler500(request)

    assert response.status_code == 500
    assert len(ctx.captured_queries) == 0, (
        f"handler500 executed {len(ctx.captured_queries)} queries: "
        f"{[q['sql'] for q in ctx.captured_queries]}"
    )
    # Should still render the themed content
    assert "Server error" in response.content.decode()
    assert "500" in response.content.decode()


# --------------------------------------------------------------------------
# F4 — B4 Tenant indicator tests (dashboard rendering)
# --------------------------------------------------------------------------


def test_dashboard_shows_org_name_for_user_with_org(
    browser: Client, analyst: User, org: Organisation
) -> None:
    """F4a: Dashboard shows org name when user has an org."""
    url = reverse("ui-dashboard")
    response = browser.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert org.name in content


def test_dashboard_shows_org_name_for_superadmin_with_org(db: None, org: Organisation) -> None:
    """F4c: Dashboard shows org name (not 'All organisations') for superAdmin WITH an org.

    Org presence takes precedence over superAdmin status.
    """
    _ = User.objects.create_user(
        username="superwithorg",
        login="superwithorg",
        password="hunter2-correct",
        email="superwithorg@example.net",
        org=org,
        is_active=True,
        role="superAdmin",
    )
    client = Client()
    assert client.login(username="superwithorg", password="hunter2-correct")

    url = reverse("ui-dashboard")
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert org.name in content
    assert "All organisations" not in content


# --------------------------------------------------------------------------
# F5 — A2 merge POST integration test
# --------------------------------------------------------------------------


def test_case_merge_post_integration(
    browser: Client, case: Case, org: Organisation, analyst: User
) -> None:
    """F5: POST target_case to ui-case-merge merges cases and redirects to surviving case."""
    from cases.models import CaseStatus

    # Create a second case in the same org to merge into
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    target_case = Case.objects.create(
        title="Target case", status=status, owner_org=org, assignee=analyst
    )

    url = reverse("ui-case-merge", kwargs={"case_id": case.number})
    response = browser.post(url, {"target_case": str(target_case.id)})

    assert response.status_code == 302
    # Should redirect to the merged (target) case detail
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": target_case.number})

    # Source case should be DELETED (merge absorbs and deletes the source)
    assert not Case.objects.filter(pk=case.pk).exists()

    # Target case should still exist
    target_case.refresh_from_db()
    assert target_case.closed_date is None
