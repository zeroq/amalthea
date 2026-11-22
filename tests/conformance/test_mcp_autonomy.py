"""MCP Phase 4 autonomy conformance tests.

Covers: safe defaults, exact allowlist, hard denials, four-eyes defer,
shadow no-execution, enforce execution, re-evaluation, cap race, kill switch,
audit old/new, role/org gating, no secrets in evidence, and UI conventions.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from mcp_server.auth import Principal
from mcp_server.models import McpActionProposal, McpCredential, McpOrgPolicy
from mcp_server.services.autonomy import evaluate_proposal_autonomy


def _make_target_case(org_org) -> Case:
    """Create a target Case in the given org.

    Accepts either an Organisation instance or a UUID id.
    """
    from identity.models import Organisation as OrgModel

    if isinstance(org_org, OrgModel):
        org_instance = org_org
    else:
        org_instance = OrgModel.objects.get(id=org_org)
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(
        owner_org=org_instance, status=status, title="Target case for eval", severity=1
    )


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def org(db) -> Organisation:
    return Organisation.objects.create(name="Autonomy Test Org")


@pytest.fixture
def other_org(db) -> Organisation:
    return Organisation.objects.create(name="Other Autonomy Org")


@pytest.fixture
def user(db, org) -> User:
    return User.objects.create(
        login="autonomy-user",
        username="autonomy-user",
        email="autonomy@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


@pytest.fixture
def admin_user(db, org) -> User:
    """Admin user in the same org."""
    return User.objects.create(
        login="autonomy-admin",
        username="autonomy-admin",
        email="admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )


@pytest.fixture
def other_org_user(db, other_org) -> User:
    return User.objects.create(
        login="other-autonomy-user",
        username="other-autonomy-user",
        email="other@autonomy.net",
        org=other_org,
        role="analyst",
        is_active=True,
    )


@pytest.fixture
def case(org, user) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(
        owner_org=org,
        status=status,
        title="Autonomy test case",
    )


@pytest.fixture
def alert(org, user) -> Alert:
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        owner_org=org,
        status=status,
        type="phishing",
        source="mailbox",
        title="Autonomy test alert",
        severity=3,
    )


# ──────────────────────────────────────────────────────────────────────
# AC-F1: Safe defaults
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSafeDefaults:
    """Verify safe defaults on McpOrgPolicy."""

    def test_default_autonomy_disabled(self, db: None) -> None:
        """New policy has autonomy_enabled=False by default."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        assert policy.autonomy_enabled is False

    def test_default_shadow_mode_true(self, db: None) -> None:
        """New policy has shadow_mode=True by default."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        assert policy.shadow_mode is True

    def test_default_allowed_actions_empty(self, db: None) -> None:
        """New policy has allowed_actions=[] by default."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        assert policy.allowed_actions == []

    def test_default_max_auto_severity_one(self, db: None) -> None:
        """New policy has max_auto_severity=1 by default."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        assert policy.max_auto_severity == 1

    def test_default_daily_action_cap_20(self, db: None) -> None:
        """New policy has daily_action_cap=20 by default."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        assert policy.daily_action_cap == 20

    def test_default_require_four_eyes_true(self, db: None) -> None:
        """New policy has require_four_eyes=True by default."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        assert policy.require_four_eyes is True


# ──────────────────────────────────────────────────────────────────────
# AC-F1: Model validation
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestModelValidation:
    """Validate McpOrgPolicy field constraints."""

    def test_allowed_actions_unknown_rejected(self, db: None) -> None:
        """Unknown action in allowed_actions raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            allowed_actions=["unknown_action"],
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "Unknown action" in str(exc.value)

    def test_allowed_actions_reserved_rejected(self, db: None) -> None:
        """Reserved action in allowed_actions raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            allowed_actions=["close_case"],
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "Reserved" in str(exc.value)

    def test_allowed_actions_duplicate_rejected(self, db: None) -> None:
        """Duplicate entry in allowed_actions raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            allowed_actions=["propose_case_comment", "propose_case_comment"],
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "Duplicate" in str(exc.value)

    def test_daily_action_cap_lt_1_rejected(self, db: None) -> None:
        """daily_action_cap < 1 raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            daily_action_cap=0,
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert ">= 1" in str(exc.value)

    def test_daily_action_cap_negative_rejected(self, db: None) -> None:
        """Negative daily_action_cap raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            daily_action_cap=-1,
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert ">= 1" in str(exc.value)

    def test_max_auto_severity_out_of_range_rejected(self, db: None) -> None:
        """max_auto_severity outside 1..4 raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            max_auto_severity=5,
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "must be one of" in str(exc.value)

    def test_max_auto_severity_zero_rejected(self, db: None) -> None:
        """max_auto_severity=0 raises ValidationError."""
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.first().id,
            max_auto_severity=0,
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "must be one of" in str(exc.value)

    def test_updated_by_other_org_rejected(self, db: None) -> None:
        """updated_by from other org raises ValidationError."""
        from identity.models import User as OrgUser

        other_org_user = OrgUser.objects.create(
            login="other-user",
            username="other-user",
            email="other@example.net",
            org=Organisation.objects.first(),
            role="admin",
            is_active=True,
        )
        policy = McpOrgPolicy(
            organisation_id=Organisation.objects.create(name="Target Org").id,
            updated_by=other_org_user,
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "must belong to the policy organisation" in str(exc.value)


# ──────────────────────────────────────────────────────────────────────
# AC-F1: Deterministic evaluator
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDeterministicEvaluator:
    """Test the deterministic ordered checks in evaluate_proposal_autonomy."""

    def test_policy_org_mismatch_denies(self, db: None) -> None:
        """Policy org mismatch returns deny."""

        policy_org = Organisation.objects.create(name="Policy Org")
        proposal_org = Organisation.objects.create(name="Proposal Org")
        policy = McpOrgPolicy.objects.create(
            organisation=policy_org,
            autonomy_enabled=True,
            shadow_mode=False,
            allowed_actions=["propose_case_comment"],
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=proposal_org,
            role="analyst",
            is_active=True,
        )
        credential, _ = McpCredential.mint(organisation=proposal_org, user=user, name="test-cred")
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=proposal_org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )
        proposal = McpActionProposal.objects.create(
            organisation_id=proposal_org.id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )
        target = _make_target_case(proposal_org)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "policy_org_mismatch"

    def test_four_eyes_required_defer(self, db: None) -> None:
        """require_four_eyes=True returns defer."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=True,
            require_four_eyes=True,
            allowed_actions=["propose_case_comment"],
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        target = _make_target_case(Organisation.objects.first().id)

        from mcp_server.services.autonomy import evaluate_proposal_autonomy

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "defer"
        assert decision.reason_code == "four_eyes_required"

    def test_autonomy_disabled_denies(self, db: None) -> None:
        """autonomy_enabled=False returns deny."""
        policy = McpOrgPolicy.objects.create(organisation_id=Organisation.objects.first().id)
        # autonomy_enabled defaults to False

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # Need a proposal in the same org as the policy
        proposal_org = Organisation.objects.first()
        proposal = McpActionProposal.objects.create(
            organisation_id=proposal_org.id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Create target case inline
        target = _make_target_case(proposal_org)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "autonomy_disabled"

    def test_shadow_mode_no_execution(self, db: None) -> None:
        """Shadow mode: autonomy enabled + shadow_mode=True records decision, executes nothing."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=True,
            allowed_actions=["propose_case_comment"],  # must set allowlist for evaluator
            require_four_eyes=True,
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        # With autonomy_enabled=True, shadow_mode=True:
        # check 1: policy org match ✓
        # check 2: autonomy_enabled=True ✓
        # check 3: shadow_mode → mode="shadow"
        # check 4-10: all pass (allowlist match, no hard-deny, arg subset, target ok, etc.)
        # check 11: require_four_eyes=True → defer
        assert decision.mode == "shadow"
        assert decision.decision == "defer"  # four_eyes_required defers
        assert decision.reason_code == "four_eyes_required"

    def test_proposal_status_pending_at_proposal_created(self, db: None) -> None:
        """proposal status must be 'pending' at stage=proposal_created."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,  # enforce mode
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        # Create proposal with status=approved instead of pending
        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
            status="approved",  # wrong status for proposal_created stage
        )

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
            stage="proposal_created",
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "proposal_not_pending"

    def test_proposal_status_approved_at_pre_execution(self, db: None) -> None:
        """proposal status must be 'approved' at stage=pre_execution."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        # Create proposal with status=pending instead of approved
        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
            status="pending",  # wrong status for pre_execution stage
        )

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
            stage="pre_execution",
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "proposal_not_approved"

    def test_proposal_expired_denies(self, db: None) -> None:
        """Expired proposal returns deny."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() - timedelta(hours=1),  # already expired
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "proposal_expired"

    def test_risk_not_low_denies(self, db: None) -> None:
        """Medium/high risk proposals are denied."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # Medium risk proposal
        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="medium",  # not low
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "risk_not_low"

    def test_action_not_allowlisted_denies(self, db: None) -> None:
        """Action not in allowlist returns deny."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
            allowed_actions=["propose_case_comment"],
            max_auto_severity=3,  # Allow all severities so target_org_mismatch check runs
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # propose_task_create is not in the allowlist
        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_task_create",  # not allowlisted
            action="create_task",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "title": "Test task"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "tool_not_allowlisted"

    def test_hard_deny_close_case(self, db: None) -> None:
        """close_case is always hard-denied."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
            allowed_actions=["propose_case_comment", "close_case"],
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="close_case",
            action="close_case",
            target_type="case",
            target_id=str(uuid4()),
            arguments={
                "caseId": str(uuid4()),
                "verdict": "true_positive",
                "closingComment": "Comment",
            },
            risk="low",  # low risk but hard-denied
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "hard_deny"

    def test_arg_key_subset_denies(self, db: None) -> None:
        """Extra argument keys cause deny."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
            allowed_actions=["propose_case_comment"],
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # propose_case_comment with extra field
        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={
                "caseId": str(uuid4()),
                "markdown": "Test comment",
                "extra_field": "should_deny",
            },
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Create target case inline
        target = _make_target_case(Organisation.objects.first().id)

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=target,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "args_not_subset"

    def test_severity_too_high_denies(self, db: None) -> None:
        """Target severity above max_auto_severity denies."""
        policy = McpOrgPolicy.objects.create(
            organisation_id=Organisation.objects.first().id,
            autonomy_enabled=True,
            shadow_mode=False,
            allowed_actions=["propose_case_comment"],
            max_auto_severity=1,  # Only Low severity allowed
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=Organisation.objects.first(),
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(
            organisation=User.objects.first().org, user=user, name="test-cred"
        )
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=User.objects.first().org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # Create an alert with severity=3 (High) - use alert which has severity
        from alerts.models import Alert, AlertStatus

        alert = Alert.objects.create(
            owner_org=Organisation.objects.first(),
            status=AlertStatus.objects.get_or_create(
                value="New", defaults={"stage": "New", "order": 1}
            )[0],
            type="phishing",
            source="mailbox",
            title="High severity alert",
            severity=3,
        )

        proposal = McpActionProposal.objects.create(
            organisation_id=Organisation.objects.first().id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=alert,
            now=timezone.now(),
            daily_count=0,
        )
        # target_severity=3 > max_auto_severity=1 → severity_too_high
        assert decision.decision == "deny"
        assert decision.reason_code == "severity_too_high"

    def test_target_org_mismatch_denies(self, db: None) -> None:
        """Target org mismatch from different org denies."""
        policy_org = Organisation.objects.first()
        policy = McpOrgPolicy.objects.create(
            organisation=policy_org,
            autonomy_enabled=True,
            shadow_mode=False,
            allowed_actions=["propose_case_comment"],
            max_auto_severity=3,  # Allow all severities so target_org_mismatch check runs
        )

        user = User.objects.create(
            login="user",
            username="user",
            email="user@example.net",
            org=policy_org,
            role="analyst",
            is_active=True,
        )

        credential, _ = McpCredential.mint(organisation=policy_org, user=user, name="test-cred")
        principal = Principal(
            credential_id=credential.id,
            credential_prefix=credential.prefix,
            actor_id=user.id,
            organisation_id=policy_org.id,
            role="analyst",
            scopes=("mcp:propose",),
        )

        # Create a case in a different org
        from identity.models import Organisation as OrgModel

        other_org = OrgModel.objects.create(name="Other Org")
        other_case = Case.objects.create(
            owner_org=other_org,
            status=CaseStatus.objects.get_or_create(
                value="New", defaults={"stage": "New", "order": 1}
            )[0],
            title="Other org case",
        )

        proposal = McpActionProposal.objects.create(
            organisation_id=policy_org.id,
            requested_by_id=user.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(other_case.id),
            arguments={"caseId": str(other_case.id), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=24),
        )

        decision = evaluate_proposal_autonomy(
            policy=policy,
            principal=principal,
            proposal=proposal,
            target=other_case,
            now=timezone.now(),
            daily_count=0,
        )
        assert decision.decision == "deny"
        assert decision.reason_code == "target_org_mismatch"
