"""REVIEW-2026-10-03 **C3** — idempotent ingest (AC4.3, AC4.4).

Before the fix, a source that carried no `sourceRef` could ingest **one alert, ever**: the
first insert filled `source_ref` with the empty string (the implicit default for a NOT NULL
`CharField` in SQLite) and the second collided with the unconditional unique constraint on
`(source, type, source_ref)`, killing the request with `IntegrityError`. AC4.3 ("an
unconfigured source ingests without error, never a 500") and AC4.4 ("replay is idempotent")
were therefore unreachable.

These tests pin both directions of the fix:

* two payloads with no `sourceRef` that differ in content produce **two** alerts;
* the same payload ingested twice produces **one** alert and a `created=False` outcome;
* the substitution is never silent — it lands in `ingestion_warnings` (ADR-002 §D11);
* a mapped ref still wins over the synthesised one, and is stored verbatim.

Scope note: these exercise `ingest.pipeline.store_ingested_alert` directly. The webhook
endpoint that will call it is Phase 4 work and deliberately does not exist yet.
"""

from __future__ import annotations

import pytest

from ingest.models import IngestionSource
from ingest.pipeline import DEFAULT_SEVERITY, store_ingested_alert
from ingest.references import SYNTHETIC_PREFIX, canonical_payload, payload_digest

PHISHING_PAYLOAD = {
    "event": "mailbox.rule.created",
    "actor": {"email": "attacker@example.net"},
    "rule": {"conditions": [{"field": "headerFrom", "values": ["invoice@evil.example"]}]},
}
# Same information, different serialisation: different keys order and whitespace only.
PHISHING_PAYLOAD_REORDERED = {
    "rule": {"conditions": [{"values": ["invoice@evil.example"], "field": "headerFrom"}]},
    "actor": {"email": "attacker@example.net"},
    "event": "mailbox.rule.created",
}


def test_canonical_form_ignores_key_order_and_whitespace() -> None:
    assert canonical_payload(PHISHING_PAYLOAD) == canonical_payload(PHISHING_PAYLOAD_REORDERED)
    assert payload_digest(PHISHING_PAYLOAD) == payload_digest(PHISHING_PAYLOAD_REORDERED)
    assert payload_digest(PHISHING_PAYLOAD) != payload_digest({"event": "other"})


@pytest.mark.django_db
def test_ac4_3_a_source_without_a_source_ref_ingests_repeatedly() -> None:
    """Two genuinely different payloads, neither carrying a `sourceRef`."""
    first = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="Suspicious inbox rule",
    )
    second = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload={"event": "mailbox.rule.created", "rule": {"name": "second rule"}},
        title="Another inbox rule",
    )

    assert first.created and second.created
    assert first.alert.pk != second.alert.pk, "distinct payloads must not collide"
    assert first.alert.source_ref.startswith(SYNTHETIC_PREFIX)
    assert first.alert.source_ref != second.alert.source_ref


@pytest.mark.django_db
def test_ac4_4_replaying_the_same_payload_is_idempotent() -> None:
    first = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="Suspicious inbox rule",
    )
    replay = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="Suspicious inbox rule (resent)",
    )

    assert first.created is True
    assert replay.created is False, "a byte-identical replay must be recognised"
    assert replay.alert.pk == first.alert.pk

    from alerts.models import Alert

    assert Alert.objects.filter(source="office365-hook").count() == 1


@pytest.mark.django_db
def test_a_reordered_payload_is_still_the_same_alert() -> None:
    """Dedupe is content-based, so the sender's serialisation cannot defeat it."""
    first = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="a",
    )
    replay = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD_REORDERED,
        title="b",
    )
    assert replay.created is False and replay.alert.pk == first.alert.pk


@pytest.mark.django_db
def test_the_synthesised_ref_is_recorded_as_a_warning() -> None:
    """ADR-002 D11: every tolerance is recorded on the entity."""
    stored = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="Suspicious inbox rule",
    )
    warnings = stored.alert.ingestion_warnings["warnings"]
    assert any(w.startswith("source_ref_missing:") for w in warnings), warnings
    assert stored.source_ref_synthesised is True
    # The default-severity tolerance is recorded too, rather than being silent.
    assert any(w.startswith("severity_defaulted:") for w in warnings), warnings
    assert stored.alert.severity == DEFAULT_SEVERITY


@pytest.mark.django_db
def test_a_mapped_source_ref_is_used_verbatim_and_is_not_flagged() -> None:
    """TheHive's own `sourceRef` must win: it is the sender's stable de-dup key."""
    stored = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="Suspicious inbox rule",
        mapped_source_ref="evt-4242",
        severity=3,
    )
    assert stored.alert.source_ref == "evt-4242"
    assert stored.source_ref_synthesised is False
    warnings = stored.alert.ingestion_warnings["warnings"]
    assert not any(w.startswith("source_ref_missing:") for w in warnings), warnings


@pytest.mark.django_db
def test_the_same_ref_from_a_different_source_stays_separate() -> None:
    """The unique key is the triple `(source, type, source_ref)`, not the ref alone."""
    shared = {
        "alert_type": "suspicious-mailbox-rule",
        "payload": PHISHING_PAYLOAD,
        "title": "t",
        "mapped_source_ref": "evt-4242",
    }
    a = store_ingested_alert(wire_source="office365-hook", **shared)
    b = store_ingested_alert(wire_source="proofpoint-hook", **shared)
    assert a.created and b.created
    assert a.alert.pk != b.alert.pk


@pytest.mark.django_db
def test_the_wire_source_and_the_ingestion_source_are_recorded_separately() -> None:
    """REVIEW M2/M3: the caller's verbatim string and our own feed record are distinct."""
    source = IngestionSource.objects.create(
        slug="office365-hook",
        name="Office 365",
        default_severity=2,
    )
    stored = store_ingested_alert(
        wire_source="office365-hook",
        alert_type="suspicious-mailbox-rule",
        payload=PHISHING_PAYLOAD,
        title="Suspicious inbox rule",
        ingestion_source=source,
    )
    assert stored.alert.source == "office365-hook", "the wire value is stored verbatim"
    assert stored.alert.ingestion_source_id == source.pk


@pytest.mark.django_db
def test_an_unmapped_source_is_not_blocked_by_a_missing_severity() -> None:
    """AC4.3's "never a 500": an unconfigured source must still produce an alert."""
    stored = store_ingested_alert(
        wire_source="never-configured",
        alert_type="unknown-event",
        payload={"anything": [1, 2, 3]},
        title="Unmapped",
    )
    stored.alert.full_clean(exclude=["case", "owner_org", "assignee"])
    stored.alert.refresh_from_db()
    assert stored.alert.status.value == "New"
    assert stored.alert.raw_payload == {"anything": [1, 2, 3]}, (
        "the raw payload is preserved verbatim"
    )
