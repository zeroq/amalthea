"""MCP credentials, invocation audit rows and Phase 2 proposal rows.

Plaintext tokens are returned exactly once by :func:`mcp_server.credentials.mint`;
only the Argon2 hash is persisted. Phase 2 adds scoped propose/summary tools and
an explicit human approval model. Phase 3 adds OAuth 2.1 resource-server binding.
Phase 4 adds org-scoped policy-bounded autonomy.

Phase 4 imports
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.enums import SEVERITY_CHOICES, in_values
from identity.models import AuditLog, Organisation, User

PHASE1_SCOPES: tuple[str, ...] = ("mcp:read",)
PHASE2_SCOPES: tuple[str, ...] = ("mcp:propose", "mcp:summary")
RESERVED_SCOPES: tuple[str, ...] = ("mcp:write", "mcp:admin")

_PASSWORD_HASHER = PasswordHasher()

INVOCATION_STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    ("ok", "ok"),
    ("denied", "denied"),
    ("not_found", "not_found"),
    ("invalid", "invalid"),
    ("rate_limited", "rate_limited"),
    ("error", "error"),
)

# Phase 3 OAuth scope constants
PHASE3_OAUTH_SCOPES: tuple[str, ...] = ("mcp:read",)
RESERVED_OAUTH_SCOPES: tuple[str, ...] = ("mcp:write", "mcp:admin")


def hash_secret(plaintext: str) -> str:
    """Return an Argon2 hash.  Plaintext never leaves this module."""
    return _PASSWORD_HASHER.hash(plaintext)


def verify_secret(secret_hash: str, plaintext: str) -> bool:
    """Constant-time verification against the stored Argon2 hash.

    There is deliberately no plaintext/constant-time fallback: a value that is
    not a valid Argon2 hash is a data-integrity error, not a valid credential.
    """
    try:
        return _PASSWORD_HASHER.verify(secret_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError, ValueError, TypeError):
        return False


class McpCredential(models.Model):
    """An organisation-scoped static bearer credential for one service user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="mcp_credentials",
        help_text="Tenant this credential is permanently bound to.",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="mcp_credentials",
        help_text="Effective service user; must belong to the credential organisation.",
    )
    name = models.CharField(max_length=100)
    prefix = models.CharField(
        max_length=12,
        unique=True,
        help_text="First 12 characters of the token, used for O(1) lookup.",
    )
    secret_hash = models.CharField(max_length=255)
    scopes = models.JSONField(
        default=list,
        help_text="Granted scopes. Phase 1 accepts only ['mcp:read'].",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_credential"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organisation", "-created_at"], name="mcp_org_cre_idx"),
        ]

    def __str__(self) -> str:
        return f"McpCredential({self.name})"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and timezone.now() >= self.expires_at

    @property
    def is_disabled(self) -> bool:
        return self.disabled_at is not None

    @property
    def is_active(self) -> bool:
        return not (self.is_revoked or self.is_expired or self.is_disabled)

    def clean(self) -> None:
        super().clean()
        if self.user_id and self.organisation_id and self.user.org_id != self.organisation_id:
            raise ValidationError(
                {"user": "The service user must belong to the credential organisation."}
            )
        if not self.user_id or not self.user.is_active:
            raise ValidationError({"user": "The service user must be active."})
        scopes = self.scopes or []
        allowed: set[str] = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
        if not isinstance(scopes, list) or any(scope not in allowed for scope in scopes):
            raise ValidationError({"scopes": f"Allowed scopes are: {', '.join(sorted(allowed))}"})
        if not scopes:
            raise ValidationError({"scopes": "At least one scope is required."})
        viewer_role = getattr(self.user, "role", "") == "viewer"
        proposed_summary = any(scope in PHASE2_SCOPES for scope in scopes)
        if viewer_role and proposed_summary:
            raise ValidationError(
                {
                    "scopes": (
                        "Viewer/read-only service users cannot be granted "
                        "mcp:propose or mcp:summary."
                    )
                }
            )

    def verify(self, presented: str) -> bool:
        """True only for an active credential whose Argon2 hash matches."""
        if not self.is_active:
            return False
        return verify_secret(self.secret_hash, presented)

    @classmethod
    def mint(
        cls,
        *,
        organisation: Organisation,
        user: User,
        name: str,
        created_by: User | None = None,
        expires_at: Any = None,
        scopes: list[str] | None = None,
    ) -> tuple[McpCredential, str]:
        """Create a credential and return ``(credential, plaintext)`` exactly once.

        A 32-byte URL-safe token is generated; the first 12 characters are the
        lookup prefix.  Prefix collisions are retried with a fresh token.
        """
        if user.org_id != organisation.id:
            raise ValidationError(
                {"user": "The service user must belong to the credential organisation."}
            )
        if not user.is_active:
            raise ValidationError({"user": "The service user must be active."})
        if not name.strip():
            raise ValidationError({"name": "A credential name is required."})

        if scopes is None:
            scopes = list(PHASE1_SCOPES)
        allowed: set[str] = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
        if not isinstance(scopes, list) or any(scope not in allowed for scope in scopes):
            raise ValidationError({"scopes": f"Allowed scopes are: {', '.join(sorted(allowed))}"})
        if not scopes:
            raise ValidationError({"scopes": "At least one scope is required."})
        viewer_role = getattr(user, "role", "") == "viewer"
        if viewer_role and any(scope in PHASE2_SCOPES for scope in scopes):
            raise ValidationError(
                {
                    "scopes": (
                        "Viewer/read-only service users cannot be granted "
                        "mcp:propose or mcp:summary."
                    )
                }
            )

        plaintext = ""
        prefix = ""
        for _ in range(8):
            plaintext = secrets.token_urlsafe(32)
            prefix = plaintext[:12]
            if not cls.objects.filter(prefix=prefix).exists():
                break
        else:  # pragma: no cover - astronomically unlikely
            raise RuntimeError("Could not allocate a unique MCP credential prefix.")

        credential = cls(
            organisation=organisation,
            user=user,
            name=name.strip(),
            prefix=prefix,
            secret_hash=hash_secret(plaintext),
            scopes=list(scopes),
            expires_at=expires_at,
            created_by=created_by,
        )
        credential.full_clean()
        credential.save()
        return credential, plaintext

    @classmethod
    def lookup(cls, prefix: str) -> McpCredential | None:
        return cls.objects.select_related("user", "organisation").filter(prefix=prefix).first()


