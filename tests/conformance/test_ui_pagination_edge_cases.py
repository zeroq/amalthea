"""Edge-case conformance tests for the UI pagination wave (PLAN-2026-10-18 §8).

``test_ui_pagination.py`` covers the happy matrix: slice correctness, hostile ``page``
basics, cross-org counts, rails totals. This module covers the probes that file does
NOT reach:

  §1  rendered pager hrefs: ``per_page`` x filter combinations survive into
      ``<nav class="pager">`` links (HTML-escaped, parsed, asserted per parameter).
  §2  exact row boundaries: 0 / 1 / 50 / 51 / 55 (partial last page) / 200 / 201,
      asserted on the rendered summary text ("Showing X-Y of Z", en dash in the page).
  §3  "(filtered from N)" disclosure rendered for alert lists (not just context).
  §4  hostile ``page`` (float / huge int / NUL byte / duplicated param) and hostile
      ``per_page`` (junk, out-of-range, duplicated) end to end through the request.
  §5  cross-org isolation walked through RENDERED ROWS (not just counts), plus the
      global ``settings/users`` list: 404 for an analyst, correct paging for a
      superAdmin.
  §6  genuinely-empty copy vs filtered-empty copy, and the clear-filters escape hatch.
  §7  rails: the newest-RAIL_CAP rows survive the cap and render in display order.
  §8  pager markup contract: ``rel``/``aria``/status/href shape and tag balance.

Known defects pinned down here. Tests marked ``# DEFECT`` are plain tests that are
EXPECTED TO FAIL against the current implementation — each assertion message carries
the defect id and the exact trigger:

  DEF-1  ui/templates/ui/_list_summary.html:19
      ``{{ entity_name_plural|default:entity_name|add:"s" }}`` appends "s" to the
      already-plural name -> "alertss", "casess", "observabless", "runss".
  DEF-2  The "honest empty state" blocks are gated on ``{% if page_obj and ... %}``
      (alert_list.html:87, case_list.html:79, observable_list.html:89,
      automation_list.html:94, case_share_list.html:96, settings_users.html:118).
      A zero-row ``Page`` is falsy (``Page.__len__`` -> 0), so that block is dead
      code and a filtered-empty request falls through to the legacy copy — e.g.
      ``GET /cases/?scope=closed`` with zero closed cases renders "No cases . Open one."
      instead of "No cases match your filters. Clear filters".
  DEF-3  ui/templates/ui/alert_list.html:88 excludes ``scope=unlinked`` from the
      filtered-empty branch although the view treats it as a filter
      (ui/views.py:423-429 sets ``filtered_from``). The default triage queue filtered
      to zero rows is indistinguishable from an empty queue and offers no escape
      hatch, even though context proves three rows were filtered away.
  DEF-4  ui/views.py:634 (case_list) and ui/views.py:2670 (observable_list) compute
      ``filtered_from = queryset.count()`` on the ALREADY-FILTERED queryset, so
      ``filtered_from == total`` and the template guard ``filtered_from != total``
      (_list_summary.html:20) hides "(filtered from N)" entirely.
  DEF-5  ui/templates/ui/_pager.html:21 closes ``<a rel="next">`` with ``</span>``
      instead of ``</a>`` — the rendered pager nav has 2 opening and 1 closing ``a``.

Run:  .venv/bin/pytest -q tests/conformance/test_ui_pagination_edge_cases.py
"""

from __future__ import annotations

import html
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus, TimelineEvent
from identity.models import Organisation, User
from observables.models import Observable, ObservableType
from ui.pagination import RAIL_CAP

NDASH = "\u2013"  # the summary template renders an en dash between start and end

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    """A user without a password hash — browsers below force_login, so no PBKDF2 cost."""
    return User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Edge Org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="Edge Other Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return _mk_user("edgeanalyst", org, role="analyst")


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return _mk_user("edgeoutsider", other_org, role="analyst")


@pytest.fixture
def superadmin(db: None, org: Organisation) -> User:
    return _mk_user("edgesuper", org, role="superAdmin")


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    client.force_login(analyst)
    return client


@pytest.fixture
def outsider_browser(outsider: User) -> Client:
    client = Client()
    client.force_login(outsider)
    return client


@pytest.fixture
def superadmin_browser(superadmin: User) -> Client:
    client = Client()
    client.force_login(superadmin)
    return client


@pytest.fixture
def alert_status(db: None) -> AlertStatus:
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def case_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def observable_type(db: None) -> ObservableType:
    return ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})[0]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _body(response) -> str:
    return response.content.decode()


def _text(source: str) -> str:
    """Rough visible text: unescape entities, drop tags, collapse whitespace."""
    stripped = re.sub(r"<[^>]+>", " ", html.unescape(source))
    return re.sub(r"\s+", " ", stripped).strip()


