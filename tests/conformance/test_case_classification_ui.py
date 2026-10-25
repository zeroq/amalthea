"""UI conformance for PLAN-2026-10-14 (case classification fields), P4.

``test_case_classification.py`` covers the API/applier; this file pins the *rendered contract* the
frontend depends on, because the two halves (backend enums/context, templates) were built against
each other and only the HTML binds them:

* the create form renders four selects for an admin, and for an analyst the three any-writer
  fields stay editable while ``data_classification`` becomes a **read-only span** (no control to
  submit — the Q-D5 gate is enforced server-side regardless, but a rendered control would be a
  broken affordance);
* the edit modal on the case detail page **pre-selects** the persisted value;
* the General tab and the create-form read-only span render the **human label**, never the stored
  token ("Personal Data (PII)", not ``personal_data``) — and on an **error re-render** the label is
  still human ("Unset/Unknown"), which is the F3 regression this file pins;
* all four are visible to a **viewer** on the General tab (Q-D5b), while the viewer is refused the
  write surfaces with the UI's uniform 404, not a 403.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Case, CaseStatus, TimelineEvent
from core.enums import ADMIN_ROLE, VIEWER_ROLE
from identity.models import AuditLog, Organisation, User

# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="UI Classify Org")


@pytest.fixture
def admin_user(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="ui-admin",
        login="ui-admin",
        password="pw-ui-admin",
        email="ui-admin@example.net",
        org=org,
        role=ADMIN_ROLE,
        is_active=True,
    )


@pytest.fixture
def analyst_user(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="ui-analyst",
        login="ui-analyst",
        password="pw-ui-analyst",
        email="ui-analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


@pytest.fixture
def viewer_user(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="ui-viewer",
        login="ui-viewer",
        password="pw-ui-viewer",
        email="ui-viewer@example.net",
        org=org,
        role=VIEWER_ROLE,
        is_active=True,
    )


@pytest.fixture
def case_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _case(org: Organisation, status_row: CaseStatus, **kw) -> Case:
    kw.setdefault("title", "UI classify case")
    return Case.objects.create(owner_org=org, status=status_row, **kw)


CLASSIFIED = {
    "scope": "single_department",
    "case_type": "phishing",
    "data_classification": "personal_data",
    "phase": "analysis",
}


# ---------------------------------------------------------------------------------------
# Create form rendering
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_page_admin_renders_four_selects(admin_user: User) -> None:
    resp = _login(admin_user).get(reverse("ui-case-create"))
    assert resp.status_code == 200
    body = resp.content.decode()
    for name in ("scope", "case_type", "phase", "data_classification"):
        assert f'name="{name}"' in body, name
    assert "readonly-value" not in body


@pytest.mark.django_db
def test_create_page_analyst_readonly_classification_but_editable_others(
    analyst_user: User,
) -> None:
    resp = _login(analyst_user).get(reverse("ui-case-create"))
    assert resp.status_code == 200
    body = resp.content.decode()
    # The three any-writer fields are editable...
    for name in ("scope", "case_type", "phase"):
        assert f'name="{name}"' in body, name
    # ...but data_classification is not submittable.
    assert 'name="data_classification"' not in body
    assert 'class="readonly-value">Unset/Unknown<' in body
    # The read-only span must not leak the stored token as its text.
    assert ">unset<" not in body


@pytest.mark.django_db
def test_case_list_modal_admin_renders_four_selects(admin_user: User) -> None:
    resp = _login(admin_user).get(reverse("ui-case-list"), {"new": "1"})
    assert resp.status_code == 200
    body = resp.content.decode()
    for name in ("scope", "case_type", "phase", "data_classification"):
        assert f'name="{name}"' in body, name


# ---------------------------------------------------------------------------------------
# Create POST
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_post_admin_persists_all_four(admin_user: User, org: Organisation) -> None:
    client = _login(admin_user)
    resp = client.post(reverse("ui-case-create"), {"title": "Created with class", **CLASSIFIED})
    assert resp.status_code == 302
    case = Case.objects.get(title="Created with class")
    assert case.scope == "single_department"
    assert case.case_type == "phishing"
    assert case.data_classification == "personal_data"
    assert case.phase == "analysis"


@pytest.mark.django_db
def test_create_post_analyst_cannot_smuggle_classification(
    analyst_user: User, org: Organisation
) -> None:
    client = _login(analyst_user)
    resp = client.post(
        reverse("ui-case-create"),
        {"title": "Analyst created", **CLASSIFIED},  # attempts the admin-only field anyway
    )
    assert resp.status_code == 302
    case = Case.objects.get(title="Analyst created")
    # The three any-writer fields land...
    assert case.scope == "single_department"
    assert case.case_type == "phishing"
    assert case.phase == "analysis"
    # ...the admin-only one does not (the view omits it for a non-admin; the applier would 403).
    assert case.data_classification == "unset"


@pytest.mark.django_db
def test_create_error_rerender_analyst_shows_unset_not_smuggled(
    analyst_user: User,
) -> None:
    """G2b: on the error re-render, a non-admin sees "Unset/Unknown" for data_classification,
    not the smuggled value's label. The smuggled value is not persisted and should not be echoed.

    ``title`` is required, so this POST re-renders the form with ``form_values`` populated from the
    submission. The readonly span must show the default label, not the smuggled token or its label.
    """
    client = _login(analyst_user)
    resp = client.post(
        reverse("ui-case-create"),
        {"title": "   ", **CLASSIFIED},  # empty title → CaseUpdateError → re-render
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    # Non-admin should see the default label, not the smuggled value's label
    assert 'class="readonly-value">Unset/Unknown<' in body
    assert ">personal_data<" not in body
    assert ">Personal Data (PII)<" not in body


@pytest.mark.django_db
def test_create_error_rerender_analyst_defaults_to_unset_label(analyst_user: User) -> None:
    """F3 (the other half): with no classification submitted, the span says "Unset/Unknown".

    The pre-fix template rendered the raw token (or nothing) on the error re-render.
    """
    client = _login(analyst_user)
    resp = client.post(reverse("ui-case-create"), {"title": "   "})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'class="readonly-value">Unset/Unknown<' in body
    assert ">unset<" not in body


# ---------------------------------------------------------------------------------------
# Case detail: General tab + edit modal pre-fill
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_general_tab_shows_human_labels(analyst_user: User, org: Organisation, case_status) -> None:
    case = _case(org, case_status, **CLASSIFIED)
    resp = _login(analyst_user).get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert resp.status_code == 200
    body = resp.content.decode()
    # Each row uses <span class="meta-label">Label</span><span class="meta-value">Value</span>
    for label, value in (
        ("Scope", "Single Department"),
        ("Type", "Phishing"),
        ("Classification", "Personal Data (PII)"),
        ("Phase", "Analysis"),
    ):
        assert re.search(
            rf'<span class="meta-label">{label}</span>\s*<span class="meta-value[^"]*">\s*{re.escape(value)}',
            body,
        ), label


@pytest.mark.django_db
def test_edit_modal_admin_preselects_all_four(
    admin_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status, **CLASSIFIED)
    resp = _login(admin_user).get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert resp.status_code == 200
    body = resp.content.decode()
    for value in ("single_department", "phishing", "personal_data", "analysis"):
        assert f'value="{value}" selected' in body, value


@pytest.mark.django_db
def test_edit_modal_analyst_readonly_label_no_select(
    analyst_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status, **CLASSIFIED)
    resp = _login(analyst_user).get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'name="data_classification"' not in body
    assert 'class="readonly-value">Personal Data (PII)<' in body
    # The three any-writer fields are still editable controls.
    for name in ("scope", "case_type", "phase"):
        assert f'name="{name}"' in body, name


# ---------------------------------------------------------------------------------------
# Edit POST
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_edit_post_admin_applies_all_four_with_timeline_and_audit(
    admin_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status)
    client = _login(admin_user)
    resp = client.post(
        reverse("ui-case-edit", kwargs={"case_id": case.number}),
        {"title": case.title, **CLASSIFIED},
    )
    assert resp.status_code == 302
    case.refresh_from_db()
    assert case.scope == "single_department"
    assert case.case_type == "phishing"
    assert case.data_classification == "personal_data"
    assert case.phase == "analysis"

    events = TimelineEvent.objects.filter(case=case, kind="classification-changed")
    assert {e.metadata["field"] for e in events} == set(CLASSIFIED)
    assert (
        AuditLog.objects.filter(target_id=str(case.id), action="case.data_classification").count()
        == 1
    )


@pytest.mark.django_db
def test_edit_post_analyst_sets_three_but_not_classification(
    analyst_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status, data_classification="no_data")
    client = _login(analyst_user)
    resp = client.post(
        reverse("ui-case-edit", kwargs={"case_id": case.number}),
        {"title": case.title, **CLASSIFIED},
    )
    assert resp.status_code == 302
    case.refresh_from_db()
    assert case.scope == "single_department"
    assert case.case_type == "phishing"
    assert case.phase == "analysis"
    # The admin-only field is dropped by the view (not 403), so the persisted value stands.
    assert case.data_classification == "no_data"


@pytest.mark.django_db
def test_edit_post_bad_scope_re_renders_inline_error(
    admin_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status)
    client = _login(admin_user)
    resp = client.post(
        reverse("ui-case-edit", kwargs={"case_id": case.number}),
        {"title": case.title, "scope": "not-a-scope"},
    )
    assert resp.status_code == 200  # re-rendered, not redirected
    body = resp.content.decode()
    assert "scope must be one of" in body
    case.refresh_from_db()
    assert case.scope == "unset"


# ---------------------------------------------------------------------------------------
# Viewer matrix (UI): sees the field, refused every write surface
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_viewer_sees_classification_on_general_tab(
    viewer_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status, **CLASSIFIED)
    resp = _login(viewer_user).get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert re.search(
        r'<span class="meta-label">Classification</span>\s*<span class="meta-value[^"]*">\s*Personal Data \(PII\)',
        body,
    )


@pytest.mark.django_db
def test_viewer_gets_uniform_404_on_create_and_edit(
    viewer_user: User, org: Organisation, case_status
) -> None:
    case = _case(org, case_status)
    client = _login(viewer_user)
    assert client.get(reverse("ui-case-create")).status_code == 404
    assert client.post(reverse("ui-case-create"), {"title": "x"}).status_code == 404
    edit_url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    assert client.get(edit_url).status_code == 404
    assert client.post(edit_url, {"title": "hijack"}).status_code == 404
    case.refresh_from_db()
    assert case.title == "UI classify case"
