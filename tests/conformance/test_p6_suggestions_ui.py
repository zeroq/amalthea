"""Conformance tests for the P6 server-rendered suggestion actions.

The UI suggestion actions (`/suggestions/<id>/accept|reject|ignore` and the per-case bulk
actions) are the analyst-facing half of the suggestion workflow. They must behave exactly
like their JSON API counterparts, so the shared service in `observables/suggestions.py` is
exercised here through the real HTTP stack.

These tests guard three regressions that were found and fixed during implementation:

1. The UI bulk actions used to hand a fake `MockRequest` to a DRF `@api_view` function, which
   raised `AssertionError: The 'request' argument must be an instance of
   'django.http.HttpRequest'` — the bulk buttons were dead on click.
2. The UI single accept path called `add_observable_with_status()` (which dispatches) and then
   dispatched again, firing every automation trigger twice.
3. The enrichment modal body template used the `prettyjson` filter without `{% load ui_tags %}`,
   so rendering it raised `Invalid filter: 'prettyjson'`.
"""

from unittest.mock import patch

import pytest
from django.template.loader import get_template
from django.test import Client

from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from observables.models import ObservableSuggestion, ObservableType


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Suggestion UI Org")


@pytest.fixture
def user(db, org):
    return User.objects.create(
        login="analyst", username="analyst", email="a@b.com", org=org, is_active=True
    )


@pytest.fixture
def client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def case(db, org):
    status, _ = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 0}
    )
    return Case.objects.create(title="Suggestion UI Case", status=status, owner_org=org)


@pytest.fixture
def mail_type(db):
    return ObservableType.objects.get(name="mail")


def _suggestion(case, value, status="proposed"):
    mail_type = ObservableType.objects.get(name="mail")
    return ObservableSuggestion.objects.create(
        owner_org=case.owner_org,
        data_type=mail_type,
        normalized_data=value,
        raw_data=value,
        case=case,
        source_paths=["description"],
        reason="Test",
        status=status,
    )


# --- single suggestion actions ---------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [("accept", "accepted"), ("reject", "rejected"), ("ignore", "ignored")],
)
def test_ui_single_action_sets_status(client, case, action, expected):
    suggestion = _suggestion(case, f"{action}@example.com")

    response = client.post(f"/suggestions/{suggestion.id}/{action}")

    assert response.status_code == 302
    suggestion.refresh_from_db()
    assert suggestion.status == expected
    assert suggestion.reviewed_by is not None
    assert suggestion.reviewed_at is not None


def test_ui_accept_links_observable_to_case(client, case):
    suggestion = _suggestion(case, "linked@example.com")

    client.post(f"/suggestions/{suggestion.id}/accept")

    suggestion.refresh_from_db()
    assert suggestion.status == "accepted"
    assert suggestion.observable is not None
    assert case.case_observables.filter(observable=suggestion.observable).exists()


def test_ui_accept_dispatches_exactly_once(client, case):
    """Accepting a brand-new observable fires the trigger once, not twice."""
    suggestion = _suggestion(case, "dispatch-once@example.com")

    with patch("automation.registry.dispatch_observable_linked") as mock_dispatch:
        client.post(f"/suggestions/{suggestion.id}/accept")

    assert mock_dispatch.call_count == 1


def test_ui_accept_twice_dispatches_once(client, case):
    """A re-accept is idempotent: it must not re-fire the trigger."""
    suggestion = _suggestion(case, "idempotent@example.com")

    with patch("automation.registry.dispatch_observable_linked") as mock_dispatch:
        client.post(f"/suggestions/{suggestion.id}/accept")
        client.post(f"/suggestions/{suggestion.id}/accept")

    assert mock_dispatch.call_count == 1


def test_ui_cannot_ignore_an_accepted_suggestion(client, case):
    suggestion = _suggestion(case, "already@example.com", status="accepted")

    client.post(f"/suggestions/{suggestion.id}/ignore")

    suggestion.refresh_from_db()
    assert suggestion.status == "accepted"


# --- bulk actions -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [("bulk-accept", "accepted"), ("bulk-reject", "rejected"), ("bulk-ignore", "ignored")],
)
def test_ui_bulk_action_sets_status(client, case, action, expected):
    first = _suggestion(case, f"bulk1-{action}@example.com")
    second = _suggestion(case, f"bulk2-{action}@example.com")

    response = client.post(
        f"/cases/{case.number}/suggestions/{action}",
        {"suggestion_ids": [str(first.id), str(second.id)]},
    )

    assert response.status_code == 302
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == expected
    assert second.status == expected


def test_ui_bulk_accept_dispatches_once_per_new_observable(client, case):
    first = _suggestion(case, "bulk-dispatch-1@example.com")
    second = _suggestion(case, "bulk-dispatch-2@example.com")

    with patch("automation.registry.dispatch_observable_linked") as mock_dispatch:
        client.post(
            f"/cases/{case.number}/suggestions/bulk-accept",
            {"suggestion_ids": [str(first.id), str(second.id)]},
        )

    assert mock_dispatch.call_count == 2


