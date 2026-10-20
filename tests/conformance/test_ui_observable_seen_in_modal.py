"""Phase C conformance — Case Detail → Observables tab: clickable "Seen in" → linked-cases modal.

Plan: `docs/planning/PLAN-2026-10-20-observable-seen-in-modal.md` (§8 Testing, §10 ACs).
Implemented (do not edit): `ui/views.py:_case_detail` (`linked_cases_by_observable`, scoped by
`case_visible_q`, capped at `RAIL_CAP`), `ui/templates/ui/_tab_observables.html` (trigger +
modal loop outside `can_mutate`), `ui/templates/ui/_observable_cases_modal_body.html`.

AC coverage:
- AC-1 — `test_seen_in_count_renders_trigger_and_matching_dialog`
- AC-2 — `test_seen_in_modal_lists_visible_cases_with_links_and_status`
- AC-3 — `test_seen_in_modal_never_leaks_other_org_cases` (load-bearing, §7.5 falsifiable)
- AC-5 — `test_viewer_sees_trigger_and_modal_but_no_artifact_mutation_controls`
- AC-4 — `test_seen_in_count_equals_modal_row_count`
- AC-6 — `test_zero_visible_links_render_plain_text_without_trigger`
- AC-7 — `test_seen_in_modal_caps_rows_and_shows_honest_total`
- AC-8/9 — pinned by `tests/conformance/test_ui_css_tokens.py` (undefined-class backlog) and
  `make check`; run those separately.

Setup mirrors `tests/conformance/test_viewer_affordances.py` (case-detail rendering via the
Django test `Client`) and `tests/conformance/test_tenant_scoping.py` (cross-org observable
linking via `CaseObservable.objects.create`).
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from core.enums import VIEWER_ROLE
from identity.models import Organisation, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.get_or_create(name="Default")[0]


def _user(org: Organisation, username: str, role: str = "analyst") -> User:
    return User.objects.create(username=username, org=org, role=role)


def _browser(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _case(org: Organisation, title: str = "Suspicious inbox rule", **kw):
    from cases.models import Case, CaseStatus

    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, owner_org=org, status=status, **kw)


def _mk_observable(data: str, org: Organisation, type_name: str = "ip"):
    from observables.models import Observable, ObservableType

    otype = ObservableType.objects.get_or_create(
        name=type_name, defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(
        data_type=otype, data=data, normalized_data=data, owner_org=org
    )


def _link(case, observable) -> None:
    from cases.models import CaseObservable

    CaseObservable.objects.create(case=case, observable=observable)


def _case_url(case) -> str:
    return reverse("ui-case-detail", kwargs={"case_id": case.number})


def _modal_slice(html: str, modal_id: str) -> str:
    """Return the rendered `<dialog id="…">…</dialog>` block for one modal id."""
    start = html.find(f'<dialog id="{modal_id}"')
    assert start != -1, f"dialog {modal_id!r} not rendered"
    end = html.find("</dialog>", start)
    assert end != -1, f"dialog {modal_id!r} never closed"
    return html[start : end + len("</dialog>")]


def _button_count(html: str, modal_id: str) -> int | None:
    """The N in the "Seen in" trigger button that opens `modal_id`."""
    m = re.search(
        rf'data-modal-open="{re.escape(modal_id)}"[^>]*>\s*(\d+) cases?',
        html,
    )
    return int(m.group(1)) if m else None


# --- AC-1: the count is a control, and trigger/dialog ids match ------------------


def test_seen_in_count_renders_trigger_and_matching_dialog(org: Organisation) -> None:
    viewer = _browser(_user(org, "seen-ac1-analyst"))
    viewing = _case(org, title="AC1 viewing case")
    linked = _case(org, title="AC1 linked case")
    obs = _mk_observable("203.0.113.10", org)
    _link(viewing, obs)
    _link(linked, obs)

    response = viewer.get(_case_url(viewing))
    assert response.status_code == 200
    html = response.content.decode()

    trigger = re.search(r'data-modal-open="(observable-cases-[0-9a-f-]+)"', html)
    assert trigger, "the Seen in count is not a modal trigger"
    modal_id = trigger.group(1)
    assert modal_id == f"observable-cases-{obs.id}"
    assert f'id="{modal_id}"' in html, "trigger and dialog ids must match exactly"


# --- AC-2: modal content — number (link) + title (link) + status -----------------


def test_seen_in_modal_lists_visible_cases_with_links_and_status(
    org: Organisation,
) -> None:
    browser = _browser(_user(org, "seen-ac2-analyst"))
    viewing = _case(org, title="AC2 viewing case")
    first = _case(org, title="AC2 first linked case")
    second = _case(org, title="AC2 second linked case")
    obs = _mk_observable("203.0.113.20", org)
    for case in (viewing, first, second):
        _link(case, obs)

    html = browser.get(_case_url(viewing)).content.decode()
    modal_id = f"observable-cases-{obs.id}"
    modal = _modal_slice(html, modal_id)
    assert "Cases where this artifact was seen" in modal

    for case in (first, second):
        url = _case_url(case)
        # Both columns link to the case detail: the number AND the title.
        assert f'<a href="{url}">{case.number}</a>' in modal, f"case {case.number} not listed"
        assert f'<a href="{url}">{case.title}</a>' in modal, (
            f"title of case {case.number} not a link"
        )
        assert f">{case.status.value}</span>" in modal, f"status of case {case.number} missing"


# --- AC-3 (load-bearing): cross-org isolation ------------------------------------


def test_seen_in_modal_never_leaks_other_org_cases() -> None:
    """An org-A user must never see org-B case numbers/titles in the Seen-in modal.

    The observable is deliberately linked (directly, defence-in-depth scenario per plan §4)
    to an org-B case; the org-A user's own linked cases must still appear (non-vacuity), so
    this cannot pass because the modal stopped rendering altogether. This is the §7.5
    falsifiable test: removing `.filter(case_visible_q(request.user))` from
    `ui/views.py:_case_detail` must make it FAIL.
    """
    org_a = Organisation.objects.create(name="SeenIn Org A")
    org_b = Organisation.objects.create(name="SeenIn Org B")
    user_a = _browser(_user(org_a, "seen-iso-analyst"))

    viewing = _case(org_a, title="Alpha viewing case")
    sibling = _case(org_a, title="Alpha sibling case")
    foreign = _case(org_b, title="Bravo confidential case")
    obs = _mk_observable("203.0.113.30", org_a)
    for case in (viewing, sibling, foreign):
        _link(case, obs)

    response = user_a.get(_case_url(viewing))
    assert response.status_code == 200
    html = response.content.decode()

    # Non-vacuity: the org-A sibling IS listed inside the modal.
    modal = _modal_slice(html, f"observable-cases-{obs.id}")
    sibling_url = _case_url(sibling)
    assert f'<a href="{sibling_url}">{sibling.number}</a>' in modal
    assert sibling.title in modal

    # Isolation: no trace of the org-B case — href form, link text, and title.
    foreign_url = _case_url(foreign)
    assert f'href="{foreign_url}"' not in html, "org-B case href leaked into the page"
    assert f">{foreign.number}</a>" not in html, "org-B case number leaked into the page"
    assert foreign.title not in html, "org-B case title leaked into the page"
    # The count and modal agree on the visible-only scope (2 of the 3 links).
    assert _button_count(html, f"observable-cases-{obs.id}") == 2


# --- AC-5: a viewer can open the modal; no mutation controls ---------------------


def test_viewer_sees_trigger_and_modal_but_no_artifact_mutation_controls(
    org: Organisation,
) -> None:
    viewer = _browser(_user(org, "seen-viewer", VIEWER_ROLE))
    analyst = _browser(_user(org, "seen-analyst"))
    viewing = _case(org, title="AC5 viewing case")
    linked = _case(org, title="AC5 linked case")
    obs = _mk_observable("203.0.113.40", org)
    _link(viewing, obs)
    _link(linked, obs)

    url = _case_url(viewing)
    modal_id = f"observable-cases-{obs.id}"

    # Analyst is the control: mutation affordances exist for them (non-vacuity).
    analyst_html = analyst.get(url).content.decode()
    assert f"observable-remove-{obs.id}" in analyst_html
    assert "observable-create-modal" in analyst_html

    viewer_html = viewer.get(url).content.decode()
    # The informational modal is NOT gated by can_mutate.
    assert f'data-modal-open="{modal_id}"' in viewer_html
    assert f'id="{modal_id}"' in viewer_html
    assert "203.0.113.40" in viewer_html, "a viewer still reads the artifact"
    # …but the mutation affordances are.
    assert f"observable-remove-{obs.id}" not in viewer_html
    assert f"observable-edit-{obs.id}" not in viewer_html
    assert "observable-create-modal" not in viewer_html
    assert "Remove from case" not in viewer_html


# --- AC-4: cell count == modal row count -----------------------------------------


def test_seen_in_count_equals_modal_row_count(org: Organisation) -> None:
    browser = _browser(_user(org, "seen-ac4-analyst"))
    viewing = _case(org, title="AC4 viewing case")
    obs = _mk_observable("203.0.113.50", org)
    _link(viewing, obs)
    for i in range(3):
        _link(_case(org, title=f"AC4 linked case {i + 1}"), obs)

    html = browser.get(_case_url(viewing)).content.decode()
    modal_id = f"observable-cases-{obs.id}"
    shown = _button_count(html, modal_id)
    # The viewed case is *itself* one of the cases the artifact was seen in (matching the
    # original `case_observables.count` semantics), so 1 viewing + 3 linked = 4.
    assert shown == 4

    modal = _modal_slice(html, modal_id)
    body_rows = modal.count("<tr>") - 1  # minus the single header row
    assert body_rows == shown, "the Seen in count must equal the modal's row count"


# --- AC-6: zero visible links → plain text, no trigger ---------------------------


def test_zero_visible_links_render_plain_text_without_trigger(org: Organisation) -> None:
    """The zero-visible branch (plan §9 Q1(a)) renders `0 cases` with no control.

    This is exercised at the partial level: a full case-detail page can never reach the
    zero-visible branch, because the viewing case is itself always linked to its rail's
    observables and is always visible to the caller (a 200 on the page proves it). An empty
    `linked_cases_by_observable` for a linked observable is therefore exactly the
    "no visible links" state the branch exists for.
    """
    from django.template.loader import render_to_string
    from django.test import RequestFactory

    case = _case(org, title="AC6 viewing case")
    obs = _mk_observable("198.51.100.9", org)
    _link(case, obs)
    observables = list(case.case_observables.select_related("observable__data_type"))
    context = {
        "case": case,
        "observables": observables,
        "observables_total": len(observables),
        "can_mutate": False,
        "is_owner": False,
    }
    request = RequestFactory().get(f"/cases/{case.number}")

    zero_html = render_to_string(
        "ui/_tab_observables.html",
        {**context, "linked_cases_by_observable": {}},
        request=request,
    )
    assert "0 cases" in zero_html
    assert f"observable-cases-{obs.id}" not in zero_html, "zero-visible must render no trigger"

    # Non-vacuity: the same render WITH a bucket flips the branch to a trigger + dialog,
    # so the assertion above cannot pass because the row stopped rendering entirely.
    seen_html = render_to_string(
        "ui/_tab_observables.html",
        {
            **context,
            "linked_cases_by_observable": {
                obs.id: {
                    "cases": [
                        {
                            "number": case.number,
                            "title": case.title,
                            "status": case.status.value,
                            "severity": case.severity,
                        }
                    ],
                    "total": 1,
                }
            },
        },
        request=request,
    )
    assert f'data-modal-open="observable-cases-{obs.id}"' in seen_html
    assert f'id="observable-cases-{obs.id}"' in seen_html
    assert ">0 cases<" not in seen_html


# --- AC-7: large fan-out → cap + honest total ------------------------------------


def test_seen_in_modal_caps_rows_and_shows_honest_total(org: Organisation) -> None:
    """With RAIL_CAP + 1 visible links, the modal shows RAIL_CAP rows and an honest total."""
    from ui.pagination import RAIL_CAP

    browser = _browser(_user(org, "seen-ac7-analyst"))
    viewing = _case(org, title="AC7 viewing case")
    obs = _mk_observable("203.0.113.60", org)
    _link(viewing, obs)

    from cases.models import Case

    # Unsaved instances + bulk_create: the AllocatingQuerySet manager hands out
    # distinct numbers for the whole batch in one allocation.
    fan_out = [
        Case(title=f"AC7 fan-out case {i + 1}", owner_org=org, status=viewing.status)
        for i in range(RAIL_CAP)
    ]
    Case.objects.bulk_create(fan_out)
    for case in fan_out:
        _link(case, obs)

    html = browser.get(_case_url(viewing)).content.decode()
    modal_id = f"observable-cases-{obs.id}"
    shown = _button_count(html, modal_id)
    assert shown == RAIL_CAP, "the cell count must equal the rendered rows, not the total"

    modal = _modal_slice(html, modal_id)
    body_rows = modal.count("<tr>") - 1  # minus the single header row
    assert body_rows == RAIL_CAP
    assert f"Showing latest {RAIL_CAP} of {RAIL_CAP + 1} cases" in modal
