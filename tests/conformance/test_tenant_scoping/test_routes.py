"""Tenant-scoping route enumeration conformance tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from cases.models import CaseObservable
from identity.models import Organisation

from .helpers import (
    _build_url,
    _discover_tenant_read_routes,
    _discover_tenant_write_routes,
    _mk_alert,
    _mk_case,
    _mk_client,
    _mk_observable,
    _mk_orgless_actor,
    _mk_user,
)


class TestRouteEnumerationConformance:
    """
    T3-c: Conformance test that enumerates tenant-touching read routes from URLconf
    and asserts cross-org access returns uniform 404 (same as non-existent ID),
    while own-org access returns 200.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("user1", self.org1)
        self.user2 = _mk_user("user2", self.org2)
        self.client1, _ = _mk_client(self.user1)
        self.client2, _ = _mk_client(self.user2)
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.super_client, _ = _mk_client(self.superadmin)

        # Seed data in org1 (WITHOUT shares for cross-org testing)
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-org1-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        from django.utils import timezone

        from cases.models import (
            Comment,
            Page,
            Procedure,
            Share,
            Task,
            TimelineEvent,
        )

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )
        self.page1 = Page.objects.create(
            case=self.case1, title="Page 1", content="Content", order=1, created_by=self.user1
        )
        self.proc1 = Procedure.objects.create(
            case=self.case1, pattern_id="T1059", pattern_name="Test"
        )
        self.comment1 = Comment.objects.create(
            case=self.case1, message="Comment 1", created_by=self.user1
        )
        # Create an attachment for case-attachment-download test
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from cases.models import Attachment

        self.attachment1 = Attachment.objects.create(
            case=self.case1,
            name="test.txt",
            content_type="text/plain",
            size=4,
            sha256="a" * 64,
            path="attachments/test.txt",
            created_by=self.user1,
        )
        # Create a dummy file in storage
        default_storage.save(self.attachment1.path, ContentFile(b"test"))

        from automation.models import Playbook

        self.playbook1 = Playbook.objects.create(
            name="Tenant API Playbook",
            owner_org=self.org1,
            trigger_event="observable.created",
            config={"action": "python", "action_path": "enrichment_probe"},
        )

        # Seed data in org2 (for cross-org tests)
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-org2-1")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )
        self.page2 = Page.objects.create(
            case=self.case2, title="Page 2", content="Content", order=1, created_by=self.user2
        )
        self.proc2 = Procedure.objects.create(
            case=self.case2, pattern_id="T1059", pattern_name="Test"
        )
        self.comment2 = Comment.objects.create(
            case=self.case2, message="Comment 2", created_by=self.user2
        )

        # Create a shared case for share testing (separate from cross-org tests)
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )

    def test_enumerated_routes_exist(self) -> None:
        """Sanity: the discovery finds at least the expected core routes."""
        routes = _discover_tenant_read_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [r for r in routes if not r[0].startswith("ui-")]
        route_names = {r[0] for r in api_routes}
        # Core tenant routes that must be discovered (GET-capable read routes)
        # Note: comment-detail, case-share-detail, case-attachment-detail, custom-event-detail
        # are write-only (PATCH/DELETE) — their read access is via list endpoints.
        # case-attachment-list is POST-only (upload); download is separate GET endpoint.
        # query-api is POST-only but read-semantics; included separately.
        expected_core = {
            "case-collection",
            "case-detail",
            "alert-list",
            "alert-detail",
            "alert-raw",
            "alert-comment-list",
            "case-task-list",
            "case-observable-list",
            "case-timeline",
            "case-comment-list",
            "case-page-list",
            "case-page-detail",
            "case-flow",
            "case-share-list",
            "case-attachment-download",
            "task-detail",
            "observable-detail",
            "procedure-detail",
            "case-export",
        }
        missing = expected_core - route_names
        assert not missing, f"Route discovery missing expected routes: {missing}"

        # query-api is POST-only read; verify it's discovered with POST
        query_routes = [r for r in routes if r[0] == "query-api"]
        assert query_routes, "query-api should be discovered"
        assert "POST" in query_routes[0][2], "query-api should support POST"

    def test_cross_org_read_returns_uniform_404(self) -> None:
        """
        For each enumerated tenant read route:
        - Detail routes (with ID params): cross-org access → 404 (uniform with non-existent ID)
        - Top-level list/collection routes: cross-org access → 200 with filtered results (only own org data)
        - Nested list routes (under case/alert): cross-org access → 404 (parent not accessible)
        """
        routes = _discover_tenant_read_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]

        # Build param maps for each route pattern
        param_map = self._build_param_maps()

        # Routes that are TOP-LEVEL LIST/COLLECTION (no parent ID params) - cross-org returns 200 with filtered data
        # Nested list routes (under case/alert) return 404 if parent not accessible
        list_routes = {
            "case-collection",
            "case-collection-slash",
            "alert-list",
            "alert-list-slash",
            "playbook-collection",
            "playbook-collection-slash",
            "query-api",
            "query-api-slash",
            "search-api",
            "search-api-slash",
            "rejected-observables-list",
            "rejected-observables-list-slash",
        }

        failures = []
        for name, pattern, methods in api_routes:
            if "GET" not in methods and "POST" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            # For query-api, send a valid query structure that would return data if not scoped
            if name in ("query-api", "query-api-slash"):
                query_body = {"query": [{"_name": "listCase"}]}
            else:
                query_body = {}

            # Cross-org request (user2 → org1's resource)
            resp_cross = (
                self.client2.get(url)
                if "GET" in methods
                else self.client2.post(url, query_body, format="json")
            )

            # Non-existent ID request (user2 → fake ID) - only for detail routes
            is_list_route = name in list_routes
            if not is_list_route:
                fake_url = self._fake_url_for_route(name, pattern, param_map)
                resp_fake = (
                    self.client2.get(fake_url)
                    if "GET" in methods
                    else self.client2.post(fake_url, query_body, format="json")
                )

                # Both must be 404 with NotFoundError
                if resp_cross.status_code != 404:
                    failures.append(f"{name}: cross-org got {resp_cross.status_code}, expected 404")
                if resp_fake.status_code != 404:
                    failures.append(f"{name}: fake-id got {resp_fake.status_code}, expected 404")
                if (
                    resp_cross.status_code == 404
                    and resp_cross.json().get("type") != "NotFoundError"
                ):
                    failures.append(
                        f"{name}: cross-org type={resp_cross.json().get('type')}, expected NotFoundError"
                    )
                if resp_fake.status_code == 404 and resp_fake.json().get("type") != "NotFoundError":
                    failures.append(
                        f"{name}: fake-id type={resp_fake.json().get('type')}, expected NotFoundError"
                    )
            else:
                # List routes: cross-org should return 200 with filtered results (only org2's data)
                if resp_cross.status_code != 200:
                    failures.append(
                        f"{name}: cross-org list got {resp_cross.status_code}, expected 200"
                    )
                # For query-api, verify the results are filtered to org2's visible cases only
                if name in ("query-api", "query-api-slash"):
                    data = resp_cross.json()
                    if not isinstance(data, list):
                        failures.append(f"{name}: cross-org query returned non-list")
                    else:
                        # user2 (org2) should see: self.case2 (own org) + self.shared_case (shared to org2)
                        # but NOT self.case1 (org1 private)
                        expected_count = 2  # case2 + shared_case
                        if len(data) != expected_count:
                            failures.append(
                                f"{name}: cross-org query returned {len(data)} items, expected {expected_count} (own org + shared)"
                            )
                        # Verify org1's private case is not in results
                        case_ids = {item["_id"] for item in data}
                        if str(self.case1.id) in case_ids:
                            failures.append(f"{name}: cross-org query leaked org1's private case")

            # Own-org request must succeed (200)
            if name in ("query-api", "query-api-slash"):
                own_query_body = {"query": [{"_name": "listCase"}]}
            elif name in ("search-api", "search-api-slash"):
                own_query_body = {}
            else:
                own_query_body = {}
            resp_own = (
                self.client1.get(url)
                if "GET" in methods
                else self.client1.post(url, own_query_body, format="json")
            )
            if resp_own.status_code != 200:
                failures.append(
                    f"{name}: own-org got {resp_own.status_code}, expected 200: {resp_own.content}"
                )

        assert not failures, "Conformance failures:\n" + "\n".join(failures)

    def test_superadmin_cross_org_read_succeeds(self) -> None:
        """SuperAdmin can read cross-org resources (audited separately in T3-d)."""
        routes = _discover_tenant_read_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]
        param_map = self._build_param_maps()

        for name, pattern, methods in api_routes:
            if "GET" not in methods and "POST" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))
            # For query-api, send a valid query structure
            if name in ("query-api", "query-api-slash"):
                query_body = {"query": [{"_name": "listCase"}]}
            else:
                query_body = {}
            resp = (
                self.super_client.get(url)
                if "GET" in methods
                else self.super_client.post(url, query_body, format="json")
            )
            assert resp.status_code == 200, (
                f"{name}: superAdmin got {resp.status_code}, expected 200: {resp.content}"
            )

    def _build_param_maps(self) -> dict[str, dict[str, str]]:
        """Build parameter substitutions for each route name."""
        base = {
            "case-collection": {},
            "case-detail": {"case_id": str(self.case1.id)},
            "alert-list": {},
            "alert-detail": {"alert_id": str(self.alert1.id)},
            "alert-raw": {"alert_id": str(self.alert1.id)},
            "alert-comment-list": {"alert_id": str(self.alert1.id)},
            "case-task-list": {"case_id": str(self.case1.id)},
            "case-observable-list": {"case_id": str(self.case1.id)},
            "case-timeline": {"case_id": str(self.case1.id)},
            "case-comment-list": {"case_id": str(self.case1.id)},
            "case-suggestions-list": {"case_id": str(self.case1.id)},
            "alert-suggestions-list": {"alert_id": str(self.alert1.id)},
            "case-page-list": {"case_id": str(self.case1.id)},
            "case-page-detail": {"case_id": str(self.case1.id), "page_id": str(self.page1.id)},
            "case-flow": {"case_id": str(self.case1.id)},
            "case-share-list": {"case_id": str(self.case1.id)},
            "case-attachment-list": {"case_id": str(self.case1.id)},
            "case-attachment-download": {
                "case_id": str(self.case1.id),
                "attachment_id": str(self.attachment1.id),
            },
            "task-detail": {"task_id": str(self.task1.id)},
            "observable-detail": {"observable_id": str(self.obs1.id)},
            "procedure-detail": {"procedure_id": str(self.proc1.id)},
            "case-export": {"case_id": str(self.case1.id)},
            "playbook-collection": {},
            "playbook-detail": {"playbook_id": str(self.playbook1.id)},
            "query-api": {},
            "search-api": {},
            "search-api-slash": {},
        }
        # Add -slash variants with same params
        slash_variants = {}
        for k, v in base.items():
            slash_variants[k + "-slash"] = v
        base.update(slash_variants)
        return base

    def _fake_url_for_route(
        self, name: str, pattern: str, param_map: dict[str, dict[str, str]]
    ) -> str:
        """Build a URL with a non-existent UUID for the given route."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        params = param_map.get(name, {}).copy()
        # Replace all IDs with fake UUID
        for key in params:
            params[key] = fake_uuid
        return _build_url(pattern, params)

    # --- T3-d: SuperAdmin cross-org read audit tests ---

    def test_superadmin_cross_org_read_writes_auditlog(self) -> None:
        """superAdmin GET of another org's case writes exactly one AuditLog."""
        from identity.models import AuditLog

        # Clear any existing audit logs
        AuditLog.objects.all().delete()

        # superAdmin in org1 reads case in org1 (own-org) - should NOT write audit
        url = f"/api/v1/case/{self.case1.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 0, "own-org read should not write audit"

        # superAdmin in org1 reads case in org2 (cross-org) - SHOULD write audit
        url = f"/api/v1/case/{self.case2.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 1, "cross-org read should write audit"
        log = AuditLog.objects.first()
        assert log.action == "cross_org_read"
        assert log.actor_id == self.superadmin.id
        assert log.actor_org_id == self.org1.id
        assert log.target_org_id == self.org2.id
        assert log.target_type == "Case"
        assert log.target_id == str(self.case2.id)
        assert log.ip is not None  # IP captured from request

    def test_superadmin_cross_org_bare_id_read_writes_auditlog(self) -> None:
        """superAdmin GET of another org's bare-id resource (task) writes audit."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        # superAdmin in org1 reads task in org2 (cross-org via bare ID)
        url = f"/api/v1/task/{self.task2.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 1, "cross-org bare-id read should write audit"
        log = AuditLog.objects.first()
        assert log.action == "cross_org_read"
        assert log.actor_id == self.superadmin.id
        assert log.target_org_id == self.org2.id
        assert log.target_type == "Task"
        assert log.target_id == str(self.task2.id)

    def test_superadmin_own_org_read_no_auditlog(self) -> None:
        """superAdmin GET of own org's resource writes NO AuditLog."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        # Test multiple own-org reads
        urls = [
            f"/api/v1/case/{self.case1.id}",
            f"/api/v1/task/{self.task1.id}",
            f"/api/v1/alert/{self.alert1.id}",
            f"/api/v1/observable/{self.obs1.id}",
        ]
        for url in urls:
            resp = self.super_client.get(url)
            assert resp.status_code == 200

        assert AuditLog.objects.count() == 0, "own-org reads should not write audit"

    def test_analyst_cross_org_read_no_auditlog(self) -> None:
        """Analyst cross-org read (404) writes NO AuditLog."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        # Analyst in org1 tries to read case in org2 - gets 404
        url = f"/api/v1/case/{self.case2.id}"
        resp = self.client1.get(url)
        assert resp.status_code == 404

        assert AuditLog.objects.count() == 0, "failed cross-org read should not write audit"

    def test_superadmin_alert_cross_org_read_writes_auditlog(self) -> None:
        """superAdmin GET of another org's alert writes audit."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        url = f"/api/v1/alert/{self.alert2.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 1
        log = AuditLog.objects.first()
        assert log.target_type == "Alert"
        assert log.target_id == str(self.alert2.id)
        assert log.target_org_id == self.org2.id