def test_ui_bulk_action_with_no_selection_is_refused(client, case):
    response = client.post(f"/cases/{case.number}/suggestions/bulk-accept", {})

    assert response.status_code == 302


def test_ui_bulk_action_cannot_reach_another_cases_suggestions(client, case):
    """A bulk action is scoped to the case in the URL."""
    from cases.models import Case as CaseModel

    other_case = CaseModel.objects.create(
        title="Other Case", status=case.status, owner_org=case.owner_org
    )
    foreign = _suggestion(other_case, "foreign@example.com")

    client.post(
        f"/cases/{case.number}/suggestions/bulk-accept",
        {"suggestion_ids": [str(foreign.id)]},
    )

    foreign.refresh_from_db()
    assert foreign.status == "proposed"


# --- cross-org scoping ------------------------------------------------------------


def test_ui_bulk_action_cannot_reach_another_orgs_suggestions(client, case):
    other_org = Organisation.objects.create(name="Other Org")
    other_case = Case.objects.create(title="Foreign Case", status=case.status, owner_org=other_org)
    foreign = _suggestion(other_case, "cross-org@example.com")

    client.post(
        f"/cases/{case.number}/suggestions/bulk-accept",
        {"suggestion_ids": [str(foreign.id)]},
    )

    foreign.refresh_from_db()
    assert foreign.status == "proposed"


# --- template rendering -----------------------------------------------------------


def test_enrichment_modal_body_template_loads_its_filters():
    """The enrichment modal body uses `|prettyjson`, so it must load `ui_tags`.

    Without the load tag the template raises `Invalid filter: 'prettyjson'` the first time an
    analyst opens an enrichment modal.
    """
    get_template("ui/_suggestion_enrichment_modal_body.html")


def test_suggestions_bulk_modal_body_template_compiles():
    get_template("ui/_suggestions_bulk_action_modal_body.html")


# --- re-extraction -----------------------------------------------------------------


def test_reextract_does_not_duplicate_alert_sourced_suggestions(client, case, org, mail_type):
    """Re-extracting a case with a linked alert must update, not duplicate, its suggestions.

    The unique constraint includes `source_alert`; a re-extract that omits it would create a
    second set of rows alongside the ones escalation created.
    """
    from alerts.models import Alert, AlertStatus
    from observables.suggestions import create_suggestions_for_case

    alert_status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 0}
    )
    alert = Alert.objects.create(
        title="Re-extract Alert",
        type="phishing",
        source="mock",
        source_ref="reextract-1",
        status=alert_status,
        owner_org=org,
        case=case,
        raw_payload={"user": "reextract@example.com"},
    )
    create_suggestions_for_case(
        case,
        case.title,
        case.description,
        alert.title,
        source_alert=alert,
        raw_payload=alert.raw_payload,
    )
    before = ObservableSuggestion.objects.filter(case=case, source_alert=alert).count()

    client.post(f"/cases/{case.number}/extract")

    after = ObservableSuggestion.objects.filter(case=case, source_alert=alert).count()
    unscoped = ObservableSuggestion.objects.filter(case=case, source_alert__isnull=True).count()
    assert after == before
    assert unscoped == 0


def test_reextract_reproposes_a_rejected_suggestion(client, case):
    """A value the analyst rejected is offered again on a later re-extraction."""
    case.description = "Contact repropose@example.com for details."
    case.save(update_fields=["description"])

    client.post(f"/cases/{case.number}/extract")
    suggestion = ObservableSuggestion.objects.get(
        case=case, normalized_data="repropose@example.com"
    )
    suggestion.status = "rejected"
    suggestion.save(update_fields=["status"])

    client.post(f"/cases/{case.number}/extract")

    suggestion.refresh_from_db()
    assert suggestion.status == "proposed"


def test_reextract_without_alert_is_idempotent(client, case):
    """A manual case (no source alert) must not accrue duplicates on repeated re-extraction.

    Postgres treats NULLs as distinct in a plain unique constraint, so this is the case that
    guards the `nulls_distinct=False` migration.
    """
    case.description = "Contact idempotent@example.com for details."
    case.save(update_fields=["description"])

    client.post(f"/cases/{case.number}/extract")
    client.post(f"/cases/{case.number}/extract")

    rows = ObservableSuggestion.objects.filter(
        case=case, normalized_data="idempotent@example.com", source_alert__isnull=True
    )
    assert rows.count() == 1


def test_reextract_keeps_an_accepted_suggestion_accepted(client, case):
    case.description = "Contact keep@example.com for details."
    case.save(update_fields=["description"])

    client.post(f"/cases/{case.number}/extract")
    suggestion = ObservableSuggestion.objects.get(case=case, normalized_data="keep@example.com")
    client.post(f"/suggestions/{suggestion.id}/accept")
    suggestion.refresh_from_db()
    assert suggestion.status == "accepted"
    accepted_observable_id = suggestion.observable_id

    client.post(f"/cases/{case.number}/extract")

    suggestion.refresh_from_db()
    assert suggestion.status == "accepted"
    assert suggestion.observable_id == accepted_observable_id
