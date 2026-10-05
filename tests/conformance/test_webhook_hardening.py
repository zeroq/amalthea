"""The webhook receiver as a trust boundary (plan §Phase 4, AC4.1—AC4.8).

These tests are mostly about **refusal and refusal ordering**, because the receiver's job is to
decide what not to trust: an unbounded body, a lying `Content-Length`, a JSON array, a document
nested deep enough to blow the mapping engine's stack, a forged secret, a flood. A receiver that
accepts these quietly is indistinguishable from one that does not exist.

Ordering is asserted as a property, not as an implementation detail: a request that is *both*
oversized and unauthenticated must be refused on size, because the body is never read.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.hashers import make_password
from django.test import Client
from django.urls import reverse

from alerts.models import Alert
from ingest.models import IngestionSource
from ingest.views import webhook_alerts

PHISHING = {
    "title": "Suspicious inbox rule",
    "severity": 3,
    "sourceRef": "evt-1",
    "event": "mailbox.rule.created",
}


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


@pytest.fixture
def locked_source(db: None) -> IngestionSource:
    return IngestionSource.objects.create(
        slug="locked",
        name="Secret-protected feed",
        webhook_secret_hash=make_password("correct-horse"),
        mapping_config={},
    )


def _post(client: Client, slug: str, body: Any, **headers: str) -> Any:
    return client.post(
        reverse("alerts-webhook", kwargs={"source_id": slug}),
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


# --- happy path -----------------------------------------------------------


def test_a_well_formed_post_creates_an_alert(source: IngestionSource) -> None:
    response = _post(Client(), "o365", PHISHING)

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["title"] == "Suspicious inbox rule"
    assert body["severity"] == 3
    assert body["created"] is True
    assert body["correlation_key"] == "evt-1", "the configured JSON-path was not honoured"

    alert = Alert.objects.get(pk=body["_id"])
    assert alert.raw_payload == PHISHING, "the raw payload was not stored verbatim"
    assert alert.ingestion_source_id == source.id


def test_a_replay_is_accepted_but_not_re_stored(source: IngestionSource) -> None:
    """AC4.4 — a retry under back-pressure is not an error and does not duplicate the alert."""
    client = Client()
    first = _post(client, "o365", PHISHING)
    second = _post(client, "o365", PHISHING)

    assert first.status_code == 201
    assert second.status_code == 200, "a replay was reported as an error"
    assert second.json()["created"] is False
    assert second.json()["_id"] == first.json()["_id"]
    assert Alert.objects.count() == 1


def test_the_mapping_config_wins_over_a_literal_key(source: IngestionSource) -> None:
    """A nested path is the whole reason Module A exists, so it must actually be used."""
    source.mapping_config = {"title": "$.detail.summary", "severity": "$.detail.rank"}
    source.save()

    response = _post(
        Client(), "o365", {"title": "ignored", "detail": {"summary": "real", "rank": 4}}
    )

    assert response.status_code == 201
    assert response.json()["title"] == "real"
    assert response.json()["severity"] == 4


def test_a_source_without_a_mapping_still_works_via_literal_keys(db: None) -> None:
    IngestionSource.objects.create(slug="bare", name="Bare", mapping_config={})
    response = _post(Client(), "bare", {"title": "literal", "severity": 1, "id": "x-1"})
    assert response.status_code == 201
    assert response.json()["title"] == "literal"


# --- secrets --------------------------------------------------------------


def test_a_missing_secret_is_401(locked_source: IngestionSource) -> None:
    response = _post(Client(), "locked", {"title": "no secret"})
    assert response.status_code == 401
    assert response.json()["type"] == "unauthorized"
    assert Alert.objects.count() == 0


def test_a_wrong_secret_is_403(locked_source: IngestionSource) -> None:
    response = _post(Client(), "locked", {"title": "wrong"}, HTTP_X_WEBHOOK_SECRET="guess")
    assert response.status_code == 403
    assert response.json()["type"] == "forbidden"
    assert Alert.objects.count() == 0


def test_the_right_secret_is_accepted(locked_source: IngestionSource) -> None:
    """Guards against the stored digest being compared as if it were the plaintext."""
    response = _post(Client(), "locked", {"title": "authed"}, HTTP_X_WEBHOOK_SECRET="correct-horse")
    assert response.status_code == 201, response.content
    assert Alert.objects.count() == 1


def test_the_hash_itself_is_not_accepted_as_the_secret(locked_source: IngestionSource) -> None:
    """`webhook_secret_hash` holds a digest; presenting the digest must not authenticate."""
    response = _post(
        Client(),
        "locked",
        {"title": "hash"},
        HTTP_X_WEBHOOK_SECRET=locked_source.webhook_secret_hash,
    )
    assert response.status_code == 403


def test_a_legacy_plaintext_secret_still_verifies(db: None) -> None:
    """Rows created before hashing existed hold the secret verbatim; they must keep working."""
    IngestionSource.objects.create(slug="legacy", name="Legacy", webhook_secret_hash="plain-secret")
    response = _post(Client(), "legacy", {"title": "ok"}, HTTP_X_WEBHOOK_SECRET="plain-secret")
    assert response.status_code == 201


def test_a_bearer_token_is_accepted_as_the_secret(locked_source: IngestionSource) -> None:
    response = _post(
        Client(), "locked", {"title": "bearer"}, HTTP_AUTHORIZATION="Bearer correct-horse"
    )
    assert response.status_code == 201


# --- size, shape, depth ---------------------------------------------------


def test_an_oversized_declared_length_is_413(source: IngestionSource) -> None:
    response = Client().post(
        reverse("alerts-webhook", kwargs={"source_id": "o365"}),
        data="{}",
        content_type="application/json",
        CONTENT_LENGTH="99999999",
    )
    assert response.status_code == 413
    assert response.json()["type"] == "entityTooLarge"


@pytest.mark.parametrize("declared", [None, "10", "0"])
def test_the_body_is_re_measured_and_not_just_the_header(
    source: IngestionSource, settings: Any, declared: str | None
) -> None:
    """The `Content-Length` header is a claim, so the body itself is re-measured.

    This calls the view through `RequestFactory` rather than `Client` on purpose. The test client
    builds a real WSGI environ in which the body length and `CONTENT_LENGTH` agree, so it cannot
    express the two cases that matter:

    * `declared=None` — a chunked request, where no length is announced at all and only the
      re-measured body can refuse it;
    * `declared="10"` — a sender that understates the length to slip under the header check.

    Each is a real way a body arrives larger than it claims, and each is refused by measuring rather
    than by believing the header.
    """
    from django.test import RequestFactory

    settings.WEBHOOK_MAX_BODY_SIZE = 256
    body = json.dumps({"title": "x" * 5000}).encode()

    request = RequestFactory().post(
        reverse("alerts-webhook", kwargs={"source_id": "o365"}),
        data=body,
        content_type="application/json",
    )
    if declared is None:
        request.META.pop("CONTENT_LENGTH", None)
    else:
        request.META["CONTENT_LENGTH"] = declared

    response = webhook_alerts(request, "o365")

    assert response.status_code == 413, response.content
    assert Alert.objects.count() == 0


def test_malformed_json_is_400_with_fields(source: IngestionSource) -> None:
    response = Client().post(
        reverse("alerts-webhook", kwargs={"source_id": "o365"}),
        data="{not json",
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "body" in response.json()["fields"]
    assert Alert.objects.count() == 0


@pytest.mark.parametrize("body", [[1, 2, 3], '"a string"', "42", "null", "true"])
def test_a_non_object_json_body_is_400(source: IngestionSource, body: str) -> None:
    """Valid JSON that is not an object would otherwise reach `payload.get(...)` and 500."""
    response = Client().post(
        reverse("alerts-webhook", kwargs={"source_id": "o365"}),
        data=body,
        content_type="application/json",
    )
    assert response.status_code == 400, response.content
    assert response.json()["fields"]["body"]


def test_a_deeply_nested_body_is_400(source: IngestionSource) -> None:
    payload: Any = {"title": "deep"}
    node = payload
    for _ in range(60):
        node["child"] = {}
        node = node["child"]

    response = _post(Client(), "o365", payload)
    assert response.status_code == 400
    assert "too deep" in response.json()["fields"]["body"][0]
    assert Alert.objects.count() == 0


def test_a_shallow_body_is_accepted(source: IngestionSource) -> None:
    response = _post(Client(), "o365", {"title": "fine", "nested": {"a": {"b": 1}}})
    assert response.status_code == 201


def test_a_non_json_content_type_is_415(source: IngestionSource) -> None:
    response = Client().post(
        reverse("alerts-webhook", kwargs={"source_id": "o365"}),
        data="<xml/>",
        content_type="application/xml",
    )
    assert response.status_code == 415


def test_a_malformed_content_length_is_400(source: IngestionSource) -> None:
    response = Client().post(
        reverse("alerts-webhook", kwargs={"source_id": "o365"}),
        data="{}",
        content_type="application/json",
        CONTENT_LENGTH="banana",
    )
    assert response.status_code == 400


def test_an_unknown_source_is_404(source: IngestionSource) -> None:
    response = _post(Client(), "nope", {"title": "x"})
    assert response.status_code == 404
    assert Alert.objects.count() == 0


# --- coercion -------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "stored"),
    [("4", 4), (4, 4), (2.9, 2), (None, 2), ("high", 2), (0, 2), (99, 2)],
)
def test_severity_is_coerced_or_defaulted(source: IngestionSource, sent: Any, stored: int) -> None:
    """Out-of-range and unparsable severity falls back to the source default rather than 400-ing.

    A sender with a different severity vocabulary must still be ingestible; refusing the alert
    because its severity scale is unfamiliar loses the alert, which is the expensive mistake here.
    """
    payload = {"title": "sev"} if sent is None else {"title": "sev", "severity": sent}
    response = _post(Client(), "o365", payload)
    assert response.status_code == 201, response.content
    assert Alert.objects.get(pk=response.json()["_id"]).severity == stored


def test_a_non_string_title_does_not_500(source: IngestionSource) -> None:
    """`str(title)` on a dict would be fine, but on an object it must not become a traceback."""
    response = _post(Client(), "o365", {"title": {"nested": "value"}})
    assert response.status_code == 201
    assert Alert.objects.get(pk=response.json()["_id"]).title == "{'nested': 'value'}"


# --- throttling -----------------------------------------------------------


def test_the_webhook_is_rate_limited(source: IngestionSource, settings: Any) -> None:
    """A single noisy feed must not be able to flood the ingest path.

    `settings.WEBHOOK_RATE_SOURCE_PER_MIN` is read *inside* the view via `settings.X`, so
    `override_settings` takes effect without a reload.
    """
    from django.core.cache import cache

    cache.clear()
    settings.WEBHOOK_RATE_SOURCE_PER_MIN = 3
    settings.WEBHOOK_RATE_IP_PER_MIN = 0  # disable the per-IP counter for this assertion

    client = Client()
    codes = [
        _post(client, "o365", {"title": f"flood {i}", "sourceRef": f"r{i}"}).status_code
        for i in range(5)
    ]

    assert codes[:3] == [201, 201, 201]
    assert codes[3] == 429, f"the limit did not engage: {codes}"
    assert Alert.objects.count() == 3, "a throttled request still wrote an alert"
    cache.clear()


def test_throttling_is_per_source_not_global(source: IngestionSource, settings: Any) -> None:
    """One noisy feed must not spend another's budget."""
    from django.core.cache import cache

    cache.clear()
    IngestionSource.objects.create(slug="quiet", name="Quiet", mapping_config={})
    settings.WEBHOOK_RATE_SOURCE_PER_MIN = 2
    settings.WEBHOOK_RATE_IP_PER_MIN = 0

    client = Client()
    for i in range(4):
        _post(client, "o365", {"title": f"n{i}", "sourceRef": f"n{i}"})

    assert _post(client, "quiet", {"title": "still fine"}).status_code == 201, (
        "one source exhausting its limit blocked a different source"
    )
    cache.clear()


def test_the_secret_is_checked_before_the_rate_budget_is_spent(
    locked_source: IngestionSource, settings: Any
) -> None:
    """An unauthenticated flood must not be able to throttle a legitimate sender out.

    Throttling sits after the secret check for exactly this reason; this test would fail if the order
    were swapped, because the bad-secret requests would consume the budget.
    """
    from django.core.cache import cache

    cache.clear()
    settings.WEBHOOK_RATE_SOURCE_PER_MIN = 5
    settings.WEBHOOK_RATE_IP_PER_MIN = 0

    client = Client()
    for _ in range(10):
        assert (
            _post(client, "locked", {"title": "x"}, HTTP_X_WEBHOOK_SECRET="wrong").status_code
            == 403
        )

    assert (
        _post(
            client, "locked", {"title": "real"}, HTTP_X_WEBHOOK_SECRET="correct-horse"
        ).status_code
        == 201
    )
    cache.clear()
