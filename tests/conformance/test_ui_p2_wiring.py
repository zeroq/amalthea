"""P2 wiring tests — the UI half of the triage/edit wave (plan §P2, C1-C3).

Covers:
- C1 alert triage: bounded status vocabulary, audit, case timeline, uniform 404.
- C2 case edit: shared applier parity with the API PATCH, bounded status, CSRF, 404.
- C3 observable edit/delete: camelCase wire shape, immutable `data`, cross-case force
  guard on *both* edit and delete, audit row carries the real pk (not "None"), 404.

These assert the *wiring* the frontend templates depend on (field names, select values,
form actions) as well as the behaviour, because the two agents build the two halves
independently and only the rendered contract binds them.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import AlertStatus
from cases.models import Case, CaseObservable, CaseStatus
from cases.update import CaseUpdateError, apply_case_update
from identity.models import AuditLog, Organisation, User
from observables.models import Observable, ObservableType

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="P2 Org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="P2 Other Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="p2analyst",
        login="p2analyst",
        password="hunter2-correct",
        email="p2analyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return User.objects.create_user(
        username="p2outsider",
        login="p2outsider",
        password="hunter2-correct",
        email="p2outsider@example.net",
        org=other_org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="p2analyst", password="hunter2-correct")
    return client


@pytest.fixture
def outsider_browser(outsider: User) -> Client:
    client = Client()
    assert client.login(username="p2outsider", password="hunter2-correct")
    return client


@pytest.fixture
def csrf_browser(analyst: User) -> Client:
    """A client that actually enforces CSRF, so the guard can be asserted."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(analyst)
    return client


@pytest.fixture
def case(db: None, org: Organisation, analyst: User) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(
        title="P2 case", status=status, owner_org=org, assignee=analyst, tlp=2, pap=2
    )


@pytest.fixture
def alert(db: None, org: Organisation):
    from alerts.models import Alert

    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        type="phishing",
        source="p2-source",
        source_ref="p2-ref-1",
        title="P2 phishing alert",
        owner_org=org,
        status=status,
    )


@pytest.fixture
def observable(db: None, org: Organisation) -> Observable:
    otype, _ = ObservableType.objects.get_or_create(name="ip")
    return Observable.objects.create(
        data_type=otype, owner_org=org, data="192.0.2.10", normalized_data="192.0.2.10"
    )


def _link(observable: Observable, case: Case) -> CaseObservable:
    return CaseObservable.objects.create(case=case, observable=observable)


# --------------------------------------------------------------------------
# C1 — Alert triage
# --------------------------------------------------------------------------


def test_alert_detail_renders_triage_control(browser: Client, alert) -> None:
    """C1: the triage form posts to ui-alert-status and submits status *values*, not pks.

    The view validates `AlertStatus.value`; if the template submitted `opt.pk` every
    submission would be rejected ("Invalid status: <uuid>").
    """
    url = reverse("ui-alert-detail", kwargs={"alert_id": alert.id})
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    assert f'action="{reverse("ui-alert-status", kwargs={"alert_id": alert.id})}"' in body
    assert 'name="status"' in body
    # Vocabulary values, never the pk.
    assert 'value="Triaged"' in body
    assert 'value="Dismissed"' in body
    assert f'value="{alert.status.pk}"' not in body
    # "Imported" is system-assigned and is not offered.
    assert 'value="Imported"' not in body


def test_alert_status_post_changes_status_and_audits(browser: Client, alert) -> None:
    """C1: a valid value changes the status and records an audit row."""
    AuditLog.objects.all().delete()
    url = reverse("ui-alert-status", kwargs={"alert_id": alert.id})
    response = browser.post(url, {"status": "Triaged"})

    assert response.status_code == 302
    alert.refresh_from_db()
    assert alert.status.value == "Triaged"

    audit = AuditLog.objects.filter(action="alert.status_changed", target_id=str(alert.id)).first()
    assert audit is not None
    assert audit.actor.login == "p2analyst"
    assert audit.target_type == "Alert"


