"""Shared service for RejectedObservable operations.

Provides tenant-safe scoping and promotion logic that both the API views and
the UI can depend on, without duplicating org-Q filtering or state-transition
guards.
"""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from identity.models import User
from identity.scoping import is_viewer, org_q

from .models import ObservableSuggestion, RejectedObservable

#: Reason used when a caller does not supply an explicit rejection reason. It is a real
#: `RejectedObservable` vocabulary token, so suppression and reason filters keep working for
#: API clients that still post an empty body.
DEFAULT_REJECTION_REASON = "other"
REJECTION_REASON_VALUES = frozenset(
    token for token, _label in RejectedObservable.REJECTED_REASON_CHOICES
)


def normalize_rejection_reason(reason: str | None) -> str:
    """Return a valid `RejectedObservable.reason` token or raise ``ValueError``.

    Empty values fall back to `other` for backwards compatibility with callers that predate
    the rejection-reason UI. Unknown tokens are refused rather than silently downgraded.
    """
    token = (reason or "").strip()
    if not token:
        return DEFAULT_REJECTION_REASON
    if token not in REJECTION_REASON_VALUES:
        allowed = ", ".join(sorted(REJECTION_REASON_VALUES))
        raise ValueError(f"Unknown rejection reason {token!r}; expected one of: {allowed}")
    return token


def scoped_rejected_observables(
    user: User | None,
    *,
    status: str | None = None,
    reason: str | None = None,
) -> QuerySet[RejectedObservable]:
    """Return tenant-scoped QuerySet[RejectedObservable].

    Identity scoping rules follow ``identity.scoping.org_q``: superAdmin sees all
    rows, an authenticated user sees only their own org, and unauthenticated or
    org-less users see nothing.

    Status tokens mapping:
    - pending / unpromoted / false / 0  -> is_promoted=False
    - promoted / true / 1             -> is_promoted=True
    - Invalid status values are silently ignored (no filter applied).
    """
    queryset = RejectedObservable.objects.select_related("data_type", "alert", "owner_org").filter(
        org_q("owner_org_id", user)
    )

    # Empty filter values mean "no filter". The UI and API both pass through raw query
    # params ("" for an untouched select), so treating "" as an explicit token hides every row.
    status_token = (status or "").strip().lower()
    promoted_tokens = {"promoted", "true", "1"}
    demoted_tokens = {"pending", "unpromoted", "false", "0"}
    if status_token in promoted_tokens:
        queryset = queryset.filter(is_promoted=True)
    elif status_token in demoted_tokens:
        queryset = queryset.filter(is_promoted=False)

    reason_token = (reason or "").strip()
    if reason_token:
        queryset = queryset.filter(reason=reason_token)

    return queryset.order_by("-created_at")


def promote_rejected_observable(
    user: User | None, pk: str | UUID
) -> tuple[RejectedObservable, bool]:
    """Promote a rejected observable idempotently within the caller's scope.

    Promotion clears suppression (``is_promoted=True``) so future ingest/extraction can
    re-surface the value for manual review (typically as an ObservableSuggestion). It does
    not auto-create an Observable or CaseObservable.

    Raises ``RejectedObservable.DoesNotExist`` when the row is not visible to the
    caller. Returns ``(obj, False)`` when already promoted, otherwise saves the
    row and returns ``(obj, True)``.
    """
    if getattr(user, "is_authenticated", False) and is_viewer(user):
        raise RejectedObservable.DoesNotExist("Viewer promotion denied")

    obj = scoped_rejected_observables(user).filter(pk=pk).first()
    if obj is None:
        raise RejectedObservable.DoesNotExist("Rejected observable not found")

    if obj.is_promoted:
        return (obj, False)

    with transaction.atomic():
        obj.is_promoted = True
        obj.save(update_fields=["is_promoted", "updated_at"])

    return (obj, True)


@transaction.atomic
def reject_suggestion(
    suggestion: ObservableSuggestion,
    *,
    actor: User | None,
    reason: str | None = None,
) -> RejectedObservable:
    """Reject one suggestion and record the value in the suppression ledger.

    This is the single inbound path from the suggestion workflow into
    `RejectedObservable`. The UI and JSON API both call it so the management page and the
    ingest suppression check see the same tenant-scoped row. It is idempotent: rejecting the
    same artifact again updates the reason and clears a prior promotion instead of creating a
    duplicate.

    Rejection does not remove the suggestion from the case's suggestion list; re-extraction may
    propose it again. This deliberately preserves the suggestion-workflow decision recorded in
    PLAN-2026-10-21 while making the value available to the rejected-observable management UI
    and mapping-rule suppression path.
    """
    reason_token = normalize_rejection_reason(reason)
    rejected, created = RejectedObservable.objects.get_or_create(
        owner_org=suggestion.owner_org,
        data_type=suggestion.data_type,
        normalized_data=suggestion.normalized_data,
        alert=suggestion.source_alert,
        defaults={"reason": reason_token, "is_promoted": False},
    )
    if not created and (rejected.reason != reason_token or rejected.is_promoted):
        rejected.reason = reason_token
        rejected.is_promoted = False
        rejected.save(update_fields=["reason", "is_promoted", "updated_at"])

    suggestion.status = "rejected"
    suggestion.reviewed_by = actor
    suggestion.reviewed_at = timezone.now()
    suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    return rejected
