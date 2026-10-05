"""The AGENTS.md §4 loop, end to end, over HTTP (MVP steps 1-3).

Scope: webhook → alert → escalate to case → observable extracted → observable readable on the case.
Step 4 (automation) is `test_mvp_loop_with_automation`, kept separate so a failure in the async
layer does not make the ingest half unreadable.

No mocks. The webhook is the real view, the mapping is the real jsonpath engine, the case is a real
row and the observables are real rows with real digests — the only thing stood in for is the
database, which is SQLite in memory by the project's own decision.

One deliberate asymmetry in the setup: the **webhook call is unauthenticated** (it is
machine-to-machine and gated by the per-source secret) while the **analyst calls are authenticated**.
That split is the contract, not a test convenience, so the fixtures reflect it rather than
authenticating everything with one client.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from alerts.models import Alert
from cases.models import Case, CaseObservable, CaseStatus
from identity.models import Organisation, User
from ingest.models import IngestionSource

PHISHING_PAYLOAD = {
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
    return User.objects.create(
        login="analyst",
        username="analyst",
        email="analyst@example.net",
        org=org,
        is_active=True,
    )


@pytest.fixture
def api(analyst: User) -> object:
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=analyst)
    return client


@pytest.fixture
def anonymous_api() -> object:
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def source(db: None) -> IngestionSource:
    return IngestionSource.objects.create(
        slug="o365",
        name="Microsoft 365 hook",
        mapping_config={
            "title": "$.title",
            "severity": "$.severity",
            "sourceRef": "$.sourceRef",
            "correlation_key": "$.sourceRef",
        },
    )


def _post_alert(anonymous_api: object, source: IngestionSource, **overrides: object) -> str:
    payload = {**PHISHING_PAYLOAD, **overrides}
    posted = anonymous_api.post(
        reverse("alerts-webhook", kwargs={"source_id": source.slug}),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert posted.status_code == 201, posted.content
    return str(posted.json()["_id"])


def test_mvp_loop_ingest_escalate_extract(
    anonymous_api: object, api: object, source: IngestionSource
) -> None:
    """`POST webhook` → `POST import` → the email observable is on the case."""
    alert_id = _post_alert(anonymous_api, source)

    alert = Alert.objects.get(pk=alert_id)
    assert alert.source_ref == "evt-4242"
    assert alert.severity == 3
    assert alert.title == PHISHING_PAYLOAD["title"]
    # H3-4: the configured rule populated the key the `(correlation_key, date)` index is built on.
    assert alert.correlation_key == "evt-4242"

    escalated = api.post(f"/api/v1/alert/{alert_id}/import", {}, format="json")
    assert escalated.status_code == 201, escalated.content
    case_id = escalated.json()["_id"]

    alert.refresh_from_db()
    case = Case.objects.get(pk=case_id)
    assert alert.case_id == case.pk
    assert alert.status.value == "Imported"

    values = {
        link.observable.normalized_data
        for link in CaseObservable.objects.filter(case=case).select_related("observable")
    }
    assert "attacker@example.net" in values, values
    assert "invoice@evil.example.com" in values, values
    # The host inside the address is *not* also reported as a bare fqdn: the mail pattern claimed
    # that span, and one artifact must not become two rows for the same infrastructure.
    assert "evil.example.com" not in values, values

    # The extraction is visible on the case, and the ledger recorded the escalation.
    detail = api.get(f"/api/v1/case/{case.number}").json()
    assert detail["_id"] == case_id
    assert {o["normalizedData"] for o in detail["observables"]} >= {"attacker@example.net"}
    assert any(e["kind"] == "alert-imported" for e in detail["timeline"]), detail["timeline"]


def test_mvp_loop_merge_into_existing_case(
    anonymous_api: object, api: object, source: IngestionSource
) -> None:
    """A second alert merges into the same case; the shared address stays one artifact."""
    status = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]
    case = Case.objects.create(
        title="Phishing wave", description="Finance mailboxes", status=status
    )

    alert_ids = [
        _post_alert(anonymous_api, source, sourceRef=ref, title=f"Alert {ref}")
        for ref in ("evt-1", "evt-2")
    ]
    for alert_id in alert_ids:
        merged = api.post(f"/api/v1/alert/{alert_id}/merge/{case.number}", {}, format="json")
        assert merged.status_code == 200, merged.content

    mails = list(
        CaseObservable.objects.filter(case=case)
        .select_related("observable__data_type")
        .filter(observable__data_type__name="mail")
    )
    assert {m.observable.normalized_data for m in mails} == {
        "attacker@example.net",
        "invoice@evil.example.com",
    }
    # Module C: the artifact is a standalone row, so a second alert adds a *link*, never a copy.
    assert len(mails) == 2
    assert len({m.observable_id for m in mails}) == 2

    detail = api.get(f"/api/v1/case/{case.number}").json()
    assert detail["alertCount"] == 2
    assert len([e for e in detail["timeline"] if e["kind"] == "alert-merged"]) == 2


def test_extraction_is_idempotent_across_repeated_merges(
    anonymous_api: object, api: object, source: IngestionSource
) -> None:
    """Re-running extraction over the same text links nothing new."""
    status = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]
    case = Case.objects.create(title="Repeat", description="", status=status)
    alert_id = _post_alert(anonymous_api, source)
    api.post(f"/api/v1/alert/{alert_id}/merge/{case.number}", {}, format="json")
    before = CaseObservable.objects.filter(case=case).count()
    assert before > 0

    again = api.post(f"/api/v1/case/{case.number}/observable/", {"extract": True}, format="json")
    assert again.status_code == 200, again.content
    assert CaseObservable.objects.filter(case=case).count() == before


def test_unauthenticated_escalation_is_refused(
    anonymous_api: object, source: IngestionSource
) -> None:
    """Escalating is an analyst action; ingestion is not. The webhook path must not imply both."""
    alert_id = _post_alert(anonymous_api, source)
    refused = anonymous_api.post(f"/api/v1/alert/{alert_id}/import", {}, format="json")
    assert refused.status_code in (401, 403), refused.content
