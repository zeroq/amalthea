"""T2 Phase P1 surface — identity, vocabularies, tags and `describe` (plan §5/§6-P1).

The minimal product-sized guard for the surface added by this wave. It is deliberately one file
and a dozen tests: the acceptance criteria below are the ones that fail *silently* if the routes
drift, while the exhaustive contract suite is a later wave's work.

=====  =====================================================================
AC-P1-1  `user/current`, `user/{idOrLogin}`, `organisation[es]` resolve and
         are scoped to the caller.
AC-P1-2  `observable/type` CRUD; `observable/type` is **not** swallowed by
         `observable/<id>` (the mount-order hazard).
AC-P1-3  `caseStatus` / `alertStatus` CRUD; an unknown stage is a 400.
AC-P1-4  `tag` CRUD plus the case/alert/observable link/unlink routes.
AC-P1-5  every in-use vocabulary row/tag refuses DELETE with a 400.
AC-P1-6  `describe/_all` and `describe/{model}` return the catalogue.
AC-P1-7  the whole surface is 401 without credentials.
=====  =====================================================================

Tests are grouped by acceptance criterion; every assertion is a status code or a row read back
from the database, so a passing stub cannot pass by returning a shape.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from alerts.models import AlertStatus
from cases.models import CaseStatus, CaseTagLink, Tag
from identity.models import AuditLog, Organisation, User
from observables.models import ObservableType

from ._t1_seed import _alert, _case, _observable


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.get_or_create(name="Test Org")[0]


@pytest.fixture
def analyst(org: Organisation) -> User:
    return User.objects.create_user(
        username="analyst", login="analyst", password="hunter2", email="a@example.net", org=org
    )


@pytest.fixture
def api(analyst: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=analyst)
    return client


@pytest.fixture
def superadmin(org: Organisation) -> User:
    return User.objects.create_user(
        username="root",
        login="root",
        password="hunter2",
        email="root@example.net",
        org=org,
        role="superAdmin",
    )


@pytest.fixture
def superadmin_api(superadmin: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=superadmin)
    return client


@pytest.fixture
def admin_api(org: Organisation) -> APIClient:
    admin = User.objects.create_user(
        username="admin-user",
        login="admin-user",
        password="hunter2",
        email="admin@example.net",
        org=org,
        role="admin",
    )
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


@pytest.fixture
def anonymous_api() -> APIClient:
    return APIClient()


# --- AC-P1-1 — identity ----------------------------------------------------


def test_user_current_is_the_caller(api: APIClient, analyst: User) -> None:
    response = api.get("/api/v1/user/current")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["_id"] == str(analyst.id)
    assert body["_type"] == "user"
    assert body["login"] == "analyst"


def test_user_detail_accepts_login_and_unknown_is_404(api: APIClient, analyst: User) -> None:
    by_login = api.get(f"/api/v1/user/{analyst.login}")
    assert by_login.status_code == 200, by_login.content
    assert by_login.json()["_id"] == str(analyst.id)

    assert api.get("/api/v1/user/nobody-at-all").status_code == 404
    assert api.get("/api/v1/user/00000000-0000-4000-8000-0000000000ff").status_code == 404


def test_organisation_routes_are_scoped_to_the_caller(api: APIClient, analyst: User) -> None:
    listed = api.get("/api/v1/organisation")
    assert listed.status_code == 200, listed.content
    bodies = listed.json()
    assert all(entry["_id"] == str(analyst.org_id) for entry in bodies), "a foreign tenant leaked"
    assert any(entry["name"] == "Test Org" for entry in bodies)

    detail = api.get(f"/api/v1/organisation/{analyst.org_id}")
    assert detail.status_code == 200, detail.content
    assert detail.json()["_type"] == "Organisation"

    foreign = Organisation.objects.create(name="Other Org")
    assert api.get(f"/api/v1/organisation/{foreign.id}").status_code == 404

    refused = api.patch(
        f"/api/v1/organisation/{foreign.id}", {"description": "hacked"}, format="json"
    )
    assert refused.status_code == 404, refused.content
    foreign.refresh_from_db()
    assert foreign.description == "", "a refused foreign PATCH wrote to the row"


def test_organisation_patch_updates_the_callers_own_org(api: APIClient, analyst: User) -> None:
    response = api.patch(
        f"/api/v1/organisation/{analyst.org.name}",
        {"description": "blue team"},
        format="json",
    )
    assert response.status_code == 204, response.content
    assert response.content == b""
    analyst.org.refresh_from_db()
    assert analyst.org.description == "blue team"


# --- AC-P1-2 / AC-P1-5 — observable types ----------------------------------


def test_observable_type_crud_and_not_shadowed_by_observable_detail(
    superadmin_api: APIClient, api: APIClient, db: None
) -> None:
    """`observable/type` must resolve to the type collection, not to `observable/<id>`.

    `cases/urls.py` registers `observable/<str:observable_id>`; this module is mounted first in
    `amalthea/urls.py`. If that order were reversed the POST below would be a 404 (or a 405), so
    the create succeeding is the proof the mount order holds.
    """
    created = superadmin_api.post(
        "/api/v1/observable/type",
        {"name": "sha256", "isCaseSensitive": False},
        format="json",
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["_type"] == "ObservableType"
    assert body["name"] == "sha256"
    assert body["isCaseSensitive"] is False

    # Reads stay open to an analyst.
    listed = api.get("/api/v1/observable/type")
    assert listed.status_code == 200, listed.content
    assert any(entry["name"] == "sha256" for entry in listed.json())

    patched = superadmin_api.patch(
        "/api/v1/observable/type/sha256", {"isCaseSensitive": True}, format="json"
    )
    assert patched.status_code == 204, patched.content
    assert ObservableType.objects.get(name="sha256").is_case_sensitive is True

    renamed = superadmin_api.patch(
        "/api/v1/observable/type/sha256", {"name": "sha-256"}, format="json"
    )
    assert renamed.status_code == 204, renamed.content
    assert ObservableType.objects.filter(name="sha-256").exists()
    assert superadmin_api.get("/api/v1/observable/type/sha256").status_code == 404

    deleted = superadmin_api.delete("/api/v1/observable/type/sha-256")
    assert deleted.status_code == 204, deleted.content
    assert not ObservableType.objects.filter(name="sha-256").exists()


def test_observable_type_in_use_is_a_400_not_a_cascade(
    superadmin_api: APIClient, org: Organisation
) -> None:
    _observable("10.20.30.40", org=org)  # creates type "ip" with one observable behind it
    response = superadmin_api.delete("/api/v1/observable/type/ip")
    assert response.status_code == 400, response.content
    assert response.json()["type"] == "BadRequest"
    assert ObservableType.objects.filter(name="ip").exists()


# --- AC-P1-3 — status vocabularies -----------------------------------------


def test_case_status_requires_a_known_stage_and_is_immutable(
    superadmin_api: APIClient, superadmin: User, db: None
) -> None:
    bad = superadmin_api.post(
        "/api/v1/caseStatus", {"value": "Weird", "stage": "Bogus"}, format="json"
    )
    assert bad.status_code == 400, bad.content
    assert bad.json()["type"] == "BadRequest"
    assert not CaseStatus.objects.filter(value="Weird").exists()

    ok = superadmin_api.post(
        "/api/v1/caseStatus",
        {"value": "Containment", "stage": "InProgress", "order": 3},
        format="json",
    )
    assert ok.status_code == 201, ok.content
    status_id = ok.json()["_id"]
    assert ok.json()["_type"] == "CaseStatus"
    assert ok.json()["stage"] == "InProgress"

    # `value`/`stage` are immutable, so a body carrying only `stage` has nothing to write.
    assert (
        superadmin_api.patch(
            "/api/v1/caseStatus/Containment", {"stage": "Closed"}, format="json"
        ).status_code
        == 400
    )
    assert (
        superadmin_api.patch(
            "/api/v1/caseStatus/Containment", {"order": 9}, format="json"
        ).status_code
        == 204
    )
    assert CaseStatus.objects.get(value="Containment").order == 9

    renamed = superadmin_api.patch(
        f"/api/v1/caseStatus/{status_id}", {"value": "Quarantined"}, format="json"
    )
    assert renamed.status_code == 204, renamed.content
    assert CaseStatus.objects.filter(value="Quarantined").exists()
    assert superadmin_api.get("/api/v1/caseStatus/Containment").status_code == 404
    assert superadmin_api.get(f"/api/v1/caseStatus/{status_id}").status_code == 200

    audit = AuditLog.objects.get(
        action="taxonomy.case_status.renamed", target_id=str(status_id), actor=superadmin
    )
    assert audit.details["old"] == {"value": "Containment"}
    assert audit.details["new"] == {"value": "Quarantined"}


def test_alert_status_lists_and_refuses_an_unknown_stage(
    superadmin_api: APIClient, db: None
) -> None:
    AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    listed = superadmin_api.get("/api/v1/alertStatus")
    assert listed.status_code == 200, listed.content
    assert any(entry["value"] == "New" for entry in listed.json())

    bad = superadmin_api.post("/api/v1/alertStatus", {"value": "X", "stage": "Nope"}, format="json")
    assert bad.status_code == 400, bad.content
    assert not AlertStatus.objects.filter(value="X").exists()


@pytest.mark.parametrize("client_fixture", ["admin_api", "api"])
def test_taxonomy_writes_require_superadmin(
    client_fixture: str, request: pytest.FixtureRequest
) -> None:
    client = request.getfixturevalue(client_fixture)
    case_status = CaseStatus.objects.create(value="Triage", stage="New", order=1, description="")
    alert_status = AlertStatus.objects.create(value="Triage", stage="New", order=1, description="")
    otype = ObservableType.objects.create(name="url-perm-test", is_attachment=False)

    assert (
        client.post(
            "/api/v1/caseStatus",
            {"value": "Sneaky", "stage": "New"},
            format="json",
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/caseStatus/{case_status.id}",
            {"value": "Renamed"},
            format="json",
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/caseStatus/{case_status.id}").status_code == 404

    assert (
        client.post(
            "/api/v1/alertStatus",
            {"value": "Sneaky", "stage": "New"},
            format="json",
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/alertStatus/{alert_status.id}",
            {"value": "Renamed"},
            format="json",
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/alertStatus/{alert_status.id}").status_code == 404

    assert (
        client.post(
            "/api/v1/observable/type",
            {"name": "sneaky"},
            format="json",
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/observable/type/{otype.id}",
            {"name": "renamed"},
            format="json",
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/observable/type/{otype.id}").status_code == 404

    case_status.refresh_from_db()
    alert_status.refresh_from_db()
    otype.refresh_from_db()
    assert case_status.value == "Triage"
    assert alert_status.value == "Triage"
    assert otype.name == "url-perm-test"
    assert not CaseStatus.objects.filter(value="Sneaky").exists()
    assert not AlertStatus.objects.filter(value="Sneaky").exists()
    assert not ObservableType.objects.filter(name="sneaky").exists()


# --- AC-P1-4 / AC-P1-5 — tags and links ------------------------------------


def test_tag_crud_and_case_alert_observable_links(api: APIClient, org: Organisation) -> None:
    created = api.post("/api/v1/tag", {"name": "ransomware", "colour": "#e8560a"}, format="json")
    assert created.status_code == 201, created.content
    tag = created.json()
    assert tag["_type"] == "Tag"
    assert tag["namespace"] == "_freetags_"
    assert tag["predicate"] == "ransomware"
    assert tag["colour"] == "#e8560a"

    case = _case("Tagged case", org=org)
    linked = api.post(
        f"/api/v1/case/{case.id}/tag", {"tags": ["ransomware", "phishing"]}, format="json"
    )
    assert linked.status_code == 200, linked.content
    assert {entry["predicate"] for entry in linked.json()} == {"ransomware", "phishing"}
    assert CaseTagLink.objects.filter(case=case).count() == 2

    observable = _observable("203.0.113.55", org=org)
    obs_link = api.post(
        f"/api/v1/observable/{observable.id}/tag", {"tags": ["phishing"]}, format="json"
    )
    assert obs_link.status_code == 200, obs_link.content
    assert {entry["predicate"] for entry in obs_link.json()} == {"phishing"}

    alert = _alert("Tagged alert", ref="tag-link-ref", org=org)
    alert_link = api.post(f"/api/v1/alert/{alert.id}/tag", {"tags": ["phishing"]}, format="json")
    assert alert_link.status_code == 200, alert_link.content
    assert {entry["predicate"] for entry in alert_link.json()} == {"phishing"}

    # In-use tag: 400, and the row stands.
    guard = api.delete(f"/api/v1/tag/{tag['_id']}")
    assert guard.status_code == 400, guard.content
    assert Tag.objects.filter(name="ransomware").exists()

    # Un-link "ransomware" from the case; the other links are untouched.
    unlinked = api.delete(f"/api/v1/case/{case.id}/tag", {"tags": ["ransomware"]}, format="json")
    assert unlinked.status_code == 200, unlinked.content
    assert {entry["predicate"] for entry in unlinked.json()} == {"phishing"}
    assert not CaseTagLink.objects.filter(case=case, tag__name="ransomware").exists()
    assert Tag.objects.filter(name="ransomware").exists(), "un-link must not delete the tag"


def test_tag_link_requires_a_tags_body(api: APIClient, org: Organisation) -> None:
    case = _case("Tagless case", org=org)
    response = api.post(f"/api/v1/case/{case.id}/tag", {}, format="json")
    assert response.status_code == 400, response.content
    assert response.json()["fields"] == {"tags": ["required"]}


# --- AC-P1-6 — describe ----------------------------------------------------


def test_describe_catalogue_covers_the_new_entities(api: APIClient) -> None:
    everything = api.get("/api/v1/describe/_all")
    assert everything.status_code == 200, everything.content
    catalogue = everything.json()
    assert {
        "case",
        "alert",
        "task",
        "observable",
        "user",
        "organisation",
        "tag",
        "caseStatus",
        "alertStatus",
        "observableType",
    } <= set(catalogue)

    one = api.get("/api/v1/describe/case")
    assert one.status_code == 200, one.content
    description = one.json()
    assert description["label"] == "case"
    assert description["initialQuery"] == "listCase"
    assert any(attribute["name"] == "severity" for attribute in description["attributes"])

    assert api.get("/api/v1/describe/not-a-model").status_code == 404


# --- AC-P1-7 — auth --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/user/current",
        "/api/v1/organisation",
        "/api/v1/observable/type",
        "/api/v1/caseStatus",
        "/api/v1/alertStatus",
        "/api/v1/tag",
        "/api/v1/describe/_all",
    ],
)
def test_the_new_surface_requires_authentication(path: str, anonymous_api: APIClient) -> None:
    response = anonymous_api.get(path)
    assert response.status_code == 401, (path, response.status_code, response.content)


def test_slashless_and_slashed_body_routes_both_resolve(api: APIClient, org: Organisation) -> None:
    """`APPEND_SLASH` would 301 a POST and drop its body, so both spellings must be explicit."""
    slashless = api.post("/api/v1/tag", {"name": "one"}, format="json")
    slashed = api.post("/api/v1/tag/", {"name": "two"}, format="json")
    assert slashless.status_code == 201, slashless.content
    assert slashed.status_code == 201, slashed.content