class McpInvocation(models.Model):
    """One audit row per authenticated MCP tool call."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.CharField(max_length=100, db_index=True)
    credential = models.ForeignKey(
        McpCredential,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invocations",
    )
    auth_mode = models.CharField(
        max_length=16,
        choices=[
            ("static", "static"),
            ("oauth", "oauth"),
        ],
        default="static",
        help_text="Authentication mode used for this invocation.",
    )
    oauth_binding = models.ForeignKey(
        "McpOAuthBinding",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invocations",
        help_text="OAuth binding attribution for OAuth-mode invocations.",
    )
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    organisation = models.ForeignKey(
        Organisation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    tool_name = models.CharField(max_length=100, db_index=True)
    arguments_hash = models.CharField(max_length=64)
    arguments_redacted = models.JSONField(default=dict)
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=list(INVOCATION_STATUS_CHOICES))
    error_code = models.CharField(max_length=100, blank=True, default="")
    duration_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mcp_invocation"
        ordering = ["-created_at"]
        constraints = [
            in_values(
                "mcp_invocation_status_valid",
                "status",
                tuple(value for value, _ in INVOCATION_STATUS_CHOICES),
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "-created_at"], name="mcp_inv_org_cre_idx"),
            models.Index(fields=["actor", "-created_at"], name="mcp_inv_actor_cre_idx"),
            models.Index(fields=["tool_name", "-created_at"], name="mcp_inv_tool_cre_idx"),
            models.Index(fields=["status", "-created_at"], name="mcp_inv_status_cre_idx"),
        ]

    def __str__(self) -> str:
        return f"McpInvocation({self.tool_name}) status={self.status}"


class McpActionProposal(models.Model):
    """A proposed domain mutation awaiting explicit human approval."""

    class RiskChoices(models.TextChoices):
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    class StatusChoices(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        DECLINED = "declined"
        EXPIRED = "expired"
        EXECUTED = "executed"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="mcp_action_proposals",
        help_text="Tenant this proposal is bound to.",
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Effective agent service user that created the proposal.",
    )
    credential = models.ForeignKey(
        McpCredential,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proposals",
        help_text="Credential that created the proposal.",
    )
    tool_name = models.CharField(
        max_length=100, help_text="Canonical tool name, e.g. propose_case_update."
    )
    action = models.CharField(max_length=100, help_text="Canonical service action name.")
    target_type = models.CharField(max_length=32, help_text="Case, alert, observable or task.")
    target_id = models.CharField(max_length=64, help_text="UUID of the target record.")
    arguments = models.JSONField(
        help_text="Exact validated arguments after canonicalisation; immutable after creation."
    )
    arguments_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 of the canonical JSON representation of arguments.",
    )
    risk = models.CharField(
        max_length=10,
        choices=list(RiskChoices.choices),
        default=RiskChoices.LOW,
        help_text="low / medium / high. High-risk requires admin decision.",
    )
    status = models.CharField(
        max_length=16,
        choices=list(StatusChoices.choices),
        default=StatusChoices.PENDING,
        help_text="pending / approved / declined / expired / executed / failed.",
    )
    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        help_text="Deterministic per credential/tool/target/args; prevents duplicate proposals.",
    )
    expires_at = models.DateTimeField(
        help_text="Default now + 24h. Expired proposals cannot be approved or executed."
    )
    decided_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Human decision maker who approved or declined.",
    )
    decided_at = models.DateTimeField(
        null=True, blank=True, help_text="When the decision was made."
    )
    executed_at = models.DateTimeField(null=True, blank=True, help_text="When execution completed.")
    result = models.JSONField(
        default=dict,
        help_text="Bounded result; no secrets/raw payload. Serialised to audit log.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_action_proposal"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organisation", "status", "-created_at"],
                name="mcp_proposal_org_status_idx",
            ),
            models.Index(
                fields=["requested_by", "-created_at"],
                name="mcp_proposal_requestor_idx",
            ),
            models.Index(
                fields=["target_type", "target_id", "status"],
                name="mcp_proposal_target_status_idx",
            ),
            models.Index(fields=["expires_at"], name="mcp_proposal_expiry_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"], name="uniq_mcp_proposal_idempotency_key"
            ),
        ]

    def __str__(self) -> str:
        return (
            f"McpActionProposal({self.tool_name} {self.target_type}:{self.target_id} {self.status})"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Ensure arguments are immutable after first save and audit creation."""
        created = self._state.adding
        if created:
            canonical = json.dumps(
                self.arguments or {}, sort_keys=True, default=str, separators=(",", ":")
            )
            self.arguments_hash = hashlib.sha256(canonical.encode()).hexdigest()
        else:
            orig = McpActionProposal.objects.get(pk=self.pk)
            if orig.arguments != self.arguments or orig.arguments_hash != self.arguments_hash:
                raise ValidationError(
                    {"arguments": "Arguments are immutable after proposal creation."}
                )
        super().save(*args, **kwargs)
        if created:
            requester = None
            if self.requested_by_id is not None:
                requester = (
                    User.objects.filter(pk=self.requested_by_id).select_related("org").first()
                )
            if (
                requester is not None
                and Organisation.objects.filter(pk=self.organisation_id).exists()
            ):
                AuditLog.objects.create(
                    actor=requester,
                    actor_org=requester.org,
                    target_org_id=self.organisation_id,
                    target_type="McpActionProposal",
                    target_id=str(self.id),
                    action="mcp_proposal.created",
                    details={
                        "proposal_id": str(self.id),
                        "tool_name": self.tool_name,
                        "action": self.action,
                        "risk": self.risk,
                        "target_type": self.target_type,
                        "target_id": str(self.target_id),
                    },
                )

    def clean(self) -> None:
        super().clean()
        if not self.idempotency_key:
            raise ValidationError({"idempotency_key": "Idempotency key is required."})
        canonical = json.dumps(
            self.arguments or {}, sort_keys=True, default=str, separators=(",", ":")
        )
        if self.arguments_hash != hashlib.sha256(canonical.encode()).hexdigest():
            raise ValidationError(
                {"arguments_hash": "arguments_hash does not match canonical SHA-256 of arguments."}
            )
        if self.target_type not in ("case", "alert", "observable", "task"):
            raise ValidationError({"target_type": f"Unknown target type {self.target_type}."})

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            self.StatusChoices.DECLINED,
            self.StatusChoices.EXPIRED,
            self.StatusChoices.EXECUTED,
            self.StatusChoices.FAILED,
        )


