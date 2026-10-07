"""AC-B4 — the unknown-field sweep (plan §9 B4, §12 AC-B4).

AC10.2 reads: *"unknown fields / unknown options are ignored or cleanly rejected — never a bare
`400 "Bad request"` with no detail, and never silent mis-parses."* That is two claims, and they
need different evidence.

**Never a bare 400.** A client that sends a field Amalthea has never heard of must not be told
"Bad request" and left to guess which of the eleven keys it sent was the wrong one. The sweep
therefore walks every entity write endpoint — six `POST`s and five `PATCH`es — with unknown keys
riding alongside known ones, and applies one matcher: no 5xx, and any 400 must carry a `message`
that says something *or* a non-empty `fields` map. The `type == "BadRequest"` envelope alone is
not accepted as detail, because `compat/errors.py` produces exactly that envelope for DRF's
undetailed 400s too — the bare shape is what the AC is about, so that is what is asserted.

**Never a silent mis-parse.** Being *tolerated* is not the same as being *ignored*: an unknown
key that leaked into a column would pass every status-code assertion in this file. So each row
carries a probe value, and after the write that value is hunted through the response body *and*
through a read-back of the entity itself. It may not appear anywhere. Its *key* is a different
matter: `POST /api/v1/alert` deliberately records unknown keys under
`ingestionWarnings.unmapped_fields` (alerts/views.py), and test F pins that as the honest
outcome — recorded as unmapped, rather than dropped as though nothing was sent.

The complementary direction is the *unknown-only* PATCH. Every `PATCH` on this surface is
"apply only the keys present", so a body of nothing but unknown keys has to be refused rather
than quietly answered 204 — a 204 there would mean the view had decided an unknown key was
updatable after all. Each of those is checked for a detailed 400 *and* for an untouched row.

The row is the thing. A status code says the view returned; only re-reading the entity says it
did not write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from alerts.models import Alert
from cases.models import Case, Task, TimelineEvent
from core.serializers import custom_event_json
from identity.models import User
from observables.models import Observable

from ._t1_seed import _alert, _call, _case, _event, _observable, _task

# The probe is deliberately unlike anything a real payload would carry: a value that cannot
# collide with an existing column's contents, and a key whose spelling no view reads.
PROBE_KEY = "__wave_b_unknown_field__"
PROBE_VALUE = "wave-b-probe-value-4f2c"
PROBE: dict[str, Any] = {PROBE_KEY: PROBE_VALUE, "theHiveId": "~999999"}

# `InputCustomEvent.date` is epoch-ms (plan §5.1 keeps the timeline on ms integers, P8-1).
EVENT_MS = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp() * 1000)


@dataclass(frozen=True)
class Probe:
    """One write endpoint, called with known fields *and* unknown ones alongside."""

    name: str
    method: str
    path: str  # `{case}`-style placeholders, filled from the seed
    body: dict[str, Any]
    expected: int
    # Where the entity can be re-read after the call: `None` means the response body *is* the
    # entity (the 201/200 echoes); `"orm:event"` because `customEvent` has no GET route;
    # `"orm:observable"` because the detail route omits the column being written; or a
    # `"GET /path/{id}"` template.
    read: str | None = None
    applied: dict[str, Any] = field(default_factory=dict)


# Every row carries at least one *known* field, so a correct implementation answers 2xx: unknown
# keys riding along may not turn a valid request into a rejection.
SWEEP: list[Probe] = [
    Probe(
        "alert-create",
        "POST",
        "/api/v1/alert",
        {
            "title": "Probe alert",
            "type": "probe",
            "source": "probe-source",
            "sourceRef": "probe-ref-1",
            "severity": 3,
        }
        | PROBE,
        201,
        applied={"title": "Probe alert", "severity": 3},
    ),
    Probe(
        "alert-patch",
        "PATCH",
        "/api/v1/alert/{alert}",
        {"title": "Probe title"} | PROBE,
        200,
        read="GET /api/v1/alert/{alert}",
        applied={"title": "Probe title"},
    ),
    Probe(
        "case-create",
        "POST",
        "/api/v1/case",
        {"title": "Probe case", "description": "probe description"} | PROBE,
        201,
        applied={"title": "Probe case", "description": "probe description"},
    ),
    Probe(
        "case-patch",
        "PATCH",
        "/api/v1/case/{case}",
        {"title": "Probe case title"} | PROBE,
        200,
        read="GET /api/v1/case/{case}",
        applied={"title": "Probe case title"},
    ),
    Probe(
        "task-create",
        "POST",
        "/api/v1/case/{case}/task",
        {"title": "Probe task", "status": "Waiting"} | PROBE,
        201,
        applied={"title": "Probe task", "status": "Waiting"},
    ),
    Probe(
        "task-patch",
        "PATCH",
        "/api/v1/task/{task}",
        {"title": "Probe task title"} | PROBE,
        204,
        read="GET /api/v1/task/{task}",
        applied={"title": "Probe task title"},
    ),
    Probe(
        "custom-event-create",
        "POST",
        "/api/v1/case/{case}/customEvent",
        {"title": "Probe event", "date": EVENT_MS} | PROBE,
        201,
        applied={"title": "Probe event"},
    ),
    Probe(
        "custom-event-patch",
        "PATCH",
        "/api/v1/customEvent/{event}",
        {"title": "Probe event title"} | PROBE,
        204,
        read="orm:event",
        applied={"title": "Probe event title"},
    ),
    Probe(
        # Slashless, the spelling thehive4py 2.1.0 posts. A pre-verifier route split served
        # GET on the bare path and POST on the slashed one, so this spelling answered 405;
        # both now resolve to the one GET+POST dispatcher (`case_observable_list`).
        "case-observable-create",
        "POST",
        "/api/v1/case/{case}/observable",
        {"dataType": "ip", "data": "10.11.12.13"} | PROBE,
        200,
        applied={"data": "10.11.12.13"},
    ),
    Probe(
        "alert-observable-create",
        "POST",
        "/api/v1/alert/{alert}/observable",
        {"dataType": "ip", "data": "10.11.12.14"} | PROBE,
        201,
        applied={"data": "10.11.12.14"},
    ),
    Probe(
        "observable-patch",
        "PATCH",
        "/api/v1/observable/{observable}",
        {"message": "Probe message"} | PROBE,
        204,
        read="orm:observable",
        applied={"message": "Probe message"},
    ),
]

# The opposite direction: a body made of *nothing but* keys no view reads. Every PATCH here is
# "apply only the keys present, no field no write", so these must all be refused with the
# "No updatable field supplied" message — which is a *specific* message with an empty `fields`
# map, and therefore passes AC10.2 where a bare "Bad request" would not.
UNKNOWN_ONLY: list[Probe] = [
    Probe("alert", "PATCH", "/api/v1/alert/{alert}", dict(PROBE), 400),
    Probe("case", "PATCH", "/api/v1/case/{case}", dict(PROBE), 400),
    Probe("task", "PATCH", "/api/v1/task/{task}", dict(PROBE), 400),
    Probe("custom-event", "PATCH", "/api/v1/customEvent/{event}", dict(PROBE), 400),
    Probe("observable", "PATCH", "/api/v1/observable/{observable}", dict(PROBE), 400),
    # Case-sensitivity is the quiet variant of the same failure: `Title` is one keystroke away
    # from a key every view reads, and a `setattr(payload, ...)` loop that did not care about
    # spelling would accept it and rename the row.
    Probe("task-case-variant", "PATCH", "/api/v1/task/{task}", {"Title": "Hijacked"}, 400),
    Probe("case-case-variant", "PATCH", "/api/v1/case/{case}", {"Status": "Closed"}, 400),
]


@pytest.fixture
def ids(db: None) -> dict[str, str]:
    case = _case("Unknown-field case")
    return {
        "case": str(case.id),
        "alert": str(_alert("Unknown-field alert", ref="unknown-ref", case=case).id),
        "task": str(_task(case).id),
        "event": str(_event(case).id),
        "observable": str(_observable("10.0.0.11").id),
    }


# --- the AC10.2 matcher ------------------------------------------------------


def _assert_detailed_400(response: Any) -> None:
    """A 400 is only allowed when it says *which* field or *why*.

    `type` alone is not detail: `thehive_exception_handler` wraps DRF's undetailed validation
    errors into `{"type": "BadRequest", "message": "Bad request", "fields": {...}}`, so the bare
    AC10.2 failure mode arrives wearing the right `type`. Either the map names a field, or the
    message has to be something more specific than the default it would have got anyway.
    """
    body = response.json()
    assert body.get("type") == "BadRequest", body
    fields = body.get("fields")
    assert isinstance(fields, dict), f"400 without a `fields` map: {body}"
    message = body.get("message") or ""
    assert fields or message not in ("", "Bad request"), f"bare 400 with no detail: {body}"


def _assert_tolerated(response: Any, probe: Probe) -> None:
    """No 5xx, and a 400 that explains itself — the AC10.2 floor."""
    assert response.status_code != 500, (probe.name, response.content)
    if response.status_code == 400:
        _assert_detailed_400(response)


def _wire(api: APIClient, ids: dict[str, str], probe: Probe, response: Any) -> Any:
    """The entity as it stands *after* the write, in whatever shape it can be read back."""
    if probe.read is None:
        assert response.content, f"{probe.name}: an echoing endpoint answered with no body"
        return response.json()
    if probe.read == "orm:event":
        return custom_event_json(TimelineEvent.objects.get(pk=ids["event"]))
    if probe.read == "orm:observable":
        # The observable *detail* route omits `message`, `ioc` and `sighted` entirely, so a GET
        # read-back would scan a body that never contained the column the PATCH wrote — which is
        # exactly where a mis-parse would hide. Every column `_update_observable` can touch is
        # enumerated here instead.
        row = Observable.objects.get(pk=ids["observable"])
        return {
            "message": row.message,
            "tlp": row.tlp,
            "pap": row.pap,
            "ioc": row.ioc,
            "sighted": row.sighted,
            "ignoreSimilarity": row.ignore_similarity,
            "dataType": row.data_type.name,
            "data": row.data,
            "enrichmentData": row.enrichment_data or {},
        }
    method, _, path = probe.read.partition(" ")
    return _call(api, method, path.format(**ids)).json()


def _find(wire: Any, key: str) -> list[Any]:
    """Every value stored under `key`, wherever it sits in a nested body."""
    found: list[Any] = []
    if isinstance(wire, dict):
        for name, value in wire.items():
            if name == key:
                found.append(value)
            found.extend(_find(value, key))
    elif isinstance(wire, list):
        for item in wire:
            found.extend(_find(item, key))
    return found


# --- AC-B4: tolerated, and never mis-parsed -----------------------------------


@pytest.mark.parametrize("probe", SWEEP, ids=lambda p: p.name)
def test_unknown_fields_are_tolerated_never_a_bare_400(
    probe: Probe, api: APIClient, ids: dict[str, str]
) -> None:
    """Known + unknown, together: the unknown keys may not turn a valid request into a rejection.

    Each body carries at least one known field with a valid value, so the only correct answers
    are 2xx. The matcher still runs first, because "the request was refused" and "the refusal was
    useless" are separate failures and a test that only asserted the status would pass a bare 400
    as happily as a 201.
    """
    response = _call(api, probe.method, probe.path.format(**ids), probe.body)
    _assert_tolerated(response, probe)
    assert response.status_code == probe.expected, (probe.name, response.content)


@pytest.mark.parametrize("probe", SWEEP, ids=lambda p: p.name)
def test_unknown_fields_never_reach_a_known_column(
    probe: Probe, api: APIClient, ids: dict[str, str]
) -> None:
    """The probe *value* may not appear in the entity, the response, or a read-back of either.

    Tolerating an unknown key is only half of AC10.2 — the other half is that it was dropped
    rather than absorbed. `PROBE_VALUE` is searched for as text in the response body and in the
    entity as re-read afterwards; finding it in a column would mean some `payload.get(...)` had
    picked it up under a name no view claims to read.

    The probe *key* is allowed to show up in one documented place: `POST /alert` records the
    names of unmapped keys under `ingestionWarnings.unmapped_fields`, which is the opposite of a
    silent mis-parse and is pinned by `test_alert_create_records_unknown_keys_as_unmapped`.
    """
    response = _call(api, probe.method, probe.path.format(**ids), probe.body)
    assert response.status_code == probe.expected, (probe.name, response.content)

    for label, wire in (
        ("response", response.json() if response.content else None),
        ("entity", _wire(api, ids, probe, response)),
    ):
        if wire is None:
            continue
        assert PROBE_VALUE not in json.dumps(wire), (
            f"{probe.name}: the unknown value reached the {label}: {wire}"
        )

    for key, expected in probe.applied.items():
        assert expected in _find(_wire(api, ids, probe, response), key), (
            f"{probe.name}: known field {key!r} was not applied to {expected!r}"
        )


@pytest.mark.parametrize("probe", UNKNOWN_ONLY, ids=lambda p: p.name)
def test_a_patch_of_unknown_fields_only_is_refused_not_applied(
    probe: Probe, api: APIClient, ids: dict[str, str]
) -> None:
    """No known key, no write — a 204 here would mean an unknown key was treated as updatable."""
    path = probe.path.format(**ids)
    snapshot = _snapshot(ids, probe.path)
    response = _call(api, probe.method, path, probe.body)

    assert response.status_code == 400, response.content
    _assert_detailed_400(response)
    assert response.json()["message"] == "No updatable field supplied", response.content
    assert _snapshot(ids, probe.path) == snapshot, f"{probe.name}: an unknown key wrote anyway"


def _snapshot(ids: dict[str, str], path: str) -> tuple[Any, ...]:
    """The columns an unknown key could plausibly have reached, before and after the call.

    Keyed off the route rather than the row id, so a row added to `UNKNOWN_ONLY` is covered
    without another branch — and `customEvent` needs no GET route to be snapshot, which is the
    whole reason this reads the ORM instead of the API.
    """
    if "/alert" in path:
        row = Alert.objects.get(pk=ids["alert"])
        return (row.title, row.description, row.severity, row.tlp, row.pap, row.status_id)
    if "/customEvent" in path:
        row = TimelineEvent.objects.get(pk=ids["event"])
        return (row.title, row.description, row.date, row.end_date)
    if "/task" in path:
        row = Task.objects.get(pk=ids["task"])
        return (row.title, row.description, row.status, row.order, row.assignee_id)
    if "/observable" in path:
        row = Observable.objects.get(pk=ids["observable"])
        return (row.message, row.tlp, row.pap, row.ioc, row.sighted, row.data_type_id)
    row = Case.objects.get(pk=ids["case"])
    return (row.title, row.description, row.severity, row.status_id, row.assignee_id)


# --- the two directions AC10.2 names explicitly ------------------------------


def test_alert_create_records_unknown_keys_as_unmapped(api: APIClient, ids: dict[str, str]) -> None:
    """Unknown keys on ingest are *recorded*, not dropped: that is what "not silent" means.

    `_create_alert` sorts every key it does not read into `ingestion_warnings.unmapped_fields`
    and echoes it as `ingestionWarnings`, so an operator can see that a parser is missing a
    field instead of discovering it six months later. The key's *value* still goes nowhere —
    which is the difference between "recorded as unmapped" and "mis-parsed".
    """
    response = _call(
        api,
        "POST",
        "/api/v1/alert",
        {
            "title": "Unmapped alert",
            "type": "probe",
            "source": "probe-source",
            "sourceRef": "probe-ref-2",
        }
        | PROBE,
    )
    assert response.status_code == 201, response.content

    warnings = response.json()["ingestionWarnings"]
    assert PROBE_KEY in warnings["unmapped_fields"], warnings
    assert PROBE_VALUE not in json.dumps(warnings), (
        f"the unknown key's *value* was stored: {warnings}"
    )
    assert response.json()["title"] == "Unmapped alert"


def test_a_create_with_only_unknown_keys_still_names_the_missing_field(
    api: APIClient, ids: dict[str, str]
) -> None:
    """Unknown keys may not mask validation: the refusal has to name `title`, not the probe."""
    response = _call(api, "POST", "/api/v1/case", dict(PROBE))
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["fields"] == {"title": ["required"]}, body
    assert PROBE_KEY not in json.dumps(body), f"the refusal blamed the unknown key: {body}"


def test_task_create_with_unknown_keys_keeps_its_recorded_response_shape(
    api: APIClient, ids: dict[str, str]
) -> None:
    """The 201 body stays the three keys A3's create view emits plus both id spellings.

    Unknown keys must not *add* a field to a response a client parses structurally, and a known
    key the body echoes (`title`, `status`) must not be *dropped* because something rode along
    with it — the two failure modes of an envelope assembled from the wrong dict.
    """
    response = _call(
        api,
        "POST",
        f"/api/v1/case/{ids['case']}/task",
        {"title": "Bare task", "status": "Waiting"} | PROBE,
    )
    assert response.status_code == 201, response.content
    assert set(response.json()) == {"_id", "id", "title", "status"}, response.json()


def test_login_ignores_unknown_payload_keys(anonymous_api: APIClient, db: None) -> None:
    """`POST /login` reads `user`, `password` and `organisation` — nothing else, and it says so
    by answering 200 rather than refusing a credential pair over an unrelated key."""
    User.objects.create_user(username="probe-login", password="correct-horse-battery-staple")
    response = _call(
        anonymous_api,
        "POST",
        "/api/v1/login",
        {"user": "probe-login", "password": "correct-horse-battery-staple"} | PROBE,
    )
    assert response.status_code == 200, response.content
    assert response.json()["login"] == "probe-login"
    assert PROBE_VALUE not in response.content.decode()
