"""Conformance tests for the Case Details → Similar tab "Similar alerts" card.

Covers PLAN-2026-10-20-alert-fuzzy-similarity.md (Phase B/D):
- The exact-overlap based tests (AC-2..AC-11 of the prior plan) have been removed
  because the "Similar alerts" card switched to SSDEEP-based fuzzy similarity
  (PLAN §3.4, Q4: replace). Phase D will add new fuzzy-similarity tests.

The remaining test here covers the "Similar cases" card (unchanged by Phase B).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cases.models import Case, CaseObservable, CaseStatus
from identity.models import Organisation, User
from observables.models import Observable, ObservableType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui"


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=login,
        login=login,
        password=f"pw-{login}",
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _mk_case(title: str, org: Organisation, status: CaseStatus | None = None) -> Case:
    if status is None:
        status = CaseStatus.objects.get_or_create(
            value="InProgress", defaults={"stage": "InProgress", "order": 1}
        )[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_obs_type(name: str) -> ObservableType:
    return ObservableType.objects.get_or_create(name=name, defaults={"is_case_sensitive": False})[0]


def _mk_shared_observable(data: str, org: Organisation, *cases: Case) -> Observable:
    """One observable, linked into every case passed (per-org dedupe row)."""
    otype = _mk_obs_type("ip")
    obs = Observable.objects.create(data_type=otype, data=data, normalized_data=data, owner_org=org)
    for case in cases:
        CaseObservable.objects.create(case=case, observable=obs)
    return obs


def _login(user: User):
    from django.test import Client

    client = Client()
    client.force_login(user)
    return client


def _similar_url(case: Case) -> str:
    from django.urls import reverse

    return f"{reverse('ui-case-detail', kwargs={'case_id': case.number})}?tab=similar"


# ---------------------------------------------------------------------------
# AC-11 (from prior plan): status label fixes — similar-CASES card
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusLabel:
    """AC-11 — the similar-cases card renders ``status.value`` (not a blank pill from
    the non-existent ``get_status_display``), and no template still calls
    ``get_status_display`` on a Case.
    """

    def test_similar_tab_renders_case_status_value(self, db: None):
        org = _mk_org("OrgStatusLabel")
        user = _mk_user("analyst", org)
        status = CaseStatus.objects.get_or_create(
            value="Review Pending", defaults={"stage": "InProgress", "order": 2}
        )[0]
        source = _mk_case("Source Case", org, status)
        neighbour = _mk_case("Neighbour Case", org, status)
        _mk_shared_observable("10.42.42.42", org, source, neighbour)

        client = _login(user)
        resp = client.get(_similar_url(source))

        assert resp.status_code == 200
        body = resp.content.decode()
        # The similar-cases card row for `neighbour` shows the status label text.
        assert "Neighbour Case" in body
        assert "Review Pending" in body

    def test_fixed_templates_contain_no_get_status_display(self):
        for name in ("_tab_similar.html", "observable_detail.html"):
            source = (TEMPLATES_DIR / name).read_text()
            assert "get_status_display" not in source, name
