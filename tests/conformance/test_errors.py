"""The error envelope's contract, including the 500 that must never carry an exception's text.

`test_error_enveloped_unauth` pins the shaped (handled) half — DRF status in, envelope out.
The two tests below pin the *unhandled* half: `thehive_exception_handler` is also the path
every non-APIException takes, and echoing `str(exc)` there turned any 500 into an information
leak (auditor F6). The traceback still reaches the operator — `logger.exception` — while the
wire gets one fixed body.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from compat.errors import thehive_exception_handler

from ._t1_seed import _case


@pytest.mark.django_db
def test_error_envelope_unauth():
    client = APIClient()
    resp = client.get("/api/v1/nonexistent")
    assert resp.status_code == 401
    body = resp.json()
    assert set(body.keys()) == {"type", "message"}
    assert body["type"] == "AuthenticationError"


def test_an_unhandled_exception_becomes_a_fixed_500_envelope() -> None:
    """The handler itself: whatever the exception says, the body says "Internal error"."""
    response = thehive_exception_handler(
        ValueError("connection failed for postgres://analyst:hunter2@db01"),
        {"view": "probe"},
    )
    assert response is not None
    assert response.status_code == 500
    # The equality *is* the no-leak assertion: `hunter2` can only appear via `str(exc)`.
    assert response.data == {"type": "GenericError", "message": "Internal error"}


@pytest.mark.django_db
def test_an_unhandled_error_on_a_real_endpoint_is_a_500_with_the_fixed_message(
    api: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a view-level crash (here, `parse_timestamp` raising) answers with the fixed body.

    `parse_timestamp` is monkeypatched at its `cases.views` name — the module global the view
    looks up at call time — so the exception travels the full DRF `dispatch → handle_exception
    → EXCEPTION_HANDLER` chain, which is the path the fixed envelope exists for. (The
    hostile-input paths that *used* to crash here are now 400s — FIX-4 — so a deliberately
    injected failure is the reliable way to exercise the 500.)
    """

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("secret-dsn=hunter2-really")

    monkeypatch.setattr("cases.views.parse_timestamp", boom)
    case = _case("Unhandled error case")
    response = api.post(
        f"/api/v1/case/{case.id}/customEvent",
        {"title": "Will crash", "date": 1745539200000},
        format="json",
    )
    assert response.status_code == 500, response.content
    body = response.json()
    assert body == {"type": "GenericError", "message": "Internal error"}
    assert "hunter2" not in response.content.decode(), "the exception text reached the wire"
