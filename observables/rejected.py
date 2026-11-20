"""Shared service for RejectedObservable operations.

Provides tenant-safe scoping and promotion logic that both the API views and
the UI can depend on, without duplicating org-Q filtering or state-transition
guards.
"""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import QuerySet

from identity.models import User
from identity.scoping import is_viewer, org_q

from .models import RejectedObservable


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
