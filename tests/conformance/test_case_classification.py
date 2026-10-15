"""Conformance tests for PLAN-2026-10-14 - Case classification fields.

Covers AC2.1-AC2.7 from the plan:
- AC2.1: create round-trip via case_json
- AC2.2: out-of-vocabulary → 400 on create and PATCH
- AC2.3: empty/null → default
- AC2.4: admin gate (single PATCH, bulk path, actor=None) + TimelineEvent/AuditLog assertions
- AC2.5: query-DSL filter/sort on the four fields + unknown field → 400
- AC2.6: template apply copies scope/case_type/phase, not data_classification
- AC2.7: case_template_json exposes the three and not dataClassification
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from cases.models import Case, CaseStatus
from core.enums import (
    ADMIN_ROLE,
    CASE_PHASE_CHOICES,
    CASE_SCOPE_CHOICES,
    CASE_TYPE_CHOICES,
    DATA_CLASSIFICATION_CHOICES,
    VIEWER_ROLE,
)
from identity.models import AuditLog, Organisation, User

# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture
def default_org() -> Organisation:
    return Organisation.objects.get_or_create(name="default")[0]


@pytest.fixture
def admin_user(default_org: Organisation) -> User:
    return User.objects.get_or_create(
        login="admin",
        defaults={
            "org": default_org,
            "role": ADMIN_ROLE,
            "is_active": True,
        },
    )[0]


@pytest.fixture
def analyst_user(default_org: Organisation) -> User:
    return User.objects.get_or_create(
        login="analyst",
        defaults={
            "org": default_org,
            "role": "analyst",
            "is_active": True,
        },
    )[0]


@pytest.fixture
def viewer_user(default_org: Organisation) -> User:
    return User.objects.get_or_create(
        login="viewer",
        defaults={
            "org": default_org,
            "role": VIEWER_ROLE,
            "is_active": True,
        },
    )[0]


@pytest.fixture
def case_status() -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _auth(api_client: APIClient, user: User) -> None:
    api_client.force_authenticate(user=user)


def _case_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "title": "Test Case",
        "description": "Description",
        "severity": 2,
        "tlp": 2,
        "pap": 2,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------------------
# AC2.1 — Create round-trip via case_json
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseClassificationCreateRoundTrip:
    """AC2.1: POST /api/v1/case with each field → 201 and value echoed by case_json."""

    def test_create_with_all_classification_fields(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        payload = _case_payload(
            scope="single_asset_user",
            caseType="malware",
            dataClassification="personal_data",
            phase="initial_triage",
        )
        resp = api_client.post("/api/v1/case/", payload, format="json")
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        data = resp.data
        assert data["scope"] == "single_asset_user"
        assert data["caseType"] == "malware"
        assert data["dataClassification"] == "personal_data"
        assert data["phase"] == "initial_triage"

    def test_create_with_individual_fields(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        # Test each field individually
        test_cases = [
            ("scope", "multiple_assets_users"),
            ("caseType", "phishing"),
            ("dataClassification", "sensitive_data"),
            ("phase", "containment"),
        ]
        for wire_name, value in test_cases:
            payload = _case_payload(**{wire_name: value})
            resp = api_client.post("/api/v1/case/", payload, format="json")
            assert resp.status_code == status.HTTP_201_CREATED, f"{wire_name}: {resp.data}"
            # Map wire name to response key
            resp_key = {
                "scope": "scope",
                "caseType": "caseType",
                "dataClassification": "dataClassification",
                "phase": "phase",
            }[wire_name]
            assert resp.data[resp_key] == value, f"{wire_name} mismatch"


# ---------------------------------------------------------------------------------------
# AC2.2 — Out-of-vocabulary → 400 on create and PATCH
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseClassificationOutOfVocabulary:
    """AC2.2: Out-of-vocabulary value → 400 (both create and PATCH), no IntegrityError, no 500."""

    @pytest.mark.parametrize(
        "wire_name,model_field,value",
        [
            ("scope", "scope", "not_a_scope"),
            ("caseType", "case_type", "not_a_type"),
            ("dataClassification", "data_classification", "not_a_classification"),
            ("phase", "phase", "not_a_phase"),
        ],
    )
    def test_create_rejects_oov(
        self,
        api_client: APIClient,
        admin_user: User,
        case_status: CaseStatus,
        wire_name: str,
        model_field: str,
        value: str,
    ) -> None:
        _auth(api_client, admin_user)
        payload = _case_payload(**{wire_name: value})
        resp = api_client.post("/api/v1/case/", payload, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, f"{wire_name}: {resp.data}"
        assert resp.data["type"] == "BadRequest"
        # Error response uses model field names (snake_case)
        assert model_field in resp.data["fields"]

    @pytest.mark.parametrize(
        "wire_name,model_field,value",
        [
            ("scope", "scope", "not_a_scope"),
            ("caseType", "case_type", "not_a_type"),
            ("dataClassification", "data_classification", "not_a_classification"),
            ("phase", "phase", "not_a_phase"),
        ],
    )
    def test_patch_rejects_oov(
        self,
        api_client: APIClient,
        admin_user: User,
        case_status: CaseStatus,
        wire_name: str,
        model_field: str,
        value: str,
    ) -> None:
        _auth(api_client, admin_user)
        # Create a case first
        create_resp = api_client.post("/api/v1/case/", _case_payload(), format="json")
        case_id = create_resp.data["id"]
        # Patch with OOV value
        payload = {wire_name: value}
        resp = api_client.patch(f"/api/v1/case/{case_id}/", payload, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, f"{wire_name}: {resp.data}"
        assert resp.data["type"] == "BadRequest"
        # Error response uses model field names (snake_case)
        assert model_field in resp.data["fields"]


# ---------------------------------------------------------------------------------------
# AC2.3 — Empty/null → default
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseClassificationEmptyNullDefaults:
    """AC2.3: null/"" → model default, not a rejection (tolerance preserved)."""

    @pytest.mark.parametrize("wire_name", ["scope", "caseType", "dataClassification", "phase"])
    def test_create_with_null_uses_default(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus, wire_name: str
    ) -> None:
        _auth(api_client, admin_user)
        payload = _case_payload(**{wire_name: None})
        resp = api_client.post("/api/v1/case/", payload, format="json")
        assert resp.status_code == status.HTTP_201_CREATED, f"{wire_name}: {resp.data}"
        # Check the default was applied
        defaults = {
            "scope": "unset",
            "caseType": "unset",
            "dataClassification": "unset",
            "phase": "initial_triage",
        }
        resp_key = {
            "scope": "scope",
            "caseType": "caseType",
            "dataClassification": "dataClassification",
            "phase": "phase",
        }[wire_name]
        assert resp.data[resp_key] == defaults[wire_name], f"{wire_name} default mismatch"

    @pytest.mark.parametrize("wire_name", ["scope", "caseType", "dataClassification", "phase"])
    def test_create_with_empty_string_uses_default(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus, wire_name: str
    ) -> None:
        _auth(api_client, admin_user)
        payload = _case_payload(**{wire_name: ""})
        resp = api_client.post("/api/v1/case/", payload, format="json")
        assert resp.status_code == status.HTTP_201_CREATED, f"{wire_name}: {resp.data}"
        defaults = {
            "scope": "unset",
            "caseType": "unset",
            "dataClassification": "unset",
            "phase": "initial_triage",
        }
        resp_key = {
            "scope": "scope",
            "caseType": "caseType",
            "dataClassification": "dataClassification",
            "phase": "phase",
        }[wire_name]
        assert resp.data[resp_key] == defaults[wire_name], f"{wire_name} default mismatch"

    @pytest.mark.parametrize("wire_name", ["scope", "caseType", "dataClassification", "phase"])
    def test_patch_with_null_keeps_existing(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus, wire_name: str
    ) -> None:
        _auth(api_client, admin_user)
        # Create with a specific value
        create_payload = _case_payload(
            **{
                wire_name: {
                    "scope": "single_asset_user",
                    "caseType": "malware",
                    "dataClassification": "personal_data",
                    "phase": "analysis",
                }[wire_name]
            }
        )
        create_resp = api_client.post("/api/v1/case/", create_payload, format="json")
        case_id = create_resp.data["id"]
        original_value = create_resp.data[
            {
                "scope": "scope",
                "caseType": "caseType",
                "dataClassification": "dataClassification",
                "phase": "phase",
            }[wire_name]
        ]

        # Patch with null + another field (e.g. title) - null should be dropped, not change the value
        resp = api_client.patch(
            f"/api/v1/case/{case_id}/", {wire_name: None, "title": "Updated Title"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK, f"{wire_name}: {resp.data}"
        resp_key = {
            "scope": "scope",
            "caseType": "caseType",
            "dataClassification": "dataClassification",
            "phase": "phase",
        }[wire_name]
        assert resp.data[resp_key] == original_value, f"{wire_name} should not change on null patch"
        assert resp.data["title"] == "Updated Title"


# ---------------------------------------------------------------------------------------
# AC2.4 — Admin gate for data_classification
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestDataClassificationAdminGate:
    """AC2.4: data_classification is admin+ only.

    - Single PATCH by non-admin → 403
    - Bulk PATCH by non-admin → per-item 403
    - actor=None → denied (fail-closed)
    - Admin → succeeds + TimelineEvent(kind="classification-changed") + AuditLog
    """

    def test_single_patch_non_admin_forbidden(
        self, api_client: APIClient, analyst_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, analyst_user)
        create_resp = api_client.post("/api/v1/case/", _case_payload(), format="json")
        case_id = create_resp.data["id"]
        resp = api_client.patch(
            f"/api/v1/case/{case_id}/", {"dataClassification": "personal_data"}, format="json"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.data
        assert resp.data["type"] == "AuthorizationError"
        # Verify value unchanged
        get_resp = api_client.get(f"/api/v1/case/{case_id}/")
        assert get_resp.data["dataClassification"] == "unset"

    def test_bulk_patch_non_admin_forbidden_per_item(
        self, api_client: APIClient, analyst_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, analyst_user)
        # Create two cases
        case1 = api_client.post("/api/v1/case/", _case_payload(), format="json").data
        case2 = api_client.post("/api/v1/case/", _case_payload(), format="json").data
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {"ids": [case1["id"], case2["id"]], "dataClassification": "personal_data"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        results = resp.data["results"]
        # Both items should report 403
        assert len(results) == 2
        for result in results:
            assert result["status"] == 403, result
            assert "AuthorizationError" in result.get(
                "message", ""
            ) or "administrator" in result.get("message", "")
        # Values unchanged
        for case in (case1, case2):
            get_resp = api_client.get(f"/api/v1/case/{case['id']}/")
            assert get_resp.data["dataClassification"] == "unset"

    def test_bulk_patch_admin_succeeds(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        case1 = api_client.post("/api/v1/case/", _case_payload(), format="json").data
        case2 = api_client.post("/api/v1/case/", _case_payload(), format="json").data
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {"ids": [case1["id"], case2["id"]], "dataClassification": "personal_data"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        results = resp.data["results"]
        assert all(r["status"] == 200 for r in results)
        # Values updated
        for case in (case1, case2):
            get_resp = api_client.get(f"/api/v1/case/{case['id']}/")
            assert get_resp.data["dataClassification"] == "personal_data"

    def test_actor_none_denied_at_applier(
        self, admin_user: User, case_status: CaseStatus, default_org: Organisation
    ) -> None:
        """Direct call to apply_case_update with actor=None must raise AuthorizationError."""
        from cases.update import CaseUpdateError, apply_case_update

        case = Case.objects.create(title="Direct Test", status=case_status, owner_org=default_org)
        with pytest.raises(CaseUpdateError) as exc:
            apply_case_update(case, {"data_classification": "personal_data"}, actor=None)
        assert exc.value.error_type == "AuthorizationError"
        # Value unchanged
        case.refresh_from_db()
        assert case.data_classification == "unset"

    def test_admin_patch_succeeds_with_timeline_and_audit(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        create_resp = api_client.post("/api/v1/case/", _case_payload(), format="json")
        case_id = create_resp.data["id"]
        resp = api_client.patch(
            f"/api/v1/case/{case_id}/", {"dataClassification": "personal_data"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["dataClassification"] == "personal_data"

        # TimelineEvent(kind="classification-changed")
        from cases.models import TimelineEvent

        events = TimelineEvent.objects.filter(case_id=case_id, kind="classification-changed")
        assert events.count() == 1
        event = events.first()
        assert event.metadata["field"] == "data_classification"
        assert event.metadata["from"] == "unset"
        assert event.metadata["to"] == "personal_data"

        # AuditLog
        audit = AuditLog.objects.filter(
            target_type="case", target_id=str(case_id), action="case.data_classification"
        )
        assert audit.count() == 1
        assert audit.first().actor_id == admin_user.id


# ---------------------------------------------------------------------------------------
# AC2.5 — Query DSL filter/sort on the four fields + unknown field → 400
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseClassificationQueryDSL:
    """AC2.5: query-DSL filter/sort on the four fields; unknown field → 400."""

    @pytest.fixture(autouse=True)
    def setup_cases(self, admin_user: User, case_status: CaseStatus) -> None:
        # Create cases with different classification values
        Case.objects.create(
            title="Case A",
            status=case_status,
            owner_org=admin_user.org,
            scope="single_asset_user",
            case_type="malware",
            data_classification="personal_data",
            phase="initial_triage",
        )
        Case.objects.create(
            title="Case B",
            status=case_status,
            owner_org=admin_user.org,
            scope="multiple_assets_users",
            case_type="phishing",
            data_classification="public_data",
            phase="containment",
        )
        Case.objects.create(
            title="Case C",
            status=case_status,
            owner_org=admin_user.org,
            scope="single_department",
            case_type="ransomware",
            data_classification="sensitive_data",
            phase="analysis",
        )

    @pytest.mark.parametrize(
        "wire_name,value,expected_count",
        [
            ("scope", "single_asset_user", 1),
            ("caseType", "malware", 1),
            ("dataClassification", "personal_data", 1),
            ("phase", "initial_triage", 1),
        ],
    )
    def test_filter_by_classification_field(
        self,
        api_client: APIClient,
        admin_user: User,
        wire_name: str,
        value: str,
        expected_count: int,
    ) -> None:
        _auth(api_client, admin_user)
        # Query DSL uses POST /api/v1/query with query steps
        payload = {
            "query": [
                {"_name": "listCase"},
                {"_name": "filter", "_eq": {"_field": wire_name, "_value": value}},
            ]
        }
        resp = api_client.post("/api/v1/query/", payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert len(resp.data) == expected_count

    @pytest.mark.parametrize(
        "wire_name",
        [
            "scope",
            "caseType",
            "dataClassification",
            "phase",
        ],
    )
    def test_sort_by_classification_field(
        self, api_client: APIClient, admin_user: User, wire_name: str
    ) -> None:
        _auth(api_client, admin_user)
        payload = {
            "query": [{"_name": "listCase"}, {"_name": "sort", "_fields": [{wire_name: "asc"}]}]
        }
        resp = api_client.post("/api/v1/query/", payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert len(resp.data) == 3
        # Verify sort order by checking values are non-decreasing
        values = [r[wire_name] for r in resp.data]
        assert values == sorted(values), f"Sort by {wire_name} failed: {values}"

    def test_unknown_field_rejected(self, api_client: APIClient, admin_user: User) -> None:
        _auth(api_client, admin_user)
        payload = {
            "query": [
                {"_name": "listCase"},
                {"_name": "filter", "_field": "unknown_field", "_eq": "value"},
            ]
        }
        resp = api_client.post("/api/v1/query/", payload, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data


# ---------------------------------------------------------------------------------------
# AC2.6 — Template apply copies scope/case_type/phase, NOT data_classification
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseTemplateApplyClassification:
    """AC2.6: Template apply copies scope/case_type/phase and does NOT touch data_classification."""

    def test_template_apply_copies_three_fields(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        # Create a template with classification fields
        template_payload = {
            "name": "Test Template",
            "scope": "complete_enterprise",
            "caseType": "ransomware",
            "phase": "remediation",
        }
        template_resp = api_client.post("/api/v1/case/template/", template_payload, format="json")
        assert template_resp.status_code == status.HTTP_201_CREATED, template_resp.data
        template_id = template_resp.data["id"]

        # Create a case with different values
        case_resp = api_client.post(
            "/api/v1/case/",
            _case_payload(
                scope="single_asset_user",
                caseType="malware",
                phase="initial_triage",
                dataClassification="personal_data",  # This should NOT be overwritten
            ),
            format="json",
        )
        case_id = case_resp.data["id"]

        # Apply template
        resp = api_client.post(
            "/api/v1/case/_bulk/caseTemplate",
            {"ids": [case_id], "caseTemplate": template_id},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data

        # Verify the three fields were copied
        get_resp = api_client.get(f"/api/v1/case/{case_id}/")
        assert get_resp.data["scope"] == "complete_enterprise"
        assert get_resp.data["caseType"] == "ransomware"
        assert get_resp.data["phase"] == "remediation"
        # dataClassification should remain unchanged
        assert get_resp.data["dataClassification"] == "personal_data"


# ---------------------------------------------------------------------------------------
# AC2.7 — case_template_json exposes three fields, not dataClassification
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseTemplateJsonExposure:
    """AC2.7: case_template_json exposes scope, caseType, phase but NOT dataClassification."""

    def test_template_json_has_three_fields_not_fourth(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        _auth(api_client, admin_user)
        template_payload = {
            "name": "Template for JSON test",
            "scope": "third_party_supplier",
            "caseType": "bec",
            "phase": "cleanup",
        }
        create_resp = api_client.post("/api/v1/case/template/", template_payload, format="json")
        assert create_resp.status_code == status.HTTP_201_CREATED, create_resp.data
        template_id = create_resp.data["id"]

        # Fetch the template
        resp = api_client.get(f"/api/v1/case/template/{template_id}/")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        data = resp.data

        # Should have the three fields
        assert "scope" in data
        assert "caseType" in data
        assert "phase" in data
        assert data["scope"] == "third_party_supplier"
        assert data["caseType"] == "bec"
        assert data["phase"] == "cleanup"

        # Should NOT have dataClassification
        assert "dataClassification" not in data, (
            "dataClassification must not be exposed in case_template_json"
        )


# ---------------------------------------------------------------------------------------
# Additional: Bulk PATCH camelCase→snake_case mapping for all four fields
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkPatchCamelCaseMapping:
    """Ensure bulk PATCH accepts camelCase wire names for all four classification fields."""

    @pytest.mark.parametrize(
        "wire_name,model_field,value",
        [
            ("scope", "scope", "multiple_departments"),
            ("caseType", "case_type", "phishing"),
            ("dataClassification", "data_classification", "public_data"),
            ("phase", "phase", "cleanup"),
        ],
    )
    def test_bulk_patch_accepts_camel_case(
        self,
        api_client: APIClient,
        admin_user: User,
        case_status: CaseStatus,
        wire_name: str,
        model_field: str,
        value: str,
    ) -> None:
        _auth(api_client, admin_user)
        case = api_client.post("/api/v1/case/", _case_payload(), format="json").data
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {"ids": [case["id"]], wire_name: value},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, f"{wire_name}: {resp.data}"
        results = resp.data["results"]
        assert results[0]["status"] == 200
        # Verify the value was applied
        get_resp = api_client.get(f"/api/v1/case/{case['id']}/")
        resp_key = {
            "scope": "scope",
            "caseType": "caseType",
            "dataClassification": "dataClassification",
            "phase": "phase",
        }[wire_name]
        assert get_resp.data[resp_key] == value


# ---------------------------------------------------------------------------------------
# Additional: Verify the choice_label template filter works
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestChoiceLabelTemplateFilter:
    """Verify the choice_label template filter renders human labels."""

    def test_choice_label_returns_human_label(self) -> None:
        from ui.templatetags.ui_tags import choice_label

        # Test with a valid value
        assert choice_label("personal_data", DATA_CLASSIFICATION_CHOICES) == "Personal Data (PII)"
        assert choice_label("malware", CASE_TYPE_CHOICES) == "Malware"
        assert choice_label("initial_triage", CASE_PHASE_CHOICES) == "Initial Triage"
        assert choice_label("single_asset_user", CASE_SCOPE_CHOICES) == "Single Asset/User"

    def test_choice_label_fallback_for_unknown(self) -> None:
        from ui.templatetags.ui_tags import choice_label

        assert choice_label("unknown_value", DATA_CLASSIFICATION_CHOICES) == "unknown_value"

    def test_choice_label_fallback_for_empty(self) -> None:
        from ui.templatetags.ui_tags import choice_label

        assert choice_label("", DATA_CLASSIFICATION_CHOICES) == "Unset/Unknown"
        assert choice_label(None, DATA_CLASSIFICATION_CHOICES) == "Unset/Unknown"


# ---------------------------------------------------------------------------------------
# Additional: Form-encoded body (multipart) works for create/update
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestFormEncodedBody:
    """F4: form-encoded body carrying caseType works (no 500 from mutating QueryDict)."""

    def test_create_with_form_encoded(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        payload = _case_payload(caseType="phishing", scope="single_department")
        resp = api_client.post("/api/v1/case/", payload, format="multipart")
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data["caseType"] == "phishing"
        assert resp.data["scope"] == "single_department"

    def test_update_with_form_encoded(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        create_resp = api_client.post("/api/v1/case/", _case_payload(), format="json")
        case_id = create_resp.data["id"]
        payload = {"caseType": "ransomware", "phase": "analysis"}
        resp = api_client.patch(f"/api/v1/case/{case_id}/", payload, format="multipart")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["caseType"] == "ransomware"
        assert resp.data["phase"] == "analysis"


# ---------------------------------------------------------------------------------------
# Additional: Other classification fields are any-writer (not admin-gated)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestOtherClassificationFieldsAnyWriter:
    """scope, case_type, phase are any-writer (not admin-gated)."""

    @pytest.mark.parametrize(
        "wire_name,value",
        [
            ("scope", "multiple_departments"),
            ("caseType", "bec"),
            ("phase", "close"),
        ],
    )
    def test_analyst_can_patch_other_fields(
        self,
        api_client: APIClient,
        analyst_user: User,
        case_status: CaseStatus,
        wire_name: str,
        value: str,
    ) -> None:
        _auth(api_client, analyst_user)
        create_resp = api_client.post("/api/v1/case/", _case_payload(), format="json")
        case_id = create_resp.data["id"]
        resp = api_client.patch(f"/api/v1/case/{case_id}/", {wire_name: value}, format="json")
        assert resp.status_code == status.HTTP_200_OK, f"{wire_name}: {resp.data}"
        resp_key = {"scope": "scope", "caseType": "caseType", "phase": "phase"}[wire_name]
        assert resp.data[resp_key] == value
