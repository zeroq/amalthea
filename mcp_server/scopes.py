"""Credential-bound tenant scoping helpers for MCP reads.

Every helper derives the tenant from the authenticated credential.  Tool
arguments are never allowed to select an organisation.  Cross-tenant and
non-existent ids resolve identically to ``None`` (the caller returns a uniform
not-found).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet

from mcp_server.auth import Principal


def actor_from_principal(principal: Principal) -> SimpleNamespace:
    """A user-shaped object for the shared scoping helpers.

    ``identity.scoping`` only needs ``is_authenticated``, ``org_id`` and ``role``
    for the read paths used here, so no extra User query is required per call.
    """
    # The shared helpers branch on ``superAdmin`` for break-glass reads.  A
    # credential is never allowed that path, so the scoping actor deliberately
    # carries the ordinary role while the audit row keeps the real role.
    return SimpleNamespace(
        pk=principal.actor_id,
        is_authenticated=True,
        org_id=principal.organisation_id,
        role="analyst",
    )


def case_visible_q(principal: Principal) -> Q:
    """Cases owned by the credential org or shared to it."""
    return _case_visible_q(principal.organisation_id)


def _case_visible_q(org_id: UUID) -> Q:
    return Q(owner_org_id=org_id) | Q(shares__organisation_id=org_id)


def visible_cases(principal: Principal) -> QuerySet[Any]:
    from cases.models import Case

    return Case.objects.filter(_case_visible_q(principal.organisation_id)).distinct()


def resolve_case(principal: Principal, case_id: str) -> Any | None:
    from cases.models import Case

    try:
        return (
            Case.objects.filter(_case_visible_q(principal.organisation_id))
            .select_related("status", "assignee")
            .distinct()
            .get(pk=case_id)
        )
    except (Case.DoesNotExist, ValidationError, ValueError, TypeError):
        return None


def resolve_alert(principal: Principal, alert_id: str) -> Any | None:
    from alerts.models import Alert

    try:
        return (
            Alert.objects.filter(pk=alert_id, owner_org_id=principal.organisation_id)
            .select_related("status", "assignee", "case")
            .get()
        )
    except (Alert.DoesNotExist, ValidationError, ValueError, TypeError):
        return None


def resolve_observable(principal: Principal, observable_id: str) -> Any | None:
    from observables.models import Observable

    try:
        return (
            Observable.objects.filter(pk=observable_id, owner_org_id=principal.organisation_id)
            .select_related("data_type")
            .get()
        )
    except (Observable.DoesNotExist, ValidationError, ValueError, TypeError):
        return None


def visible_case_count(principal: Principal, observable: Any) -> int:
    from cases.models import CaseObservable

    return (
        CaseObservable.objects.filter(
            observable=observable,
            case__in=visible_cases(principal),
        )
        .values("case_id")
        .distinct()
        .count()
    )


def org_alerts(principal: Principal) -> QuerySet[Any]:
    from alerts.models import Alert

    return Alert.objects.filter(owner_org_id=principal.organisation_id)


def org_observables(principal: Principal) -> QuerySet[Any]:
    from observables.models import Observable

    return Observable.objects.filter(owner_org_id=principal.organisation_id)


def org_playbooks(principal: Principal) -> QuerySet[Any]:
    from automation.models import Playbook

    return Playbook.objects.filter(owner_org_id=principal.organisation_id)


def case_observable_links(principal: Principal, case: Any) -> QuerySet[Any]:
    from cases.models import CaseObservable

    return (
        CaseObservable.objects.filter(case=case)
        .select_related("observable__data_type")
        .order_by("-created_at")
    )


def case_tasks(principal: Principal, case: Any) -> QuerySet[Any]:
    from cases.models import Task

    return Task.objects.filter(case=case).select_related("assignee")


def visible_tasks(principal: Principal) -> QuerySet[Any]:
    from cases.models import Task

    return Task.objects.filter(case__in=visible_cases(principal)).select_related("assignee", "case")
