"""Conformance tests for case custom fields on the API detail response.

Covers PLAN-2026-11-02-case-custom-fields-tab.md Phase 1:
- detail `GET /api/v1/case/{id}` returns `customFields` with exact keys and values;
- `_id` is the `CaseCustomFieldValue` UUID;
- ordering is deterministic (`order`, then `custom_field.name`);
- no values -> `customFields: []`;
- list responses are unchanged (no `customFields` key).
"""

from __future__ import annotations

import pytest

from cases.custom_fields import update_case_custom_field_value
from cases.models import Case, CaseCustomFieldValue, CaseStatus, CustomField, TimelineEvent
from identity.models import User


def _case(org, title: str = "Custom-field case") -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(title=title, status=status, owner_org=org)


def _field(name: str, field_type: str = "string", *, group: str = "") -> CustomField:
    return CustomField.objects.create(name=name, type=field_type, group=group)


def test_detail_includes_custom_fields_with_exact_wire_shape(api, analyst: User) -> None:
    case = _case(analyst.org)
    field = _field("Business unit", group="Ownership")
    value = CaseCustomFieldValue.objects.create(
        case=case, custom_field=field, value="Payments", order=0
    )

    response = api.get(f"/api/v1/case/{case.id}")

    assert response.status_code == 200
    rows = response.json()["customFields"]
    assert rows == [
        {
            "_id": str(value.id),
            "name": "Business unit",
            "type": "string",
            "value": "Payments",
            "order": 0,
        }
    ]


def test_detail_orders_by_order_then_field_name(api, analyst: User) -> None:
    case = _case(analyst.org)
    later = _field("Zulu")
    first_by_name = _field("Alpha")
    first_by_order = _field("Bravo")
    CaseCustomFieldValue.objects.create(case=case, custom_field=later, value="later", order=2)
    CaseCustomFieldValue.objects.create(
        case=case, custom_field=first_by_name, value="alpha", order=0
    )
    CaseCustomFieldValue.objects.create(
        case=case, custom_field=first_by_order, value="bravo", order=0
    )

    response = api.get(f"/api/v1/case/{case.id}")

    assert response.status_code == 200
    assert [row["name"] for row in response.json()["customFields"]] == [
        "Alpha",
        "Bravo",
        "Zulu",
    ]


def test_detail_without_values_returns_empty_list(api, analyst: User) -> None:
    case = _case(analyst.org)

    response = api.get(f"/api/v1/case/{case.id}")

    assert response.status_code == 200
    assert response.json()["customFields"] == []


@pytest.mark.parametrize(
    "value",
    [
        False,
        0,
        "",
        {},
        [],
        {"nested": [1, 2]},
    ],
)
def test_detail_preserves_falsy_and_structured_values(api, analyst: User, value) -> None:
    case = _case(analyst.org)
    field = _field("Value")
    CaseCustomFieldValue.objects.create(case=case, custom_field=field, value=value, order=0)

    response = api.get(f"/api/v1/case/{case.id}")

    assert response.status_code == 200
    assert response.json()["customFields"][0]["value"] == value


def test_list_response_has_no_custom_fields_key(api, analyst: User) -> None:
    case = _case(analyst.org)
    field = _field("Business unit")
    CaseCustomFieldValue.objects.create(case=case, custom_field=field, value="Payments", order=0)

    response = api.get("/api/v1/case/")

    assert response.status_code == 200
    rows = response.json()
    assert any(row["_id"] == str(case.id) for row in rows)
    assert all("customFields" not in row for row in rows)


def test_patch_updates_an_existing_value_and_emits_one_event(api, analyst: User) -> None:
    case = _case(analyst.org)
    field = _field("Business unit")
    value = CaseCustomFieldValue.objects.create(
        case=case, custom_field=field, value="Payments", order=0
    )

    response = api.patch(
        f"/api/v1/case/{case.id}",
        {"customFields": [{"_id": str(value.id), "value": "Fraud"}]},
        format="json",
    )

    assert response.status_code == 200
    value.refresh_from_db()
    assert value.value == "Fraud"
    events = TimelineEvent.objects.filter(case=case, kind="custom-field-updated")
    assert events.count() == 1
    assert events.get().metadata == {"field": "Business unit"}


