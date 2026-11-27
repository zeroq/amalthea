"""Tenant-scoping write-enforcement and sharing regression tests."""

from __future__ import annotations

from automation.models import AutomationRun, Playbook
from cases.models import Case, Procedure, Share, Task
from observables.models import Observable

from .helpers import _mk_case, _mk_client, _mk_observable, _mk_org, _mk_user


class TestCaseOrgScopeRegression:
    """Regression tests for the _case_org_scope Q-object fix.

    The old implementation returned a raw WhereNode that only worked when the
    caller used select_related("case"). The new implementation returns a proper
    Q object via identity.scoping.org_q, which works regardless of join strategy.
    """

    def test_task_list_without_select_related_scoped_to_org(self, db: None) -> None:
        """Tasks on an org-owned case are visible without select_related on the case.

        This would have failed with the old WhereNode approach (column
        case_record.owner_org_id would not resolve without the join alias).
        """
        from django.test import RequestFactory

        from cases.models import Task
        from cases.views import _case_org_scope

        org = _mk_org("Org1")
        user = _mk_user("analyst", org)
        case = _mk_case("Case with tasks", org)
        task = Task.objects.create(case=case, title="Task 1")

        # Build a request with the user (no select_related on the queryset)
        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/task")
        request.user = user

        # Apply the scope Q directly to a bare Task queryset (no select_related)
        scope_q = _case_org_scope(request)
        qs = Task.objects.filter(scope_q)
        assert qs.count() == 1
        assert qs.first().id == task.id

    def test_timeline_without_select_related_scoped_to_org(self, db: None) -> None:
        """Timeline events on an org-owned case are visible without select_related on the case."""
        from django.test import RequestFactory
        from django.utils import timezone

        from cases.models import TimelineEvent
        from cases.views import _case_org_scope

        org = _mk_org("Org1")
        user = _mk_user("analyst", org)
        case = _mk_case("Case with timeline", org)
        event = TimelineEvent.objects.create(
            case=case, title="Event 1", description="Note", date=timezone.now()
        )

        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/timeline")
        request.user = user

        scope_q = _case_org_scope(request)
        qs = TimelineEvent.objects.filter(scope_q)
        assert qs.count() == 1
        assert qs.first().id == event.id

    def test_task_cross_tenant_denied_without_select_related(self, db: None) -> None:
        """Cross-tenant tasks are denied even without select_related on the case."""
        from django.test import RequestFactory

        from cases.models import Task
        from cases.views import _case_org_scope

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("analyst1", org1)
        case2 = _mk_case("Other org case", org2)
        Task.objects.create(case=case2, title="Task in org2")

        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/task")
        request.user = user1

        scope_q = _case_org_scope(request)
        qs = Task.objects.filter(scope_q)
        assert qs.count() == 0

    def test_timeline_cross_tenant_denied_without_select_related(self, db: None) -> None:
        """Cross-tenant timeline events are denied even without select_related on the case."""
        from django.test import RequestFactory
        from django.utils import timezone

        from cases.models import TimelineEvent
        from cases.views import _case_org_scope

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("analyst1", org1)
        case2 = _mk_case("Other org case", org2)
        TimelineEvent.objects.create(
            case=case2, title="Event in org2", description="", date=timezone.now()
        )

        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/timeline")
        request.user = user1

        scope_q = _case_org_scope(request)
        qs = TimelineEvent.objects.filter(scope_q)
        assert qs.count() == 0


# =============================================================================
# P4 — Write enforcement tests (T4-a, T4-b, T4-c, T4-d)
# =============================================================================