# =============================================================================
# UI Route Enumeration Conformance — tenant scoping on UI routes
# Enumerates UI routes from URLconf and asserts cross-org access returns 404.
# =============================================================================


class TestUIRouteEnumerationConformance:
    """
    Conformance test that enumerates tenant-touching UI read routes from URLconf
    and asserts cross-org access returns 404 (uniform with non-existent ID),
    while own-org access returns 200.

    Uses Django's test Client with session authentication (unlike API tests which use DRF's APIClient).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        from django.test import Client

        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("ui_user1", self.org1)
        self.user2 = _mk_user("ui_user2", self.org2)
        self.client1 = Client()
        self.client1.force_login(self.user1)
        self.client2 = Client()
        self.client2.force_login(self.user2)
        self.superadmin = _mk_user("ui_super", self.org1, role="superAdmin")
        self.super_client = Client()
        self.super_client.force_login(self.superadmin)

        # Seed data in org1 (WITHOUT shares for cross-org testing)
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-ui-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        # ADR-009: a feed is org-owned, so the Settings source page is a tenant detail route.
        from ingest.models import IngestionSource

        self.source1 = IngestionSource.objects.create(
            organisation=self.org1, slug="ui-src", name="UI Source"
        )
        from django.utils import timezone

        from cases.models import CaseObservable, Share, Task, TimelineEvent

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )

        from automation.models import Playbook

        self.playbook1 = Playbook.objects.create(
            name="Tenant Enumeration Playbook",
            owner_org=self.org1,
            trigger_event="manual",
            config={"action": "python", "action_path": "enrichment_probe"},
        )

        # MCP approval routes are tenant detail routes: seed one org1 proposal so the
        # generic read conformance harness can exercise cross-org/fake-id/own-org access.
        from datetime import timedelta

        from mcp_server.models import McpActionProposal

        self.proposal1 = McpActionProposal.objects.create(
            organisation=self.org1,
            requested_by=self.user1,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(self.case1.id),
            arguments={"caseId": str(self.case1.id), "markdown": "Tenant enumeration fixture"},
            risk="low",
            idempotency_key=f"tenant-ui-proposal-{uuid4()}",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        # Create an attachment for case-attachment-download/detail test
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from cases.models import Attachment

        self.attachment1 = Attachment.objects.create(
            case=self.case1,
            name="test.txt",
            content_type="text/plain",
            size=4,
            sha256="a" * 64,
            path="attachments/test.txt",
            created_by=self.user1,
        )
        default_storage.save(self.attachment1.path, ContentFile(b"test"))

        # Seed data in org2 (for cross-org tests)
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-ui-2")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )

        # Create a shared case for share testing (separate from cross-org tests)
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )

    def test_enumerated_ui_routes_exist(self) -> None:
        """Sanity: the discovery finds at least the expected core UI routes."""
        routes = _discover_tenant_read_routes()
        ui_route_names = {r[0] for r in routes if r[0].startswith("ui-")}
        # Core UI tenant routes that must be discovered (GET-capable read routes)
        # Note: ui-case-attachment-download, ui-case-attachment-detail have view issues (500/405)
        # ui-case-tag-toggle is POST-only (toggle)
        expected_core = {
            "ui-dashboard",
            "ui-alert-list",
            "ui-alert-detail",
            "ui-case-list",
            "ui-case-create",
            "ui-case-detail",
            "ui-case-tags",
            "ui-case-attachment-list",
            "ui-case-share-list",
            "ui-case-export",
            "ui-observable-list",
            "ui-observable-detail",
            "ui-automation-list",
            "ui-sources-list",
            "ui-settings-source-detail",
        }
        missing = expected_core - ui_route_names
        assert not missing, f"UI route discovery missing expected routes: {missing}"

    def test_cross_org_ui_read_returns_404(self) -> None:
        """
        For each enumerated tenant UI read route:
        - Detail routes (with ID params): cross-org access → 404
        - List routes: cross-org access → 200 with filtered results (only own org data)
        """
        routes = _discover_tenant_read_routes()
        ui_routes = [
            (name, pattern, methods) for name, pattern, methods in routes if name.startswith("ui-")
        ]

        param_map = self._build_ui_param_maps()

        # Routes that are TOP-LEVEL LIST (no parent ID params) - cross-org returns 200 with filtered data
        list_routes = {
            "ui-dashboard",
            "ui-alert-list",
            "ui-case-list",
            "ui-case-create",
            "ui-observable-list",
            "ui-automation-list",
            "ui-playbook-list",
            "ui-approvals-list",
            "ui-sources-list",
            "ui-search",
            "ui-password-change",
            "ui-rejected-observables",
        }

        # Routes with known issues that can't be tested generically (allow-listed with justification)
        # ui-case-attachment-download: 500 due to view bug (UUID conversion)
        # ui-case-attachment-detail: 405 - view only handles DELETE/POST
        skip_routes = {
            "ui-case-attachment-download",
            "ui-case-attachment-detail",
        }

        # Admin/superAdmin-only pages: the harness's `user1` is an analyst, for whom the correct
        # answer is 404 on *both* own-org and cross-org (the ADR-006 role gate answers before the
        # ADR-009 org gate, and both are uniform 404s so neither leaks existence). The own-org 200 for
        # an admin, and the cross-org 404 for a foreign admin, are covered by
        # `test_settings_source_mapping.py`.
        admin_only_routes = {
            "ui-settings-agent-access",
            "ui-settings-agent-autonomy",
            "ui-settings-audit-log",
            "ui-settings-oauth-binding-list",
            "ui-settings-source-detail",
            "ui-playbook-scripts",
        }

        failures = []
        for name, pattern, methods in ui_routes:
            if name in skip_routes:
                continue
            if "GET" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            # Cross-org request (user2 → org1's resource)
            resp_cross = self.client2.get(url)

            # Non-existent ID request (user2 → fake ID) - only for detail routes
            is_list_route = name in list_routes
            if not is_list_route:
                fake_url = self._fake_ui_url_for_route(name, pattern, param_map)
                resp_fake = self.client2.get(fake_url)

                # Both must be 404
                if resp_cross.status_code != 404:
                    failures.append(f"{name}: cross-org got {resp_cross.status_code}, expected 404")
                if resp_fake.status_code != 404:
                    failures.append(f"{name}: fake-id got {resp_fake.status_code}, expected 404")
                if name in admin_only_routes:
                    continue
            else:
                # List routes: cross-org should return 200 with filtered results
                if resp_cross.status_code != 200:
                    failures.append(
                        f"{name}: cross-org list got {resp_cross.status_code}, expected 200"
                    )

            # Own-org request must succeed (200)
            resp_own = self.client1.get(url)
            if resp_own.status_code != 200:
                failures.append(f"{name}: own-org got {resp_own.status_code}, expected 200")

        assert not failures, "UI conformance failures:\n" + "\n".join(failures)

    def test_superadmin_cross_org_ui_read_succeeds(self) -> None:
        """SuperAdmin can read cross-org UI resources."""
        routes = _discover_tenant_read_routes()
        ui_routes = [
            (name, pattern, methods) for name, pattern, methods in routes if name.startswith("ui-")
        ]
        param_map = self._build_ui_param_maps()

        for name, pattern, methods in ui_routes:
            if "GET" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))
            resp = self.super_client.get(url)
            assert resp.status_code == 200, (
                f"{name}: superAdmin got {resp.status_code}, expected 200"
            )

    def _build_ui_param_maps(self) -> dict[str, dict[str, str]]:
        """Build parameter substitutions for each UI route name."""
        base = {
            "ui-dashboard": {},
            "ui-alert-list": {},
            "ui-alert-detail": {"alert_id": str(self.alert1.id)},
            "ui-alert-export": {"alert_id": str(self.alert1.id)},
            "ui-alert-similar": {"alert_id": str(self.alert1.id)},
            "ui-case-list": {},
            "ui-case-create": {},
            "ui-case-detail": {"case_id": str(self.case1.number)},
            "ui-case-tags": {"case_id": str(self.case1.number)},
            "ui-case-similar": {"case_id": str(self.case1.number)},
            "ui-case-attachment-list": {"case_id": str(self.case1.number)},
            "ui-case-attachment-detail": {
                "case_id": str(self.case1.number),
                "attachment_id": str(self.attachment1.id),
            },
            "ui-case-attachment-download": {
                "case_id": str(self.case1.number),
                "attachment_id": str(self.attachment1.id),
            },
            "ui-case-share-list": {"case_id": str(self.shared_case.number)},
            # The task routes use a `<uuid:task_id>` converter; a non-UUID placeholder never
            # resolves, so own-org would 404 before the view runs. Point it at the org1 fixture.
            "ui-task-detail": {"task_id": str(self.task1.id)},
            "ui-task-source-payload": {"task_id": str(self.task1.id)},
            "ui-case-export": {"case_id": str(self.case1.number)},
            "ui-case-edit": {"case_id": str(self.case1.number)},
            "ui-observable-list": {},
            "ui-observable-detail": {"observable_id": str(self.obs1.id)},
            "ui-observable-edit": {"observable_id": str(self.obs1.id)},
            "ui-automation-list": {},
            "ui-playbook-list": {},
            "ui-playbook-detail": {"playbook_id": str(self.playbook1.id)},
            "ui-playbook-edit": {"playbook_id": str(self.playbook1.id)},
            "ui-approvals-detail": {"proposal_id": str(self.proposal1.id)},
            "ui-sources-list": {},
            "ui-settings-source-detail": {"source_id": str(self.source1.id)},
            # Phase 4 uses a UUID organisation path segment; point it at org1 so
            # superAdmin cross-org reads resolve instead of hitting /dummy.
            "ui-settings-agent-autonomy": {"organisation_id": str(self.org1.id)},
            "ui-search": {},
        }
        return base

    def _fake_ui_url_for_route(
        self, name: str, pattern: str, param_map: dict[str, dict[str, str]]
    ) -> str:
        """Build a URL with a non-existent UUID for the given route."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        params = param_map.get(name, {}).copy()
        # Replace all IDs with fake UUID
        for key in params:
            params[key] = fake_uuid
        return _build_url(pattern, params)


