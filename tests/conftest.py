"""Shared fixtures for the entire test suite.

Provides:
* `default_org` — the organisation named by `AMALTHEA_DEFAULT_ORG_NAME` (get_or_create).
* Factories that stamp `owner_org`/`org` automatically.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from identity.models import Organisation, User


@pytest.fixture
def default_org(db: None) -> Organisation:
    """The default organisation used by the greenfield migration."""
    name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
    org, _ = Organisation.objects.get_or_create(name=name)
    return org


@pytest.fixture
def make_case(default_org: Organisation):
    """Factory for cases stamped with an org.

    Usage: `case = make_case(title="X", severity=2)` — org defaults to `default_org`.
    Pass `org=other_org` to override.
    """
    from cases.models import Case, CaseStatus

    def _make(**kw):
        org = kw.pop("org", default_org)
        status = kw.pop("status", None)
        if status is None:
            status, _ = CaseStatus.objects.get_or_create(
                value="New", defaults={"stage": "New", "order": 1}
            )
        return Case.objects.create(owner_org=org, status=status, **kw)

    return _make


@pytest.fixture
def make_alert(default_org: Organisation):
    """Factory for alerts stamped with an org."""
    from alerts.models import Alert, AlertStatus

    def _make(**kw):
        org = kw.pop("org", default_org)
        status = kw.pop("status", None)
        if status is None:
            status, _ = AlertStatus.objects.get_or_create(
                value="New", defaults={"stage": "New", "order": 1}
            )
        return Alert.objects.create(owner_org=org, status=status, **kw)

    return _make


@pytest.fixture
def make_observable(default_org: Organisation):
    """Factory for observables stamped with an org."""
    from observables.models import Observable, ObservableType

    def _make(**kw):
        org = kw.pop("org", default_org)
        type_name = kw.pop("data_type", None)
        if type_name is None:
            type_name = "ip"
        otype, _ = ObservableType.objects.get_or_create(
            name=type_name, defaults={"is_case_sensitive": False}
        )
        data = kw.pop("data", "10.0.0.1")
        normalized = kw.pop("normalized_data", data)
        return Observable.objects.create(
            owner_org=org, data_type=otype, data=data, normalized_data=normalized, **kw
        )

    return _make


@pytest.fixture
def make_user(default_org: Organisation):
    """Factory for users belonging to an org."""

    def _make(**kw):
        org = kw.pop("org", default_org)
        return User.objects.create(org=org, **kw)

    return _make


__all__ = ["default_org", "make_alert", "make_case", "make_observable", "make_user"]