def test_patch_ignores_read_only_wire_fields(api, analyst: User) -> None:
    case = _case(analyst.org)
    field = _field("Business unit")
    value = CaseCustomFieldValue.objects.create(
        case=case, custom_field=field, value="Payments", order=0
    )

    response = api.patch(
        f"/api/v1/case/{case.id}",
        {
            "customFields": [
                {
                    "_id": str(value.id),
                    "value": "Fraud",
                    "name": "Spoofed",
                    "type": "integer",
                    "order": 99,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    value.refresh_from_db()
    field.refresh_from_db()
    assert value.value == "Fraud"
    assert value.order == 0
    assert field.name == "Business unit"
    assert field.type == "string"


@pytest.mark.parametrize(
    ("field_type", "submitted", "message"),
    [
        ("integer", "not-a-number", "Value must be an integer"),
        ("float", "NaN", "Value must be a finite number"),
        ("boolean", "perhaps", "Value must be true or false"),
        ("date", "2026-99-99", "Value must be a date in YYYY-MM-DD form"),
    ],
)
def test_patch_rejects_invalid_types_without_partial_writes(
    api, analyst: User, field_type: str, submitted: str, message: str
) -> None:
    case = _case(analyst.org)
    field = _field("Typed", field_type)
    value = CaseCustomFieldValue.objects.create(case=case, custom_field=field, value="old", order=0)

    response = api.patch(
        f"/api/v1/case/{case.id}",
        {
            "title": "must not persist",
            "customFields": [{"_id": str(value.id), "value": submitted}],
        },
        format="json",
    )

    assert response.status_code == 400
    assert message in str(response.json()["fields"]["customFields"])
    case.refresh_from_db()
    value.refresh_from_db()
    assert case.title == "Custom-field case"
    assert value.value == "old"
    assert not TimelineEvent.objects.filter(case=case, kind="custom-field-updated").exists()


def test_patch_rejects_an_oversized_value_without_writing(api, analyst: User) -> None:
    case = _case(analyst.org)
    field = _field("Business unit")
    value = CaseCustomFieldValue.objects.create(
        case=case, custom_field=field, value="Payments", order=0
    )

    response = api.patch(
        f"/api/v1/case/{case.id}",
        {"customFields": [{"_id": str(value.id), "value": "x" * (65 * 1024)}]},
        format="json",
    )

    assert response.status_code == 400
    assert "64 KiB" in str(response.json()["fields"]["customFields"])
    value.refresh_from_db()
    assert value.value == "Payments"
    assert not TimelineEvent.objects.filter(case=case, kind="custom-field-updated").exists()


def test_patch_rejects_a_foreign_value_id(api, analyst: User) -> None:
    case = _case(analyst.org)
    other = _case(analyst.org, title="Other case")
    field = _field("Scoped")
    foreign = CaseCustomFieldValue.objects.create(
        case=other, custom_field=field, value="secret", order=0
    )

    response = api.patch(
        f"/api/v1/case/{case.id}",
        {"customFields": [{"_id": str(foreign.id), "value": "overwritten"}]},
        format="json",
    )

    assert response.status_code == 400
    assert "unknown _id" in str(response.json()["fields"]["customFields"])
    foreign.refresh_from_db()
    assert foreign.value == "secret"
    assert not TimelineEvent.objects.filter(case=case, kind="custom-field-updated").exists()


@pytest.mark.parametrize("payload", [None, {}, {"_id": "not-a-uuid"}, {"value": "x"}])
def test_patch_rejects_malformed_custom_field_items(api, analyst: User, payload) -> None:
    case = _case(analyst.org)

    response = api.patch(f"/api/v1/case/{case.id}", {"customFields": [payload]}, format="json")

    assert response.status_code == 400
    assert "customFields" in response.json()["fields"]


def test_patch_rejects_a_non_list_custom_fields_value(api, analyst: User) -> None:
    case = _case(analyst.org)

    response = api.patch(f"/api/v1/case/{case.id}", {"customFields": {"_id": "x"}}, format="json")

    assert response.status_code == 400
    assert "must be a list" in str(response.json()["fields"]["customFields"])


def test_patch_repeated_value_is_a_no_op(api, analyst: User) -> None:
    case = _case(analyst.org)
    field = _field("Business unit")
    value = CaseCustomFieldValue.objects.create(
        case=case, custom_field=field, value="Payments", order=0
    )

    response = api.patch(
        f"/api/v1/case/{case.id}",
        {"customFields": [{"_id": str(value.id), "value": "Payments"}]},
        format="json",
    )

    assert response.status_code == 200
    value.refresh_from_db()
    assert value.value == "Payments"
    assert not TimelineEvent.objects.filter(case=case, kind="custom-field-updated").exists()


def test_api_and_ui_writes_share_value_and_event_contract(api, analyst: User) -> None:
    api_case = _case(analyst.org, title="API case")
    ui_case = _case(analyst.org, title="UI case")
    field = _field("Business unit")
    api_value = CaseCustomFieldValue.objects.create(
        case=api_case, custom_field=field, value="Payments", order=0
    )
    ui_value = CaseCustomFieldValue.objects.create(
        case=ui_case, custom_field=field, value="Payments", order=0
    )

    response = api.patch(
        f"/api/v1/case/{api_case.id}",
        {"customFields": [{"_id": str(api_value.id), "value": "Fraud"}]},
        format="json",
    )
    update_case_custom_field_value(ui_case, ui_value, "Fraud", actor=analyst)

    assert response.status_code == 200
    api_value.refresh_from_db()
    ui_value.refresh_from_db()
    assert api_value.value == ui_value.value == "Fraud"
    api_event = TimelineEvent.objects.get(case=api_case, kind="custom-field-updated")
    ui_event = TimelineEvent.objects.get(case=ui_case, kind="custom-field-updated")
    assert api_event.title == ui_event.title
    assert api_event.metadata == ui_event.metadata
