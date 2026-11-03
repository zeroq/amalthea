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

from cases.models import Case, CaseCustomFieldValue, CaseStatus, CustomField
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