class McpOrgPolicy(models.Model):
    """Organisation-scoped policy that controls MCP autonomy behaviour.

    Defaults: autonomy_enabled=False, shadow_mode=True, low severity cap,
    daily cap of 20, four-eyes required.  Autonomy is off by default; no
    organisation starts in enforce mode.  Policy is created lazily on first
    admin view or first evaluation.
    """

    organisation = models.OneToOneField(
        Organisation,
        on_delete=models.PROTECT,
        related_name="mcp_policy",
        help_text="Organisation this policy belongs to. One policy per org.",
    )
    autonomy_enabled = models.BooleanField(
        default=False,
        help_text="Master kill switch: when False, autonomy is disabled regardless of shadow_mode.",
    )
    shadow_mode = models.BooleanField(
        default=True,
        help_text="When True, decisions are recorded but nothing is executed. "
        "Always True at org start; switched to False only via explicit admin UI "
        "confirmation after shadow evaluation.",
    )
    allowed_actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Exact allowlist entries from server-side schema registry. "
        "No wildcards, prefixes, unknown, reserved, destructive, or duplicate entries. "
        "Validated on save and on every policy update.  Empty list is valid (autonomy "
        "will be effectively disabled until admin sets allowed actions).",
    )
    max_auto_severity = models.SmallIntegerField(
        default=1,
        help_text="Maximum target severity eligible for autonomy. Must be within "
        "SEVERITY_MIN..SEVERITY_MAX (1=Low, 4=Critical). Default 1.",
    )
    daily_action_cap = models.PositiveIntegerField(
        default=20,
        help_text="CAP per org per UTC day. Only enforce-mode pre_execution decisions "
        "with execution_outcome=executed consume quota. Default 20.",
    )
    require_four_eyes = models.BooleanField(
        default=True,
        help_text="When True, autonomy returns defer; the proposal stays pending for a human. "
        "Agent self-approval does not satisfy four-eyes. Default True.",
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_policy_editor",
        help_text="Last human editor of this policy. When set, must belong to the policy organisation.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "MCP organisation policy"
        verbose_name_plural = "MCP organisation policies"

    def __str__(self) -> str:
        return f"McpOrgPolicy(org={self.organisation_id})"

    def clean(self) -> None:
        super().clean()
        # One policy per org is enforced by OneToOne, but validate allowed_actions content
        if not isinstance(self.allowed_actions, list):
            raise ValidationError({"allowed_actions": "allowed_actions must be a list."})
        # Reject wildcards, duplicates, unknown, reserved, or destructive entries
        # Allow empty list (blank=True) - autonomy will be effectively disabled until admin sets actions
        if self.allowed_actions:
            reserved = {
                "close_case",
                "run_playbook",
                "escalate_alert",
                "apply_case_update",
                "add_observable_with_status",
                "apply_alert_triage",
            }
            known_safe = {"propose_case_comment", "propose_task_create"}
            seen = set()
            for entry in self.allowed_actions:
                if not isinstance(entry, str):
                    raise ValidationError(
                        {
                            "allowed_actions": f"All allowed_actions entries must be strings. Found non-string: {entry}"
                        }
                    )
                if entry in reserved:
                    raise ValidationError(
                        {
                            "allowed_actions": f"Reserved action '{entry}' cannot be in allowed_actions."
                        }
                    )
                if entry in seen:
                    raise ValidationError(
                        {"allowed_actions": f"Duplicate entry '{entry}' in allowed_actions."}
                    )
                seen.add(entry)
                # Only known safe entries are allowed; reject unknown/reserved
                if entry not in known_safe:
                    raise ValidationError(
                        {
                            "allowed_actions": f"Unknown action '{entry}' in allowed_actions. "
                            f"Allowed: {', '.join(sorted(known_safe))}"
                        }
                    )
        # Validate daily_action_cap >= 1
        if self.daily_action_cap < 1:
            raise ValidationError({"daily_action_cap": "daily_action_cap must be >= 1."})

        # Validate max_auto_severity within range
        severity_values = {s[0] for s in SEVERITY_CHOICES}
        if self.max_auto_severity not in severity_values:
            raise ValidationError(
                {
                    "max_auto_severity": f"max_auto_severity must be one of {', '.join(str(s[0]) for s in SEVERITY_CHOICES)}."
                }
            )

        # When updated_by is set, must belong to the policy organisation
        if self.updated_by_id is not None:
            updated_by = self.updated_by
            if updated_by is None or updated_by.org_id != self.organisation_id:
                raise ValidationError(
                    {"updated_by": "The updated_by user must belong to the policy organisation."}
                )


class McpPolicyDecision(models.Model):
    """Append-only decision log for MCP autonomy evaluation.

    One row per evaluation. Never deleted. execution_outcome may transition
    once from pending to terminal (executed/failed). Shadow rows are
    not_applicable. Used for shadow evidence and UTC-day cap counting.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="mcp_policy_decisions",
        help_text="Tenant this decision belongs to.",
    )
    policy = models.ForeignKey(
        McpOrgPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Policy in force at the time of this decision. May be null if policy was deleted.",
    )
    proposal = models.ForeignKey(
        "McpActionProposal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Source proposal that was evaluated.",
    )
    tool_name = models.CharField(
        max_length=100,
        help_text="Canonical tool name, e.g. propose_case_comment.",
    )
    action = models.CharField(
        max_length=100,
        help_text="Canonical service action name, e.g. append_timeline_event.",
    )
    target_type = models.CharField(
        max_length=32,
        help_text="Case, alert, observable or task.",
    )
    target_id = models.CharField(
        max_length=64,
        help_text="UUID of the target record.",
    )
    risk = models.CharField(
        max_length=10,
        help_text="low / medium / high. Proposals with medium/high are always denied/deferred.",
    )
    mode = models.CharField(
        max_length=16,
        help_text="shadow or enforce.",
    )
    decision = models.CharField(
        max_length=16,
        help_text="allow, deny, or defer.",
    )
    reason_code = models.CharField(
        max_length=32,
        help_text="Stable machine-readable reason for the decision.",
    )
    evidence = models.JSONField(
        default=dict,
        help_text="Bounded, non-secret facts only. Never raw payloads, tokens, URLs, headers, scripts.",
    )
    policy_snapshot = models.JSONField(
        default=dict,
        help_text="Exact policy fields used for this decision at evaluation time. Bounded.",
    )
    credential_id = models.CharField(  # noqa: DJ001 - optional attribution, null means not attributable
        max_length=128,
        null=True,
        blank=True,
        help_text="Attribution: credential that created the proposal.",
    )
    oauth_binding_id = models.CharField(  # noqa: DJ001 - optional attribution, null means not applicable
        max_length=128,
        null=True,
        blank=True,
        help_text="Attribution: OAuth binding ID.",
    )
    actor_id = models.CharField(  # noqa: DJ001 - optional attribution, null means system actor
        max_length=128,
        null=True,
        blank=True,
        help_text="Attribution: actor user ID who triggered the proposal.",
    )
    stage = models.CharField(
        max_length=32,
        help_text="proposal_created or pre_execution. Distinguishes creation-time decision from executor re-check.",
    )
    execution_outcome = models.CharField(
        max_length=16,
        help_text="not_applicable | pending | executed | failed. Shadow rows are not_applicable. "
        "May transition once from pending to terminal.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "MCP policy decision"
        verbose_name_plural = "MCP policy decisions"
        ordering = ["-created_at"]
        # Append-only: no custom delete; use administrative purge only with justification.
        indexes = [
            models.Index(fields=["organisation", "-created_at"], name="mcp_pol_org_cre_idx"),
            models.Index(
                fields=["organisation", "mode", "decision", "execution_outcome", "-created_at"],
                name="mcp_pol_org_mode_exec_idx",
            ),
            models.Index(
                fields=["decision", "mode", "-created_at"],
                name="mcp_pol_decision_mode_idx",
            ),
            models.Index(
                fields=["proposal", "-created_at"],
                name="mcp_pol_proposal_cre_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"McpPolicyDecision(id={str(self.id)[:8]} org={self.organisation_id} "
            f"proposal={self.proposal_id} mode={self.mode} decision={self.decision} "
            f"stage={self.stage} outcome={self.execution_outcome})"
        )

    def clean(self) -> None:
        super().clean()
        # Enforce append-only: no deletes at model level; this constraint is
        # reinforced by the admin and migrations.
        # execution_outcome may transition once from pending to terminal;
        # no decision row is ever deleted.
        valid_outcomes = {"not_applicable", "pending", "executed", "failed"}
        if self.execution_outcome not in valid_outcomes:
            raise ValidationError(
                {
                    "execution_outcome": f"execution_outcome must be one of {', '.join(valid_outcomes)}."
                }
            )
        valid_modes = {"shadow", "enforce"}
        if self.mode not in valid_modes:
            raise ValidationError({"mode": f"mode must be one of {', '.join(valid_modes)}."})
        valid_decisions = {"allow", "deny", "defer"}
        if self.decision not in valid_decisions:
            raise ValidationError(
                {"decision": f"decision must be one of {', '.join(valid_decisions)}."}
            )
        valid_stages = {"proposal_created", "pre_execution"}
        if self.stage not in valid_stages:
            raise ValidationError({"stage": f"stage must be one of {', '.join(valid_stages)}."})

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: DJ012
        # Append-only is enforced by refusing model-level deletion, not by
        # disabling ORM saves. Callers must be able to persist decisions.
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("McpPolicyDecision rows are append-only and cannot be deleted.")


class McpOAuthBinding(models.Model):
    """One OAuth 2.1 binding from (issuer, client_id, subject) to an org/service user.

    Tokens presenting this binding are authenticated; client-supplied org claims are
    ignored — the organisation comes only from the active binding row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Human label in Settings.")
    issuer = models.CharField(
        max_length=500, help_text="Exact expected `iss`; must match configured issuer."
    )
    client_id = models.CharField(max_length=255, help_text="OAuth client identifier from the IdP.")
    subject = models.CharField(max_length=255, help_text="Expected JWT `sub`; required in v1.")
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="mcp_oauth_bindings",
        help_text="Tenant this agent is bound to.",
    )
    service_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="mcp_oauth_bindings",
        help_text="Effective actor; must belong to organisation and be active.",
    )
    allowed_scopes = models.JSONField(
        default=list,
        help_text="Subset of mcp:read, mcp:propose, mcp:summary; non-empty, no reserved scopes.",
    )
    is_active = models.BooleanField(default=True, help_text="Revocation/disable without delete.")
    last_used_at = models.DateTimeField(
        null=True, blank=True, help_text="Atomic touch at most once per minute."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Human administrator.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_oauth_binding"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "client_id", "subject"],
                name="uniq_mcp_oauth_binding_issuer_client_subject",
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "-created_at"], name="mcp_oauth_org_idx"),
            models.Index(
                fields=["issuer", "client_id", "subject"],
                name="mcp_oauth_issuer_sub_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"McpOAuthBinding({self.name} issuer={self.issuer} client={self.client_id})"

    @property
    def is_revoked(self) -> bool:
        return not self.is_active

    def clean(self) -> None:
        super().clean()
        # Service user must belong to the organisation
        if self.service_user_id and self.organisation_id:
            if self.service_user.org_id != self.organisation_id:
                raise ValidationError(
                    {"service_user": "The service user must belong to the credential organisation."}
                )
        # User must be active
        if self.service_user_id and not self.service_user.is_active:
            raise ValidationError({"service_user": "The service user must be active."})
        # Viewer service users may receive only mcp:read
        viewer_role = getattr(self.service_user, "role", "") == "viewer"
        scopes = self.allowed_scopes or []
        proposed = any(scope in PHASE2_SCOPES for scope in scopes)
        if viewer_role and proposed:
            raise ValidationError(
                {
                    "allowed_scopes": (
                        "Viewer/read-only service users cannot be granted "
                        "mcp:propose or mcp:summary."
                    )
                }
            )
        # Reject reserved scopes
        for scope in scopes:
            if scope in RESERVED_OAUTH_SCOPES:
                raise ValidationError(
                    {"allowed_scopes": f"Reserved scope '{scope}' is not allowed."}
                )
        # allowed_scopes must be non-empty
        if not scopes:
            raise ValidationError({"allowed_scopes": "At least one scope is required."})
        # All scopes must be MCP-relevant (begin with mcp:)
        allowed_scopes = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
        for scope in scopes:
            if not scope.startswith("mcp:"):
                raise ValidationError({"allowed_scopes": f"Scope '{scope}' is not an MCP scope."})
            if scope not in allowed_scopes:
                raise ValidationError({"allowed_scopes": f"Unknown MCP scope '{scope}'."})

    @classmethod
    def validate_scopes(cls, scopes: list[str]) -> set[str]:
        """Return the set of validated scopes, rejecting reserved/unknown mcp:* scopes."""
        allowed: set[str] = set(PHASE1_SCOPES) | set(PHASE2_SCOPES)
        validated: set[str] = set()
        for scope in scopes:
            if scope in RESERVED_OAUTH_SCOPES:
                raise ValidationError(
                    {"allowed_scopes": f"Reserved scope '{scope}' is not allowed."}
                )
            if not scope.startswith("mcp:"):
                # Non-MCP scopes are ignored (caller handles intersection)
                continue
            if scope in allowed:
                validated.add(scope)
            elif scope.startswith("mcp:"):
                # Unknown mcp:* scope — reject
                raise ValidationError({"allowed_scopes": f"Unknown MCP scope '{scope}'."})
        return validated