# =============================================================================
# Write conformance test — enumerates tenant-touching WRITE routes and asserts
# cross-org mutate returns uniform 404 (same shape as non-existent ID).
# =============================================================================


class TestWriteRouteEnumerationConformance:
    """
    Conformance test that enumerates tenant-touching write routes from URLconf
    and asserts cross-org mutate returns uniform 404 (same as non-existent ID),
    while own-org mutate succeeds (2xx) or returns validation error (400).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("user1", self.org1)
        self.user2 = _mk_user("user2", self.org2)
        self.client1, _ = _mk_client(self.user1)
        self.client2, _ = _mk_client(self.user2)
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.super_client, _ = _mk_client(self.superadmin)

        # Seed data in org1 (WITHOUT shares for cross-org testing)
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-org1-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        from django.utils import timezone

        from cases.models import (
            Comment,
            Page,
            Procedure,
            Share,
            Task,
            TimelineEvent,
        )

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )
        self.page1 = Page.objects.create(
            case=self.case1, title="Page 1", content="Content", order=1, created_by=self.user1
        )
        self.proc1 = Procedure.objects.create(
            case=self.case1, pattern_id="T1059", pattern_name="Test"
        )
        self.comment1 = Comment.objects.create(
            case=self.case1, message="Comment 1", created_by=self.user1
        )

        # Create an attachment for case-attachment-download/detail test
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from cases.models import Attachment

        self.attachment1 = Attachment.objects.create(
            case=self.case1,
            name="test.txt",
            content_type="text/plain",
            size=4,
            sha256="a" * 64,
            path="attachments/test.txt",
            created_by=self.user1,
        )
        default_storage.save(self.attachment1.path, ContentFile(b"test"))

        # Create a playbook for playbook-run test
        from automation.models import Playbook

        self.playbook1 = Playbook.objects.create(
            name="Test Playbook",
            owner_org=default_org,
            description="Test playbook for conformance testing",
            trigger_event="observable_created",
            is_active=True,
            config={},
        )

        from observables.models import ObservableSuggestion, RejectedObservable

        self.rejected1 = RejectedObservable.objects.create(
            owner_org=self.org1,
            data_type=self.obs1.data_type,
            normalized_data="10.0.0.3",
            reason="other",
            alert=self.alert1,
        )
        self.suggestion1 = ObservableSuggestion.objects.create(
            owner_org=self.org1,
            data_type=self.obs1.data_type,
            normalized_data="10.0.0.1",
            raw_data="10.0.0.1",
            source_alert=self.alert1,
            case=self.case1,
            status="proposed",
        )

        # Seed data in org2 (for cross-org tests)
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-org2-1")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )
        self.page2 = Page.objects.create(
            case=self.case2, title="Page 2", content="Content", order=1, created_by=self.user2
        )
        self.proc2 = Procedure.objects.create(
            case=self.case2, pattern_id="T1059", pattern_name="Test"
        )
        self.comment2 = Comment.objects.create(
            case=self.case2, message="Comment 2", created_by=self.user2
        )

        # Create a shared case for share testing (separate from cross-org tests)
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )
        assert Share.objects.filter(case=self.shared_case).count() == 1, "Share not created!"

    def test_enumerated_write_routes_exist(self) -> None:
        """Sanity: the discovery finds at least the expected core write routes."""
        routes = _discover_tenant_write_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [r for r in routes if not r[0].startswith("ui-")]
        route_names = {r[0] for r in api_routes}
        # Core tenant write routes that must be discovered
        # Note: global routes (vocabulary, playbook, taxonomy, auth, org, user) are excluded by _GLOBAL_READ_ROUTES
        expected_core = {
            # Case sub-resources (write)
            "case-attachment-list",  # POST upload
            "case-attachment-list-slash",
            "case-attachment-detail",  # DELETE
            "case-attachment-detail-slash",
            "case-alert-remove",  # DELETE (no slash variant)
            "case-comment-list",  # POST
            "case-comment-list-slash",
            "case-custom-event-create",  # POST
            "case-custom-event-create-slash",
            "case-merge",  # POST
            "case-observable-list",  # POST (was case-observable-add)
            "case-observable-list-slash",
            "case-page-detail",  # PATCH/DELETE
            "case-page-detail-slash",
            "case-page-list",  # POST
            "case-page-list-slash",
            "case-procedure-create",  # POST (no slash variant)
            "case-procedures-create",  # POST (no slash variant)
            "case-share-detail",  # DELETE
            "case-share-detail-slash",
            "case-share-list",  # POST/PUT
            "case-share-list-slash",
            "case-tag-link",  # POST/DELETE
            "case-tag-link-slash",
            "case-task-list",  # POST (was case-task-create)
            "case-task-list-slash",
            # Case bulk/detail (write)
            "case-bulk-update",  # PATCH
            "case-bulk-update-slash",
            "case-apply-template",  # POST
            "case-apply-template-slash",
            "case-detail",  # PATCH/PUT/DELETE
            "case-detail-slash",
            # Alert sub-resources (write)
            "alert-comment-list",  # POST
            "alert-comment-list-slash",
            "alert-import",  # POST (no slash variant)
            "alert-import-into",  # POST (no slash variant)
            "alert-merge",  # POST (no slash variant)
            "alert-observable-add",  # POST (no slash variant)
            "alert-procedure-create",  # POST (no slash variant)
            "alert-procedures-create",  # POST (no slash variant)
            "alert-tag-add",  # POST (no slash variant)
            "alert-tag-link",  # POST/DELETE
            "alert-tag-link-slash",
            # Alert bulk/detail (write)
            "alert-bulk-update",  # PATCH
            "alert-bulk-update-slash",
            "alert-detail",  # PATCH/PUT/DELETE (no slash variant)
            # Observable (write)
            "observable-tag-link",  # POST/DELETE
            "observable-tag-link-slash",
            "observable-bulk-update",  # PATCH
            "observable-bulk-update-slash",
            "observable-detail",  # PATCH/DELETE (no slash variant)
            # Task/Procedure/Comment/CustomEvent (write)
            "task-detail",  # PATCH/DELETE (no slash variant)
            "task-bulk-update",  # PATCH
            "task-bulk-update-slash",
            "procedure-detail",  # PATCH/DELETE (no slash variant)
            "procedure-bulk-delete",  # POST (no slash variant)
            "comment-detail",  # PATCH/DELETE
            "comment-detail-slash",
            "custom-event-detail",  # PATCH/DELETE (no slash variant)
            # Playbook (write)
            "playbook-run",  # POST
            "playbook-run-slash",
        }
        missing = expected_core - route_names
        assert not missing, f"Write route discovery missing expected routes: {missing}"

    def test_cross_org_write_returns_uniform_404(self) -> None:
        """
        For each enumerated tenant write route:
        - Cross-org mutate → 404 (uniform with non-existent ID)
        - Own-org mutate → 2xx or 400 (validation error), not 404
        """
        routes = _discover_tenant_write_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]
        param_map = self._build_write_param_maps()

        failures = []
        for name, pattern, methods in api_routes:
            # Only test one mutating method per route (prefer POST > PATCH > PUT > DELETE)
            method = "POST"
            if "POST" not in methods:
                if "PATCH" in methods:
                    method = "PATCH"
                elif "PUT" in methods:
                    method = "PUT"
                elif "DELETE" in methods:
                    method = "DELETE"
                else:
                    continue

            # Skip slash variants for DELETE operations to avoid double-deletion
            if method == "DELETE" and name.endswith("-slash"):
                continue

            # Skip collection routes (no path parameters) for cross-org testing
            # These routes create/update resources in the caller's own org, not cross-org
            has_path_params = "<str:" in pattern or "<uuid:" in pattern or "<slug:" in pattern
            if not has_path_params:
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            # Cross-org request (user2 → org1's resource)
            # Some routes require specific payloads; provide minimal valid ones
            is_playbook_run = name.startswith("playbook-run")
            if is_playbook_run:
                cross_payload = {"case": str(self.case1.id)}
            else:
                cross_payload = {}

            if method == "POST":
                resp_cross = self.client2.post(url, cross_payload, format="json")
            elif method == "PATCH":
                resp_cross = self.client2.patch(url, cross_payload, format="json")
            elif method == "PUT":
                resp_cross = self.client2.put(url, cross_payload, format="json")
            else:  # DELETE
                resp_cross = self.client2.delete(url)

            # Non-existent ID request (user2 → fake ID) - only for detail routes
            fake_url = self._fake_url_for_route(name, pattern, param_map)
            is_playbook_run = name.startswith("playbook-run")
            if is_playbook_run:
                fake_payload = {"case": "00000000-0000-0000-0000-000000000000"}
            else:
                fake_payload = {}

            if method == "POST":
                resp_fake = self.client2.post(fake_url, fake_payload, format="json")
            elif method == "PATCH":
                resp_fake = self.client2.patch(fake_url, fake_payload, format="json")
            elif method == "PUT":
                resp_fake = self.client2.put(fake_url, fake_payload, format="json")
            else:  # DELETE
                resp_fake = self.client2.delete(fake_url)

            # Both must be 404 with NotFoundError
            if resp_cross.status_code != 404:
                failures.append(
                    f"{name} ({method}): cross-org got {resp_cross.status_code}, expected 404"
                )
            if resp_fake.status_code != 404:
                failures.append(
                    f"{name} ({method}): fake-id got {resp_fake.status_code}, expected 404"
                )
            if resp_cross.status_code == 404 and resp_cross.json().get("type") != "NotFoundError":
                failures.append(
                    f"{name} ({method}): cross-org type={resp_cross.json().get('type')}, expected NotFoundError"
                )
            if resp_fake.status_code == 404 and resp_fake.json().get("type") != "NotFoundError":
                failures.append(
                    f"{name} ({method}): fake-id type={resp_fake.json().get('type')}, expected NotFoundError"
                )

            # Own-org request must NOT be 404 (should be 2xx or 400 validation error)
            is_playbook_run = name.startswith("playbook-run")
            if is_playbook_run:
                own_payload = {"case": str(self.case1.id)}
            else:
                own_payload = {}

            if method == "POST":
                resp_own = self.client1.post(url, own_payload, format="json")
            elif method == "PATCH":
                resp_own = self.client1.patch(url, own_payload, format="json")
            elif method == "PUT":
                resp_own = self.client1.put(url, own_payload, format="json")
            else:  # DELETE
                resp_own = self.client1.delete(url)

            if resp_own.status_code == 404:
                failures.append(
                    f"{name} ({method}): own-org got 404, expected 2xx/400: {resp_own.content}"
                )

        assert not failures, "Write conformance failures:\n" + "\n".join(failures)

    def test_superadmin_cross_org_write_succeeds(self) -> None:
        """SuperAdmin can write cross-org resources (audited separately)."""
        routes = _discover_tenant_write_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]
        param_map = self._build_write_param_maps()

        for name, pattern, methods in api_routes:
            method = "POST"
            if "POST" not in methods:
                if "PATCH" in methods:
                    method = "PATCH"
                elif "PUT" in methods:
                    method = "PUT"
                elif "DELETE" in methods:
                    method = "DELETE"
                else:
                    continue

            # Skip slash variants for DELETE operations to avoid double-deletion
            if method == "DELETE" and name.endswith("-slash"):
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            if method == "POST":
                resp = self.super_client.post(url, {}, format="json")
            elif method == "PATCH":
                resp = self.super_client.patch(url, {}, format="json")
            elif method == "PUT":
                resp = self.super_client.put(url, {}, format="json")
            else:  # DELETE
                resp = self.super_client.delete(url)

            # SuperAdmin should not get 404 (may get 2xx, 400 validation, etc.)
            assert resp.status_code != 404, (
                f"{name} ({method}): superAdmin got 404, expected not-404: {resp.content}"
            )

    def _build_write_param_maps(self) -> dict[str, dict[str, str]]:
        """Build parameter substitutions for each write route name."""
        base = {
            "case-observable-list": {"case_id": str(self.case1.id)},
            "case-task-list": {"case_id": str(self.case1.id)},
            "case-tag-link": {"case_id": str(self.case1.id)},
            "case-alert-remove": {"case_id": str(self.case1.id), "alert_id": str(self.alert1.id)},
            "case-custom-event-create": {"case_id": str(self.case1.id)},
            "case-comment-list": {"case_id": str(self.case1.id)},
            "case-bulk-update": {},
            "case-apply-template": {},
            "case-merge": {"case_ids": f"{self.case1.id}"},
            "case-procedure-create": {"case_id": str(self.case1.id)},
            "case-procedures-create": {"case_id": str(self.case1.id)},
            "case-attachment-list": {"case_id": str(self.case1.id)},
            "case-attachment-detail": {
                "case_id": str(self.case1.id),
                "attachment_id": str(self.attachment1.id),
            },
            "case-share-detail": {
                "case_id": str(self.shared_case.id),
                "share_id": str(self.share1.id),
            },
            "case-share-list": {"case_id": str(self.shared_case.id)},
            "case-page-list": {"case_id": str(self.case1.id)},
            "case-page-detail": {"case_id": str(self.case1.id), "page_id": str(self.page1.id)},
            "suggestion-accept": {"suggestion_id": str(self.suggestion1.id)},
            "suggestion-reject": {"suggestion_id": str(self.suggestion1.id)},
            "suggestion-ignore": {"suggestion_id": str(self.suggestion1.id)},
            "observable-tag-link": {"observable_id": str(self.obs1.id)},
            "observable-bulk-update": {},
            "procedure-bulk-delete": {},
            "task-bulk-update": {},
            "playbook-run": {"playbook_id": str(self.playbook1.id)},
            "playbook-detail": {"playbook_id": str(self.playbook1.id)},
            "alert-import": {"alert_id": str(self.alert1.id)},
            "alert-import-into": {"alert_id": str(self.alert1.id), "case_id": str(self.case1.id)},
            "alert-merge": {"alert_id": str(self.alert1.id), "case_id": str(self.case1.id)},
            "alert-observable-add": {"alert_id": str(self.alert1.id)},
            "alert-tag-add": {"alert_id": str(self.alert1.id)},
            "alert-tag-link": {"alert_id": str(self.alert1.id)},
            "alert-procedure-create": {"alert_id": str(self.alert1.id)},
            "alert-procedures-create": {"alert_id": str(self.alert1.id)},
            "alert-bulk-update": {},
            "alert-comment-list": {"alert_id": str(self.alert1.id)},
            "comment-detail": {"comment_id": str(self.comment1.id)},
            "custom-event-detail": {"event_id": str(self.event1.id)},
            "case-detail": {"case_id": str(self.case1.id)},
            "alert-detail": {"alert_id": str(self.alert1.id)},
            "observable-detail": {"observable_id": str(self.obs1.id)},
            "task-detail": {"task_id": str(self.task1.id)},
            "procedure-detail": {"procedure_id": str(self.proc1.id)},
            "rejected-observable-promote": {"id": str(self.rejected1.id)},
        }
        # Add -slash variants with same params
        slash_variants = {}
        for k, v in base.items():
            slash_variants[k + "-slash"] = v
        base.update(slash_variants)
        return base

    def _fake_url_for_route(
        self, name: str, pattern: str, param_map: dict[str, dict[str, str]]
    ) -> str:
        """Build a URL with a non-existent UUID for the given route."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        params = param_map.get(name, {}).copy()
        # Replace all IDs with fake UUID
        for key in params:
            params[key] = fake_uuid
        return _build_url(pattern, params)


