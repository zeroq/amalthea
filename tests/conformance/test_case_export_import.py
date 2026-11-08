"""Conformance tests for TODO §18 — case JSON export v2 + import (IMP-1..7)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert, AlertObservable, AlertStatus
from automation.models import AutomationRun, Playbook
from cases.importing import CaseImportError, import_case_document, parse_case_export
from cases.ledger import append_timeline_event
from cases.models import (
    Attachment,
    Case,
    CaseCustomFieldValue,
    CaseObservable,
    CaseStatus,
    CaseTagLink,
    Comment,
    CustomField,
    Page,
    Procedure,
    Share,
    Tag,
    Task,
    TaskNote,
)
from cases.views import _case_export_document
from identity.models import Organisation, User
from observables.extractor import resolve_observable
from observables.models import ObservableType

pytestmark = pytest.mark.django_db


def _admin(org: Organisation) -> User:
    return User.objects.create(
        login="admin", username="admin", email="admin@example.net", org=org, role="admin"
    )


def _status(value: str) -> CaseStatus:
    stage = "Closed" if value == "Closed" else ("New" if value == "New" else "InProgress")
    status, _ = CaseStatus.objects.get_or_create(value=value, defaults={"stage": stage, "order": 1})
    return status


def _alert(org: Organisation, **overrides) -> Alert:
    defaults = {
        "title": "Phish",
        "type": "phishing",
        "source": "mail",
        "source_ref": "case-import-ref",
        "status": AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0],
        "owner_org": org,
        "raw_payload": {"src_ip": "1.2.3.4"},
        "summary": "Clicked",
        "severity": 3,
        "tlp": 2,
        "pap": 2,
    }
    defaults.update(overrides)
    return Alert.objects.create(**defaults)


def _full_case(org: Organisation, analyst: User) -> Case:
    now = timezone.now()
    case = Case.objects.create(
        title="Round-trip case",
        description="Full description that must not be truncated",
        summary="Short summary",
        severity=3,
        tlp=2,
        pap=2,
        flag=True,
        status=_status("InProgress"),
        assignee=analyst,
        scope="single_asset_user",
        case_type="phishing",
        data_classification="sensitive_data",
        phase="containment",
        verdict="true_positive",
        start_date=now - timedelta(days=2),
        end_date=now - timedelta(days=1),
        owner_org=org,
        ingestion_warnings={"source": "manual"},
    )

    tag = Tag.objects.create(name="phishing")
    CaseTagLink.objects.create(case=case, tag=tag)
    field = CustomField.objects.create(name="Impact", type="string")
    CaseCustomFieldValue.objects.create(case=case, custom_field=field, value={"level": "high"})

    ObservableType.objects.get_or_create(name="ip")
    observable, _ = resolve_observable("ip", "1.2.3.4", owner_org=org)
    CaseObservable.objects.create(case=case, observable=observable, added_by=analyst)

    task = Task.objects.create(
        case=case,
        title="Contain host",
        description="Isolate the endpoint",
        group="response",
        status="InProgress",
        flag=True,
        assignee=analyst,
        order=1,
        due_date=now + timedelta(days=1),
        mandatory=True,
    )
    note = TaskNote.objects.create(task=task, message="Started isolation", created_by=analyst)
    append_timeline_event(
        case,
        title="Task note",
        description="Started isolation",
        kind="task-note",
        actor=analyst,
        metadata={"task_id": str(task.id), "note_id": str(note.id)},
    )

    Comment.objects.create(case=case, message="Analyst comment", created_by=analyst)
    Page.objects.create(case=case, title="Runbook", content="# Steps", order=1, category="ir")
    Procedure.objects.create(
        case=case,
        occur_date=now,
        pattern_id="T1566",
        pattern_name="Phishing",
        tactic="initial-access",
    )

    other = Organisation.objects.create(name="Shared With")
    Share.objects.create(case=case, organisation=other, permissions={"read": True})
    Attachment.objects.create(
        case=case,
        name="evidence.txt",
        content_type="text/plain",
        size=3,
        sha256="a" * 64,
        path="opaque/path",
    )

    alert = _alert(org, case=case, payload_ssdeep="internal-ssdeep")
    AlertObservable.objects.create(alert=alert, observable=observable, added_by=analyst)
    from alerts.models import AlertCustomFieldValue, AlertTagLink

    AlertTagLink.objects.create(alert=alert, tag=tag)
    AlertCustomFieldValue.objects.create(alert=alert, custom_field=field, value={"level": "high"})
    Comment.objects.create(alert=alert, message="Alert comment", created_by=analyst)
    Procedure.objects.create(alert=alert, pattern_id="T1566", tactic="initial-access")

    playbook = Playbook.objects.create(
        owner_org=org, name="Enrich IP", trigger_event="observable.created"
    )
    AutomationRun.objects.create(
        case=case,
        playbook=playbook,
        playbook_name="Enrich IP",
        status="Success",
        output_log="done",
        idempotency_key="run-1",
        started_at=now,
        finished_at=now,
    )
    return case


def _upload(document: dict | str, name: str = "case.json") -> SimpleUploadedFile:
    body = document if isinstance(document, str) else json.dumps(document)
    return SimpleUploadedFile(name, body.encode(), content_type="application/json")


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_export_v2_has_all_children_and_no_secrets(analyst: User) -> None:
    case = _full_case(analyst.org, analyst)
    doc = _case_export_document(case)

    assert doc["_type"] == "CaseExport"
    assert doc["version"] == 2
    assert doc["case"]["title"] == "Round-trip case"
    assert doc["case"]["description"] == "Full description that must not be truncated"
    assert doc["case"]["ownerOrg"] == analyst.org.name
    assert doc["case"]["tags"] == [{"name": "phishing"}]
    assert doc["case"]["customFields"] == [{"name": "Impact", "value": {"level": "high"}}]
    assert doc["observables"][0]["data"] == "1.2.3.4"
    assert doc["tasks"][0]["title"] == "Contain host"
    assert doc["tasks"][0]["notes"][0]["message"] == "Started isolation"
    assert len(doc["timeline"]) == 1
    assert doc["comments"][0]["message"] == "Analyst comment"
    assert doc["pages"][0]["title"] == "Runbook"
    assert doc["procedures"][0]["patternId"] == "T1566"
    assert doc["shares"][0]["organisation"] == "Shared With"
    assert doc["attachments"][0]["sha256"] == "a" * 64
    assert doc["alerts"][0]["_type"] == "AlertExport"
    assert doc["alerts"][0]["observables"][0]["data"] == "1.2.3.4"
    assert doc["automationRuns"][0]["playbookName"] == "Enrich IP"
    assert "internal-ssdeep" not in json.dumps(doc)
    assert "payload_ssdeep" not in json.dumps(doc)


def test_round_trip_preserves_children_and_provenance(analyst: User) -> None:
    target_org = Organisation.objects.create(name="Target Org")
    admin = _admin(target_org)
    source = _full_case(analyst.org, analyst)
    imported = import_case_document(
        _case_export_document(source),
        actor=admin,
        source_filename="cases/../../case.json",
    )

    assert imported.id != source.id
    assert imported.number != source.number
    assert imported.owner_org == target_org
    assert imported.title == "Round-trip case"
    assert imported.description == "Full description that must not be truncated"
    assert imported.assignee is None
    assert any(
        "not in this organisation" in w for w in imported.ingestion_warnings["imported"]["warnings"]
    )
    assert imported.status.value == "InProgress"
    assert imported.data_classification == "sensitive_data"
    assert list(imported.tags.values_list("name", flat=True)) == ["phishing"]
    assert imported.custom_field_values.get().value == {"level": "high"}
    assert imported.case_observables.get().observable.data == "1.2.3.4"
    assert imported.tasks.get().notes.get().message == "Started isolation"
    assert imported.comments.get().message == "Analyst comment"
    assert imported.pages.get().title == "Runbook"
    assert imported.procedures.get().pattern_id == "T1566"
    assert imported.shares.count() == 0
    assert imported.attachments.count() == 0
    assert AutomationRun.objects.filter(case=imported).count() == 0

    alert = imported.alerts.get()
    assert alert.case_id == imported.id
    assert alert.title == "Phish"
    assert alert.alert_observables.get().observable.data == "1.2.3.4"
    assert alert.payload_ssdeep != "internal-ssdeep"

    task_notes = imported.timeline_events.filter(kind="task-note")
    assert task_notes.count() == 1
    assert imported.timeline_events.filter(title="Case imported from JSON").exists()

    provenance = imported.ingestion_warnings["imported"]
    assert provenance["by"] == "admin"
    assert provenance["sourceFile"] == "case.json"
    assert provenance["originalNumber"] == source.number
    assert provenance["originalId"] == str(source.id)
    assert any("attachments: skipped" in w for w in provenance["warnings"])
    assert any("shares: skipped" in w for w in provenance["warnings"])
    assert any("automationRuns: skipped" in w for w in provenance["warnings"])


def test_unknown_vocabulary_tokens_fall_back_with_warnings(analyst: User) -> None:
    doc = {
        "_type": "CaseExport",
        "version": 2,
        "case": {
            "title": "Bad vocabulary tokens",
            "scope": "galaxy",
            "caseType": "phish",
            "dataClassification": "internal",
            "phase": "remediate",
            "verdict": "positive",
        },
    }

    case = import_case_document(doc, actor=analyst, source_filename="case.json")

    assert case.scope == "unset"
    assert case.case_type == "unset"
    assert case.data_classification == "unset"
    assert case.phase == "initial_triage"
    assert case.verdict == "undefined"
    warnings = case.ingestion_warnings["imported"]["warnings"]
    for field in ("scope", "caseType", "dataClassification", "phase", "verdict"):
        assert any(field in warning and "unknown" in warning for warning in warnings)


def test_closed_case_gets_verdict_and_closing_entry(analyst: User) -> None:
    status = _status("Closed")
    doc = {
        "_type": "CaseExport",
        "version": 2,
        "case": {"title": "Closed case", "status": "Closed", "verdict": ""},
        "timeline": [],
    }
    case = import_case_document(doc, actor=analyst, source_filename="closed.json")

    assert case.status == status
    assert case.verdict == "undefined"
    assert case.closed_date is not None
    assert case.timeline_events.filter(kind="case-closed").count() == 1


def test_v1_document_is_accepted_with_a_warning(analyst: User) -> None:
    doc = {"_type": "CaseExport", "version": 1, "case": {"title": "Legacy"}}
    case = import_case_document(doc, actor=analyst, source_filename="legacy.json")

    assert case.title == "Legacy"
    assert any("v1" in warning for warning in case.ingestion_warnings["imported"]["warnings"])


def test_cross_org_assignee_is_not_assigned(analyst: User) -> None:
    other = Organisation.objects.create(name="Other Org")
    outsider = User.objects.create(
        login="outsider", username="outsider", email="o@example.net", org=other
    )
    doc = {
        "_type": "CaseExport",
        "version": 2,
        "case": {"title": "Cross org", "assignee": outsider.login, "ownerOrg": other.name},
    }
    case = import_case_document(doc, actor=analyst, source_filename="cross.json")

    assert case.owner_org == analyst.org
    assert case.assignee is None
    assert any(
        "not in this organisation" in w for w in case.ingestion_warnings["imported"]["warnings"]
    )


def test_duplicate_embedded_alert_rolls_back_the_whole_case(analyst: User) -> None:
    _alert(analyst.org)
    other = Organisation.objects.create(name="Source Org")
    outsider = User.objects.create(
        login="outsider", username="outsider", email="o@example.net", org=other
    )
    doc = _case_export_document(_full_case(other, outsider))
    before = Case.objects.count()

    with pytest.raises(CaseImportError) as excinfo:
        import_case_document(doc, actor=analyst, source_filename="dupe.json")

    assert excinfo.value.code == "alert_duplicate"
    assert Case.objects.count() == before


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d.update(_type="Nope"), "wrong_type"),
        (lambda d: d.update(version=99), "unsupported_version"),
        (lambda d: d.update(version=True), "unsupported_version"),
        (lambda d: d["case"].update(title=""), "missing_title"),
        (lambda d: d.update(case=[]), "missing_title"),
        (lambda d: d.update(extra={}), None),
    ],
)
def test_parse_rejects_bad_documents(mutate, code) -> None:
    doc = {"_type": "CaseExport", "version": 2, "case": {"title": "ok"}}
    mutate(doc)
    raw = json.dumps(doc).encode()
    if code is None:
        assert parse_case_export(raw)["case"]["title"] == "ok"
    else:
        with pytest.raises(CaseImportError) as excinfo:
            parse_case_export(raw)
        assert excinfo.value.code == code


def test_parse_rejects_nan_and_oversize(analyst: User) -> None:
    with pytest.raises(CaseImportError) as excinfo:
        parse_case_export(b'{"_type":"CaseExport","version":2,"case":{"title":"x"},"n":NaN}')
    assert excinfo.value.code == "malformed_json"

    with pytest.raises(CaseImportError) as excinfo:
        parse_case_export(b"{" + b" " * (5 * 1024 * 1024) + b"}")
    assert excinfo.value.code == "oversized_file"


def test_import_view_is_admin_only_and_redirects(analyst: User) -> None:
    admin = _admin(analyst.org)
    assert _login(admin).get(reverse("ui-case-import")).status_code == 200
    assert _login(analyst).get(reverse("ui-case-import")).status_code == 404

    doc = {
        "_type": "CaseExport",
        "version": 2,
        "case": {"title": "UI imported case", "status": "InProgress"},
    }
    before = set(Case.objects.values_list("id", flat=True))
    response = _login(admin).post(reverse("ui-case-import"), {"file": _upload(doc, "my-case.json")})
    assert response.status_code == 302
    imported = Case.objects.exclude(id__in=before).get()
    assert response["Location"] == reverse("ui-case-detail", args=[str(imported.id)])


def test_import_view_rerenders_errors(analyst: User) -> None:
    admin = _admin(analyst.org)
    response = _login(admin).post(
        reverse("ui-case-import"),
        {"file": _upload({" _type": "wrong"}, "bad.json")},
    )
    assert response.status_code == 400
    assert b"CaseExport" in response.content

    _alert(analyst.org)
    other = Organisation.objects.create(name="Source Org")
    outsider = User.objects.create(
        login="outsider", username="outsider", email="o@example.net", org=other
    )
    duplicate = _case_export_document(_full_case(other, outsider))
    response = _login(admin).post(
        reverse("ui-case-import"), {"file": _upload(duplicate, "dupe.json")}
    )
    assert response.status_code == 409


def test_new_case_modal_has_import_link_for_admins_only(analyst: User) -> None:
    admin = _admin(analyst.org)
    body = _login(admin).get(reverse("ui-case-list")).content.decode()
    assert "Import from JSON" in body
    assert reverse("ui-case-import") in body

    analyst_body = _login(analyst).get(reverse("ui-case-list")).content.decode()
    assert "Import from JSON" not in analyst_body