def test_alert_status_post_never_mints_a_status(browser: Client, alert) -> None:
    """C1: a crafted/garbage value neither changes the alert nor auto-creates a status."""
    before = AlertStatus.objects.count()
    url = reverse("ui-alert-status", kwargs={"alert_id": alert.id})
    response = browser.post(url, {"status": "TotallyMadeUp"})

    assert response.status_code == 302
    alert.refresh_from_db()
    assert alert.status.value == "New"
    assert AlertStatus.objects.count() == before
    assert not AlertStatus.objects.filter(value="TotallyMadeUp").exists()


def test_alert_status_post_writes_case_timeline(browser: Client, alert, case) -> None:
    """C1: when the alert is attached to a case, the change lands in the case ledger."""
    alert.case = case
    alert.save(update_fields=["case"])
    url = reverse("ui-alert-status", kwargs={"alert_id": alert.id})
    browser.post(url, {"status": "Dismissed"})

    assert case.timeline_events.filter(kind="status-changed").exists()


def test_alert_status_requires_post(browser: Client, alert) -> None:
    """C1: GET is not allowed (405) — the endpoint is a POST-only mutation."""
    url = reverse("ui-alert-status", kwargs={"alert_id": alert.id})
    assert browser.get(url).status_code == 405


def test_alert_status_is_org_scoped_uniform_404(outsider_browser: Client, alert) -> None:
    """C1: another org sees a uniform 404 and cannot mutate the alert."""
    url = reverse("ui-alert-status", kwargs={"alert_id": alert.id})
    assert outsider_browser.post(url, {"status": "Dismissed"}).status_code == 404
    alert.refresh_from_db()
    assert alert.status.value == "New"


# --------------------------------------------------------------------------
# C2 — Case edit
# --------------------------------------------------------------------------


def test_case_edit_get_prefills_and_preselects(browser: Client, case: Case) -> None:
    """C2: GET renders the form, prefilled, with the enum selects actually preselected.

    Guards the int-vs-str comparison bug: `form_values.tlp` is a string and the choices
    are ints, so a naive `==` never marks an option `selected`.
    """
    case.tlp = 3
    case.severity = 4
    case.save(update_fields=["tlp", "severity"])

    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    assert f'action="{url}"' in body
    assert 'value="P2 case"' in body
    assert 'value="3" selected' in body  # TLP = Red
    assert 'value="4" selected' in body  # severity = Critical


def test_case_edit_post_applies_updates(browser: Client, case: Case) -> None:
    """C2: POST applies the field set and redirects to the case detail page."""
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "title": "Renamed case",
            "description": "new body",
            "summary": "new summary",
            "severity": "4",
            "tlp": "1",
            "pap": "0",
            "flag": "on",
            "status": "New",
            "assignee": "p2analyst",
            "tags": "phishing, apt29",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})

    case.refresh_from_db()
    assert case.title == "Renamed case"
    assert case.severity == 4
    assert case.tlp == 1
    assert case.pap == 0
    assert case.flag is True
    assert set(case.tags.values_list("name", flat=True)) == {"phishing", "apt29"}


def test_case_edit_flag_can_be_cleared(browser: Client, case: Case) -> None:
    """C2: the form always renders the checkbox, so an absent key clears the flag."""
    case.flag = True
    case.save(update_fields=["flag"])
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    browser.post(url, {"title": case.title, "status": "New"})

    case.refresh_from_db()
    assert case.flag is False


def test_case_edit_status_is_bounded(browser: Client, case: Case) -> None:
    """C2: a crafted status value re-renders with an error and mints no new status."""
    before = CaseStatus.objects.count()
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    response = browser.post(url, {"title": case.title, "status": "MadeUpStage"})

    assert response.status_code == 200  # re-rendered, not redirected
    assert CaseStatus.objects.count() == before
    case.refresh_from_db()
    assert case.status.value == "New"


def test_case_edit_status_change_writes_timeline(browser: Client, case: Case) -> None:
    """C2: parity with the API PATCH — a status change appends a `status-changed` event."""
    target, _ = CaseStatus.objects.get_or_create(
        value="Investigating", defaults={"stage": "InProgress", "order": 2}
    )
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    response = browser.post(url, {"title": case.title, "status": target.value})

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status_id == target.id

    event = case.timeline_events.filter(kind="status-changed").first()
    assert event is not None
    assert event.metadata.get("to") == "Investigating"


def test_case_edit_is_org_scoped_uniform_404(outsider_browser: Client, case: Case) -> None:
    """C2: another org gets a uniform 404 on GET *and* POST, and cannot mutate."""
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    assert outsider_browser.get(url).status_code == 404
    assert outsider_browser.post(url, {"title": "hijacked"}).status_code == 404
    case.refresh_from_db()
    assert case.title == "P2 case"


def test_case_edit_post_requires_csrf(csrf_browser: Client, case: Case) -> None:
    """C2: the mutation is CSRF-protected."""
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    response = csrf_browser.post(url, {"title": "csrf-bypass"})
    assert response.status_code == 403
    case.refresh_from_db()
    assert case.title == "P2 case"


# --------------------------------------------------------------------------
# C3 — Observable edit / delete
# --------------------------------------------------------------------------


def test_observable_edit_get_renders_the_form(browser: Client, observable: Observable) -> None:
    """C3: GET renders a real form with the applier's camelCase field names."""
    url = reverse("ui-observable-edit", kwargs={"observable_id": observable.id})
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    assert f'action="{url}"' in body
    assert 'name="dataType"' in body
    assert 'name="ignoreSimilarity"' in body
    assert 'name="enrichment_data"' in body
    # The value is displayed read-only, never as an editable input.
    assert f'value="{observable.data}"' in body
    assert 'id="f-observable-data"' in body


