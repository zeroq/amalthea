"""Single source of truth for tenant isolation scoping (P1 — Foundations).

This module provides the authoritative helpers used by **both** HTTP views and
WebSocket consumers to enforce org boundaries. The design goals are:

* **Greppable** — one place to audit every org check.
* **Fail-closed** — org-less callers get no access; `None`/empty Q means deny.
* **Uniform 404** — cross-tenant and non-existent resolve identically to "not
  found" (no existence oracle).
* **Reusable** — `case_visible_q`, `resolve_case`, `org_filtered` cover the
  three patterns in the codebase: list/retrieve on `Case`, object access via a
  case FK, and direct-org rows (alerts, observables).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Q

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from alerts.models import Alert
    from cases.models import Case, Task
    from identity.models import User
    from observables.models import Observable


def _user_org_id(user: User | None) -> UUID | None:
    """Return the caller's organisation PK, or `None` if unauthenticated/org-less."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "org_id", None)


def _is_superadmin(user: User | None) -> bool:
    """True iff the caller is an authenticated superAdmin."""
    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "role", "") == "superAdmin"
    )


def is_viewer(user: User | None) -> bool:
    """
    True iff the caller is an authenticated user with the viewer (read-only) role.

    Deliberately **deny-only**: anonymous callers, `None`, ingestion tokens and
    other non-`User` identities are never viewers, so this predicate can be used
    as an additive guard without turning machine-to-machine paths into denials.
    See ADR-006.
    """
    from core.enums import VIEWER_ROLE

    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "role", "") == VIEWER_ROLE
    )


def is_admin(user: User | None) -> bool:
    """
    True iff the caller may perform administrative writes (`admin` or `superAdmin`).

    Used for the writes that are deliberately narrower than the writer role — currently
    `Case.data_classification` (PLAN-2026-10-14 Q-D5): classifying an incident's data changes
    how it is reported, so it is an administrative act rather than an analyst one. Like
    `is_viewer`, this is **deny-only**: anonymous or non-`User` identities are never admins.

    `superAdmin` "sees all" (ADR-004) and therefore also passes; `_is_superadmin` remains the
    narrower predicate for the cross-org read paths that must audit a break-glass read.
    """
    from core.enums import CASE_CLASSIFICATION_ROLES

    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "role", "") in CASE_CLASSIFICATION_ROLES
    )


def case_visible_q(user: User | None) -> Q:
    """
    Return a `Q` object matching cases visible to `user`.

    Visible cases are those where:
    - `owner_org == user.org` (the caller's own org), OR
    - a `Share` grants read access to `user.org`, OR
    - `user.role == "superAdmin"` (sees all, audited by callers).

    Org-less or unauthenticated callers → empty `Q()` (matches nothing).
    """
    if _is_superadmin(user):
        return Q()  # all cases
    org_id = _user_org_id(user)
    if org_id is None:
        return Q(pk__in=[])  # deny: no org = no access
    return Q(owner_org_id=org_id) | Q(shares__organisation_id=org_id)


def org_q(field: str, user: User | None) -> Q:
    """
    Return a `Q` filtering `field` to the caller's organisation.

    Used for direct-org rows (Alert, Observable) that carry `owner_org` directly.
    Org-less/superAdmin handling mirrors `case_visible_q`.
    """
    if _is_superadmin(user):
        return Q()
    org_id = _user_org_id(user)
    if org_id is None:
        return Q(pk__in=[])
    return Q(**{field: org_id})


def org_filtered(
    queryset: QuerySet[Any],
    user: User | None,
    *,
    path: str = "case__owner_org",
) -> QuerySet[Any]:
    """
    Scope a queryset reached *through* a case to the caller's organisation.

    Example: `Task.objects.filter(case=case)` → `org_filtered(tasks, user, path="case__owner_org")`.

    SuperAdmin → unfiltered. Org-less → empty queryset.
    """
    if _is_superadmin(user):
        return queryset
    org_id = _user_org_id(user)
    if org_id is None:
        return queryset.none()
    return queryset.filter(**{f"{path}_id": org_id})


