"""AC-B2 — the authentication/authorization matrix for the T1 surface (plan §8).

Every row is one endpoint, every test is one *claim about a whole matrix*, parametrized so a
failure names the endpoint rather than the policy:

===============  =====================================  =====================
test             anonymous          bad bearer token     read-only key
===============  =====================================  =====================
`anonymous_*`    401 `AuthError`    —                    —
`bad_key_*`      —                  401 `AuthError`      —
`readonly_*`     —                  —                    GET → 200, else 403
`readwrite_*`    —                  —                    never 401/403/5xx
===============  =====================================  =====================

Beyond the four claims, every assertion also demands `status_code < 500`: a policy that
refuses correctly and then crashes elsewhere is not a refusal, and an unhandled model
constraint firing on the way to a 403 is exactly the kind of regression this sweep exists
to catch.

`POST /api/v1/query` is in the **mutating** half deliberately. `ScopePermission` decides on
the verb alone (deviation **P10-z**), the plan's matrix lists `query` among its rows, and the
permission layer is where the plan asked for the check. A read-only key therefore runs reads
through `GET` but not through `POST /api/v1/query`; say so out loud rather than quietly
exempting the one path a test would notice.

The three genuinely public endpoints — the per-source webhook and the session login/logout —
are asserted separately, because they are public *by contract* and would otherwise show up
below as failures to refuse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from rest_framework.test import APIClient

from automation.models import Playbook
from cases.models import TTP, CaseTemplate, Procedure
from identity.models import Organisation, User
from ingest.models import IngestionSource

from ._t1_seed import _alert, _api_key_client, _call, _case, _event, _observable, _task

# --- the matrix ------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str  # `{case}`-style placeholders, filled from the seed
    mutating: bool


READ: list[Endpoint] = [
    Endpoint("alert-list", "GET", "/api/v1/alert", False),
    Endpoint("alert-detail", "GET", "/api/v1/alert/{alert}", False),
    Endpoint("alert-raw", "GET", "/api/v1/alert/{alert}/raw", False),
    Endpoint("case-list", "GET", "/api/v1/case", False),
    Endpoint("case-detail", "GET", "/api/v1/case/{case}", False),
    Endpoint("case-task-list", "GET", "/api/v1/case/{case}/task", False),
    Endpoint("case-observable-list", "GET", "/api/v1/case/{case}/observable", False),
    Endpoint("case-timeline", "GET", "/api/v1/case/{case}/timeline", False),
    Endpoint("task-detail", "GET", "/api/v1/task/{task}", False),
    Endpoint("custom-field-list", "GET", "/api/v1/customField", False),
    Endpoint("observable-detail", "GET", "/api/v1/observable/{observable}", False),
    # T2 P4 — case templates list/detail and the aggregate taxonomy read.
    Endpoint("case-template-list", "GET", "/api/v1/case/template", False),
    Endpoint("case-template-detail", "GET", "/api/v1/case/template/{template}", False),
    Endpoint("taxonomy", "GET", "/api/v1/taxonomy", False),
    # T2 P5 — case export, procedure lookup and the TTP vocabulary.
    Endpoint("case-export", "GET", "/api/v1/case/{case}/export", False),
    Endpoint("procedure-detail", "GET", "/api/v1/procedure/{procedure}", False),
    Endpoint("ttp-list", "GET", "/api/v1/ttp", False),
    Endpoint("ttp-detail", "GET", "/api/v1/ttp/{ttp}", False),
    # T2 P2 — playbook authoring surface.
    Endpoint("playbook-list", "GET", "/api/v1/playbook", False),
    Endpoint("playbook-meta", "GET", "/api/v1/playbook/_meta", False),
    Endpoint("playbook-detail", "GET", "/api/v1/playbook/{playbook}", False),
]

MUTATING: list[Endpoint] = [
    Endpoint("alert-create", "POST", "/api/v1/alert", True),
    Endpoint("alert-patch", "PATCH", "/api/v1/alert/{alert}", True),
    Endpoint("alert-delete", "DELETE", "/api/v1/alert/{alert}", True),
    Endpoint("alert-import", "POST", "/api/v1/alert/{alert}/import", True),
    Endpoint("alert-import-into", "POST", "/api/v1/alert/{alert}/import/{case}", True),
    Endpoint("alert-merge", "POST", "/api/v1/alert/{alert}/merge/{case}", True),
    Endpoint("alert-observable", "POST", "/api/v1/alert/{alert}/observable", True),
    Endpoint("case-create", "POST", "/api/v1/case", True),
    Endpoint("case-patch", "PATCH", "/api/v1/case/{case}", True),
    Endpoint("case-delete", "DELETE", "/api/v1/case/{case}", True),
    Endpoint("case-task-create", "POST", "/api/v1/case/{case}/task", True),
    # Slashless, the exact spelling thehive4py 2.1.0 posts (`endpoints/observable.py:38`). A
    # pre-verifier route split served GET on the slashless path and POST on the slashed one, so
    # this spelling 405'd through the untyped `GenericError` branch after the permission check
    # — a row that would pass while never reaching the view it claims to cover (verifier V1).
    Endpoint("case-observable-create", "POST", "/api/v1/case/{case}/observable", True),
    Endpoint("case-custom-event", "POST", "/api/v1/case/{case}/customEvent", True),
    Endpoint("case-alert-remove", "DELETE", "/api/v1/case/{case}/alert/{alert}", True),
    Endpoint("task-patch", "PATCH", "/api/v1/task/{task}", True),
    Endpoint("task-delete", "DELETE", "/api/v1/task/{task}", True),
    Endpoint("event-patch", "PATCH", "/api/v1/customEvent/{event}", True),
    Endpoint("event-delete", "DELETE", "/api/v1/customEvent/{event}", True),
    Endpoint("observable-patch", "PATCH", "/api/v1/observable/{observable}", True),
    Endpoint("observable-delete", "DELETE", "/api/v1/observable/{observable}", True),
    Endpoint("query", "POST", "/api/v1/query", True),
    # T2 P4 — per-item bulk patches, merge, template apply/CRUD. Bodies are omitted on purpose:
    # the claim is only that the permission layer does not block the verbs, and the generic
    # `ids is required` 400 is well under the 500 ceiling this matrix allows.
    Endpoint("case-bulk", "PATCH", "/api/v1/case/_bulk", True),
    Endpoint("task-bulk", "PATCH", "/api/v1/task/_bulk", True),
    Endpoint("observable-bulk", "PATCH", "/api/v1/observable/_bulk", True),
    Endpoint("alert-bulk", "PATCH", "/api/v1/alert/_bulk", True),
    Endpoint("case-merge", "POST", "/api/v1/case/_merge/{case}", True),
    Endpoint("case-apply-template", "POST", "/api/v1/case/_bulk/caseTemplate", True),
    Endpoint("case-template-create", "POST", "/api/v1/case/template", True),
    Endpoint("case-template-patch", "PATCH", "/api/v1/case/template/{template}", True),
    Endpoint("case-template-delete", "DELETE", "/api/v1/case/template/{template}", True),
    # T2 P5 — procedure attach (case and alert), procedure edits, and the TTP vocabulary.
    Endpoint("case-procedure-create", "POST", "/api/v1/case/{case}/procedure", True),
    Endpoint("case-procedures-create", "POST", "/api/v1/case/{case}/procedures", True),
    Endpoint("alert-procedure-create", "POST", "/api/v1/alert/{alert}/procedure", True),
    Endpoint("alert-procedures-create", "POST", "/api/v1/alert/{alert}/procedures", True),
    Endpoint("procedure-patch", "PATCH", "/api/v1/procedure/{procedure}", True),
    Endpoint("procedure-delete", "DELETE", "/api/v1/procedure/{procedure}", True),
    Endpoint("procedure-bulk-delete", "POST", "/api/v1/procedure/delete/_bulk", True),
    Endpoint("ttp-create", "POST", "/api/v1/ttp", True),
    Endpoint("ttp-patch", "PATCH", "/api/v1/ttp/{ttp}", True),
    Endpoint("ttp-delete", "DELETE", "/api/v1/ttp/{ttp}", True),
    # T2 P2 — playbook authoring surface.
    Endpoint("playbook-create", "POST", "/api/v1/playbook", True),
    Endpoint("playbook-patch", "PATCH", "/api/v1/playbook/{playbook}", True),
    Endpoint("playbook-delete", "DELETE", "/api/v1/playbook/{playbook}", True),
    Endpoint("playbook-run", "POST", "/api/v1/playbook/{playbook}/run", True),
]

MATRIX = READ + MUTATING


@pytest.fixture
def ids(db: None) -> dict[str, str]:
    """One row of every shape the matrix addresses, keyed by the placeholder it fills."""
    case = _case("Authz case")
    procedure = Procedure.objects.create(
        case=case, pattern_id="T1059", pattern_name="Command Shell"
    )
    playbook = Playbook.objects.filter(name="enrich-observable").first()
    return {
        "case": str(case.id),
        "alert": str(_alert("Authz alert", ref="authz-ref").id),
        "task": str(_task(case).id),
        "event": str(_event(case).id),
        "observable": str(_observable("10.0.0.7").id),
        "template": str(CaseTemplate.objects.create(name="authz-template").id),
        "procedure": str(procedure.id),
        "ttp": str(TTP.objects.create(name="authz-ttp", ttp_code="T1059").id),
        "playbook": str(playbook.id) if playbook else "",
    }


def _path(ep: Endpoint, ids: dict[str, str]) -> str:
    return ep.path.format(**ids)


def _assert_refused(response: object, expected: int, envelope_type: str, ep: Endpoint) -> None:
    body = response.json()
    assert response.status_code == expected, (ep.name, response.status_code, body)
    assert body["type"] == envelope_type, (ep.name, body)
    assert response.status_code < 500


# --- AC-B2: anonymous and bad tokens are refused everywhere ----------------


@pytest.mark.parametrize("ep", MATRIX, ids=lambda ep: ep.name)
def test_anonymous_is_refused(ep: Endpoint, ids: dict[str, str], anonymous_api: APIClient) -> None:
    """No credential at all → 401, on every T1 endpoint, with the auth envelope (plan §8)."""
    response = _call(anonymous_api, ep.method, _path(ep, ids))
    _assert_refused(response, 401, "AuthenticationError", ep)


@pytest.mark.parametrize("ep", MATRIX, ids=lambda ep: ep.name)
def test_bad_api_key_is_refused(ep: Endpoint, ids: dict[str, str]) -> None:
    """A syntactically valid `Bearer` that matches no row → 401, never a 404 or a 200."""
    client = APIClient(HTTP_AUTHORIZATION="Bearer invalidkey123456789012")
    response = _call(client, ep.method, _path(ep, ids))
    _assert_refused(response, 401, "AuthenticationError", ep)


# --- AC-B2: read-only keys -------------------------------------------------


@pytest.mark.parametrize("ep", MUTATING, ids=lambda ep: ep.name)
def test_readonly_key_cannot_mutate(ep: Endpoint, ids: dict[str, str], analyst: User) -> None:
    """`scope="read"` on a write verb → 403 `AuthorizationError` (plan §8, P10-z)."""
    client, _token = _api_key_client(analyst, scope="read")
    response = _call(client, ep.method, _path(ep, ids))
    _assert_refused(response, 403, "AuthorizationError", ep)


@pytest.mark.parametrize("ep", READ, ids=lambda ep: ep.name)
def test_readonly_key_can_read(ep: Endpoint, ids: dict[str, str], analyst: User) -> None:
    """The same key on a read verb → 200. A policy that blocked both halves would be useless."""
    client, _token = _api_key_client(analyst, scope="read")
    response = client.get(_path(ep, ids))
    assert response.status_code == 200, (ep.name, response.content)


@pytest.mark.parametrize("ep", MATRIX, ids=lambda ep: ep.name)
def test_readwrite_key_is_not_blocked_by_scope(
    ep: Endpoint, ids: dict[str, str], analyst: User
) -> None:
    """`scope="readwrite"` must never be the reason a call fails.

    Anything under 500 is acceptable here, including a 400 for an empty body or a 404 for a
    row another part of the matrix deleted — the claim is only that the *permission layer*
    let it through.
    """
    client, _token = _api_key_client(analyst, scope="readwrite")
    response = _call(client, ep.method, _path(ep, ids))
    assert response.status_code not in (401, 403), (ep.name, response.status_code, response.content)
    assert response.status_code < 500, (ep.name, response.status_code, response.content)


# --- AC-B2: the deliberately public surface --------------------------------


def test_webhook_and_session_endpoints_are_reachable_anonymously(
    anonymous_api: APIClient, db: None
) -> None:
    """The three endpoints that are public by contract must *not* be caught by the sweep.

    The webhook is gated by its per-source secret, login by the password itself; both would
    be 401s if `IsAuthenticated` applied to them, which is the exact overreach the matrix
    above is guarding against.
    """
    source = IngestionSource.objects.create(slug="authz-src", name="authz source")
    assert source.pk is not None

    login = anonymous_api.post(
        "/api/v1/login", {"user": "nobody@example.net", "password": "wrong"}, format="json"
    )
    # 400, never 401 — credential enumeration is the documented reason (AC-A6).
    assert login.status_code == 400, login.content

    logout = anonymous_api.get("/api/v1/logout")
    assert logout.status_code == 200, logout.content

    webhook = anonymous_api.post(
        f"/api/v1/alerts/webhook/{source.slug}",
        data=json.dumps({"title": "from the matrix", "sourceRef": "authz-1"}),
        content_type="application/json",
    )
    assert webhook.status_code == 201, webhook.content
    assert webhook.status_code < 500


# --- AC-B2: org boundaries on the direct lookups ---------------------------


@pytest.mark.parametrize(
    ("method", "template", "body"),
    [
        ("GET", "/api/v1/task/{task}", None),
        ("PATCH", "/api/v1/customEvent/{event}", {"title": "Rewrite"}),
    ],
    ids=["task-detail", "custom-event-patch"],
)
def test_a_foreign_org_gets_a_404_on_the_org_scoped_direct_lookups(
    method: str, template: str, body: dict[str, str] | None, db: None
) -> None:
    """Rows on an organisation-owned case are invisible outside that organisation (auditor F7).

    `owner_org` is seeded explicitly because nothing in the product sets it yet — with it
    NULL the scope clause (`case__owner_org__isnull=True`) makes the row visible to everyone
    and there is no boundary to test. The owning org's own key on the same row is the
    control: a lookup that 404s for *both* orgs would be a broken route, not an org guard.
    """
    owner_org = Organisation.objects.create(name="Boundary Owner")
    outsider_org = Organisation.objects.create(name="Boundary Outsider")
    case = _case("Boundary case", owner_org=owner_org)
    ids = {"task": str(_task(case).id), "event": str(_event(case).id)}
    path = template.format(**ids)

    outsider = User.objects.create(
        login="outsider",
        username="outsider",
        email="outsider@example.net",
        org=outsider_org,
        is_active=True,
    )
    outsider_client, _token = _api_key_client(outsider, scope="readwrite")
    response = _call(outsider_client, method, path, body)
    assert response.status_code == 404, (method, path, response.status_code, response.content)
    assert response.json()["type"] == "NotFoundError"
    if method == "PATCH":
        from cases.models import TimelineEvent

        assert TimelineEvent.objects.get(pk=ids["event"]).title == "Analyst note", (
            "the refused patch wrote anyway"
        )

    # The owning org's own key on the same row: a lookup that 404s for *both* orgs would be
    # a broken route, not an org guard.
    insider = User.objects.create(
        login="insider",
        username="insider",
        email="insider@example.net",
        org=owner_org,
        is_active=True,
    )
    insider_client, _token = _api_key_client(insider, scope="readwrite")
    control = _call(insider_client, method, path, body)
    assert control.status_code < 400, (method, path, control.status_code, control.content)
