"""T2 P5 surface: procedures, the TTP vocabulary and case export (AC6.1-P5-a/b).

* AC6.1-P5-a — `GET /api/v1/case/{id}/export` is a self-contained document that carries the linked
  alerts' `rawPayload` **verbatim** (the one deliberate exception to "raw_payload never inlined"),
  and a foreign organisation cannot export another org's case.
* AC6.1-P5-b — TTP is an editable vocabulary with the same in-use refusal as the P1 vocabularies:
  a technique referenced by any procedure cannot be deleted until the references are gone.

The endpoints are exercised through the API on purpose: this is a wire contract, so the tests speak
TheHive's field names (`patternId`, `occurDate`, `ttpId`) rather than the model's.
"""

from __future__ import annotations

from rest_framework.test import APIClient

from cases.models import TTP, Procedure
from identity.models import Organisation, User

from ._t1_seed import _alert, _api_key_client, _case

# --- AC6.1-P5-a: procedure attach on a case and an alert -------------------------------


def test_a_procedure_round_trips_on_a_case(api: APIClient, db: None) -> None:
    case = _case("Procedure case")
    created = api.post(
        f"/api/v1/case/{case.id}/procedure",
        {
            "occurDate": 1_700_000_000_000,
            "patternId": "T1059",
            "patternName": "Command and Scripting Interpreter",
            "tactic": "execution",
            "description": "ran a shell",
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    body = created.json()
    pid = body["_id"]
    assert body["patternId"] == "T1059"
    assert body["patternName"] == "Command and Scripting Interpreter"
    assert body["occurDate"] is not None
    assert Procedure.objects.get(pk=pid).case_id == case.id

    fetched = api.get(f"/api/v1/procedure/{pid}")
    assert fetched.status_code == 200, fetched.content

    patched = api.patch(f"/api/v1/procedure/{pid}", {"tactic": "defense-evasion"}, format="json")
    assert patched.status_code == 204, patched.content
    assert Procedure.objects.get(pk=pid).tactic == "defense-evasion"

    removed = api.delete(f"/api/v1/procedure/{pid}")
    assert removed.status_code == 204, removed.content
    assert not Procedure.objects.filter(pk=pid).exists()


def test_procedures_can_be_attached_in_bulk(api: APIClient, db: None) -> None:
    case = _case("Bulk procedure case")
    created = api.post(
        f"/api/v1/case/{case.id}/procedures",
        {"procedures": [{"patternId": "T1001"}, {"patternId": "T1002"}]},
        format="json",
    )
    assert created.status_code == 201, created.content
    assert [p["patternId"] for p in created.json()] == ["T1001", "T1002"]
    assert case.procedures.count() == 2


def test_a_procedure_attaches_to_an_alert(api: APIClient, db: None) -> None:
    alert = _alert("Alert with a technique", ref="proc-alert")
    created = api.post(
        f"/api/v1/alert/{alert.id}/procedure",
        {"patternId": "T1071", "patternName": "Application Layer Protocol"},
        format="json",
    )
    assert created.status_code == 201, created.content
    procedure = Procedure.objects.get(pk=created.json()["_id"])
    assert procedure.alert_id == alert.id
    assert procedure.case_id is None


def test_a_procedure_can_point_at_a_ttp(api: APIClient, db: None) -> None:
    case = _case("TTP case")
    ttp = TTP.objects.create(name="Command Shell", ttp_code="T1059")
    created = api.post(
        f"/api/v1/case/{case.id}/procedure",
        {"ttpId": str(ttp.id), "patternId": "T1059"},
        format="json",
    )
    assert created.status_code == 201, created.content
    assert Procedure.objects.get(pk=created.json()["_id"]).ttp_id == ttp.id


# --- AC6.1-P5-b: the TTP vocabulary refuses to delete an in-use technique -------------


def test_ttp_crud_and_the_in_use_guard(api: APIClient, db: None) -> None:
    created = api.post("/api/v1/ttp", {"name": "Phishing", "ttpCode": "T1566"}, format="json")
    assert created.status_code == 201, created.content
    ttp_id = created.json()["id"]

    listed = api.get("/api/v1/ttp")
    assert listed.status_code == 200, listed.content
    assert any(row["id"] == ttp_id for row in listed.json())

    patched = api.patch(f"/api/v1/ttp/{ttp_id}", {"tactic": "initial-access"}, format="json")
    assert patched.status_code == 204, patched.content
    assert api.get(f"/api/v1/ttp/{ttp_id}").json()["tactic"] == "initial-access"

    case = _case("In-use TTP case")
    procedure = api.post(
        f"/api/v1/case/{case.id}/procedure",
        {"ttpId": str(ttp_id), "patternId": "T1566"},
        format="json",
    )
    assert procedure.status_code == 201, procedure.content

    refused = api.delete(f"/api/v1/ttp/{ttp_id}")
    assert refused.status_code == 400, refused.content
    assert TTP.objects.filter(pk=ttp_id).exists(), "the in-use technique was deleted anyway"

    assert api.delete(f"/api/v1/procedure/{procedure.json()['_id']}").status_code == 204
    assert api.delete(f"/api/v1/ttp/{ttp_id}").status_code == 204
    assert not TTP.objects.filter(pk=ttp_id).exists()


# --- AC6.1-P5-a: the export document ---------------------------------------------------


def test_export_carries_the_linked_alert_raw_payload_verbatim(api: APIClient, db: None) -> None:
    case = _case("Export case")
    alert = _alert("Phishing report", ref="export-ref", case=case)
    raw = {"src_ip": "203.0.113.7", "user": "victim@example.net", "subject": "Invoice"}
    alert.raw_payload = raw
    alert.save(update_fields=["raw_payload"])

    response = api.get(f"/api/v1/case/{case.id}/export")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["_type"] == "CaseExport"
    assert body["version"] == 1
    assert body["case"]["_id"] == str(case.id)
    assert body["alerts"][0]["rawPayload"] == raw, "raw payload was not carried verbatim"
    assert body["procedures"] == []


def test_a_foreign_organisation_cannot_export_a_case(db: None) -> None:
    owner_org = Organisation.objects.create(name="Export Owner")
    outsider_org = Organisation.objects.create(name="Export Outsider")

    case = _case("Owned export case", owner_org=owner_org)

    outsider = User.objects.create(
        login="export-outsider",
        username="export-outsider",
        email="export-outsider@example.net",
        org=outsider_org,
        is_active=True,
    )
    outsider_client, _ = _api_key_client(outsider, scope="read")
    refused = outsider_client.get(f"/api/v1/case/{case.id}/export")
    assert refused.status_code == 404, refused.content

    insider = User.objects.create(
        login="export-insider",
        username="export-insider",
        email="export-insider@example.net",
        org=owner_org,
        is_active=True,
    )
    insider_client, _ = _api_key_client(insider, scope="read")
    allowed = insider_client.get(f"/api/v1/case/{case.id}/export")
    assert allowed.status_code == 200, allowed.content
