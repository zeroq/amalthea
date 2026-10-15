"""Deep / edge-case conformance tests for PLAN-2026-10-14 (case classification fields).

This module complements ``test_case_classification.py`` (the implementer's AC-aligned suite, which
pins AC2.1-AC2.7). It exists because mutation testing showed several *kinds* of behaviour the AC
suite did not discriminate:

* input **type** hardening (an int/list/dict/bool reaching the string-enum membership check — a
 300 on a type confusion is the classic regression);
* the **viewer** read/write matrix (Q-D5b: a viewer may *see* ``dataClassification`` but may not
  write anything — the API is a flat 403 by permission class, the UI a uniform 404, never an
  existence oracle either way);
* **cross-org** access (a foreign case must be a uniform 404 for read *and* write);
* **mixed-case bulk** mapping and per-item **isolation** (a non-admin smuggling ``dataClassification``
  into a bulk body must not let the *other* fields in the same body commit);
* the **unchanged-value** no-op (a PATCH that restates the same value must not emit a timeline event
  or an audit row);
* the **create** path (the admin gate is enforced by the shared applier even on create, and
  ``import_alert_to_case`` lands the model defaults);
* the **migration** backfill (a case that predates the four columns must come out with the
  documented defaults, not NULL);
* the query DSL's **branch-drop** semantics (``listAny`` filtering on a case-only field).

A classification change on the **bulk** path must be audited exactly like the single-PATCH path
(plan S2): see ``test_bulk_classification_change_is_audited``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from rest_framework import status
from rest_framework.test import APIClient

from alerts.models import Alert
from cases.models import Case, CaseStatus, TimelineEvent
from core.enums import ADMIN_ROLE, VIEWER_ROLE
from identity.models import AuditLog, Organisation, User

# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture
def default_org() -> Organisation:
    return Organisation.objects.get_or_create(name="default")[0]


def _role_user(org: Organisation, login: str, role: str) -> User:
    user = User.objects.filter(login=login).first()
    if user is None:
        user = User.objects.create(
            login=login,
            username=login,
            email=f"{login}@example.net",
            org=org,
            role=role,
            is_active=True,
        )
    return user


@pytest.fixture
def admin_user(default_org: Organisation) -> User:
    return _role_user(default_org, "hard-admin", ADMIN_ROLE)


@pytest.fixture
def analyst_user(default_org: Organisation) -> User:
    return _role_user(default_org, "hard-analyst", "analyst")


@pytest.fixture
def viewer_user(default_org: Organisation) -> User:
    return _role_user(default_org, "hard-viewer", VIEWER_ROLE)


@pytest.fixture
def other_org() -> Organisation:
    return Organisation.objects.create(name="Rival Org")


@pytest.fixture
def outsider_user(other_org: Organisation) -> User:
    return _role_user(other_org, "hard-outsider", ADMIN_ROLE)


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
    base = {"title": "Hardening Case", "severity": 2, "tlp": 2, "pap": 2}
    base.update(overrides)
    return base


def _make_case(status_row: CaseStatus, org: Organisation, **kw: Any) -> Case:
    kw.setdefault("title", "H")
    return Case.objects.create(status=status_row, owner_org=org, **kw)


# ---------------------------------------------------------------------------------------
# Type confusion / enum input hardening
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnumInputHardening:
    """A non-string value must be a clean 400, never a 500 (type confusion)."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            123,
            1.5,
            True,
            ["malware"],
            {"malware": 1},
            "Malware",  # correct token, wrong case
            "malware!",  # trailing punctuation is not stripped
            "m" * 300,
        ],
    )
    def test_patch_bad_case_type_is_400_not_500(
        self,
        api_client: APIClient,
        admin_user: User,
        case_status: CaseStatus,
        bad_value: Any,
    ) -> None:
        _auth(api_client, admin_user)
        case = _make_case(case_status, admin_user.org)
        resp = api_client.patch(f"/api/v1/case/{case.id}/", {"caseType": bad_value}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, (
            f"caseType={bad_value!r} produced {resp.status_code}: {getattr(resp, 'data', '')}"
        )
        case.refresh_from_db()
        assert case.case_type == "unset"

    @pytest.mark.parametrize(
        "bad_value", [123, True, ["unset"], {"x": 1}, "Complete Enterprise", "complete-enterprise"]
    )
    def test_patch_bad_scope_is_400(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus, bad_value: Any
    ) -> None:
        _auth(api_client, admin_user)
        case = _make_case(case_status, admin_user.org)
        resp = api_client.patch(f"/api/v1/case/{case.id}/", {"scope": bad_value}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, bad_value

    def test_surrounding_whitespace_is_trimmed_and_accepted(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        """The applier strips before the membership check — an over-strict test here would be wrong."""
        _auth(api_client, admin_user)
        case = _make_case(case_status, admin_user.org)
        resp = api_client.patch(
            f"/api/v1/case/{case.id}/", {"scope": "  single_department  "}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["scope"] == "single_department"

    @pytest.mark.parametrize("bad_value", [123, True, ["malware"], {"x": 1}, "Malware"])
    def test_create_bad_case_type_is_400_not_500(
        self, api_client: APIClient, admin_user: User, bad_value: Any
    ) -> None:
        _auth(api_client, admin_user)
        resp = api_client.post("/api/v1/case/", _case_payload(caseType=bad_value), format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, bad_value


# ---------------------------------------------------------------------------------------
# Viewer read/write matrix (Q-D5b)
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestViewerClassification:
    def test_viewer_reads_classification_on_get(
        self, api_client: APIClient, viewer_user: User, case_status: CaseStatus
    ) -> None:
        """Q-D5b: a viewer sees the value; the field is not hidden from readers."""
        case = _make_case(
            case_status,
            viewer_user.org,
            data_classification="sensitive_data",
            scope="complete_enterprise",
        )
        _auth(api_client, viewer_user)
        resp = api_client.get(f"/api/v1/case/{case.id}/")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["dataClassification"] == "sensitive_data"
        assert resp.data["scope"] == "complete_enterprise"

    def test_viewer_patch_of_any_field_is_403_no_oracle(
        self, api_client: APIClient, viewer_user: User, case_status: CaseStatus
    ) -> None:
        """ADR-006's global `ViewerReadOnly` refuses the verb before the handler, so a valid value,
        a bad value and a non-existent id are all the same 403 — the role is not an existence
        oracle. (The *UI* uses `not_viewer`, a uniform 404; the API is 403 by permission class.)"""
        case = _make_case(case_status, viewer_user.org)
        _auth(api_client, viewer_user)
        for target in (str(case.id), "00000000-0000-0000-0000-000000000000"):
            for body in (
                {"dataClassification": "personal_data"},
                {"title": "nope"},
                {"scope": "bogus"},
            ):
                resp = api_client.patch(f"/api/v1/case/{target}/", body, format="json")
                assert resp.status_code == status.HTTP_403_FORBIDDEN, (target, body)
        case.refresh_from_db()
        assert case.data_classification == "unset"
        assert case.title == "H"


# ---------------------------------------------------------------------------------------
# Cross-org: uniform 404, no existence oracle
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossOrgNoOracle:
    def test_foreign_admin_cannot_read_or_write(
        self, api_client: APIClient, admin_user: User, outsider_user: User, case_status: CaseStatus
    ) -> None:
        case = _make_case(
            case_status,
            admin_user.org,
            data_classification="personal_data",
            scope="single_department",
        )
        _auth(api_client, outsider_user)
        assert api_client.get(f"/api/v1/case/{case.id}/").status_code == status.HTTP_404_NOT_FOUND
        resp = api_client.patch(
            f"/api/v1/case/{case.id}/", {"dataClassification": "public_data"}, format="json"
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        case.refresh_from_db()
        assert case.data_classification == "personal_data"

    def test_foreign_bulk_reports_per_item_404(
        self, api_client: APIClient, admin_user: User, outsider_user: User, case_status: CaseStatus
    ) -> None:
        case = _make_case(case_status, admin_user.org)
        _auth(api_client, outsider_user)
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {"ids": [str(case.id)], "scope": "single_asset_user"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["results"][0]["status"] == 404
        case.refresh_from_db()
        assert case.scope == "unset"


# ---------------------------------------------------------------------------------------
# Bulk: mixed wire names + per-item isolation
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkClassification:
    def test_admin_bulk_mixed_snake_and_camel_applies_all(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        c1 = _make_case(case_status, admin_user.org)
        c2 = _make_case(case_status, admin_user.org)
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {
                "ids": [str(c1.id), str(c2.id)],
                # Deliberately mixed: snake `scope`/`phase`, camel `caseType`/`dataClassification`.
                "scope": "single_department",
                "caseType": "phishing",
                "dataClassification": "personal_data",
                "phase": "analysis",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["updated"] == 2
        for case in (c1, c2):
            case.refresh_from_db()
            assert case.scope == "single_department"
            assert case.case_type == "phishing"
            assert case.data_classification == "personal_data"
            assert case.phase == "analysis"

    def test_non_admin_bulk_smuggling_classification_is_isolated_per_item(
        self, api_client: APIClient, analyst_user: User, case_status: CaseStatus
    ) -> None:
        """The 403 on `dataClassification` must not let the *other* fields in the same body commit."""
        _auth(api_client, analyst_user)
        c1 = _make_case(case_status, analyst_user.org, title="untouched-1")
        c2 = _make_case(case_status, analyst_user.org, title="untouched-2")
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {
                "ids": [str(c1.id), str(c2.id)],
                "title": "hijacked",
                "scope": "complete_enterprise",
                "dataClassification": "personal_data",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        results = resp.data["results"]
        assert [r["status"] for r in results] == [403, 403]
        for case in (c1, c2):
            case.refresh_from_db()
            assert case.title in ("untouched-1", "untouched-2")
            assert case.scope == "unset"
            assert case.data_classification == "unset"

    def test_bulk_with_only_denied_field_does_not_touch_row(
        self, api_client: APIClient, analyst_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, analyst_user)
        case = _make_case(case_status, analyst_user.org, title="keep")
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {"ids": [str(case.id)], "dataClassification": "public_data"},
            format="json",
        )
        assert resp.data["results"][0]["status"] == 403
        case.refresh_from_db()
        assert case.title == "keep"
        assert case.data_classification == "unset"

    def test_bulk_classification_change_is_audited(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        _auth(api_client, admin_user)
        case = _make_case(case_status, admin_user.org)
        resp = api_client.patch(
            "/api/v1/case/_bulk/",
            {"ids": [str(case.id)], "dataClassification": "personal_data"},
            format="json",
        )
        assert resp.data["results"][0]["status"] == 200
        assert TimelineEvent.objects.filter(case=case, kind="classification-changed").count() == 1
        assert (
            AuditLog.objects.filter(
                target_type="case", target_id=str(case.id), action="case.data_classification"
            ).count()
            == 1
        )


# ---------------------------------------------------------------------------------------
# Unchanged value: no event, no audit
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnchangedValueNoop:
    def test_restating_the_same_value_emits_no_event_or_audit(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        case = _make_case(case_status, admin_user.org, data_classification="personal_data")
        before_events = TimelineEvent.objects.filter(case=case).count()
        _auth(api_client, admin_user)
        resp = api_client.patch(
            f"/api/v1/case/{case.id}/",
            {"dataClassification": "personal_data", "scope": "single_department"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        # The restated `data_classification` must NOT produce a classification event...
        assert not TimelineEvent.objects.filter(
            case=case, kind="classification-changed", metadata__field="data_classification"
        ).exists()
        assert not AuditLog.objects.filter(
            target_id=str(case.id), action="case.data_classification"
        ).exists()
        # ...but the genuinely-changed `scope` must produce exactly one.
        changed = TimelineEvent.objects.filter(case=case, kind="classification-changed")
        assert [e.metadata["field"] for e in changed] == ["scope"]
        assert TimelineEvent.objects.filter(case=case).count() == before_events + 1


# ---------------------------------------------------------------------------------------
# Create path
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreatePath:
    def test_non_admin_api_create_smuggling_classification_is_403(
        self, api_client: APIClient, analyst_user: User
    ) -> None:
        _auth(api_client, analyst_user)
        before = Case.objects.count()
        resp = api_client.post(
            "/api/v1/case/", _case_payload(dataClassification="personal_data"), format="json"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.data
        assert resp.data["type"] == "AuthorizationError"
        assert Case.objects.count() == before

    def test_admin_create_with_all_four_round_trips(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        _auth(api_client, admin_user)
        resp = api_client.post(
            "/api/v1/case/",
            _case_payload(
                scope="third_party_supplier",
                caseType="bec",
                dataClassification="public_data",
                phase="cleanup",
            ),
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        case = Case.objects.get(id=resp.data["id"])
        assert (case.scope, case.case_type, case.data_classification, case.phase) == (
            "third_party_supplier",
            "bec",
            "public_data",
            "cleanup",
        )

    def test_import_alert_to_case_lands_model_defaults(self, make_alert, admin_user: User) -> None:
        from alerts.escalation import import_alert_to_case

        alert: Alert = make_alert(
            org=admin_user.org,
            title="Phish",
            type="phishing",
            source="mailbox",
            source_ref="ref-1",
        )
        case, _warnings = import_alert_to_case(alert, actor=admin_user)
        assert case.scope == "unset"
        assert case.case_type == "unset"
        assert case.data_classification == "unset"
        assert case.phase == "initial_triage"

    def test_case_json_emits_exactly_the_four_wire_keys(
        self, api_client: APIClient, admin_user: User, case_status: CaseStatus
    ) -> None:
        case = _make_case(
            case_status,
            admin_user.org,
            scope="single_asset_user",
            case_type="malware",
            data_classification="no_data",
            phase="containment",
        )
        _auth(api_client, admin_user)
        resp = api_client.get(f"/api/v1/case/{case.id}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["scope"] == "single_asset_user"
        assert resp.data["caseType"] == "malware"
        assert resp.data["dataClassification"] == "no_data"
        assert resp.data["phase"] == "containment"


# ---------------------------------------------------------------------------------------
# Query DSL: deeper operator + branch-drop coverage
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueryDSLDeep:
    @pytest.fixture(autouse=True)
    def _seed(self, admin_user: User, case_status: CaseStatus) -> None:
        Case.objects.create(
            title="A",
            status=case_status,
            owner_org=admin_user.org,
            case_type="malware",
            phase="initial_triage",
        )
        Case.objects.create(
            title="B",
            status=case_status,
            owner_org=admin_user.org,
            case_type="phishing",
            phase="containment",
        )
        Case.objects.create(
            title="C",
            status=case_status,
            owner_org=admin_user.org,
            case_type="ransomware",
            phase="analysis",
        )

    def _query(self, api_client: APIClient, admin_user: User, steps: list[dict[str, Any]]):
        _auth(api_client, admin_user)
        return api_client.post("/api/v1/query/", {"query": steps}, format="json")

    def test_in_operator_on_case_type(self, api_client: APIClient, admin_user: User) -> None:
        resp = self._query(
            api_client,
            admin_user,
            [
                {"_name": "listCase"},
                {
                    "_name": "filter",
                    "_in": {"_field": "caseType", "_values": ["malware", "ransomware"]},
                },
            ],
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert {r["caseType"] for r in resp.data} == {"malware", "ransomware"}

    def test_not_operator_on_phase(self, api_client: APIClient, admin_user: User) -> None:
        resp = self._query(
            api_client,
            admin_user,
            [
                {"_name": "listCase"},
                {"_name": "filter", "_not": {"_eq": {"_field": "phase", "_value": "analysis"}}},
            ],
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert "analysis" not in {r["phase"] for r in resp.data}
        assert len(resp.data) == 2

    def test_and_or_composition(self, api_client: APIClient, admin_user: User) -> None:
        resp = self._query(
            api_client,
            admin_user,
            [
                {"_name": "listCase"},
                {
                    "_name": "filter",
                    "_or": [
                        {"_eq": {"_field": "caseType", "_value": "malware"}},
                        {"_eq": {"_field": "caseType", "_value": "ransomware"}},
                    ],
                },
                {"_name": "filter", "_and": [{"_eq": {"_field": "phase", "_value": "analysis"}}]},
            ],
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert {r["caseType"] for r in resp.data} == {"ransomware"}

    def test_list_any_branch_drop_on_case_only_field(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        """Observables have no `caseType`; the `listAny` branch drops them rather than erroring."""
        resp = self._query(
            api_client,
            admin_user,
            [
                {"_name": "listAny"},
                {"_name": "filter", "_eq": {"_field": "caseType", "_value": "malware"}},
            ],
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert {r["_type"] for r in resp.data} == {"case"}
        assert len(resp.data) == 1

    def test_unknown_classification_adjacent_field_is_400(
        self, api_client: APIClient, admin_user: User
    ) -> None:
        resp = self._query(
            api_client,
            admin_user,
            [
                {"_name": "listCase"},
                {"_name": "filter", "_eq": {"_field": "dataClassificationX", "_value": "x"}},
            ],
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert "dataClassificationX" in str(resp.data)


# ---------------------------------------------------------------------------------------
# Migration backfill
# ---------------------------------------------------------------------------------------


def _previous_cases_migration() -> str:
    """The cases migration immediately before the classification AddFields."""
    return "0016_task_note"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_migration_backfills_a_preexisting_case_with_defaults() -> None:
    """A row created before 0017 must come out `unset`/`unset`/`unset`/`initial_triage`, not NULL.

    AddField with a default would NOT NULL the existing rows; this proves the columns are
    queryable with the vocabulary default too (a plain `AddField` without a default leaves NULLs on
    any DB that allows it and the model would surface `None`, which is off-vocabulary).
    """
    from identity.models import Organisation

    # Need a status/org to satisfy the historical FKs. `serialized_rollback` restores seed rows.
    org, _ = Organisation.objects.get_or_create(name="migration-backfill-org")
    status_row, _ = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )

    executor = MigrationExecutor(connection)
    target = [("cases", _previous_cases_migration())]
    executor.migrate(target)
    old_apps = MigrationExecutor(connection).loader.project_state(target).apps
    OldCase = old_apps.get_model("cases", "Case")  # noqa: N806 — Django's historical-model idiom
    old = OldCase.objects.create(title="Legacy", status_id=status_row.id, owner_org_id=org.id)

    try:
        MigrationExecutor(connection).migrate([("cases", "0017_case_classification")])
        row = Case.objects.get(id=old.id)
        assert row.scope == "unset"
        assert row.case_type == "unset"
        assert row.data_classification == "unset"
        assert row.phase == "initial_triage"
    finally:
        # Leave the schema at the head for every later test, even on assertion failure.
        MigrationExecutor(connection).migrate([("cases", "0017_case_classification")])