def _nav(body: str) -> str:
    """The rendered pager nav, or '' when the list fits on one page."""
    match = re.search(r'<nav class="pager".*?</nav>', body, re.S)
    return match.group(0) if match else ""


def _nav_queries(body: str) -> list[dict[str, list[str]]]:
    """Parsed query strings of every href inside the pager nav.

    Hrefs are HTML-escaped in the source (``&amp;``), so unescape before parsing.
    """
    nav = html.unescape(_nav(body))
    return [parse_qs(urlsplit(href).query) for href in re.findall(r'href="([^"]+)"', nav)]


def _make_alerts(
    org: Organisation,
    status: AlertStatus,
    count: int,
    *,
    prefix: str = "edge",
    case: Case | None = None,
) -> list[Alert]:
    alerts = [
        Alert(
            type="phishing",
            source=f"src-{prefix}",
            source_ref=f"{prefix}-{i}",
            title=f"{prefix} alert {i}",
            owner_org=org,
            status=status,
            case=case,
        )
        for i in range(count)
    ]
    return Alert.objects.bulk_create(alerts)


def _make_cases(
    org: Organisation,
    status: CaseStatus,
    assignee: User,
    count: int,
    *,
    prefix: str = "Edge case",
    closed: bool = False,
) -> list[Case]:
    cases = [
        Case(
            title=f"{prefix} {i}",
            status=status,
            owner_org=org,
            assignee=assignee,
            tlp=2,
            pap=2,
            closed_date=(timezone.now() - timedelta(days=5)) if closed else None,
        )
        for i in range(count)
    ]
    return Case.objects.bulk_create(cases)


def _make_observables(
    org: Organisation, otype: ObservableType, count: int, *, prefix: str = "192.0.2."
) -> list[Observable]:
    observables = [
        Observable(
            data_type=otype,
            owner_org=org,
            data=f"{prefix}{i}",
            normalized_data=f"{prefix}{i}",
        )
        for i in range(count)
    ]
    return Observable.objects.bulk_create(observables)


def _make_runs(case: Case, count: int) -> int:
    from automation.models import AutomationRun, Playbook

    playbook, _ = Playbook.objects.get_or_create(
        name="Edge Playbook",
        defaults={"trigger_event": "observable.created", "config": {"action": "http"}},
    )
    runs = [
        AutomationRun(
            case=case,
            playbook=playbook,
            playbook_name="Edge Playbook",
            status="Success",
            output_log=f"Run {i} output",
            idempotency_key=f"edge-{case.id}-{i}",
        )
        for i in range(count)
    ]
    return len(AutomationRun.objects.bulk_create(runs))


def _make_users(org: Organisation, count: int, *, prefix: str = "edgeu") -> list[User]:
    return [_mk_user(f"{prefix}{i:03d}", org) for i in range(count)]


# --- §1: rendered pager hrefs preserve per_page x filter -------------------


