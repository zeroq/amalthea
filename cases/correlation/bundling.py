"""Audited bundle writer and ingest auto-bundle service."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from alerts.escalation import merge_alert_into_case
from alerts.models import Alert, AlertObservable
from cases.models import Case, CaseObservable, CorrelationRule
from ingest.models import IngestionSource

from ._constants import AUTO_BUNDLE_SOURCE, MAX_CORRELATION_OBS
from ._types import AutoBundleDecision

if TYPE_CHECKING:
    from cases.models import TimelineEvent
    from identity.models import User


def record_alert_bundled(
    case: Case,
    alert: Alert,
    *,
    actor: User | None = None,
    reason: str,
    source: str,
) -> TimelineEvent:
    """
    Record an alert→case bundle in the ledger and audit log.

    Writes exactly ONE TimelineEvent (kind="correlation-bundle") and ONE AuditLog
    row (action="case.alert_bundled"). Caller owns the transaction.

    Refuses:
    - Closed case
    - Cross-org (alert.owner_org != case.owner_org)
    - Viewer actor
    - Duplicate bundle (idempotence): if a correlation-bundle event for this
      alert+case already exists, refuse to prevent double-recording on replay.

    Details carries only non-secret data: counts, correlation key, source refs.
    """
    from django.core.exceptions import PermissionDenied

    from cases.ledger import append_timeline_event
    from cases.models import TimelineEvent
    from identity.models import AuditLog
    from identity.scoping import is_viewer

    # Refuse closed case
    if case.status and case.status.stage == "Closed":
        raise PermissionDenied("Cannot bundle alert into a closed case")

    # Refuse cross-org
    if alert.owner_org_id != case.owner_org_id:
        raise PermissionDenied("Cross-org alert bundle refused")

    # Refuse viewer actor
    if actor and is_viewer(actor):
        raise PermissionDenied("Viewer role cannot bundle alerts")

    # Idempotence: check if a correlation-bundle event for this alert+case already exists
    # (This handles the case where import_alert_to_case / merge_alert_into_case already
    # linked the alert, and we're now recording the bundle - we must not record twice.)
    existing_bundle = TimelineEvent.objects.filter(
        case=case,
        kind="correlation-bundle",
        metadata__alert_id=str(alert.id),
    ).exists()
    if existing_bundle:
        raise PermissionDenied("Alert already bundled into this case")

    # Build non-secret details
    details = {
        "alert_id": str(alert.id),
        "alert_source": alert.source,
        "alert_type": alert.type,
        "alert_source_ref": alert.source_ref,
        "correlation_key": alert.correlation_key,
        "reason": reason,
        "source": source,  # "escalate" or "merge"
    }

    # Ledger entry via the single write path, so the bundle reaches live clients in the case
    # room and the choke-point scan in tests/conformance/test_realtime.py stays honest.
    event = append_timeline_event(
        case,
        title=f"Alert bundled: {alert.title}",
        kind="correlation-bundle",
        description=f"Reason: {reason}. Source: {alert.source}, type {alert.type}, ref {alert.source_ref}",
        actor=actor,
        metadata={"alert_id": str(alert.id), "reason": reason, "source": source},
    )

    # Audit log entry (idiom from cases/update.py:280-289)
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None) if actor else None,
        target_org=case.owner_org,
        action="case.alert_bundled",
        target_type="Case",
        target_id=str(case.id),
        ip=None,
        details=details,
    )

    return event


def find_auto_bundle_case(
    alert: Alert,
    *,
    rule: CorrelationRule,
    org_id: UUID,
) -> Case | None:
    """Determine whether an alert should be auto-bundled into an existing case.

    Returns the target Case if a match is found, or None if bundling should be skipped.
    The caller (auto_bundle_ingested_alert) is responsible for all pre-query skips
    (replay, already_linked, tenant mismatch, rule disabled, source disabled)
    and for wrapping the final re-check + merge in ``transaction.atomic()``.
    """
    # Correlation-key mode (default)
    if rule.match_mode == "correlation_key":
        return _find_bundle_by_correlation_key(alert, rule)

    # Shared-observable mode
    if rule.match_mode == "shared_observable":
        return _find_bundle_by_shared_observable(alert, rule)

    # Unknown mode - should not happen thanks to the DB constraint
    return None


def _find_bundle_by_correlation_key(
    alert: Alert,
    rule: CorrelationRule,
) -> Case | None:
    """Correlation-key mode: drive from Alert with exact filters/order/cap from plan §6.2."""
    from datetime import timedelta

    anchor = timezone.now()
    window_start = anchor - timedelta(minutes=rule.window_minutes)

    if not alert.correlation_key:
        return None

    from ui.pagination import PICKER_PAGE_SIZE

    candidate_alerts = (
        Alert.objects.filter(
            owner_org_id=rule.organisation_id,
            correlation_key=alert.correlation_key,
            date__gt=window_start,
            date__lte=anchor,
            case__isnull=False,
            case__owner_org_id=rule.organisation_id,
            case__closed_date__isnull=True,
            case__status__stage__in=("New", "InProgress"),
        )
        .exclude(pk=alert.pk)
        .select_related("case", "case__status")
        .order_by("-date", "-id")[:PICKER_PAGE_SIZE]
    )

    # Distinct case_id pick
    matched_case_id: UUID | None = None
    for ca in candidate_alerts:
        if ca.case_id is not None and ca.case_id != alert.pk:
            matched_case_id = ca.case_id
            break

    if matched_case_id is None:
        return None

    # Re-fetch the case
    try:
        case = Case.objects.get(pk=matched_case_id)
    except Case.DoesNotExist:
        return None

    # Defensive re-checks (caller does the final re-check + merge)
    if case.closed_date is not None:
        return None
    if case.owner_org_id != rule.organisation_id:
        return None
    if case.status is None or case.status.stage not in ("New", "InProgress"):
        return None

    return case


def _find_bundle_by_shared_observable(
    alert: Alert,
    rule: CorrelationRule,
) -> Case | None:
    """Shared-observable mode: use AlertObservable links and CaseObservable as in plan §6.3."""
    from django.db.models import Count, Max

    from ui.pagination import PICKER_PAGE_SIZE

    window = timedelta(minutes=rule.window_minutes)
    anchor = timezone.now()
    window_start = anchor - window

    # Fetch at most MAX_CORRELATION_OBS observable IDs from this alert
    observable_ids = list(
        AlertObservable.objects.filter(alert=alert).values_list("observable_id", flat=True)[
            :MAX_CORRELATION_OBS
        ]
    )

    if not observable_ids:
        return None

    matches = (
        CaseObservable.objects.filter(
            observable_id__in=observable_ids,
            observable__ignore_similarity=False,
            case__owner_org_id=rule.organisation_id,
            case__closed_date__isnull=True,
            case__status__stage__in=("New", "InProgress"),
            created_at__gt=window_start,
            created_at__lte=anchor,
        )
        .values("case_id")
        .annotate(shared_count=Count("id"), latest_match=Max("created_at"))
        .order_by("-shared_count", "-latest_match", "case_id")[:PICKER_PAGE_SIZE]
    )

    if not matches:
        return None

    # Pick the first case_id
    first_match = matches[0]
    matched_case_id: UUID = first_match["case_id"]

    # Re-fetch the case
    try:
        case = Case.objects.get(pk=matched_case_id)
    except Case.DoesNotExist:
        return None

    # Defensive re-checks (caller does the final re-check + merge)
    if case.closed_date is not None:
        return None
    if case.owner_org_id != rule.organisation_id:
        return None
    if case.status is None or case.status.stage not in ("New", "InProgress"):
        return None

    return case


def auto_bundle_ingested_alert(
    alert: Alert,
    *,
    source: IngestionSource,
    org_id: UUID,
    created: bool,
) -> AutoBundleDecision:
    """Evaluate the CorrelationRule and auto-bundle the alert into a case if eligible.

    Pre-condition: Caller has already performed these skips (see plan §6.1 #1-6):
      1. created=False → replay
      2. alert.case_id already set → already_linked
      3. alert.owner_org_id != org_id → tenant_mismatch
      4. No CorrelationRule for org_id → rule_disabled
      5. source.correlation_enabled=False → source_correlation_disabled
      6. window_minutes clamped to 1..60; PICKER_PAGE_SIZE as cap

    One ``transaction.atomic()`` encloses the final re-check + merge + record.
    Actor is None. Known exceptions (PermissionDenied, ValidationError) are caught
    and returned as skipped/failed; unexpected exceptions are logged and return failed.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Load the CorrelationRule for this org; missing/row means disabled
    rule = CorrelationRule.objects.filter(organisation_id=org_id).order_by("-created_at").first()

    # ── Pre-existing skip checks (caller's responsibility, guarded here) ──
    if not created:
        return AutoBundleDecision(
            status="skipped",
            reason="replay",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    if alert.case_id is not None:
        return AutoBundleDecision(
            status="skipped",
            reason="already_linked",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    if alert.owner_org_id != org_id:
        return AutoBundleDecision(
            status="skipped",
            reason="tenant_mismatch",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    if rule is None or not rule.enabled:
        return AutoBundleDecision(
            status="skipped",
            reason="rule_disabled",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    # Source-level kill switch
    if not source.correlation_enabled:
        return AutoBundleDecision(
            status="skipped",
            reason="source_correlation_disabled",
            mode=rule.match_mode,
            window_minutes=rule.window_minutes,
        )

    # Clamp window_minutes to 1..60 and use PICKER_PAGE_SIZE as candidate cap.
    # The in-memory copy is deliberate: an out-of-range DB value is never persisted.
    effective_window = max(1, min(60, rule.window_minutes))
    effective_rule = rule
    effective_rule.window_minutes = effective_window

    if rule.match_mode == "correlation_key" and not alert.correlation_key:
        return AutoBundleDecision(
            status="skipped",
            reason="no_correlation_key",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )

    alert_observables: list[tuple[str, str]] = []
    if rule.match_mode == "shared_observable":
        from alerts.models import AlertObservable

        alert_observables = list(
            AlertObservable.objects.filter(alert=alert).values_list(
                "observable__data_type__name", "observable__normalized_data"
            )[:5]
        )
        if not alert_observables:
            return AutoBundleDecision(
                status="skipped",
                reason="no_observables",
                mode=rule.match_mode,
                window_minutes=effective_window,
            )

    # Choose a case using the selected mode
    case = find_auto_bundle_case(alert, rule=effective_rule, org_id=org_id)

    if case is None:
        if rule.match_mode == "correlation_key" and alert.correlation_key:
            signal = f"correlation_key='{alert.correlation_key}'"
        elif rule.match_mode == "shared_observable" and alert_observables:
            obs_str = ", ".join(f"{dt}:{val}" for dt, val in alert_observables)
            signal = f"shared observable '{obs_str}'"
        else:
            signal = ""

        return AutoBundleDecision(
            status="skipped",
            reason="no_open_candidate",
            mode=rule.match_mode,
            window_minutes=effective_window,
            signal=signal,
        )

    # ── Inside one transaction.atomic(): re-fetch, re-check, merge, record ──
    from django.db import transaction

    try:
        with transaction.atomic():
            # Re-fetch both rows under lock so a concurrent merge cannot slip through.
            locked_case = Case.objects.select_for_update().get(pk=case.pk)
            fresh_alert = Alert.objects.select_for_update().get(pk=alert.pk)

            if locked_case.closed_date is not None:
                return AutoBundleDecision(
                    status="skipped",
                    reason="closed_target_recheck",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if locked_case.owner_org_id != org_id:
                return AutoBundleDecision(
                    status="skipped",
                    reason="cross_org_target_recheck",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if locked_case.status is None or locked_case.status.stage not in (
                "New",
                "InProgress",
            ):
                return AutoBundleDecision(
                    status="skipped",
                    reason="closed_target_recheck",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if fresh_alert.case_id is not None:
                return AutoBundleDecision(
                    status="skipped",
                    reason="already_linked",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if fresh_alert.owner_org_id != org_id:
                return AutoBundleDecision(
                    status="skipped",
                    reason="tenant_mismatch",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )

            merge_alert_into_case(fresh_alert, locked_case, actor=None)

            if rule.match_mode == "correlation_key" and fresh_alert.correlation_key:
                reason_str = (
                    f"correlation_key='{fresh_alert.correlation_key}' "
                    f"within {effective_window} minutes"
                )
                signal_str = reason_str
            elif rule.match_mode == "shared_observable":
                from cases.models import CaseObservable

                obs_list = list(
                    CaseObservable.objects.filter(case=locked_case).values_list(
                        "observable__data_type__name", "observable__normalized_data"
                    )[:3]
                )
                if obs_list:
                    obs_str = ", ".join(f"{dt}:{val}" for dt, val in obs_list)
                    shared_count = (
                        CaseObservable.objects.filter(case=locked_case)
                        .filter(observable__ignore_similarity=False)
                        .count()
                    )
                    reason_str = (
                        f"shared observable '{obs_str}' within {effective_window} minutes "
                        f"(count={shared_count})"
                    )
                else:
                    reason_str = f"shared observable within {effective_window} minutes"
                signal_str = reason_str
            else:
                reason_str = ""
                signal_str = ""

            record_alert_bundled(
                locked_case,
                fresh_alert,
                actor=None,
                reason=reason_str,
                source=AUTO_BUNDLE_SOURCE,
            )

            return AutoBundleDecision(
                status="bundled",
                reason=reason_str,
                mode=rule.match_mode,
                case_id=locked_case.id,
                window_minutes=effective_window,
                signal=signal_str,
            )

    except PermissionDenied:
        # Known refusal - non-fatal to ingest; return skipped without raising
        return AutoBundleDecision(
            status="skipped",
            reason="bundle_failed",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )
    except ValidationError:
        # Known validation error - non-fatal to ingest
        return AutoBundleDecision(
            status="skipped",
            reason="bundle_failed",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )
    except Exception:
        # Unexpected exception - log, return failed; alert remains stored
        logger.exception("Unexpected error during auto-bundle evaluation")
        return AutoBundleDecision(
            status="failed",
            reason="bundle_failed",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )
