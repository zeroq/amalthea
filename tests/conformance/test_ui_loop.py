"""The MVP loop through the UI: what a signed-in analyst can see and do.

These tests are the UI's acceptance criteria. They exist because "the page renders" is not the same
claim as "escalating from the page runs the playbook and the result lands on the case timeline" — and
the second one is the whole product. Each test drives the HTML a browser would receive: forms POSTed,
links followed, rows counted.

The webhook leg goes through `Client` (unauthenticated, as a sender is) and the escalation leg goes
through the UI form, so the test walks the same path a person does.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert
from automation.models import AutomationRun, Playbook
from cases.models import Case
from identity.models import Organisation, User
from ingest.models import IngestionSource

PHISHING = {
    "event": "mailbox.rule.created",
    "actor": {"email": "attacker@example.net"},
    "rule": {
        "name": "auto-forward to evil",
        "conditions": [{"field": "headerFrom", "values": ["invoice@evil.example.com"]}],
    },
    "sourceRef": "evt-4242",
    "severity": 3,
    "title": "Suspicious inbox rule on finance mailbox",
}


@pytest.fixture
def analyst(db: None) -> User:
    org = Organisation.objects.create(name="Test Org")
    user = User.objects.create_user(
        username="analyst", password="hunter2-correct", email="a@example.net", org=org
    )
    return user


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="analyst", password="hunter2-correct")
    return client


@pytest.fixture
def source(db: None) -> IngestionSource:
    return IngestionSource.objects.create(
        slug="o365",
        name="Microsoft 365 hook",
        mapping_config={
            "title": "$.title",
            "severity": "$.severity",
            "correlation_key": "$.sourceRef",
        },
    )


@pytest.fixture
def mail_playbook(db: None) -> Playbook:
    return Playbook.objects.create(
        name="enrich-mail",
        trigger_event="observable.created",
        is_active=True,
        config={"action": "python", "action_path": "amalthea.automation.executor.enrichment_probe"},
    )


def _case(title: str, **kwargs) -> Case:
    """`Case.status` is a required FK (ADR-002 §D4: statuses are domain data, seeded by migration
    0003), so a bare `Case.objects.create(title=...)` is not a valid case. Every test that wants a
    case goes through here so the fixture cannot silently drift from the schema."""
    from cases.models import CaseStatus

    status = (
        CaseStatus.objects.filter(value="New").first()
        or CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    )
    return Case.objects.create(title=title, status=status, **kwargs)


def _ingest(source: IngestionSource) -> Alert:
    response = Client().post(
        reverse("alerts-webhook", kwargs={"source_id": source.slug}),
        data=json.dumps(PHISHING),
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    return Alert.objects.get(pk=response.json()["_id"])


# --- access control -------------------------------------------------------


def test_every_ui_page_requires_a_session(db: None) -> None:
    """No analyst page may render for an anonymous visitor.

    Enumerated rather than sampled: a single missed `login_required` on an action view would be an
    unauthenticated case-mutating endpoint, which is the exact class of bug worth enumerating.
    """
    anon = Client()
    _case("Confidential")
    for name in (
        "ui-dashboard",
        "ui-alert-list",
        "ui-case-list",
        "ui-automation-list",
        "ui-sources-list",
    ):
        response = anon.get(reverse(name))
        assert response.status_code == 302, (
            f"{name} served an anonymous visitor {response.status_code}"
        )
        assert "/login" in response["Location"]


def test_an_anonymous_post_cannot_escalate(db: None) -> None:
    alert = _create_alert_directly()
    response = Client().post(reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}), {})
    assert response.status_code == 302
    assert Case.objects.count() == 0, "an anonymous POST created a case"


def test_a_get_cannot_mutate(db: None) -> None:
    """Escalation, status change and extraction are POST-only.

    Asserted per-view because a `@require_GET` on a mutating view is a CSRF-adjacent hole: a link, a
    prefetch or an image tag would perform the action.
    """
    alert = _create_alert_directly()
    user = User.objects.create_user(username="u1", password="pw-long-enough")
    client = Client()
    client.force_login(user)

    assert (
        client.get(reverse("ui-alert-escalate", kwargs={"alert_id": alert.id})).status_code == 405
    )
    assert client.get(reverse("ui-alert-merge", kwargs={"alert_id": alert.id})).status_code == 405
    assert Case.objects.count() == 0


def test_a_post_without_a_csrf_token_is_refused(browser: Client) -> None:
    """`Client(enforce_csrf_checks=True)` makes the missing token a real 403."""
    alert = _create_alert_directly()
    strict = Client(enforce_csrf_checks=True)
    strict.login(username="analyst", password="hunter2-correct")

    response = strict.post(reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}), {})

    assert response.status_code == 403
    assert Case.objects.count() == 0


# --- navigation -----------------------------------------------------------


def test_the_dashboard_renders_with_no_data(browser: Client) -> None:
    """An empty database must render, not raise — it is the first page anyone sees."""
    response = browser.get(reverse("ui-dashboard"))
    assert response.status_code == 200
    assert b"Alerts waiting" in response.content
    assert b"Nothing waiting" in response.content, "the empty state is missing its explanation"


def test_the_dashboard_counts_waiting_work(browser: Client, source: IngestionSource) -> None:
    alert = _ingest(source)
    response = browser.get(reverse("ui-dashboard"))
    body = response.content.decode()

    assert alert.title in body
    assert "Alert imported" not in body, "an unlinked alert must not appear as imported"
    assert response.context["counts"]["alerts_unlinked"] == 1


def test_the_rail_links_every_page(browser: Client) -> None:
    """The navigation is what this UI is; every rail target must be reachable."""
    for name in (
        "ui-dashboard",
        "ui-alert-list",
        "ui-case-list",
        "ui-automation-list",
        "ui-sources-list",
    ):
        assert browser.get(reverse(name)).status_code == 200, f"{name} did not render"


def test_a_signed_in_analyst_is_redirected_away_from_login(browser: Client) -> None:
    response = browser.get(reverse("login"))
    assert response.status_code == 302
    assert response["Location"] == reverse("ui-dashboard")


# --- the loop, through the UI --------------------------------------------


def test_escalating_from_the_page_runs_the_playbook_and_shows_the_result(
    browser: Client,
    source: IngestionSource,
    mail_playbook: Playbook,
    django_capture_on_commit_callbacks: object,
) -> None:
    """The product claim, end to end, through HTML.

    webhook → triage queue → escalate click → artifacts extracted → playbook runs → result on the
    case timeline. Every step asserts something the analyst would actually look at.
    """
    alert = _ingest(source)

    # 1. It is in the queue, linked to nothing.
    queue = browser.get(reverse("ui-alert-list"))
    assert queue.status_code == 200
    assert alert.title in queue.content.decode()
    assert (
        browser.get(reverse("ui-alert-list", query={"scope": "linked"})).context["alerts"].count()
        == 0
    )

    # 2. Its page renders the raw payload and offers escalation.
    detail = browser.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert detail.status_code == 200
    assert b"auto-forward to evil" in detail.content, (
        "the raw payload is not visible to the analyst"
    )

    # 3. Escalate. The commit releases the queued worker, which is what makes the run `Success`.
    with django_capture_on_commit_callbacks(execute=True):
        response = browser.post(
            reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}),
            {"title": "Phish on finance mailbox", "assign_me": "on"},
        )
    assert response.status_code == 302

    case = Case.objects.get()
    assert case.title == "Phish on finance mailbox"
    assert case.assignee.login == "analyst"
    assert response["Location"] == reverse("ui-case-detail", kwargs={"case_id": case.number})

    # 4. Automation fired from the extraction the escalation performed.
    runs = list(AutomationRun.objects.filter(case=case))
    assert runs, "escalating from the UI did not run the bound playbook"
    assert all(r.status == "Success" for r in runs), [(r.status, r.error) for r in runs]

    # 5. The result is on the ledger the analyst is now looking at.
    ledger = browser.get(
        reverse("ui-case-detail", kwargs={"case_id": case.number})
    ).content.decode()
    assert "enrichment probe" in ledger, "the automation output is not on the case page"
    assert "attacker@example.net" in ledger, "the extracted artifact is not on the case page"
    assert "Alert imported" in ledger
    assert "enrich-mail" in ledger

    # 6. The alert is no longer in the queue; it is inside the case.
    assert (
        browser.get(reverse("ui-alert-list", query={"scope": "unlinked"})).context["alerts"].count()
        == 0
    )
    assert (
        browser.get(reverse("ui-alert-list", query={"scope": "linked"})).context["alerts"].count()
        == 1
    )


def test_the_case_page_is_reachable_by_number_and_by_uuid(
    browser: Client, source: IngestionSource
) -> None:
    alert = _ingest(source)
    browser.post(reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}), {})
    case = Case.objects.get()

    by_number = browser.get(reverse("ui-case-detail", kwargs={"case_id": str(case.number)}))
    by_uuid = browser.get(reverse("ui-case-detail", kwargs={"case_id": str(case.id)}))

    assert by_number.status_code == 200
    assert by_uuid.status_code == 200
    assert by_number.context["case"].id == by_uuid.context["case"].id


def test_a_nonexistent_case_is_404_not_500(browser: Client) -> None:
    assert browser.get(reverse("ui-case-detail", kwargs={"case_id": "999999"})).status_code == 404


# --- ledger interaction ---------------------------------------------------


def test_closing_a_case_stamps_the_closed_date_and_logs_the_transition(browser: Client) -> None:
    case = _case("To close")

    browser.post(
        reverse("ui-case-status", kwargs={"case_id": str(case.number)}), {"stage": "Closed"}
    )

    case.refresh_from_db()
    assert case.status.stage == "Closed"
    assert case.closed_date is not None, (
        "a closed case with no closed_date vanishes from open lists"
    )
    assert case.timeline_events.filter(kind="status-change").count() == 1


def test_reopening_a_case_clears_the_closed_date(browser: Client) -> None:
    """The inverse must hold too, or "reopen" leaves the case invisible on every open list."""
    from django.utils import timezone

    case = _case("Reopened", closed_date=timezone.now())
    browser.post(
        reverse("ui-case-status", kwargs={"case_id": str(case.number)}), {"stage": "InProgress"}
    )

    case.refresh_from_db()
    assert case.closed_date is None
    assert case.status.stage == "InProgress"


def test_a_note_lands_on_the_timeline(browser: Client) -> None:
    case = _case("Notes")

    response = browser.post(
        reverse("ui-case-comment", kwargs={"case_id": str(case.number)}),
        {"body": "Checked the mail flow."},
    )

    assert response.status_code == 302
    event = case.timeline_events.get(kind="comment")
    assert event.description == "Checked the mail flow."
    assert event.actor.login == "analyst"
    assert (
        "Checked the mail flow."
        in browser.get(
            reverse("ui-case-detail", kwargs={"case_id": str(case.number)})
        ).content.decode()
    )


def test_an_empty_note_is_refused_rather_than_stored(browser: Client) -> None:
    case = _case("No blank notes")
    browser.post(reverse("ui-case-comment", kwargs={"case_id": str(case.number)}), {"body": "   "})
    assert case.timeline_events.filter(kind="comment").count() == 0


def test_a_task_can_be_added_and_completed_from_the_page(browser: Client) -> None:
    case = _case("Tasks")

    browser.post(
        reverse("ui-case-task-create", kwargs={"case_id": str(case.number)}),
        {"title": "Isolate the mailbox", "assignee": "analyst"},
    )
    task = case.tasks.get()

    assert task.title == "Isolate the mailbox"
    assert task.assignee.login == "analyst"
    assert task.status == "Waiting"

    browser.post(
        reverse(
            "ui-case-task-toggle", kwargs={"case_id": str(case.number), "task_id": str(task.id)}
        )
    )
    task.refresh_from_db()
    assert task.status == "Completed"

    # And back again, so the toggle is not a one-way door.
    browser.post(
        reverse(
            "ui-case-task-toggle", kwargs={"case_id": str(case.number), "task_id": str(task.id)}
        )
    )
    task.refresh_from_db()
    assert task.status == "Waiting"


def test_a_task_with_an_unknown_assignee_is_left_unassigned(browser: Client) -> None:
    """ADR-002 §D11: tolerate on input, and never create a user to satisfy a wire value."""
    case = _case("Unknown assignee")
    before = User.objects.count()

    browser.post(
        reverse("ui-case-task-create", kwargs={"case_id": str(case.number)}),
        {"title": "Do the thing", "assignee": "does-not-exist"},
    )

    assert case.tasks.get().assignee is None
    assert User.objects.count() == before, "assigning to an unknown login created a user account"


def test_re_extracting_does_not_duplicate_artifacts_or_runs(
    browser: Client,
    source: IngestionSource,
    mail_playbook: Playbook,
    django_capture_on_commit_callbacks: object,
) -> None:
    """Pressing the button twice must be safe: the operator will."""
    alert = _ingest(source)
    with django_capture_on_commit_callbacks(execute=True):
        browser.post(reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}), {})
    case = Case.objects.get()
    observables = case.case_observables.count()
    runs = AutomationRun.objects.filter(case=case).count()
    assert observables > 0

    with django_capture_on_commit_callbacks(execute=True):
        browser.post(reverse("ui-case-extract", kwargs={"case_id": str(case.number)}), {})

    assert case.case_observables.count() == observables
    assert AutomationRun.objects.filter(case=case).count() == runs


def test_merging_from_the_page_links_the_alert_to_the_case(
    browser: Client, source: IngestionSource
) -> None:
    case = _case("Existing")
    alert = _ingest(source)

    response = browser.post(
        reverse("ui-alert-merge", kwargs={"alert_id": alert.id}), {"case_id": str(case.number)}
    )

    assert response.status_code == 302
    alert.refresh_from_db()
    assert alert.case_id == case.id
    assert case.timeline_events.filter(kind="alert-merged").count() == 1


# --- pages that must render the Module D state ---------------------------


def test_the_automation_page_shows_runs_and_their_errors(
    browser: Client, source: IngestionSource, django_capture_on_commit_callbacks: object
) -> None:
    """A failed playbook must be legible here, not just a red dot on the dashboard."""
    Playbook.objects.create(
        name="broken",
        trigger_event="observable.created",
        is_active=True,
        config={"action": "python", "action_path": "amalthea.does.not.exist"},
    )
    alert = _ingest(source)
    with django_capture_on_commit_callbacks(execute=True):
        browser.post(reverse("ui-alert-escalate", kwargs={"alert_id": alert.id}), {})

    body = browser.get(reverse("ui-automation-list")).content.decode()

    assert "broken" in body
    assert "Failed" in body
    assert "no registered action" in body, "the failure reason is not shown to the analyst"


def test_the_sources_page_shows_whether_a_secret_is_required(
    browser: Client, source: IngestionSource
) -> None:
    """An "open" feed is a deployment mistake; the page should make it visible."""
    from django.contrib.auth.hashers import make_password

    source.webhook_secret_hash = make_password("a-secret")
    source.save()

    body = browser.get(reverse("ui-sources-list")).content.decode()

    assert "required" in body
    assert "open" not in body.split("Alerts")[0], "a locked source still renders as open"


def test_an_artifact_seen_in_two_cases_shows_its_case_count(browser: Client) -> None:
    """The cross-case link is Module C's promise; the UI is where an analyst sees it."""
    from observables.extractor import add_observable

    first = _case("First")
    second = _case("Second")
    observable = add_observable(first, "fqdn", "evil.example")
    assert observable is not None
    add_observable(second, "fqdn", "evil.example")

    body = browser.get(
        reverse("ui-case-detail", kwargs={"case_id": str(first.number)})
    ).content.decode()
    assert "evil.example" in body
    assert "2 case(s)" in body, "the artifact does not report that it appears in both cases"


def test_the_timeline_is_ordered_oldest_first(browser: Client) -> None:
    """The ledger reads as a story; a reverse-chronological timeline reads as noise."""
    case = _case("Ordered")
    browser.post(
        reverse("ui-case-comment", kwargs={"case_id": str(case.number)}), {"body": "first"}
    )
    browser.post(
        reverse("ui-case-comment", kwargs={"case_id": str(case.number)}), {"body": "second"}
    )

    body = browser.get(
        reverse("ui-case-detail", kwargs={"case_id": str(case.number)})
    ).content.decode()
    assert body.index("first") < body.index("second"), "the timeline is not in chronological order"


def test_the_html_declares_the_accessibility_basics(browser: Client) -> None:
    """Skip link, landmarks and a lang attribute, asserted because they are easy to lose in a rewrite."""
    body = browser.get(reverse("ui-dashboard")).content.decode()
    assert '<html lang="en"' in body
    assert 'class="skip-link"' in body
    assert 'aria-label="Primary"' in body
    assert 'id="main"' in body


def test_severity_is_shown_as_text_not_colour_alone(
    browser: Client, source: IngestionSource
) -> None:
    """WCAG 1.4.1: the severity pill must carry its label, not just a hue."""
    _ingest(source)  # without this the queue is empty and the assertion passes vacuously
    body = browser.get(reverse("ui-alert-list")).content.decode()
    assert "High" in body, "the severity label is missing"
    assert "sev-high" in body, "the severity class is missing"


def _create_alert_directly() -> Alert:
    """An alert without going through the webhook, for tests about the alert pages themselves."""
    from alerts.models import AlertStatus

    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        title="Directly created",
        type="generic",
        source="test",
        source_ref="direct-1",
        severity=2,
        status=status,
        raw_payload={"title": "Directly created"},
    )
