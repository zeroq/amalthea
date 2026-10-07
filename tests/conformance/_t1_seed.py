"""Seeding helpers shared by the Wave B T1 conformance tests.

Three test modules (`test_authz`, `test_t1_surface`, `test_unknown_fields`) need the same
background — a case, an alert, a task, a ledger event and an observable — and all three build
it the way the rest of the suite does:

* every dictionary row (`CaseStatus`, `AlertStatus`, `ObservableType`) is `get_or_create`d, so
  nothing assumes the seed migrations ran (`test_mvp_loop.py`, `test_query_api.py`);
* rows are created through the ORM rather than through the API. An authorization matrix that
  authenticates in order to build its own fixtures cannot then claim anything about what
  happens when it is *not* authorized.

The shared client fixtures (`analyst`, `api`, `anonymous_api`) and the suite-wide throttle
reset live one level up, in `conformance/conftest.py`.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases.ledger import append_timeline_event
from cases.models import Case, CaseStatus, Task, TimelineEvent
from identity.models import ApiKey, User
from observables.models import Observable, ObservableType


def _case(title: str, **kwargs: Any) -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, status=status, **kwargs)


def _alert(title: str, *, ref: str, case: Case | None = None) -> Alert:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=ref,
        title=title,
        status=status,
        case=case,
    )


def _task(case: Case, title: str = "Isolate the host", **kwargs: Any) -> Task:
    return Task.objects.create(case=case, title=title, **kwargs)


def _event(case: Case, title: str = "Analyst note") -> TimelineEvent:
    return append_timeline_event(case, title=title, kind="custom")


def _observable(data: str, type_name: str = "ip") -> Observable:
    otype = ObservableType.objects.get_or_create(
        name=type_name, defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(data_type=otype, data=data, normalized_data=data)


def _call(client: APIClient, method: str, path: str, body: Any = None) -> Any:
    """Issue `method` against `path`, with a JSON body where one is expected.

    Django's `Client.request()` is the keyword-only low-level entry point (`request(METHOD=…)`),
    and `generic()` defaults to an empty octet-stream body that a JSON view cannot parse —
    hence one dispatcher rather than a `generic()` call at every one of the 30-odd matrix rows.
    `GET` and `DELETE` are the two verbs whose body the T1 surface ignores; `body=None` on the
    other three means "an empty JSON object", which is what a caller with nothing to say sends.
    """
    if method == "GET":
        return client.get(path)
    if method == "DELETE":
        return client.delete(path)
    if method in ("POST", "PATCH", "PUT"):
        payload = b"{}" if body is None else json.dumps(body).encode()
        return client.generic(method, path, data=payload, content_type="application/json")
    raise ValueError(f"unhandled method {method!r}")


def _api_key_client(user: User, *, scope: str) -> tuple[APIClient, str]:
    """A real bearer key for `user`, verified the way `compat.auth` verifies one.

    Returns the client and the plaintext token so a test can assert on what was sent. The
    prefix lookup in `ApiKeyAuthentication` needs `len(token) > 12`, hence the `k` + hex
    construction — a token short enough to be its own prefix could not match the row it was
    hashed from.
    """
    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=user,
        name=f"{scope} key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope=scope,
    )
    return APIClient(HTTP_AUTHORIZATION=f"Bearer {token}"), token