def test_observable_edit_post_updates_and_ignores_data(
    browser: Client, observable: Observable
) -> None:
    """C3: editable fields apply through the shared applier; `data` is immutable."""
    url = reverse("ui-observable-edit", kwargs={"observable_id": observable.id})
    response = browser.post(
        url,
        {
            "data": "changed-on-purpose",  # must be ignored
            "dataType": "ip",
            "message": "seen in firewall logs",
            "tlp": "1",
            "pap": "2",
            "ioc": "on",
            "ignoreSimilarity": "on",
            "enrichment_data": json.dumps({"vt": "clean"}),
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("ui-observable-detail", kwargs={"observable_id": observable.id})

    observable.refresh_from_db()
    assert observable.data == "192.0.2.10"  # unchanged
    assert observable.message == "seen in firewall logs"
    assert observable.tlp == 1
    assert observable.ioc is True
    assert observable.ignore_similarity is True
    assert observable.enrichment_data == {"vt": "clean"}

    audit = AuditLog.objects.filter(
        action="observable.updated", target_id=str(observable.id)
    ).first()
    assert audit is not None


def test_observable_edit_post_bad_json_rerenders(browser: Client, observable: Observable) -> None:
    """C3: malformed enrichment JSON is reported, not saved, and never 500s."""
    url = reverse("ui-observable-edit", kwargs={"observable_id": observable.id})
    response = browser.post(url, {"message": "x", "enrichment_data": "{not json"})

    assert response.status_code == 200  # re-rendered with the error
    observable.refresh_from_db()
    assert observable.message == ""
    assert observable.enrichment_data == {}


def test_observable_edit_post_unknown_type_is_reported(
    browser: Client, observable: Observable
) -> None:
    """C3: an unknown dataType is rejected by the applier and nothing is persisted."""
    url = reverse("ui-observable-edit", kwargs={"observable_id": observable.id})
    response = browser.post(url, {"dataType": "no-such-type", "message": "x"})

    assert response.status_code == 200
    observable.refresh_from_db()
    assert observable.data_type.name == "ip"
    assert observable.message == ""


def test_observable_edit_shared_requires_force(
    browser: Client, observable: Observable, case: Case
) -> None:
    """C3: editing a multi-case observable needs the acknowledgement (API parity)."""
    second = Case.objects.create(
        title="Second case", status=case.status, owner_org=case.owner_org, assignee=case.assignee
    )
    _link(observable, case)
    _link(observable, second)

    url = reverse("ui-observable-edit", kwargs={"observable_id": observable.id})
    response = browser.post(url, {"message": "nope", "enrichment_data": "{}"})
    assert response.status_code == 200  # refused, re-rendered
    observable.refresh_from_db()
    assert observable.message == ""

    # With the acknowledgement it goes through.
    response = browser.post(url, {"message": "ok", "enrichment_data": "{}", "force": "on"})
    assert response.status_code == 302
    observable.refresh_from_db()
    assert observable.message == "ok"


def test_observable_delete_single_case(browser: Client, observable: Observable, case: Case) -> None:
    """C3: a single-case observable deletes and audits the *real* pk."""
    _link(observable, case)
    pk = str(observable.id)
    AuditLog.objects.all().delete()

    url = reverse("ui-observable-delete", kwargs={"observable_id": observable.id})
    response = browser.post(url)

    assert response.status_code == 302
    assert response.url == reverse("ui-observable-list")
    assert not Observable.objects.filter(pk=pk).exists()

    audit = AuditLog.objects.filter(action="observable.deleted").first()
    assert audit is not None
    assert audit.target_id == pk  # not "None" — the pk was captured before delete()


def test_observable_delete_shared_without_force_is_refused(
    browser: Client, observable: Observable, case: Case
) -> None:
    """C3: a shared observable cannot be deleted without acknowledgement."""
    second = Case.objects.create(
        title="Second case", status=case.status, owner_org=case.owner_org, assignee=case.assignee
    )
    _link(observable, case)
    _link(observable, second)

    url = reverse("ui-observable-delete", kwargs={"observable_id": observable.id})
    response = browser.post(url)

    assert response.status_code == 302  # back to the detail page with an error
    assert response.url == reverse("ui-observable-detail", kwargs={"observable_id": observable.id})
    assert Observable.objects.filter(pk=observable.pk).exists()


def test_observable_delete_shared_with_force_deletes(
    browser: Client, observable: Observable, case: Case
) -> None:
    """C3: the acknowledgement (`force=on`) deletes across all linked cases."""
    second = Case.objects.create(
        title="Second case", status=case.status, owner_org=case.owner_org, assignee=case.assignee
    )
    _link(observable, case)
    _link(observable, second)
    pk = observable.pk

    url = reverse("ui-observable-delete", kwargs={"observable_id": observable.id})
    response = browser.post(url, {"force": "on"})

    assert response.status_code == 302
    assert not Observable.objects.filter(pk=pk).exists()


def test_observable_mutations_are_org_scoped_uniform_404(
    outsider_browser: Client, observable: Observable, case: Case
) -> None:
    """C3: another org gets a uniform 404 for edit and delete."""
    edit_url = reverse("ui-observable-edit", kwargs={"observable_id": observable.id})
    delete_url = reverse("ui-observable-delete", kwargs={"observable_id": observable.id})

    assert outsider_browser.get(edit_url).status_code == 404
    assert outsider_browser.post(edit_url, {"message": "x"}).status_code == 404
    assert outsider_browser.post(delete_url).status_code == 404
    assert Observable.objects.filter(pk=observable.pk).exists()


# --------------------------------------------------------------------------
# Security regressions (SEC-2026-10-11 M1 / L1)
# --------------------------------------------------------------------------


def test_case_edit_cross_org_assignee_is_refused(
    browser: Client, case: Case, outsider: User
) -> None:
    """M1: an in-org writer cannot bind the case to another org's user.

    The auditor's PoC posted an org-B login and got a 302 with `case.assignee` switched
    to the org-B user (and a cross-tenant login-existence oracle). The UI now rejects a
    non-member and the shared applier scopes the lookup, so the assignee is unchanged.
    """
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    for value in (outsider.login, str(outsider.pk)):
        response = browser.post(url, {"title": case.title, "assignee": value})
        assert response.status_code == 200  # re-rendered with an error, not redirected
        case.refresh_from_db()
        assert case.assignee_id != outsider.id
    assert case.assignee.login == "p2analyst"


def test_shared_applier_scopes_assignee_to_owner_org(case: Case, outsider: User) -> None:
    """M1 (API parity): the shared applier resolves a cross-org login to 'nobody'."""
    apply_case_update(case, {"assignee": outsider.login}, actor=None)
    case.refresh_from_db()
    assert case.assignee_id is None  # cross-org value can never bind


def test_case_edit_out_of_range_severity_rerenders_not_500(browser: Client, case: Case) -> None:
    """L1: an out-of-range enum is a form error, never an unhandled IntegrityError 500."""
    before = case.severity
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    response = browser.post(url, {"title": case.title, "severity": "99"})

    assert response.status_code == 200  # re-rendered, not a 500
    case.refresh_from_db()
    assert case.severity == before


def test_shared_applier_rejects_out_of_range_enums(case: Case) -> None:
    """L1 (API parity): the shared applier raises CaseUpdateError, so the API returns 400."""
    for field in ("severity", "tlp", "pap"):
        with pytest.raises(CaseUpdateError):
            apply_case_update(case, {field: 99}, actor=None)


def test_case_edit_prefills_the_assignee_login(browser: Client, case: Case) -> None:
    """NEW-1: the assignee select is prefilled with the *login* its options are keyed by.

    A prefilled UUID never matches an option, so the browser submits the first option
    ("Unassigned") and a save that never touched the field silently unassigned the case.
    """
    url = reverse("ui-case-edit", kwargs={"case_id": case.number})
    body = browser.get(url).content.decode()

    assert f'value="{case.assignee.login}" selected' in body
    assert f'value="{case.assignee_id}" selected' not in body

    response = browser.post(
        url, {"title": case.title, "status": "New", "assignee": case.assignee.login}
    )
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.assignee_id is not None


def test_alert_status_audit_failure_rolls_back(browser: Client, alert, monkeypatch) -> None:
    """NEW-2: the audit row shares the mutation's transaction, so a failed audit rolls back."""
    from ui import views as ui_views

    def boom(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(ui_views.AuditLog.objects, "create", boom)
    url = reverse("ui-alert-status", kwargs={"alert_id": alert.id})
    with pytest.raises(RuntimeError):
        browser.post(url, {"status": "Triaged"})

    alert.refresh_from_db()
    assert alert.status.value == "New"  # the change did not survive the audit failure


# --------------------------------------------------------------------------
# Template/AJAX surface contract
# --------------------------------------------------------------------------


def test_observable_detail_has_edit_and_delete_affordances(
    browser: Client, observable: Observable, case: Case
) -> None:
    """C3: the detail page links to edit and opens the delete modal for a *shared* observable.

    The acknowledgement checkbox is only rendered when the observable is linked to more
    than one case, and it lives *inside* the form (so it both gates and forces); a hidden
    `force` input would let the guard be bypassed.
    """
    second = Case.objects.create(
        title="Second case", status=case.status, owner_org=case.owner_org, assignee=case.assignee
    )
    _link(observable, case)
    _link(observable, second)
    url = reverse("ui-observable-detail", kwargs={"observable_id": observable.id})
    body = browser.get(url).content.decode()

    assert reverse("ui-observable-edit", kwargs={"observable_id": observable.id}) in body
    assert reverse("ui-observable-delete", kwargs={"observable_id": observable.id}) in body
    assert 'name="force"' in body
    assert 'type="hidden" name="force"' not in body


def test_case_detail_has_edit_link(browser: Client, case: Case) -> None:
    """C2: the case detail page exposes the edit entry point."""
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    body = browser.get(url).content.decode()
    assert reverse("ui-case-edit", kwargs={"case_id": case.number}) in body


# --------------------------------------------------------------------------
# TLP/PAP display rendering (zero-safe choice_name filter)
# --------------------------------------------------------------------------


def test_case_detail_renders_tlp_pap_labels(browser: Client, case: Case) -> None:
    """Case detail renders TLP/PAP as labels (Amber/Amber), not raw integers.

    Regression guard: TLP 0 / PAP 0 must render "White" (choice_label collapses
    falsy values to "Unset/Unknown", so this would fail if the template used
    choice_label instead of the new choice_name filter).
    """
    # Default fixture has tlp=2, pap=2 (Amber/Amber)
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    body = browser.get(url).content.decode()

    # The TLP/PAP row in _tab_general.html shows "Amber / Amber". Assert the joined pair rather
    # than the bare word "Amber": the edit modal on the same page already lists the option
    # "Amber", so a bare `"Amber" in body` would pass even if the row still rendered `2 / 2`.
    assert "Amber / Amber" in body
    assert "2 / 2" not in body  # raw integers must not appear


def test_case_detail_renders_tlp_zero_as_white(browser: Client, case: Case) -> None:
    """Zero-trap guard: TLP 0 / PAP 0 render as White, not Unset/Unknown.

    This test would FAIL if the template used `choice_label` (which returns
    "Unset/Unknown" for falsy values including 0). The new `choice_name` filter
    correctly handles 0 as a legitimate value meaning "White".
    """
    case.tlp = 0
    case.pap = 0
    case.save(update_fields=["tlp", "pap"])

    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    body = browser.get(url).content.decode()

    # The TLP/PAP row specifically should show "White / White"
    assert "White / White" in body
    # The raw integers must not appear in the TLP/PAP row
    assert "0 / 0" not in body
    # Scope/Type/Classification/Phase legitimately show "Unset/Unknown" for their "unset" defaults
    # so we don't assert absence of that string globally


def test_case_detail_renders_tlp_unknown(browser: Client, case: Case) -> None:
    """TLP 4 renders as Unknown (the full TLP_CHOICES includes 4=Unknown)."""
    case.tlp = 4
    case.save(update_fields=["tlp"])

    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    body = browser.get(url).content.decode()

    # TLP 4 keeps its own label while the PAP column (still 2) must also be a name, not a number.
    assert "Unknown / Amber" in body
    assert "4 / 2" not in body


# --------------------------------------------------------------------------
# Taxonomy page content (upstream 3255f1f broke the template's tag nesting)
# --------------------------------------------------------------------------


def test_taxonomy_page_renders_every_vocabulary_card(browser: Client) -> None:
    """Every taxonomy card renders, including when Observable Types is non-empty.

    Regression guard for upstream 3255f1f: the refactor deleted the ``{% endif %}`` that
    closed the Observable Types card, so the four new vocabulary cards fell *inside* the
    ``{% else %}`` branch and disappeared whenever at least one ``ObservableType`` row
    existed (the normal case). The suite only caught the follow-on syntax error (HTTP 500);
    a "page renders" smoke test cannot see the content loss, so assert the real content.
    """
    ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})

    body = browser.get(reverse("ui-taxonomy")).content.decode()

    # The non-empty branch of `{% if observable_types %}` must be the one rendered.
    assert "No observable types defined." not in body
    for heading in (
        "Observable Types",
        "Case Scope",
        "Case Phase",
        "Case Type",
        "Data Classification",
    ):
        assert heading in body
