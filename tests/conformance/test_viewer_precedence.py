"""ADR-006 decisions 4 and 6 — the viewer role outranks every other grant (plan §9.5).

A share is additive (T6): holding one widens what an organisation can reach. The viewer rule is not
a grant at all — it is a cap on the *person* — so it has to win over a `write` share, otherwise an
owner could hand write access to a viewer by sharing the case with their org and the role would mean
nothing. Three things are asserted, in both directions:

1. a viewer holding an explicit **write** share still cannot mutate the case (role beats share);
2. the same viewer **can still read** it (the cap is on writing, not on the share);
3. a non-viewer with the identical share **can** write — the control that keeps (1) from passing
   because sharing broke altogether.

`resolve_case` is exercised directly as well as through HTTP, because it is the single choke point
the WebSocket consumer also calls: a rule that only held in one of the two callers would be a bug
the route-level tests could not see.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from core.enums import VIEWER_ROLE
from identity.models import Organisation, User
from identity.scoping import resolve_case

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner_org() -> Organisation:
    return Organisation.objects.get_or_create(name="Owner Org")[0]


@pytest.fixture
def shared_org() -> Organisation:
    return Organisation.objects.get_or_create(name="Shared Org")[0]


def _case(org: Organisation):
    from cases.models import Case, CaseStatus

    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title="Shared case", owner_org=org, status=status, description="d")


def _shared_case_with_write(owner_org: Organisation, shared_org: Organisation):
    """A case owned by `owner_org` with an explicit read+write share for `shared_org`."""
    from cases.models import Share

    case = _case(owner_org)
    Share.objects.create(
        case=case, organisation=shared_org, permissions={"read": True, "write": True}
    )
    return case


def _api_browser(org: Organisation, username: str, role: str) -> APIClient:
    """A session-authenticated API client (`APIClient` so `format="json"` binds a JSON body)."""
    client = APIClient()
    client.force_login(User.objects.create(username=username, org=org, role=role))
    return client


# --- 1. role beats share -------------------------------------------------------


def test_resolve_case_denies_write_for_a_viewer_even_with_a_write_share(
    owner_org: Organisation, shared_org: Organisation
) -> None:
    case = _shared_case_with_write(owner_org, shared_org)
    viewer = User.objects.create(username="prec-viewer", org=shared_org, role=VIEWER_ROLE)

    assert resolve_case(viewer, str(case.number), write=True) is None, (
        "a write share let a viewer reach a mutation"
    )
    # ...and the very same viewer still resolves the case for a read.
    assert resolve_case(viewer, str(case.number), write=False) is not None


def test_a_viewer_with_a_write_share_is_refused_by_the_api_and_the_ui(
    owner_org: Organisation, shared_org: Organisation
) -> None:
    case = _shared_case_with_write(owner_org, shared_org)
    user = User.objects.create(username="prec-api-viewer", org=shared_org, role=VIEWER_ROLE)
    api = APIClient()
    api.force_login(user)
    ui = Client()
    ui.force_login(user)

    # API: the global permission layer answers first — 403, not the scoping 404. (It also runs
    # before the body is parsed, which is why a viewer never reaches a 415 on a malformed body.)
    response = api.patch(f"/api/v1/case/{case.id}", {"title": "hijacked"}, format="json")
    assert response.status_code == 403, response.content
    case.refresh_from_db()
    assert case.title == "Shared case", "a viewer with a write share changed the case"

    # UI: a uniform 404 (ADR-006 decision 3).
    assert ui.post(reverse("ui-case-status", kwargs={"case_id": case.number})).status_code == 404

    # Reads through both surfaces survive.
    assert api.get(f"/api/v1/case/{case.id}").status_code == 200
    assert ui.get(reverse("ui-case-detail", kwargs={"case_id": case.number})).status_code == 200


def test_the_same_write_share_lets_a_non_viewer_write(
    owner_org: Organisation, shared_org: Organisation
) -> None:
    """The control: without it, `test_..._is_refused_...` would pass if sharing were broken."""
    case = _shared_case_with_write(owner_org, shared_org)
    analyst = _api_browser(shared_org, "prec-analyst", "analyst")

    response = analyst.patch(f"/api/v1/case/{case.id}", {"title": "legit edit"}, format="json")
    assert response.status_code == 200, response.content
    case.refresh_from_db()
    assert case.title == "legit edit"


# --- 2. granting and revoking ------------------------------------------------


def test_a_viewer_with_a_write_share_cannot_grant_a_share(
    owner_org: Organisation, shared_org: Organisation
) -> None:
    from cases.models import Share

    case = _shared_case_with_write(owner_org, shared_org)
    viewer = _api_browser(shared_org, "prec-grant-viewer", VIEWER_ROLE)
    third = Organisation.objects.get_or_create(name="Third Org")[0]

    body = {"shares": [{"organisation": str(third.id), "permissions": {"write": True}}]}
    response = viewer.post(f"/api/v1/case/{case.id}/shares", body, format="json")
    assert response.status_code == 403, response.content
    assert not Share.objects.filter(case=case, organisation=third).exists()

    # The UI entry point is denied too, and the tenant-qualified route does not leak a 405/500.
    assert (
        viewer.post(reverse("ui-case-share-list", kwargs={"case_id": case.number})).status_code
        == 404
    )
    assert Share.objects.filter(case=case).count() == 1


def test_a_viewer_cannot_revoke_the_share_that_lets_it_read(
    owner_org: Organisation, shared_org: Organisation
) -> None:
    from cases.models import Share

    case = _shared_case_with_write(owner_org, shared_org)
    share = Share.objects.get(case=case)
    viewer = _api_browser(shared_org, "prec-revoke-viewer", VIEWER_ROLE)

    response = viewer.delete(f"/api/v1/case/{case.id}/share/{share.id}")
    assert response.status_code == 403, response.content
    assert Share.objects.filter(pk=share.id).exists(), "a viewer revoked a share"

    revoke = reverse("ui-case-share-revoke", kwargs={"case_id": case.number, "share_id": share.id})
    assert viewer.post(revoke).status_code == 404
    assert Share.objects.filter(pk=share.id).exists()


# --- 3. the deny precedes the privileged branch -------------------------------


@pytest.mark.parametrize("resolver_kind", ["case", "alert", "observable"])
def test_the_viewer_deny_precedes_the_privileged_branch(
    resolver_kind: str, owner_org: Organisation, shared_org: Organisation, monkeypatch
) -> None:
    """The ordering is the invariant, and it is deliberately pinned rather than implied.

    Today the deny is *also* redundant: every resolver keys its privileged branch on
    `role == "superAdmin"`, which a viewer's role cannot be, so the two conditions are mutually
    exclusive and a viewer would never have reached that branch anyway. This test forces them to
    overlap — `_is_superadmin` is patched to claim the caller is privileged — so the ordering is
    observable: if the deny ever moves below the privileged branch (or a future role gains
    `is_superuser` semantics), this fails while a role-only test would still pass.

    An earlier version of this test set Django's `is_superuser=True` and asserted the deny. That
    passed for an unrelated reason — `_is_superadmin` keys on `role`, never on `is_superuser` — so it
    proved nothing; the security review caught it (REVIEW-2026-10-12 round 2, R2-L1).
    """
    from alerts.models import Alert, AlertStatus
    from identity import scoping
    from observables.models import Observable, ObservableType

    monkeypatch.setattr(scoping, "_is_superadmin", lambda user: True)

    shared_case = _shared_case_with_write(owner_org, shared_org)
    viewer = User.objects.create(username="prec-order-viewer", org=shared_org, role=VIEWER_ROLE)
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    alert = Alert.objects.create(title="Phish", owner_org=owner_org, status=status, source="s1")
    otype = ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})[
        0
    ]
    observable = Observable.objects.create(data_type=otype, data="10.0.0.1", owner_org=owner_org)

    resolvers = {
        "case": (scoping.resolve_case, str(shared_case.number)),
        "alert": (scoping.resolve_alert, str(alert.id)),
        "observable": (scoping.resolve_observable, str(observable.id)),
    }
    resolve, identifier = resolvers[resolver_kind]

    # The patched privileged branch is live — a read proves the call reaches it and returns a row,
    # so `None` below can only come from the viewer deny that runs first.
    assert resolve(viewer, identifier, write=False) is not None
    assert resolve(viewer, identifier, write=True) is None