class TestP4WriteEnforcement:
    """Tests for P4 write/object authorization (plan §9 P4, ACs T4-a..T4-d)."""

    # --- T4-a: cross-tenant mutate → uniform 404 ------------------------------

    def test_cross_tenant_observable_patch_returns_404(self, db: None) -> None:
        """PATCH /api/v1/observable/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        obs2 = _mk_observable("10.0.0.2", org2)

        # Owner can patch
        resp = client2.patch(
            f"/api/v1/observable/{obs2.id}", {"message": "owner update"}, format="json"
        )
        assert resp.status_code == 204

        # Cross-tenant → 404 (not 403)
        resp = client1.patch(f"/api/v1/observable/{obs2.id}", {"message": "hacker"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row unchanged
        obs2.refresh_from_db()
        assert obs2.message == "owner update"

    def test_cross_tenant_observable_delete_returns_404(self, db: None) -> None:
        """DELETE /api/v1/observable/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        obs2 = _mk_observable("10.0.0.2", org2)

        # Cross-tenant → 404
        resp = client1.delete(f"/api/v1/observable/{obs2.id}")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row still exists
        assert Observable.objects.filter(pk=obs2.id).exists()

    def test_cross_tenant_comment_patch_returns_404(self, db: None) -> None:
        """PATCH /api/v1/comment/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        case2 = _mk_case("Case2", org2)
        comment = case2.comments.create(message="Original", created_by=_user2)

        # Cross-tenant → 404
        resp = client1.patch(f"/api/v1/comment/{comment.id}", {"message": "hacker"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row unchanged
        comment.refresh_from_db()
        assert comment.message == "Original"

    def test_cross_tenant_task_patch_returns_404(self, db: None) -> None:
        """PATCH /api/v1/task/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        case2 = _mk_case("Case2", org2)
        task = Task.objects.create(case=case2, title="Original")

        # Cross-tenant → 404
        resp = client1.patch(f"/api/v1/task/{task.id}", {"title": "hacker"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row unchanged
        task.refresh_from_db()
        assert task.title == "Original"

    def test_cross_tenant_case_merge_returns_404(self, db: None) -> None:
        """POST /api/v1/case/_merge/{id1,id2} where ALL ids are cross-tenant → 404."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        case2 = _mk_case("Source", org2)
        case3 = _mk_case("Source2", org2)

        # Cross-tenant merge (user1 tries to merge two org2 cases) → 404 (nothing resolves)
        resp = client1.post(f"/api/v1/case/_merge/{case2.id},{case3.id}", {}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Both cases unchanged
        assert Case.objects.filter(pk=case2.id).exists()
        assert Case.objects.filter(pk=case3.id).exists()

    def test_cross_tenant_case_merge_partial_resolves(self, db: None) -> None:
        """POST /api/v1/case/_merge with mix of own and cross-tenant → 200, only own cases merged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        case1 = _mk_case("Target", org1)
        case2 = _mk_case("Source", org2)

        # Mix of own and cross-tenant → 200 (own case resolves, cross-tenant skipped)
        resp = client1.post(f"/api/v1/case/_merge/{case1.id},{case2.id}", {}, format="json")
        assert resp.status_code == 200
        # Target still exists, source unchanged
        assert Case.objects.filter(pk=case1.id).exists()
        assert Case.objects.filter(pk=case2.id).exists()

    # --- T4-b: read-only share denies mutates --------------------------------

    def test_readonly_share_allows_get_denies_patch(self, db: None) -> None:
        """A read-only share permits GET but denies PATCH on case, task.

        Observables are directly org-owned (not share-scoped), so observable access
        is controlled by the observable's owner_org, not by case shares.
        """
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)  # owner
        user2 = _mk_user("u2", org2)  # shared org
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Shared", org1)
        Share.objects.create(case=case1, organisation=org2, permissions={"read": True})

        task = Task.objects.create(case=case1, title="Task")

        # Shared org can READ case and task (via case share)
        assert client2.get(f"/api/v1/case/{case1.id}").status_code == 200
        assert client2.get(f"/api/v1/task/{task.id}").status_code == 200

        # Shared org CANNOT PATCH (write) → 404
        assert (
            client2.patch(f"/api/v1/case/{case1.id}", {"title": "hack"}, format="json").status_code
            == 404
        )
        assert (
            client2.patch(f"/api/v1/task/{task.id}", {"title": "hack"}, format="json").status_code
            == 404
        )

        # Owner CAN PATCH - case returns 200, task returns 204
        assert (
            client1.patch(f"/api/v1/case/{case1.id}", {"title": "owner"}, format="json").status_code
            == 200
        )
        assert (
            client1.patch(f"/api/v1/task/{task.id}", {"title": "owner"}, format="json").status_code
            == 204
        )

    def test_readwrite_share_allows_mutate(self, db: None) -> None:
        """A readwrite share permits PATCH on case, task.

        Observables are directly org-owned (not share-scoped), so observable mutates
        require direct org ownership, not a case share.
        """
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        _user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        _ = _mk_client(_user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Shared", org1)
        Share.objects.create(
            case=case1, organisation=org2, permissions={"read": True, "write": True}
        )

        task = Task.objects.create(case=case1, title="Task")

        # Shared org CAN PATCH (write) - case returns 200, task returns 204
        assert (
            client2.patch(
                f"/api/v1/case/{case1.id}", {"title": "shared-writer"}, format="json"
            ).status_code
            == 200
        )
        assert (
            client2.patch(
                f"/api/v1/task/{task.id}", {"title": "shared-writer"}, format="json"
            ).status_code
            == 204
        )

    # --- T4-c: merge does not widen shares, preserves AutomationRun ------------

    def test_merge_does_not_copy_shares(self, db: None) -> None:
        """Merging two cases does not copy source's shares to target (no share-widening)."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        org3 = _mk_org("Org3")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        user3 = _mk_user("u3", org3)
        client1, _ = _mk_client(user1)

        target = _mk_case("Target", org1)
        source = _mk_case("Source", org1)

        # Only SOURCE has a share to org2
        Share.objects.create(case=source, organisation=org2, permissions={"read": True})
        # Target has a share to org3 (different org)
        Share.objects.create(case=target, organisation=org3, permissions={"read": True})

        # Merge source into target
        resp = client1.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
        assert resp.status_code == 200

        # Target should STILL only have its original share (org3), NOT org2
        target.refresh_from_db()
        share_orgs = set(target.shares.values_list("organisation_id", flat=True))
        assert org2.id not in share_orgs, "merge widened shares by copying source's share to org2"
        assert org3.id in share_orgs, "target's original share was lost"

        # Org2 should NOT have access to target
        client2, _ = _mk_client(user2)
        resp = client2.get(f"/api/v1/case/{target.id}")
        assert resp.status_code == 404, "org2 gained access via merge share-widening"

        # Org3 should still have access
        client3, _ = _mk_client(user3)
        resp = client3.get(f"/api/v1/case/{target.id}")
        assert resp.status_code == 200, "org3 lost access after merge"

    def test_merge_preserves_automation_runs(self, db: None) -> None:
        """Merging cases moves AutomationRun from source to target (none orphaned)."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        target = _mk_case("Target", org1)
        source = _mk_case("Source", org1)

        # Create an AutomationRun on the source case
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook",
            owner_org=org1,
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )
        run = AutomationRun.objects.create(
            case=source,
            playbook=playbook,
            playbook_name=playbook.name,
            trigger_event="manual",
            triggered_by="manual",
            status="Success",
            output_log="test output",
            idempotency_key="test-key-1",
        )

        # Merge source into target
        resp = client1.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
        assert resp.status_code == 200

        # Source is deleted, target remains
        assert not Case.objects.filter(pk=source.id).exists()
        target.refresh_from_db()

        # AutomationRun should now point to target
        run.refresh_from_db()
        assert run.case_id == target.id, "AutomationRun was orphaned (case_id not updated)"

    def test_merge_preserves_procedures(self, db: None) -> None:
        """Merging cases moves Procedure from source to target (CASCADE would delete)."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        target = _mk_case("Target", org1)
        source = _mk_case("Source", org1)

        # Create a Procedure on the source case
        proc = Procedure.objects.create(
            case=source, pattern_id="T1059", pattern_name="Test Procedure"
        )

        # Merge source into target
        resp = client1.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
        assert resp.status_code == 200

        # Procedure should now point to target
        proc.refresh_from_db()
        assert proc.case_id == target.id, "Procedure was deleted by CASCADE (not re-parented)"

    # --- T4-d: playbook_run requires visibility --------------------------------

    def test_playbook_run_cross_tenant_case_returns_404(self, db: None) -> None:
        """POST /api/v1/playbook/{id}/run with cross-tenant case → 404, no run created."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        case2 = _mk_case("Case2", org2)
        playbook = Playbook.objects.create(
            name="test-playbook",
            owner_org=org1,
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant case → 404
        resp = client1.post(
            f"/api/v1/playbook/{playbook.id}/run", {"case": str(case2.id)}, format="json"
        )
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # No AutomationRun created
        assert AutomationRun.objects.count() == 0

    def test_playbook_run_cross_tenant_observable_returns_404(self, db: None) -> None:
        """POST /api/v1/playbook/{id}/run with cross-tenant observable → 404, no run created."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        obs2 = _mk_observable("10.0.0.2", org2)
        playbook = Playbook.objects.create(
            name="test-playbook",
            owner_org=org1,
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant observable → 404
        resp = client1.post(
            f"/api/v1/playbook/{playbook.id}/run", {"observable": str(obs2.id)}, format="json"
        )
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # No AutomationRun created
        assert AutomationRun.objects.count() == 0

    def test_playbook_run_own_org_returns_202(self, db: None) -> None:
        """POST /api/v1/playbook/{id}/run with own-org case/observable → 202, run created."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        case1 = _mk_case("Case1", org1)
        playbook = Playbook.objects.create(
            name="test-playbook",
            owner_org=org1,
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Own-org case → 202
        resp = client1.post(
            f"/api/v1/playbook/{playbook.id}/run", {"case": str(case1.id)}, format="json"
        )
        assert resp.status_code == 202
        assert AutomationRun.objects.count() == 1
        run = AutomationRun.objects.first()
        assert run.case_id == case1.id
        assert run.playbook_id == playbook.id


# =============================================================================
# P6 — Sharing authorization (T6-a, T6-b)
# =============================================================================
#
# A share is additive and default-deny: it grants exactly one tenant read (and optionally write),
# and only the case's *owner* org may manage the share ACL itself. A write-share holder can mutate
# the case body (P4) but must not be able to grant itself or anyone else more, nor revoke the
# owner's grants. Refusals are the uniform 404 — never 403 — so there is no existence oracle.


class TestP6SharingAuthz:
    """Plan §9 P6: grant/revoke is owner-org only; shares stay additive/default-deny."""

    def _setup(self) -> None:
        self.org_a = _mk_org("Owner Org A")
        self.org_b = _mk_org("Shared Org B")
        self.org_c = _mk_org("Third Org C")
        self.owner = _mk_user("p6-owner", self.org_a)
        self.member_b = _mk_user("p6-member-b", self.org_b)
        self.owner_client, _ = _mk_client(self.owner)
        self.b_client, _ = _mk_client(self.member_b)
        self.case = _mk_case("Owner case", self.org_a)

    def _grant_b(self, *, write: bool) -> Share:
        resp = self.owner_client.post(
            f"/api/v1/case/{self.case.id}/shares",
            {"shares": [{"organisation": str(self.org_b.id), "permissions": {"write": write}}]},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        return Share.objects.get(case=self.case, organisation=self.org_b)

    def _grant_c_body(self) -> dict:
        return {"shares": [{"organisation": str(self.org_c.id), "permissions": {"write": True}}]}

    # --- T6-a: non-owner cannot grant/revoke; owner can ----------------------

    def test_t6a_non_owner_with_readwrite_share_cannot_grant_or_revoke(self, db: None) -> None:
        self._setup()
        self._grant_b(write=True)
        assert Share.objects.filter(case=self.case).count() == 1

        # POST (add a share for org C) → 404, nothing created.
        resp = self.b_client.post(
            f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
        )
        assert resp.status_code == 404, resp.content
        assert resp.json()["type"] == "NotFoundError"
        assert not Share.objects.filter(case=self.case, organisation=self.org_c).exists()

        # PUT (replace the whole set) → 404, original set intact.
        resp = self.b_client.put(
            f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
        )
        assert resp.status_code == 404, resp.content
        assert Share.objects.filter(case=self.case, organisation=self.org_b).exists()
        assert not Share.objects.filter(case=self.case, organisation=self.org_c).exists()
        assert Share.objects.filter(case=self.case).count() == 1

        # DELETE (revoke its own share) → 404, row retained.
        share_b = Share.objects.get(case=self.case, organisation=self.org_b)
        resp = self.b_client.delete(f"/api/v1/case/{self.case.id}/share/{share_b.id}")
        assert resp.status_code == 404, resp.content
        assert Share.objects.filter(pk=share_b.id).exists()

    def test_t6a_owner_org_can_grant_replace_and_revoke(self, db: None) -> None:
        self._setup()
        share_b = self._grant_b(write=True)
        assert Share.objects.filter(case=self.case, organisation=self.org_b).count() == 1

        # PUT replaces the whole set with a read grant to org C.
        resp = self.owner_client.put(
            f"/api/v1/case/{self.case.id}/shares",
            {"shares": [{"organisation": str(self.org_c.id), "permissions": {"write": False}}]},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        assert not Share.objects.filter(pk=share_b.id).exists()
        share_c = Share.objects.get(case=self.case, organisation=self.org_c)

        # DELETE revokes org C.
        resp = self.owner_client.delete(f"/api/v1/case/{self.case.id}/share/{share_c.id}")
        assert resp.status_code == 204, resp.content
        assert not Share.objects.filter(case=self.case).exists()

    # --- T6-b: additive / default-deny preserved -----------------------------

    def test_t6b_readonly_share_holder_cannot_grant_or_revoke(self, db: None) -> None:
        self._setup()
        share_b = self._grant_b(write=False)

        assert (
            self.b_client.post(
                f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
            ).status_code
            == 404
        )
        assert (
            self.b_client.put(
                f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
            ).status_code
            == 404
        )
        assert (
            self.b_client.delete(f"/api/v1/case/{self.case.id}/share/{share_b.id}").status_code
            == 404
        )

        # The share set is unchanged: only the read grant to org B.
        assert Share.objects.filter(case=self.case).count() == 1
        assert Share.objects.get(pk=share_b.id).can_write is False

    def test_t6b_shared_read_grants_read_not_write_until_write_granted(self, db: None) -> None:
        self._setup()

        # Default-deny: with no share, org B has no access at all.
        assert self.b_client.get(f"/api/v1/case/{self.case.id}").status_code == 404

        self._grant_b(write=False)

        # Read is open; write is refused with the uniform 404.
        assert self.b_client.get(f"/api/v1/case/{self.case.id}").status_code == 200
        assert (
            self.b_client.patch(
                f"/api/v1/case/{self.case.id}", {"title": "hack"}, format="json"
            ).status_code
            == 404
        )

        # Owner upgrades org B to readwrite.
        resp = self.owner_client.put(
            f"/api/v1/case/{self.case.id}/shares",
            {"shares": [{"organisation": str(self.org_b.id), "permissions": {"write": True}}]},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        # Additive: read still works, and write is now open.
        assert self.b_client.get(f"/api/v1/case/{self.case.id}").status_code == 200
        assert (
            self.b_client.patch(
                f"/api/v1/case/{self.case.id}", {"title": "allowed"}, format="json"
            ).status_code
            == 200
        )
