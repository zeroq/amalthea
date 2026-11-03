"""Conformance tests for the case Custom tab and its edit endpoint.

Covers PLAN-2026-11-02-case-custom-fields-tab.md Phase 2-3:
- tab presence/absence, order after General, count and panel wiring;
- `?tab=custom` falls back to General when the case has no values;
- display formatting (boolean, empty, scalar, JSON) and escaping;
- edit coercion, no-op handling, timeline metadata and invalid input;
- case-scoped value ids, viewer denial, and the plain-POST modal form.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Case, CaseCustomFieldValue, CaseStatus, CustomField, TimelineEvent
from identity.models import Organisation, User


def _org(name: str = "Custom Fields Org") -> Organisation:
    return Organisation.objects.create(name=name)


def _user(login: str, org: Organisation, *, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=login,
        login=login,
        password="hunter2-correct",
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _case(org: Organisation, title: str = "Custom-field case") -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(title=title, status=status, owner_org=org)


def _value(
    case: Case,
    name: str,
    value,
    *,
    field_type: str = "string",
    order: int = 0,
    group: str = "",
) -> CaseCustomFieldValue:
    field = CustomField.objects.create(name=name, type=field_type, group=group)
    return CaseCustomFieldValue.objects.create(
        case=case, custom_field=field, value=value, order=order
    )


@pytest.fixture
def org(db: None) -> Organisation:
    return _org()


@pytest.fixture
def analyst(org: Organisation) -> User:
    return _user("cf-analyst", org)


@pytest.fixture
def viewer(org: Organisation) -> User:
    return _user("cf-viewer", org, role="viewer")


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    client.force_login(analyst)
    return client


@pytest.fixture
def case(org: Organisation) -> Case:
    return _case(org)


def _detail_url(case: Case, *, tab: str | None = None) -> str:
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    return f"{url}?tab={tab}" if tab else url


def _edit_url(case: Case) -> str:
    return reverse("ui-case-custom-field-edit", kwargs={"case_id": case.number})


def test_tab_is_visible_after_general_with_count_and_panel(browser, case) -> None:
    _value(case, "Business unit", "Payments", order=0)
    _value(case, "Owner", "GRC", order=1)

    response = browser.get(_detail_url(case))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="tab-custom"' in html
    assert 'id="panel-custom"' in html
    assert html.index('id="tab-general"') < html.index('id="tab-custom"')
    assert html.index('id="tab-custom"') < html.index('id="tab-alerts"')
    tab_markup = html[
        html.index('id="tab-custom"') : html.index("</a>", html.index('id="tab-custom"'))
    ]
    assert 'class="count">2</span>' in tab_markup
    assert response.context["active_tab"] == "general"


def test_tab_and_panel_are_absent_without_values(browser, case) -> None:
    response = browser.get(_detail_url(case))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="tab-custom"' not in html
    assert 'id="panel-custom"' not in html


def test_custom_query_falls_back_to_general_without_values(browser, case) -> None:
    response = browser.get(_detail_url(case, tab="custom"))

    assert response.status_code == 200
    assert response.context["active_tab"] == "general"
    html = response.content.decode()
    assert 'id="tab-custom"' not in html
    general_panel = html[html.index('id="panel-general"') : html.index('id="panel-audit"')]
    assert "hidden" not in general_panel.split(">", 1)[0]


def test_rows_render_boolean_empty_scalar_and_json_with_escaping(browser, case) -> None:
    _value(case, "Enabled", True, field_type="boolean", order=0)
    _value(case, "Missing", "", order=1)
    _value(case, "Plain", "<script>alert(1)</script>", order=2)
    _value(case, "Structured", {"nested": ["a", "b"]}, order=3)

    response = browser.get(_detail_url(case, tab="custom"))

    assert response.status_code == 200
    html = response.content.decode()
    assert response.context["active_tab"] == "custom"
    assert "Yes" in html
    assert "—" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert '<pre class="body mono">' in html
    assert "&quot;nested&quot;" in html
    assert "Structured" in html


@pytest.mark.parametrize(
    ("field_type", "initial", "posted", "expected"),
    [
        ("string", "old", " new ", "new"),
        ("integer", 7, "42", 42),
        ("float", 1.5, "2.25", 2.25),
        ("boolean", False, "true", True),
        ("date", "2026-01-01", "2026-01-02", "2026-01-02"),
    ],
)
def test_edit_coerces_typed_values(browser, case, field_type, initial, posted, expected) -> None:
    value = _value(case, f"Field {field_type}", initial, field_type=field_type)

    response = browser.post(
        _edit_url(case), {"value_id": str(value.id), "value": posted, "tab": "custom"}
    )

    assert response.status_code == 302
    assert response["Location"].endswith("?tab=custom")
    value.refresh_from_db()
    assert value.value == expected
    event = TimelineEvent.objects.get(case=case, kind="custom-field-updated")
    assert event.title == f"Custom field updated: Field {field_type}"
    assert event.metadata == {"field": f"Field {field_type}"}


def test_edit_parses_json_when_the_existing_value_is_structured(browser, case) -> None:
    value = _value(case, "Metadata", {"a": 1})

    response = browser.post(
        _edit_url(case), {"value_id": str(value.id), "value": '{"a": 2}', "tab": "custom"}
    )

    assert response.status_code == 302
    value.refresh_from_db()
    assert value.value == {"a": 2}


def test_edit_can_clear_to_empty_string(browser, case) -> None:
    value = _value(case, "Owner", "GRC")

    response = browser.post(
        _edit_url(case), {"value_id": str(value.id), "value": "   ", "tab": "custom"}
    )

    assert response.status_code == 302
    value.refresh_from_db()
    assert value.value == ""


def test_invalid_value_writes_nothing_and_shows_the_error(browser, case) -> None:
    value = _value(case, "Count", 7, field_type="integer")

    response = browser.post(
        _edit_url(case),
        {"value_id": str(value.id), "value": "not-an-int", "tab": "custom"},
        follow=True,
    )

    assert response.status_code == 200
    assert "Value must be an integer" in response.content.decode()
    value.refresh_from_db()
    assert value.value == 7
    assert not TimelineEvent.objects.filter(case=case, kind="custom-field-updated").exists()


def test_no_op_edit_does_not_emit_an_event(browser, case) -> None:
    value = _value(case, "Owner", "GRC")

    response = browser.post(
        _edit_url(case), {"value_id": str(value.id), "value": "GRC", "tab": "custom"}
    )

    assert response.status_code == 302
    value.refresh_from_db()
    assert value.value == "GRC"
    assert not TimelineEvent.objects.filter(case=case, kind="custom-field-updated").exists()


@pytest.mark.parametrize("value_id", [None, "", "not-a-uuid"])
def test_missing_or_malformed_value_id_is_404(browser, case, value_id) -> None:
    payload = {"value": "anything"}
    if value_id is not None:
        payload["value_id"] = value_id

    response = browser.post(_edit_url(case), payload)

    assert response.status_code == 404


def test_value_id_from_another_case_is_404(browser, case, org) -> None:
    other_case = _case(org, title="Other case")
    foreign = _value(other_case, "Foreign", "value")

    response = browser.post(_edit_url(case), {"value_id": str(foreign.id), "value": "changed"})

    assert response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.value == "value"


def test_viewer_gets_404_and_value_is_unchanged(browser, viewer, case, org) -> None:
    value = _value(case, "Owner", "GRC")
    viewer_browser = Client()
    viewer_browser.force_login(viewer)

    response = viewer_browser.post(_edit_url(case), {"value_id": str(value.id), "value": "changed"})

    assert response.status_code == 404
    value.refresh_from_db()
    assert value.value == "GRC"


def test_edit_form_is_wired_for_plain_post(browser, case) -> None:
    value = _value(case, "Owner", "GRC")

    response = browser.get(_detail_url(case, tab="custom"))

    assert response.status_code == 200
    html = response.content.decode()
    modal_id = f"case-cf-{value.id}"
    form_id = f"{modal_id}-form"
    assert f'id="{modal_id}"' in html
    assert f'id="{form_id}"' in html
    assert f'name="value_id" value="{value.id}" form="{form_id}"' in html
    assert f'name="tab" value="custom" form="{form_id}"' in html
    assert f'action="{_edit_url(case)}"' in html
    assert f'data-modal-open="{modal_id}"' in html