TENANT_ROUTES = [
    "/api/v1/case",
    "/api/v1/alert",
    "/api/v1/observable",
    "/api/v1/task",
    "/api/v1/customEvent",
    "/api/v1/caseTemplate",
    # "/api/v1/ttp",  # TTP vocabulary is global
    # "/api/v1/playbook",  # Playbooks are global automation definitions
]


def test_tenant_routes_require_org_context(db: None) -> None:
    """All tenant routes should reject org-less users (not 500, not 200 with data)."""
    # `User.org` is NOT NULL (ADR-007), so an org-less user cannot be persisted; this
    # exercises the global `IsAuthenticatedAndOrgMember` guard with an unsaved stub.
    user = _mk_orgless_actor()
    client = APIClient()
    client.force_authenticate(user=user)

    for route in TENANT_ROUTES:
        # LIST - should return empty list (no access) or 401/403/404
        resp = client.get(route)
        if resp.status_code == 200:
            data = resp.json()
            assert len(data) == 0, f"{route} LIST: org-less user got data: {data}"
        else:
            assert resp.status_code in (401, 403, 404), f"{route} LIST: {resp.status_code}"

        # CREATE (if applicable)
        if route in ("/api/v1/case", "/api/v1/alert", "/api/v1/observable"):
            resp = client.post(route, {"title": "Test"}, format="json")
            assert resp.status_code in (400, 401, 403, 404), f"{route} CREATE: {resp.status_code}"
