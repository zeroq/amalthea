"""ADR-006 — role semantics: the viewer role is read-only.

Covers the two primitives the whole enforcement stack rests on:

* `identity.scoping.is_viewer` — the deny-only predicate (truth table).
* the `write=True` denial in `resolve_case` / `resolve_alert` / `resolve_observable`,
  including the precedence rule *role beats share*.

The exhaustive per-route invariants live in
`tests/conformance/test_viewer_readonly_ui.py` and `..._api.py`.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from core.enums import USER_ROLE_VALUES, VIEWER_ROLE
from identity.models import Organisation
from identity.scoping import is_viewer, resolve_alert, resolve_case, resolve_observable

# --- is_viewer truth table ----------------------------------------------------


def test_viewer_role_constant_is_a_declared_role() -> None:
    """`VIEWER_ROLE` must name a value the DB CHECK constraint accepts."""
    assert VIEWER_ROLE in USER_ROLE_VALUES


@pytest.mark.django_db
def test_is_viewer_true_for_viewer(make_user) -> None:
    assert is_viewer(make_user(role=VIEWER_ROLE)) is True


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["analyst", "admin", "superAdmin"])
def test_is_viewer_false_for_other_roles(make_user, role: str) -> None:
    assert is_viewer(make_user(role=role)) is False


def test_is_viewer_false_for_anonymous_and_none() -> None:
    """Deny-only: anonymous/None identities are never viewers.

    This is what keeps the webhook receiver and `AllowAny` login/logout unaffected
    by an additive viewer guard.
    """
    assert is_viewer(None) is False
    assert is_viewer(AnonymousUser()) is False


def test_is_viewer_false_for_non_user_identity_claiming_the_role() -> None:
    """A non-`User` object (e.g. a service identity) carrying `role` is never a viewer."""
    impostor = type("Impostor", (), {"role": VIEWER_ROLE})()
    assert is_viewer(impostor) is False


# --- resolve_case: role beats share -------------------------------------------


@pytest.mark.django_db
def test_resolve_case_write_denies_viewer_in_owner_org(make_user, make_case) -> None:
    case = make_case()
    assert resolve_case(make_user(role=VIEWER_ROLE), str(case.id), write=True) is None


@pytest.mark.django_db
def test_resolve_case_read_still_allows_viewer_in_owner_org(make_user, make_case) -> None:
    case = make_case()
    viewer = make_user(role=VIEWER_ROLE)
    assert resolve_case(viewer, str(case.id)) is not None


@pytest.mark.django_db
def test_resolve_case_write_denies_viewer_holding_a_write_share(make_user, make_case) -> None:
    """Precedence (ADR-006): a write share cannot promote a viewer.

    A read `Share` row is created for the *other* org's viewer; `write=True` must
    still refuse, while the read path keeps working for them.
    """
    from cases.models import Share

    other_org = Organisation.objects.create(name="Other Org (viewer share)")
    case = make_case()
    viewer = make_user(org=other_org, role=VIEWER_ROLE)
    Share.objects.create(
        case=case, organisation=other_org, permissions={"read": True, "write": True}
    )

    assert resolve_case(viewer, str(case.id), write=True) is None
    assert resolve_case(viewer, str(case.id)) is not None


@pytest.mark.django_db
def test_resolve_case_write_allows_analyst(make_user, make_case) -> None:
    case = make_case()
    assert resolve_case(make_user(role="analyst"), str(case.id), write=True) is not None


@pytest.mark.django_db
def test_resolve_case_write_superadmin_bypasses_org(make_case, make_user) -> None:
    """superAdmin is not a viewer, so `write=True` keeps its global reach."""
    other_org = Organisation.objects.create(name="Other Org (superadmin)")
    case = make_case(org=other_org)
    superadmin = make_user(role="superAdmin")
    assert resolve_case(superadmin, str(case.id), write=True) is not None


# --- resolve_alert ------------------------------------------------------------


@pytest.mark.django_db
def test_resolve_alert_write_denies_viewer_but_read_allows(make_user, make_alert) -> None:
    alert = make_alert()
    viewer = make_user(role=VIEWER_ROLE)
    assert resolve_alert(viewer, str(alert.id)) is not None
    assert resolve_alert(viewer, str(alert.id), write=True) is None


@pytest.mark.django_db
def test_resolve_alert_write_allows_analyst(make_user, make_alert) -> None:
    alert = make_alert()
    assert resolve_alert(make_user(role="analyst"), str(alert.id), write=True) is not None


# --- resolve_observable -------------------------------------------------------


@pytest.mark.django_db
def test_resolve_observable_write_denies_viewer_but_read_allows(make_user, make_observable) -> None:
    observable = make_observable()
    viewer = make_user(role=VIEWER_ROLE)
    assert resolve_observable(viewer, str(observable.id)) is not None
    assert resolve_observable(viewer, str(observable.id), write=True) is None


@pytest.mark.django_db
def test_resolve_observable_write_allows_analyst(make_user, make_observable) -> None:
    observable = make_observable()
    assert resolve_observable(make_user(role="analyst"), str(observable.id), write=True) is not None