@pytest.mark.django_db
def test_pager_hrefs_preserve_scope_and_per_page(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """Pager links on /alerts keep scope=all and per_page=25 on first and last page."""
    _make_alerts(org, alert_status, 60)

    response = browser.get(
        reverse("ui-alert-list"), {"scope": "all", "per_page": "25", "page": "1"}
    )
    assert response.status_code == 200
    queries = _nav_queries(_body(response))
    assert queries, "60 rows at per_page=25 spans 3 pages — the pager nav must render"
    for query in queries:
        assert query.get("scope") == ["all"], f"scope lost in pager href: {query}"
        assert query.get("per_page") == ["25"], f"per_page lost in pager href: {query}"
        assert query.get("page"), f"page missing from pager href: {query}"
    assert queries[0].get("page") == ["2"], "page 1 must offer next = page 2"

    response = browser.get(
        reverse("ui-alert-list"), {"scope": "all", "per_page": "25", "page": "3"}
    )
    assert response.status_code == 200
    queries = _nav_queries(_body(response))
    assert len(queries) == 1, "last page offers only prev (no next)"
    assert queries[0].get("page") == ["2"], "page 3 must offer prev = page 2"
    for query in queries:
        assert query.get("scope") == ["all"], f"scope lost in pager href: {query}"
        assert query.get("per_page") == ["25"], f"per_page lost in pager href: {query}"


@pytest.mark.django_db
def test_pager_hrefs_preserve_closed_filter_and_per_page(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """Pager links on /cases?scope=closed keep scope=closed and per_page=25."""
    _make_cases(org, case_status, analyst, 60, prefix="Closed case", closed=True)

    response = browser.get(
        reverse("ui-case-list"), {"scope": "closed", "per_page": "25", "page": "3"}
    )
    assert response.status_code == 200
    queries = _nav_queries(_body(response))
    assert queries, "60 closed rows at per_page=25 spans 3 pages — pager must render"
    for query in queries:
        assert query.get("scope") == ["closed"], f"scope=closed lost in pager href: {query}"
        assert query.get("per_page") == ["25"], f"per_page lost in pager href: {query}"
    assert queries[0].get("page") == ["2"], "page 3 must offer prev = page 2"


@pytest.mark.django_db
def test_pager_hrefs_preserve_q_filter_and_per_page(
    browser: Client, org: Organisation, observable_type: ObservableType
) -> None:
    """Pager links on /observables keep q and per_page; a middle page offers prev + next."""
    _make_observables(org, observable_type, 60, prefix="192.0.2.")
    _make_observables(org, observable_type, 40, prefix="198.51.100.")

    response = browser.get(
        reverse("ui-observable-list"), {"q": "192.0.2", "per_page": "25", "page": "2"}
    )
    assert response.status_code == 200
    queries = _nav_queries(_body(response))
    assert len(queries) == 2, f"middle page must offer prev and next, got {queries}"
    for query in queries:
        assert query.get("q") == ["192.0.2"], f"q lost in pager href: {query}"
        assert query.get("per_page") == ["25"], f"per_page lost in pager href: {query}"
    assert sorted(int(q["page"][0]) for q in queries) == [1, 3]


# --- §2: exact row boundaries, rendered summary text ----------------------


@pytest.mark.django_db
def test_boundary_exactly_default_page_has_no_pager(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """Exactly 50 rows (one full default page) renders 'Showing 1-50 of 50', no pager."""
    _make_alerts(org, alert_status, 50)
    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.num_pages == 1
    body = _body(response)
    assert 'class="pager"' not in body, "single page must not render the pager nav"
    assert f"Showing 1{NDASH}50 of 50" in _text(body)


@pytest.mark.django_db
def test_boundary_default_plus_one_second_page_renders_single_row(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """51 rows: page 2 renders exactly row 51 and says 'Showing 51 of 51'."""
    _make_alerts(org, alert_status, 51)
    response = browser.get(reverse("ui-alert-list"), {"page": "2"})
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.start_index() == page_obj.end_index() == 51
    assert len(page_obj.object_list) == 1
    body = _body(response)
    assert "Showing 51 of 51" in _text(body)
    assert 'rel="prev"' in body


@pytest.mark.django_db
def test_boundary_partial_last_page_renders_true_range(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """55 rows: page 2 renders 'Showing 51-55 of 55' (partial page, honest range)."""
    _make_alerts(org, alert_status, 55)
    response = browser.get(reverse("ui-alert-list"), {"page": "2"})
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.start_index() == 51
    assert page_obj.end_index() == 55
    assert "Showing 51" + NDASH + "55 of 55" in _text(_body(response))


@pytest.mark.django_db
def test_boundary_single_row_org(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """One row total: 'Showing 1 of 1', no pager, no 0-0 artefacts."""
    _make_alerts(org, alert_status, 1)
    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.count == 1
    body = _body(response)
    assert "Showing 1 of 1" in _text(body)
    assert 'class="pager"' not in body


@pytest.mark.django_db
def test_boundary_max_page_size_exact(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """200 rows at per_page=200 fits exactly one page: 'Showing 1-200 of 200', no pager."""
    _make_alerts(org, alert_status, 200)
    response = browser.get(reverse("ui-alert-list"), {"per_page": "200"})
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.per_page == 200
    assert page_obj.paginator.num_pages == 1
    body = _body(response)
    assert f"Showing 1{NDASH}200 of 200" in _text(body)
    assert 'class="pager"' not in body


@pytest.mark.django_db
def test_boundary_max_page_size_plus_one(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """201 rows at per_page=200: page 2 renders row 201 alone with prev only."""
    _make_alerts(org, alert_status, 201)
    response = browser.get(reverse("ui-alert-list"), {"per_page": "200", "page": "2"})
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.number == 2
    assert page_obj.start_index() == page_obj.end_index() == 201
    body = _body(response)
    assert "Showing 201 of 201" in _text(body)
    assert 'rel="prev"' in body
    assert 'rel="next"' not in body


@pytest.mark.django_db
def test_boundary_zero_rows_renders_no_summary(browser: Client, org: Organisation) -> None:
    """An empty list never renders 'Showing ... of 0' or a '0-0' range."""
    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    text = _text(_body(response))
    assert "Showing" not in text, f"no summary line belongs on an empty list: {text!r}"
    assert "of 0" not in text, f"no 'of 0' count belongs on an empty list: {text!r}"


# --- §3: (filtered from N) rendered disclosure (alert list does it right) --


@pytest.mark.django_db
def test_filtered_from_rendered_for_scope_linked(
    browser: Client,
    org: Organisation,
    analyst: User,
    case_status: CaseStatus,
    alert_status: AlertStatus,
) -> None:
    """scope=linked with 1 linked of 11 total renders '(filtered from 11)'."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    _make_alerts(org, alert_status, 10, prefix="unlinked")
    _make_alerts(org, alert_status, 1, prefix="linked", case=case)

    response = browser.get(reverse("ui-alert-list"), {"scope": "linked"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert "(filtered from 11)" in _text(_body(response))


@pytest.mark.django_db
def test_filtered_from_rendered_for_default_scope(
    browser: Client,
    org: Organisation,
    analyst: User,
    case_status: CaseStatus,
    alert_status: AlertStatus,
) -> None:
    """The default scope=unlinked is a filter too: 10 of 11 renders '(filtered from 11)'."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    _make_alerts(org, alert_status, 10, prefix="unlinked")
    _make_alerts(org, alert_status, 1, prefix="linked", case=case)

    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 10
    assert response.context["filtered_from"] == 11
    assert "(filtered from 11)" in _text(_body(response))


@pytest.mark.django_db
def test_filtered_from_absent_for_scope_all(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """scope=all is not a filter: no '(filtered from …)' disclosure."""
    _make_alerts(org, alert_status, 10)
    response = browser.get(reverse("ui-alert-list"), {"scope": "all"})
    assert response.status_code == 200
    assert response.context["filtered_from"] is None
    assert "(filtered from" not in _text(_body(response))


@pytest.mark.django_db
def test_filtered_from_absent_for_unfiltered_observables(
    browser: Client, org: Organisation, observable_type: ObservableType
) -> None:
    """No filter on /observables means no '(filtered from …)' disclosure."""
    _make_observables(org, observable_type, 60)
    response = browser.get(reverse("ui-observable-list"))
    assert response.status_code == 200
    assert response.context["filtered_from"] is None
    assert "(filtered from" not in _text(_body(response))


# --- §4: hostile page / per_page end to end -------------------------------


@pytest.mark.parametrize("bad_page", ["1.5", "1e309", "99999999999999999999", "\x00"])
@pytest.mark.django_db
def test_hostile_page_values_never_500(
    browser: Client, org: Organisation, alert_status: AlertStatus, bad_page: str
) -> None:
    """Float, out-of-float-int, absurd integer and NUL byte page values stay on 1 or 2."""
    _make_alerts(org, alert_status, 100)
    response = browser.get(reverse("ui-alert-list"), {"page": bad_page})
    assert response.status_code == 200, f"page={bad_page!r} caused {response.status_code}"
    assert response.context["page_obj"].number in (1, 2)


@pytest.mark.django_db
def test_duplicate_page_params_last_wins(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """?page=2&page=3 resolves via QueryDict semantics (last value wins): page 3, no 500."""
    _make_alerts(org, alert_status, 150)  # 3 pages, so 2 vs 3 is distinguishable
    response = browser.get(reverse("ui-alert-list"), {"page": ["2", "3"]})
    assert response.status_code == 200
    assert response.context["page_obj"].number == 3


@pytest.mark.parametrize("bad_per_page", ["100000", "0", "-5", "1.5", "\x00", ""])
@pytest.mark.django_db
def test_hostile_per_page_values_fall_back_to_default(
    browser: Client, org: Organisation, alert_status: AlertStatus, bad_per_page: str
) -> None:
    """Out-of-allow-list per_page values (junk, zero, negative, NUL, blank) use 50."""
    _make_alerts(org, alert_status, 60)
    response = browser.get(reverse("ui-alert-list"), {"per_page": bad_per_page})
    assert response.status_code == 200, f"per_page={bad_per_page!r} caused {response.status_code}"
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.per_page == 50
    assert page_obj.paginator.num_pages == 2


@pytest.mark.django_db
def test_duplicate_per_page_params_last_wins(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """?per_page=200&per_page=25 resolves via QueryDict semantics (last wins): 25 rows/page."""
    _make_alerts(org, alert_status, 60)
    response = browser.get(reverse("ui-alert-list"), {"per_page": ["200", "25"]})
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.per_page == 25
    assert page_obj.paginator.num_pages == 3


# --- §5: cross-org isolation walked through rendered rows -----------------


@pytest.mark.django_db
def test_cross_org_alert_rows_walk(
    browser: Client,
    outsider_browser: Client,
    org: Organisation,
    other_org: Organisation,
    alert_status: AlertStatus,
) -> None:
    """Neither org ever renders the other org's alert rows on any page."""
    _make_alerts(org, alert_status, 60, prefix="ALPHA")
    _make_alerts(other_org, alert_status, 60, prefix="BRAVO")

    for page in ("1", "2"):
        own = browser.get(reverse("ui-alert-list"), {"page": page})
        assert own.status_code == 200
        own_body = _body(own)
        assert "ALPHA" in own_body, f"own rows missing on page {page}"
        assert "BRAVO" not in own_body, f"other org's alert leaked onto page {page}"

        theirs = outsider_browser.get(reverse("ui-alert-list"), {"page": page})
        assert theirs.status_code == 200
        theirs_body = _body(theirs)
        assert "BRAVO" in theirs_body, f"own rows missing on page {page}"
        assert "ALPHA" not in theirs_body, f"other org's alert leaked onto page {page}"

    assert browser.get(reverse("ui-alert-list")).context["page_obj"].paginator.count == 60
    assert outsider_browser.get(reverse("ui-alert-list")).context["page_obj"].paginator.count == 60


@pytest.mark.django_db
def test_cross_org_case_rows_walk(
    browser: Client,
    outsider_browser: Client,
    org: Organisation,
    other_org: Organisation,
    analyst: User,
    outsider: User,
    case_status: CaseStatus,
) -> None:
    """Neither org ever renders the other org's case rows on any page."""
    _make_cases(org, case_status, analyst, 60, prefix="ALPHA case")
    _make_cases(other_org, case_status, outsider, 60, prefix="BRAVO case")

    for page in ("1", "2"):
        own = browser.get(reverse("ui-case-list"), {"page": page})
        assert own.status_code == 200
        own_body = _body(own)
        assert "ALPHA case" in own_body, f"own rows missing on page {page}"
        assert "BRAVO" not in own_body, f"other org's case leaked onto page {page}"

        theirs = outsider_browser.get(reverse("ui-case-list"), {"page": page})
        assert theirs.status_code == 200
        theirs_body = _body(theirs)
        assert "BRAVO case" in theirs_body, f"own rows missing on page {page}"
        assert "ALPHA" not in theirs_body, f"other org's case leaked onto page {page}"


@pytest.mark.django_db
def test_cross_org_observable_rows_walk(
    browser: Client,
    outsider_browser: Client,
    org: Organisation,
    other_org: Organisation,
    observable_type: ObservableType,
) -> None:
    """Neither org ever renders the other org's observable rows on any page."""
    _make_observables(org, observable_type, 60, prefix="192.0.2.")
    _make_observables(other_org, observable_type, 60, prefix="198.51.100.")

    for page in ("1", "2"):
        own = browser.get(reverse("ui-observable-list"), {"page": page})
        assert own.status_code == 200
        own_body = _body(own)
        assert "192.0.2." in own_body, f"own rows missing on page {page}"
        assert "198.51.100." not in own_body, f"other org's observable leaked onto page {page}"

        theirs = outsider_browser.get(reverse("ui-observable-list"), {"page": page})
        assert theirs.status_code == 200
        theirs_body = _body(theirs)
        assert "198.51.100." in theirs_body, f"own rows missing on page {page}"
        assert "192.0.2." not in theirs_body, f"other org's observable leaked onto page {page}"


@pytest.mark.django_db
def test_settings_users_404_for_non_superadmin(browser: Client) -> None:
    """settings/users is superAdmin-only (and global by design): an analyst gets 404."""
    for params in ({}, {"page": "2"}):
        response = browser.get(reverse("ui-settings-users"), params)
        assert response.status_code == 404, f"analyst reached settings/users with {params}"


@pytest.mark.django_db
def test_settings_users_superadmin_sees_multiple_orgs(
    superadmin_browser: Client, org: Organisation, other_org: Organisation
) -> None:
    """Positive control: the superAdmin sees users from all orgs in per-org cards."""
    _make_users(org, 60)
    _mk_user("zzother", other_org)  # proves the list includes other orgs

    response = superadmin_browser.get(reverse("ui-settings-users"))
    assert response.status_code == 200
    # SuperAdmin uses per-org cards, not pagination
    assert response.context["page_obj"] is None
    org_contexts = response.context["org_contexts"]
    # There may be a Default org with 0 users; we care about our two test orgs
    test_org_contexts = [
        ctx for ctx in org_contexts if ctx["org"].name in (org.name, other_org.name)
    ]
    assert len(test_org_contexts) == 2
    # Check that users from both orgs are visible
    body = _body(response)
    assert "zzother" in body, "superadmin must see users from every organisation"
    # The first org has 61 users (60 created + superadmin), capped at 200 so not capped
    # The second org has 1 user
    for ctx in test_org_contexts:
        if ctx["org"].name == org.name:
            assert ctx["member_count"] == 61
            assert not ctx["capped"]
        else:
            assert ctx["member_count"] == 1
            assert not ctx["capped"]


# --- §6: genuinely-empty copy vs filtered-empty copy ----------------------


@pytest.mark.django_db
def test_alert_genuinely_empty_copy(browser: Client, org: Organisation) -> None:
    """A truly empty queue says 'No alerts in this scope.' and offers no clear-filters."""
    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    assert response.context["filtered_from"] is None
    text = _text(_body(response))
    assert "No alerts in this scope." in text
    assert "Clear filters" not in text


@pytest.mark.django_db
def test_case_genuinely_empty_copy(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """A truly empty case list says 'No cases open.' with the create affordance."""
    response = browser.get(reverse("ui-case-list"))
    assert response.status_code == 200
    text = _text(_body(response))
    assert "No cases open." in text
    assert "Open one" in text


@pytest.mark.django_db
def test_observable_genuinely_empty_copy_and_clear_link(browser: Client, org: Organisation) -> None:
    """A truly empty observable list says so, and the filter bar always offers Clear."""
    response = browser.get(reverse("ui-observable-list"))
    assert response.status_code == 200
    body = _body(response)
    assert "No observables found." in _text(body)
    assert '<a class="btn" href="/observables">Clear</a>' in body, (
        "the filter bar's Clear link must point at the bare list URL"
    )


@pytest.mark.django_db
def test_automation_genuinely_empty_copy(browser: Client, org: Organisation) -> None:
    """A truly empty automation list says 'No runs recorded.'"""
    response = browser.get(reverse("ui-automation-list"))
    assert response.status_code == 200
    assert "No runs recorded." in _text(_body(response))


@pytest.mark.django_db
def test_case_share_genuinely_empty_copy(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """A case with no shares says 'No shares configured. …' on the shares page."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    response = browser.get(reverse("ui-case-share-list", kwargs={"case_id": case.number}))
    assert response.status_code == 200
    assert "No shares configured." in _text(_body(response))


# --- §7: rails — newest-N survives the cap, renders in display order -------


@pytest.mark.django_db
def test_timeline_rail_cap_keeps_newest_and_count_line(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """RAIL_CAP+20 events: newest 200 kept (oldest dropped) with an honest count line."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    base = timezone.now() - timedelta(days=30)
    TimelineEvent.objects.bulk_create(
        [
            TimelineEvent(
                case=case,
                date=base + timedelta(minutes=i),
                title=f"EVENT-{i:04d}",
                kind="status-changed",
            )
            for i in range(RAIL_CAP + 20)
        ]
    )

    response = browser.get(
        reverse("ui-case-detail", kwargs={"case_id": case.number}), {"tab": "audit"}
    )
    assert response.status_code == 200
    timeline = response.context["timeline"]
    assert response.context["timeline_total"] == RAIL_CAP + 20
    assert len(timeline) == RAIL_CAP
    titles = [event.title for event in timeline]
    assert titles[0] == "EVENT-0020", "oldest survivor is the first kept row"
    assert titles[-1] == "EVENT-0219", "newest event must be the last rendered row"
    assert "EVENT-0000" not in titles, "rows beyond the cap (oldest) must be dropped"

    text = _text(_body(response))
    assert f"Showing latest {RAIL_CAP} of {RAIL_CAP + 20} events" in text
    assert "EVENT-0219" in text
    assert "EVENT-0000" not in text, "the dropped oldest event must not be rendered"


@pytest.mark.django_db
def test_timeline_rail_renders_ascending_display_order(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """Below the cap the timeline renders oldest-first, with no count line."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    base = timezone.now() - timedelta(days=30)
    TimelineEvent.objects.bulk_create(
        [
            TimelineEvent(
                case=case,
                date=base + timedelta(minutes=i),
                title=f"EVENT-{i:04d}",
                kind="status-changed",
            )
            for i in range(10)
        ]
    )

    response = browser.get(
        reverse("ui-case-detail", kwargs={"case_id": case.number}), {"tab": "audit"}
    )
    assert response.status_code == 200
    body = _body(response)
    match = re.search(r'<ol class="timeline" id="audit-timeline".*?</ol>', body, re.S)
    assert match, "the audit timeline list must render"
    segment = match.group(0)
    positions = [segment.index(f"EVENT-{i:04d}") for i in range(10)]
    assert positions == sorted(positions), "timeline must render oldest-first (display order)"
    assert "Showing latest" not in _text(body), "no count line when nothing is capped"


# --- §8: pager markup contract --------------------------------------------


@pytest.mark.django_db
def test_pager_markup_rel_aria_status_hrefs(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """The pager nav carries rel, aria-label, status text and correct page hrefs."""
    _make_alerts(org, alert_status, 60)

    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    nav = _nav(_body(response))
    assert nav, "60 rows must render the pager nav"
    assert 'aria-label="Alert pagination"' in nav
    assert "Page 1 of 2" in nav
    assert 'rel="next"' in nav and 'rel="prev"' not in nav, "page 1 offers next only"
    assert _nav_queries(_body(response))[0].get("page") == ["2"]

    response = browser.get(reverse("ui-alert-list"), {"page": "2"})
    nav = _nav(_body(response))
    assert 'rel="prev"' in nav and 'rel="next"' not in nav, "last page offers prev only"
    assert "Page 2 of 2" in nav
    assert _nav_queries(_body(response))[0].get("page") == ["1"]


@pytest.mark.django_db
def test_pager_next_link_balanced_tags_defect(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """DEFECT DEF-5 — trigger: GET /alerts/ with 60 rows (page 1 renders <a rel="next">)."""
    _make_alerts(org, alert_status, 60)
    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    nav = _nav(_body(response))
    assert nav, "60 rows must render the pager nav"
    opens = nav.count("<a ")
    closes = nav.count("</a>")
    assert opens == closes, (
        f'DEF-5 (ui/templates/ui/_pager.html:21): the <a rel="next"> element is closed '
        f"with </span> instead of </a> — pager nav has {opens} opening and {closes} "
        f"closing <a> tags. Trigger: GET /alerts/ with 60+ rows (any page with a next link)."
    )


# --- DEF-1: list summary doubles the plural entity name -------------------
# DEFECT TESTS — expected to fail. Trigger for each: the GET in the test body.


@pytest.mark.django_db
def test_summary_plural_alerts_defect(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """DEFECT DEF-1 — trigger: GET /alerts/ with 60 rows (summary renders)."""
    _make_alerts(org, alert_status, 60)
    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    text = _text(_body(response))
    assert "alertss" not in text, (
        "DEF-1 (ui/templates/ui/_list_summary.html:19): "
        '{{ entity_name_plural|default:entity_name|add:"s" }} appends "s" to the '
        "already-plural name — the summary reads 'of 60 alertss'. "
        "Trigger: GET /alerts/ with >=1 alert."
    )
    assert re.search(r"of 60 alerts\b", text), f"summary must read 'of 60 alerts': {text!r}"


@pytest.mark.django_db
def test_summary_plural_cases_defect(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """DEFECT DEF-1 — trigger: GET /cases/ with 10 rows (summary renders)."""
    _make_cases(org, case_status, analyst, 10)
    response = browser.get(reverse("ui-case-list"))
    assert response.status_code == 200
    text = _text(_body(response))
    assert "casess" not in text, (
        "DEF-1 (ui/templates/ui/_list_summary.html:19): the summary reads 'of 10 casess'. "
        "Trigger: GET /cases/ with >=1 open case."
    )
    assert re.search(r"of 10 cases\b", text), f"summary must read 'of 10 cases': {text!r}"


@pytest.mark.django_db
def test_summary_plural_observables_defect(
    browser: Client, org: Organisation, observable_type: ObservableType
) -> None:
    """DEFECT DEF-1 — trigger: GET /observables/ with 10 rows (summary renders)."""
    _make_observables(org, observable_type, 10)
    response = browser.get(reverse("ui-observable-list"))
    assert response.status_code == 200
    text = _text(_body(response))
    assert "observabless" not in text, (
        "DEF-1 (ui/templates/ui/_list_summary.html:19): the summary reads 'of 10 "
        "observabless'. Trigger: GET /observables/ with >=1 observable."
    )
    assert re.search(r"of 10 observables\b", text), (
        f"summary must read 'of 10 observables': {text!r}"
    )


@pytest.mark.django_db
def test_summary_plural_runs_defect(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """DEFECT DEF-1 — trigger: GET /automation/ with 10 runs (summary renders)."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    _make_runs(case, 10)
    response = browser.get(reverse("ui-automation-list"))
    assert response.status_code == 200
    text = _text(_body(response))
    assert "runss" not in text, (
        "DEF-1 (ui/templates/ui/_list_summary.html:19): the summary reads 'of 10 runss'. "
        "Trigger: GET /automation/ with >=1 run."
    )
    assert re.search(r"of 10 runs\b", text), f"summary must read 'of 10 runs': {text!r}"


# --- DEF-2: honest-empty block is dead code (zero-row Page is falsy) -------
# DEFECT TESTS — expected to fail. Trigger for each: the GET in the test body.


@pytest.mark.django_db
def test_case_filtered_empty_copy_defect(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """DEFECT DEF-2 — trigger: GET /cases/?scope=closed with 0 closed (5 open exist)."""
    _make_cases(org, case_status, analyst, 5)
    response = browser.get(reverse("ui-case-list"), {"scope": "closed"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    text = _text(_body(response))
    assert "No cases match your filters." in text, (
        "DEF-2 (case_list.html:79): the honest-empty block is gated on "
        "{% if page_obj and ... %} but a zero-row Page is falsy, so the legacy copy "
        f"renders instead (observed: {text[text.find('No cases') :][:120]!r}). "
        "Trigger: GET /cases/?scope=closed with zero closed cases."
    )
    assert "Clear filters" in text, "DEF-2: a filtered-empty list must offer Clear filters"


@pytest.mark.django_db
def test_observable_filtered_empty_copy_defect(
    browser: Client, org: Organisation, observable_type: ObservableType
) -> None:
    """DEFECT DEF-2 — trigger: GET /observables/?q=zzz with 40 observables present."""
    _make_observables(org, observable_type, 40)
    response = browser.get(reverse("ui-observable-list"), {"q": "zzz"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    text = _text(_body(response))
    assert "No observables match your filters." in text, (
        "DEF-2 (observable_list.html:89): the honest-empty block is unreachable "
        "(zero-row Page is falsy) - observed copy: 'No observables found.' "
        "Trigger: GET /observables/?q=<no match> with observables present."
    )
    assert "Clear filters" in text, "DEF-2: a filtered-empty list must offer Clear filters"


@pytest.mark.django_db
def test_alert_linked_empty_copy_defect(
    browser: Client, org: Organisation, alert_status: AlertStatus
) -> None:
    """DEFECT DEF-2 — trigger: GET /alerts/?scope=linked with 0 linked (3 unlinked exist)."""
    _make_alerts(org, alert_status, 3, prefix="unlinked")

    response = browser.get(reverse("ui-alert-list"), {"scope": "linked"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    assert response.context["filtered_from"] == 3
    text = _text(_body(response))
    assert "No alerts match your filters." in text, (
        "DEF-2 (alert_list.html:87): the honest-empty block is unreachable "
        "(zero-row Page is falsy) - observed copy: 'No alerts in this scope.' "
        "Trigger: GET /alerts/?scope=linked with zero linked alerts."
    )
    assert "Clear filters" in text, "DEF-2: a filtered-empty list must offer Clear filters"


# --- DEF-3: default scope=unlinked has no escape hatch when empty ----------
# DEFECT TEST — expected to fail.


@pytest.mark.django_db
def test_alert_default_scope_empty_no_escape_hatch_defect(
    browser: Client,
    org: Organisation,
    analyst: User,
    case_status: CaseStatus,
    alert_status: AlertStatus,
) -> None:
    """DEFECT DEF-3 — trigger: GET /alerts/ with 0 unlinked and 3 linked alerts."""
    case = _make_cases(org, case_status, analyst, 1)[0]
    _make_alerts(org, alert_status, 3, prefix="linked", case=case)

    response = browser.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    # The view already treats scope=unlinked as a filter and says so in context …
    assert response.context["filtered_from"] == 3, "view must pass the unfiltered total"
    text = _text(_body(response))
    # … but the page must tell the analyst rows were filtered away and offer a way out.
    assert "Clear filters" in text or "(filtered from 3)" in text, (
        "DEF-3 (alert_list.html:88): scope=unlinked is excluded from the filtered-empty "
        "branch while ui/views.py:423-429 sets filtered_from — the default triage queue "
        "filtered to zero rows is indistinguishable from an empty queue (observed: "
        f"{text[text.find('No alerts') :][:80]!r}, no clear-filters, no filtered-from "
        "disclosure). Trigger: GET /alerts/ with 3 linked and 0 unlinked alerts."
    )


# --- DEF-4: filtered_from counts the filtered set, not the unfiltered set --
# DEFECT TESTS — expected to fail. Trigger for each: the GET in the test body.


@pytest.mark.django_db
def test_case_list_filtered_from_counts_filtered_set_defect(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """DEFECT DEF-4 — trigger: GET /cases/?scope=closed with 40 open + 60 closed."""
    _make_cases(org, case_status, analyst, 40, prefix="Open case")
    _make_cases(org, case_status, analyst, 60, prefix="Closed case", closed=True)

    response = browser.get(reverse("ui-case-list"), {"scope": "closed"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 60
    observed = response.context["filtered_from"]
    text = _text(_body(response))
    assert "(filtered from 100)" in text, (
        "DEF-4 (ui/views.py:634): case_list computes filtered_from on the ALREADY-FILTERED "
        "queryset, so filtered_from == total and the template guard hides the disclosure. "
        f"Expected '(filtered from 100)', observed context filtered_from={observed!r} and "
        "no disclosure rendered. Trigger: GET /cases/?scope=closed with 40 open + 60 closed."
    )


@pytest.mark.django_db
def test_observable_list_filtered_from_counts_filtered_set_defect(
    browser: Client, org: Organisation, observable_type: ObservableType
) -> None:
    """DEFECT DEF-4 — trigger: GET /observables/?q=192.0.2 with 60 matching + 40 others."""
    _make_observables(org, observable_type, 60, prefix="192.0.2.")
    _make_observables(org, observable_type, 40, prefix="198.51.100.")

    response = browser.get(reverse("ui-observable-list"), {"q": "192.0.2"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 60
    observed = response.context["filtered_from"]
    text = _text(_body(response))
    assert "(filtered from 100)" in text, (
        "DEF-4 (ui/views.py:2670): observable_list computes filtered_from on the "
        "ALREADY-FILTERED queryset, so filtered_from == total and the template guard hides "
        f"the disclosure. Expected '(filtered from 100)', observed context "
        f"filtered_from={observed!r} and no disclosure rendered. "
        "Trigger: GET /observables/?q=192.0.2 with 60 matching + 40 other observables."
    )
