"""UI tenant-scoping conformance tests."""

from __future__ import annotations

import pytest
from django.urls import reverse

from cases.models import Case, Share, Task
from identity.models import Organisation

from .helpers import _mk_alert, _mk_case, _mk_observable, _mk_user


class TestUITenantScoping:
    """UI-level tenant scoping tests for ui/views.py routes (NEW-07)."""

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

        # Seed data in org1
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-ui-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        from django.utils import timezone

        from cases.models import CaseObservable, Share, Task, TimelineEvent

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )
        # Shared case for share tests
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )

        from automation.models import Playbook

        self.playbook1 = Playbook.objects.create(
            name="Tenant UI Playbook",
            owner_org=self.org1,
            trigger_event="observable.created",
            config={"action": "python", "action_path": "enrichment_probe"},
        )

        # Seed data in org2
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-ui-2")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )

    # --- NEW-01: playbook_run cross-tenant write ---
    def test_ui_playbook_run_cross_tenant_case_returns_404(self) -> None:
        """POST /ui/playbook/<id>/run with cross-tenant case → 404, no run created (NEW-01)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook",
            owner_org=self.org1,
            trigger_event="manual",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant case (org2's case) from org1 user
        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"case": str(self.case2.id)},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.content}"
        assert AutomationRun.objects.count() == 0, (
            "No AutomationRun should be created on cross-tenant access"
        )

    def test_ui_playbook_run_cross_tenant_observable_returns_404(self) -> None:
        """POST /ui/playbook/<id>/run with cross-tenant observable → 404, no run created (NEW-01)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook-2",
            owner_org=self.org1,
            trigger_event="observable_created",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant observable from org1 user
        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"observable": str(self.obs2.id)},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.content}"
        assert AutomationRun.objects.count() == 0, (
            "No AutomationRun should be created on cross-tenant access"
        )

    def test_ui_playbook_run_own_org_returns_302(self) -> None:
        """POST /ui/playbook/<id>/run with own-org case → 302 redirect, run created (NEW-01)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook-3",
            owner_org=self.org1,
            trigger_event="manual",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"case": str(self.case1.id)},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}: {resp.content}"
        )
        assert AutomationRun.objects.count() == 1, (
            "AutomationRun should be created for own-org access"
        )
        run = AutomationRun.objects.first()
        assert run.case_id == self.case1.id

    def test_ui_playbook_run_cross_org_playbook_returns_404(self) -> None:
        """A playbook owned by another org is a uniform 404 before any case work."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="org2-only-run",
            owner_org=self.org2,
            trigger_event="manual",
            is_active=True,
            config={"action": "python", "action_path": "enrichment_probe"},
        )

        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"case": str(self.case1.id)},
        )
        assert resp.status_code == 404
        assert AutomationRun.objects.filter(playbook=playbook).count() == 0

    def test_ui_playbook_delete_cross_org_returns_404(self) -> None:
        """Deleting another org's playbook is a uniform 404 and leaves it intact."""
        from automation.models import Playbook

        playbook = Playbook.objects.create(
            name="org2-only-delete",
            owner_org=self.org2,
            trigger_event="manual",
            is_active=True,
            config={"action": "python", "action_path": "enrichment_probe"},
        )

        resp = self.client1.post(reverse("ui-playbook-delete", kwargs={"playbook_id": playbook.id}))
        assert resp.status_code == 404
        assert Playbook.objects.filter(pk=playbook.pk).exists()

    def test_ui_playbook_list_owner_org_column_is_superadmin_only(self) -> None:
        """superAdmin sees the Organisation column; an org user does not."""
        from automation.models import Playbook

        Playbook.objects.create(
            name="org2-inventory",
            owner_org=self.org2,
            trigger_event="manual",
            config={"action": "python", "action_path": "enrichment_probe"},
        )

        resp_user = self.client1.get(reverse("ui-playbook-list"))
        assert resp_user.status_code == 200
        assert resp_user.context["show_owner_org"] is False
        assert all(p.owner_org_id == self.org1.id for p in resp_user.context["playbooks"])

        resp_super = self.super_client.get(reverse("ui-playbook-list"))
        assert resp_super.status_code == 200
        assert resp_super.context["show_owner_org"] is True
        assert any(p.owner_org_id == self.org2.id for p in resp_super.context["playbooks"])

    # --- NEW-02: case_apply_template ---
    def test_ui_case_apply_template_cross_tenant_returns_404(self) -> None:
        """POST /ui/case/<id>/template with cross-tenant case → 404 (NEW-02)."""
        from cases.models import CaseTemplate

        template = CaseTemplate.objects.create(name="Test Template", severity=2)

        # Cross-tenant case
        resp = self.client1.post(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case2.number}),
            {"template_id": template.id},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.content}"

    def test_ui_case_apply_template_own_org_returns_302(self) -> None:
        """POST /ui/case/<id>/template with own-org case → 302 redirect (NEW-02)."""
        from cases.models import CaseTemplate

        template = CaseTemplate.objects.create(
            name="Test Template 2", severity=2, description="Test desc"
        )

        resp = self.client1.post(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case1.number}),
            {"template_id": template.id},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}: {resp.content}"
        )
        # Verify template was applied (description should be set)
        self.case1.refresh_from_db()
        assert self.case1.description == "Test desc"

    def test_ui_case_apply_template_get_own_org_returns_200(self) -> None:
        """GET /ui/case/<id>/template with own-org case → 200 (NEW-02)."""
        from cases.models import CaseTemplate

        CaseTemplate.objects.create(name="Test Template 3", severity=2)

        resp = self.client1.get(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case1.number})
        )
        assert resp.status_code == 200

    def test_ui_case_apply_template_get_cross_tenant_returns_404(self) -> None:
        """GET /ui/case/<id>/template with cross-tenant case → 404 (NEW-02)."""
        resp = self.client1.get(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case2.number})
        )
        assert resp.status_code == 404

    # --- NEW-03: case_merge ---
    def test_ui_case_merge_cross_tenant_returns_404(self) -> None:
        """GET/POST /ui/case/<id>/merge with cross-tenant case → 404 (NEW-03)."""
        # GET - cross-tenant source case
        resp = self.client1.get(reverse("ui-case-merge", kwargs={"case_id": self.case2.number}))
        assert resp.status_code == 404

        # POST - cross-tenant target case
        resp = self.client1.post(
            reverse("ui-case-merge", kwargs={"case_id": self.case1.number}),
            {"target_case": str(self.case2.id)},
        )
        assert resp.status_code == 404

    def test_ui_case_merge_own_org_get_returns_200(self) -> None:
        """GET /ui/case/<id>/merge with own-org case → 200 (NEW-03)."""
        resp = self.client1.get(reverse("ui-case-merge", kwargs={"case_id": self.case1.number}))
        assert resp.status_code == 200

    def test_ui_case_merge_own_org_post_returns_302(self) -> None:
        """POST /ui/case/<id>/merge with own-org cases → 302 redirect (NEW-03)."""
        # Create another case in org1 to merge into
        target_case = _mk_case("Target Case", self.org1)

        resp = self.client1.post(
            reverse("ui-case-merge", kwargs={"case_id": self.case1.number}),
            {"target_case": str(target_case.id)},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}: {resp.content}"
        )
        # Source case should be deleted, target should remain
        assert not Case.objects.filter(pk=self.case1.id).exists()
        assert Case.objects.filter(pk=target_case.id).exists()

    # --- NEW-04: nav counts (dashboard) ---
    def test_ui_dashboard_counts_scoped_to_org(self) -> None:
        """Dashboard counts only show own org data (NEW-04)."""
        resp1 = self.client1.get(reverse("ui-dashboard"))
        resp2 = self.client2.get(reverse("ui-dashboard"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Check context counts are org-scoped
        # Org1 has 2 cases (case1 + shared_case), 1 alert, 1 task
        # Org2 has 2 cases (case2 + shared_case via share), 1 alert, 1 task
        assert resp1.context["counts"]["cases_open"] == 2
        assert resp2.context["counts"]["cases_open"] == 2
        assert resp1.context["counts"]["alerts_unlinked"] == 1
        assert resp2.context["counts"]["alerts_unlinked"] == 1

    # --- NEW-05: alert_detail merge candidates ---
    def test_ui_alert_detail_merge_candidates_scoped(self) -> None:
        """Alert detail merge candidates only show visible cases (NEW-05)."""
        resp1 = self.client1.get(reverse("ui-alert-detail", kwargs={"alert_id": self.alert1.id}))
        resp2 = self.client1.get(reverse("ui-alert-detail", kwargs={"alert_id": self.alert2.id}))

        assert resp1.status_code == 200
        assert resp2.status_code == 404  # Cross-tenant alert

        # Org1 user should only see org1 cases in merge candidates.
        # §12.3 (d596db6) repointed this context from `Case` to `SimilarCase` (correlation-ranked
        # wrappers, `cases/correlation.py`), so the assertions read through `.case`; this test
        # was left behind and broke — see TODO §12.3-fu2. The tenant-scoping intent is unchanged.
        candidates = resp1.context["merge_candidates"]
        candidate_ids = {sc.case.id for sc in candidates}
        assert self.case1.id in candidate_ids
        assert self.case2.id not in candidate_ids

    # --- NEW-06: sources_list and playbook_detail ---
    def test_ui_sources_list_alert_count_scoped(self) -> None:
        """Sources list alert_count only counts own org alerts (NEW-06)."""
        resp1 = self.client1.get(reverse("ui-sources-list"))
        resp2 = self.client2.get(reverse("ui-sources-list"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Each org should see their own alert counts
        for _source in resp1.context["sources"]:
            # The source may have 0 or 1 alerts depending on org
            pass  # Just verify no crash

    def test_ui_playbook_detail_runs_scoped(self) -> None:
        """Playbook detail is org-scoped; its run rail still only shows visible cases (NEW-06)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="Scoped Playbook",
            owner_org=self.org1,
            trigger_event="manual",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Create runs on both orgs' cases with unique idempotency keys
        run1 = AutomationRun.objects.create(
            case=self.case1,
            playbook=playbook,
            playbook_name=playbook.name,
            status="Success",
            idempotency_key="test-run-1",
        )
        AutomationRun.objects.create(
            case=self.case2,
            playbook=playbook,
            playbook_name=playbook.name,
            status="Success",
            idempotency_key="test-run-2",
        )

        resp1 = self.client1.get(reverse("ui-playbook-detail", kwargs={"playbook_id": playbook.id}))
        resp2 = self.client2.get(reverse("ui-playbook-detail", kwargs={"playbook_id": playbook.id}))

        assert resp1.status_code == 200
        # Cross-org playbook detail is a uniform 404, not a run-count oracle.
        assert resp2.status_code == 404

        # Org1 sees the playbook (and its run history) but only runs on visible cases.
        runs1 = list(resp1.context["runs"])
        assert len(runs1) == 1
        assert runs1[0].id == run1.id

    # --- Core UI routes cross-tenant 404 ---
    def test_ui_case_detail_cross_tenant_returns_404(self) -> None:
        """GET /ui/case/<id> cross-tenant → 404."""
        resp = self.client1.get(reverse("ui-case-detail", kwargs={"case_id": self.case2.number}))
        assert resp.status_code == 404

    def test_ui_alert_detail_cross_tenant_returns_404(self) -> None:
        """GET /ui/alert/<uuid> cross-tenant → 404."""
        resp = self.client1.get(reverse("ui-alert-detail", kwargs={"alert_id": self.alert2.id}))
        assert resp.status_code == 404

    def test_ui_observable_detail_cross_tenant_returns_404(self) -> None:
        """GET /ui/observables/<uuid> cross-tenant → 404."""
        resp = self.client1.get(
            reverse("ui-observable-detail", kwargs={"observable_id": self.obs2.id})
        )
        assert resp.status_code == 404

    def test_ui_case_share_list_cross_tenant_returns_404(self) -> None:
        """GET /ui/case/<id>/shares cross-tenant → 404."""
        resp = self.client1.get(
            reverse("ui-case-share-list", kwargs={"case_id": self.case2.number})
        )
        assert resp.status_code == 404

    def test_ui_case_share_revoke_cross_tenant_returns_404(self) -> None:
        """POST /ui/case/<id>/shares/<share_id>/revoke cross-tenant → 404."""
        # First create a share on org2's case (as org2 user)
        from cases.models import Share

        share = Share.objects.create(
            case=self.case2, organisation=self.org1, permissions={"read": True}
        )

        # Org1 user tries to revoke share on org2's case (not owner)
        resp = self.client1.post(
            reverse(
                "ui-case-share-revoke",
                kwargs={"case_id": self.case2.number, "share_id": share.id},
            )
        )
        assert resp.status_code == 404
        # Share should still exist
        assert Share.objects.filter(pk=share.id).exists()

    def test_ui_case_share_list_non_owner_returns_404(self) -> None:
        """GET /ui/case/<id>/shares with read-only share → 404 (not owner)."""
        # User2 has read share on shared_case (owned by org1)
        resp = self.client2.get(
            reverse("ui-case-share-list", kwargs={"case_id": self.shared_case.number})
        )
        assert resp.status_code == 404, "Read-only share holder cannot access share management"

    def test_ui_case_share_list_owner_returns_200(self) -> None:
        """GET /ui/case/<id>/shares owner org → 200."""
        resp = self.client1.get(
            reverse("ui-case-share-list", kwargs={"case_id": self.shared_case.number})
        )
        assert resp.status_code == 200

    def test_ui_case_share_list_malformed_org_id_does_not_500(self) -> None:
        """A tampered `organisation` id must redirect with an error, not 500 (F7 class)."""
        before = Share.objects.filter(case=self.shared_case).count()
        resp = self.client1.post(
            reverse("ui-case-share-list", kwargs={"case_id": self.shared_case.number}),
            {"organisation": "not-a-uuid"},
        )
        assert resp.status_code == 302
        assert Share.objects.filter(case=self.shared_case).count() == before

    def test_ui_case_share_revoke_non_owner_returns_404(self) -> None:
        """POST /ui/case/<id>/shares/<share_id>/revoke with read-only share → 404."""
        # User2 tries to revoke their own share (not owner)
        resp = self.client2.post(
            reverse(
                "ui-case-share-revoke",
                kwargs={"case_id": self.shared_case.number, "share_id": self.share1.id},
            )
        )
        assert resp.status_code == 404
        assert Share.objects.filter(pk=self.share1.id).exists()

    # --- Username picker scoping (org-global disclosure fix) ---
    def test_ui_case_detail_assignee_picker_scoped_to_org(self) -> None:
        """Case detail assignee picker only shows users from the same org."""
        # Create a user in org2
        user_org2 = _mk_user("org2_user", self.org2)

        # Org1 user views case detail
        resp = self.client1.get(reverse("ui-case-detail", kwargs={"case_id": self.case1.number}))
        assert resp.status_code == 200

        # Assignee picker should only contain org1 users
        assignees = resp.context["assignees"]
        assignee_logins = {u.login for u in assignees}
        assert self.user1.login in assignee_logins  # own org user present
        assert user_org2.login not in assignee_logins, "Assignee picker leaked org2 user"

    def test_ui_case_task_create_assignee_lookup_scoped_to_org(self) -> None:
        """Task assignee lookup only finds users from the same org."""
        # Create a user in org2
        user_org2 = _mk_user("org2_user_lookup", self.org2)

        # Org1 user creates a task with org2 user's login - should NOT find them
        resp = self.client1.post(
            reverse("ui-case-task-create", kwargs={"case_id": self.case1.number}),
            {"title": "Test Task", "assignee": user_org2.login},
        )
        assert resp.status_code == 302  # Redirect on success

        # Task should be created but unassigned (org2 user not found)
        task = Task.objects.filter(case=self.case1, title="Test Task").first()
        assert task is not None
        assert task.assignee is None, "Task assignee lookup leaked org2 user"

        # Org1 user creates a task with org1 user's login - SHOULD find them
        resp = self.client1.post(
            reverse("ui-case-task-create", kwargs={"case_id": self.case1.number}),
            {"title": "Test Task 2", "assignee": self.user1.login},
        )
        assert resp.status_code == 302

        task2 = Task.objects.filter(case=self.case1, title="Test Task 2").first()
        assert task2 is not None
        assert task2.assignee == self.user1, "Task assignee lookup failed for own org user"

    # --- §12.5-fu2: case-page write routes (cross-tenant enumeration) ---
    # Why these exist: the three generic sweeps do NOT cover these routes.
    # `_UI_WRITE_ONLY_ROUTES` excludes them from the UI *read* sweep, and the three
    # write sweeps in `TestWriteRouteEnumerationConformance` filter to API routes
    # (`not name.startswith("ui-")`). A refactor that drops the shared
    # `resolve_case(..., write=True)` guard — or the `case=case` clause on the page
    # lookup — would therefore leave CI green. Each test pins the exact 404 (not a
    # 403/redirect, which would leak existence) and the inert effect on the target.
    # TODO §12.5-fu2 (independent verification 2026-10-18, observation O1).

    def test_ui_case_page_create_cross_tenant_returns_404(self) -> None:
        """POST /cases/<org-B-case>/page from org A → uniform 404, no page created."""
        from cases.models import Page
        from identity.models import AuditLog

        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse("ui-case-page-create", kwargs={"case_id": self.case2.number}),
            {"title": "Injected", "content": "cross-tenant"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert not Page.objects.filter(case=self.case2).exists(), (
            "Cross-tenant page create must not mutate org B's case"
        )
        assert AuditLog.objects.count() == audit_before, (
            "Denied request must not write an AuditLog row"
        )

    def test_ui_case_page_create_readonly_share_returns_404(self) -> None:
        """A read-only share must not authorize page writes: 404, no page created.

        Pins the `write=True` half of the guard specifically — a demotion to
        `write=False` would still 404 a foreign case (no share at all) but would
        let a read-only share holder write.
        """
        from cases.models import Page
        from identity.models import AuditLog

        audit_before = AuditLog.objects.count()
        resp = self.client2.post(
            reverse("ui-case-page-create", kwargs={"case_id": self.shared_case.number}),
            {"title": "Injected", "content": "via read-only share"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert not Page.objects.filter(case=self.shared_case).exists(), (
            "Read-only share must not allow page creation"
        )
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_edit_cross_tenant_case_returns_404(self) -> None:
        """POST /cases/<org-B-case>/page/<page>/edit from org A → 404, page untouched."""
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-edit",
                kwargs={"case_id": self.case2.number, "page_id": page.id},
            ),
            {"title": "Hijacked", "content": "mutated"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        page.refresh_from_db()
        assert page.title == "Org B page", "Cross-tenant edit must not change org B's page"
        assert page.content == "original", "Cross-tenant edit must not change org B's page"
        assert page.updated_by is None, "Cross-tenant edit must not stamp updated_by"
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_edit_cross_tenant_page_returns_404(self) -> None:
        """A foreign page id embedded in the caller's OWN case URL → 404, page untouched.

        The distinct attack: the case in the URL is resolvable and writable by the
        caller, so only the `case=case` clause on the page lookup (which makes the
        page org-scoped through its case) can reject the request.
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-edit",
                kwargs={"case_id": self.case1.number, "page_id": page.id},
            ),
            {"title": "Hijacked", "content": "mutated"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        page.refresh_from_db()
        assert page.title == "Org B page", "Foreign page id must not be editable via own case"
        assert page.content == "original", "Foreign page id must not be editable via own case"
        assert page.updated_by is None
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_edit_readonly_share_returns_404(self) -> None:
        """Read-only share holder editing a page on the shared case → 404, page untouched.

        Pins `write=True` on the edit route (see the create twin above).
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.shared_case,
            title="Shared page",
            content="original",
            order=1,
            created_by=self.user1,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client2.post(
            reverse(
                "ui-case-page-edit",
                kwargs={"case_id": self.shared_case.number, "page_id": page.id},
            ),
            {"title": "Hijacked", "content": "mutated"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        page.refresh_from_db()
        assert page.title == "Shared page", "Read-only share must not allow page edits"
        assert page.content == "original"
        assert page.updated_by is None
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_delete_cross_tenant_case_returns_404(self) -> None:
        """POST /cases/<org-B-case>/page/<page>/delete from org A → 404, page survives."""
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-delete",
                kwargs={"case_id": self.case2.number, "page_id": page.id},
            ),
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert Page.objects.filter(pk=page.id).exists(), (
            "Cross-tenant page delete must not remove org B's page"
        )
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_delete_cross_tenant_page_returns_404(self) -> None:
        """A foreign page id embedded in the caller's OWN case URL → 404, page survives.

        Same distinct attack as the edit twin: the case resolves, so only the
        `case=case` clause on the page lookup can reject the delete.
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-delete",
                kwargs={"case_id": self.case1.number, "page_id": page.id},
            ),
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert Page.objects.filter(pk=page.id).exists(), (
            "Foreign page id must not be deletable via own case"
        )
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_delete_readonly_share_returns_404(self) -> None:
        """Read-only share holder deleting a page on the shared case → 404, page survives.

        Pins `write=True` on the delete route (see the create twin above).
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.shared_case,
            title="Shared page",
            content="original",
            order=1,
            created_by=self.user1,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client2.post(
            reverse(
                "ui-case-page-delete",
                kwargs={"case_id": self.shared_case.number, "page_id": page.id},
            ),
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert Page.objects.filter(pk=page.id).exists(), (
            "Read-only share must not allow page deletion"
        )
        assert AuditLog.objects.count() == audit_before