def resolve_case(user: User | None, identifier: str, *, write: bool = False) -> Case | None:
    """
    Resolve a case the caller may reach, or `None` (caller turns into 404).

    Used by HTTP **and** WebSocket consumers. The logic mirrors the legacy
    `_case_access` in `cases/views.py` but is the single canonical implementation.

    - SuperAdmin → any case (audited externally).
    - Org-less → `None`.
    - Owner org → allowed.
    - Shared org → allowed if share exists; `write=True` demands `permissions.write`.
    - Viewer role → `write=True` always denies (role beats share; ADR-006). The deny is checked
      **first**, so it holds even for an account that also looks privileged — "viewer" is a cap on
      the person, not another grant to arbitrate (ADR-006 decision 2). The role/privilege
      combination is unreachable by decision 4, which is exactly why the ordering is pinned by a
      test (`test_viewer_precedence.py::test_the_viewer_deny_precedes_the_privileged_branch`)
      rather than left to be re-derived by every reader.
    """
    from alerts.escalation import link_case_from_identifier
    from cases.models import Case, Share

    if write and is_viewer(user):
        return None

    if _is_superadmin(user):
        try:
            return link_case_from_identifier(identifier)
        except Case.DoesNotExist:
            return None

    org_id = _user_org_id(user)
    if org_id is None:
        return None

    try:
        case = link_case_from_identifier(identifier)
    except Case.DoesNotExist:
        return None

    # Owner org — full access
    if case.owner_org_id == org_id:
        return case

    # Shared org — check share permissions
    shares = Share.objects.filter(case=case, organisation_id=org_id)
    if write:
        shares = shares.filter(permissions__write=True)
    return case if shares.exists() else None


def resolve_alert(user: User | None, alert_id: str, *, write: bool = False) -> Alert | None:
    """Resolve an alert the caller may reach (direct org ownership).

    Alerts carry no shares, so `write=True` adds only the viewer-role denial
    (ADR-006): a viewer may read an alert but never reach a mutation through it.

    A tampered/non-UUID id is a uniform 404, not a 500: the pk lookup raises Django's own
    `ValidationError` for a non-UUID string, which is neither `DoesNotExist` nor `ValueError`
    (the same F7-class trap `link_case_from_identifier` guards against).
    """
    from alerts.models import Alert

    if write and is_viewer(user):
        return None

    if _is_superadmin(user):
        try:
            return Alert.objects.get(pk=alert_id)
        except (Alert.DoesNotExist, ValidationError, ValueError, TypeError):
            return None

    org_id = _user_org_id(user)
    if org_id is None:
        return None

    try:
        return Alert.objects.get(pk=alert_id, owner_org_id=org_id)
    except (Alert.DoesNotExist, ValidationError, ValueError, TypeError):
        return None


def resolve_observable(
    user: User | None, observable_id: str, *, write: bool = False
) -> Observable | None:
    """Resolve an observable the caller may reach. A non-UUID id is a uniform 404, not a 500.

    Observables are org-owned; `write=True` adds the viewer-role denial (ADR-006).
    """
    from observables.models import Observable

    if write and is_viewer(user):
        return None

    if _is_superadmin(user):
        try:
            return Observable.objects.get(pk=observable_id)
        except (Observable.DoesNotExist, ValidationError, ValueError, TypeError):
            return None

    org_id = _user_org_id(user)
    if org_id is None:
        return None

    try:
        return Observable.objects.get(pk=observable_id, owner_org_id=org_id)
    except (Observable.DoesNotExist, ValidationError, ValueError, TypeError):
        return None


def resolve_task(user: User | None, task_id: str, *, write: bool = False) -> Task | None:
    """Resolve a task the caller may reach, scoped through its case.

    Mirrors `resolve_observable`: the task is visible iff its case is visible to the
    caller (owner org or shared with read/write per the `write` flag). A non-UUID id
    is a uniform 404. `write=True` denies viewers (ADR-006).
    """
    from cases.models import Task

    if write and is_viewer(user):
        return None

    if _is_superadmin(user):
        try:
            return Task.objects.select_related("case").get(pk=task_id)
        except (Task.DoesNotExist, ValidationError, ValueError, TypeError):
            return None

    org_id = _user_org_id(user)
    if org_id is None:
        return None

    try:
        task = Task.objects.select_related("case").get(pk=task_id)
    except (Task.DoesNotExist, ValidationError, ValueError, TypeError):
        return None

    # Task is visible iff its case is visible to this org
    case = task.case
    if case.owner_org_id == org_id:
        return task

    # Shared org — check share permissions
    from cases.models import Share

    shares = Share.objects.filter(case=case, organisation_id=org_id)
    if write:
        shares = shares.filter(permissions__write=True)
    return task if shares.exists() else None


def is_case_owner(user: User | None, case: Case) -> bool:
    """
    Return True iff the caller is the case's owner organisation (or superAdmin).

    This is the predicate for **grant/revoke** operations on shares (plan §6-P2, P6).
    A share holder with write access may mutate the case body, but only the owner
    org (or superAdmin) may manage the share ACL itself. Returns False for org-less
    callers and cross-tenant share holders.
    """
    if _is_superadmin(user):
        return True
    org_id = _user_org_id(user)
    if org_id is None:
        return False
    return case.owner_org_id == org_id


__all__ = [
    "case_visible_q",
    "is_case_owner",
    "is_viewer",
    "org_filtered",
    "org_q",
    "resolve_alert",
    "resolve_case",
    "resolve_observable",
    "resolve_task",
]
