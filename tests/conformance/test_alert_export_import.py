"""Conformance tests for TODO §19 — alert JSON export/import (AIMP-1..5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from alerts.export import alert_export_document
from alerts.models import (
    Alert,
    AlertCustomFieldValue,
    AlertObservable,
    AlertStatus,
    AlertTagLink,
)
from cases.models import Comment, CustomField, Procedure, Tag
from identity.models import Organisation, User
from observables.extractor import resolve_observable
from observables.models import ObservableType

pytestmark = pytest.mark.django_db


def _admin(org: Organisation) -> User:
    return User.objects.create(
        login="admin", username="admin", email="admin@example.net", org=org, role="admin"
    )


def _status(value: str = "New") -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(
        value=value, defaults={"stage": value, "order": 1}
    )
    return status


def _alert(org: Organisation, **overrides) -> Alert:
    defaults = {
        "title": "Suspicious login",
        "type": "phishing",
        "source": "siem",
        "source_ref": "ref-1",
        "status": _status(),
        "owner_org": org,
        "raw_payload": {"src_ip": "1.2.3.4"},
        "summary": "A user clicked a link",
        "severity": 3,
        "tlp": 2,
        "pap": 2,
    }
    defaults.update(overrides)
    return Alert.objects.create(**defaults)


def _upload(document: dict | str) -> SimpleUploadedFile:
    body = document if isinstance(document, str) else json.dumps(document)
    return SimpleUploadedFile("alert.json", body.encode(), content_type="application/json")


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_export_document_has_children_and_no_internal_hash(analyst: User) -> None:
    org = analyst.org
    alert = _alert(org, payload_ssdeep="internal-hash")
    ObservableType.objects.get_or_create(name="ip")
    observable, _ = resolve_observable("ip", "1.2.3.4", owner_org=org)
    AlertObservable.objects.create(alert=alert, observable=observable)
    tag = Tag.objects.create(name="phish")
    AlertTagLink.objects.create(alert=alert, tag=tag)
    field = CustomField.objects.create(name="Impact", type="string")
    AlertCustomFieldValue.objects.create(alert=alert, custom_field=field, value={"level": "high"})
    Comment.objects.create(alert=alert, message="first", created_by=analyst)
    Procedure.objects.create(alert=alert, pattern_id="T1566", tactic="initial-access")

    doc = alert_export_document(alert)

    assert doc["_type"] == "AlertExport"
    assert doc["version"] == 1
    assert doc["alert"]["rawPayload"] == {"src_ip": "1.2.3.4"}
    assert doc["alert"]["sourceRef"] == "ref-1"
    assert "payload_ssdeep" not in json.dumps(doc)
    assert "internal-hash" not in json.dumps(doc)
    assert doc["observables"][0]["data"] == "1.2.3.4"
    assert doc["tags"] == [{"name": "phish"}]
    assert doc["customFields"] == [{"name": "Impact", "value": {"level": "high"}}]
    assert doc["comments"][0]["message"] == "first"
    assert doc["procedures"][0]["patternId"] == "T1566"


def test_export_view_is_an_attachment(analyst: User) -> None:
    alert = _alert(analyst.org)
    response = _login(analyst).get(reverse("ui-alert-export", args=[alert.id]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response["Content-Disposition"].startswith('attachment; filename="alert-')


def test_export_is_org_scoped(analyst: User) -> None:
    other = Organisation.objects.create(name="Other")
    alert = _alert(other)
    response = _login(analyst).get(reverse("ui-alert-export", args=[alert.id]))
    assert response.status_code == 404


def test_round_trip_imports_children_and_provenance(analyst: User) -> None:
    org = analyst.org
    admin = _admin(org)
    alert = _alert(org, payload_ssdeep="must-not-survive")
    ObservableType.objects.get_or_create(name="ip")
    observable, _ = resolve_observable("ip", "1.2.3.4", owner_org=org)
    AlertObservable.objects.create(alert=alert, observable=observable)
    tag = Tag.objects.create(name="phish")
    AlertTagLink.objects.create(alert=alert, tag=tag)
    field = CustomField.objects.create(name="Impact", type="string")
    AlertCustomFieldValue.objects.create(alert=alert, custom_field=field, value={"level": "high"})
    comment = Comment.objects.create(alert=alert, message="first", created_by=analyst)
    Procedure.objects.create(alert=alert, pattern_id="T1566", tactic="initial-access")

    doc = alert_export_document(alert)
    doc["alert"]["sourceRef"] = "ref-2"
    doc["alert"]["caseNumber"] = 999  # informational only: must not link

    response = _login(admin).post(reverse("ui-alert-import"), {"file": _upload(doc)})

    assert response.status_code == 302
    imported = Alert.objects.get(source_ref="ref-2")
    assert imported.owner_org_id == org.id
    assert imported.case_id is None
    assert imported.status.value == "New"
    assert imported.severity == 3
    assert imported.raw_payload == {"src_ip": "1.2.3.4"}
    assert imported.payload_ssdeep != "must-not-survive"
    assert imported.alert_observables.count() == 1
    assert list(imported.tag_links.values_list("tag__name", flat=True)) == ["phish"]
    assert imported.custom_field_values.count() == 1
    assert imported.comments.count() == 1
    assert imported.comments.first().created_by_id == admin.id  # type: ignore[union-attr]
    assert imported.procedures.count() == 1
    provenance = imported.ingestion_warnings["imported"]
    assert provenance["by"] == admin.login
    assert provenance["sourceFile"] == "alert.json"
    assert provenance["originalId"] == str(alert.id)
    assert str(comment.message) == "first"


def test_duplicate_is_atomic_and_rejected(analyst: User) -> None:
    org = analyst.org
    admin = _admin(org)
    alert = _alert(org)
    before = Alert.objects.count()

    response = _login(admin).post(
        reverse("ui-alert-import"), {"file": _upload(alert_export_document(alert))}
    )

    assert response.status_code == 409
    assert Alert.objects.count() == before
    assert b"already exists" in response.content


def test_malformed_and_wrong_type_rejected(analyst: User) -> None:
    client = _login(_admin(analyst.org))
    assert (
        client.post(reverse("ui-alert-import"), {"file": _upload("{not json")}).status_code == 400
    )

    wrong = {"_type": "CaseExport", "version": 1, "alert": {}}
    assert client.post(reverse("ui-alert-import"), {"file": _upload(wrong)}).status_code == 400

    bad_version = {"_type": "AlertExport", "version": 2, "alert": {}}
    assert (
        client.post(reverse("ui-alert-import"), {"file": _upload(bad_version)}).status_code == 400
    )


def test_nan_is_rejected(analyst: User) -> None:
    client = _login(_admin(analyst.org))
    body = '{"_type":"AlertExport","version":1,"alert":{"title":"x","type":"y","source":"z","sourceRef":"r","severity":NaN}}'
    response = client.post(reverse("ui-alert-import"), {"file": _upload(body)})
    assert response.status_code == 400


def test_oversized_upload_rejected(analyst: User) -> None:
    client = _login(_admin(analyst.org))
    huge = SimpleUploadedFile("alert.json", b"{" + b" " * (5 * 1024 * 1024) + b"}")
    response = client.post(reverse("ui-alert-import"), {"file": huge})
    assert response.status_code == 400


def test_non_admin_is_denied(analyst: User) -> None:
    assert _login(analyst).get(reverse("ui-alert-import")).status_code == 404
    assert _login(analyst).post(reverse("ui-alert-import"), {}).status_code == 404


def test_cross_org_assignee_is_not_resolved(analyst: User) -> None:
    org = analyst.org
    admin = _admin(org)
    other = Organisation.objects.create(name="Other")
    outsider = User.objects.create(
        login="outsider", username="outsider", email="o@example.net", org=other
    )
    alert = _alert(org)
    doc = alert_export_document(alert)
    doc["alert"]["assignee"] = outsider.login
    doc["alert"]["sourceRef"] = "ref-cross-org"

    assert _login(admin).post(reverse("ui-alert-import"), {"file": _upload(doc)}).status_code == 302
    imported = Alert.objects.get(source_ref="ref-cross-org")
    assert imported.assignee_id is None
    assert any(
        "not in this organisation" in w for w in imported.ingestion_warnings["imported"]["warnings"]
    )


def test_unknown_custom_field_and_observable_type_are_skipped(analyst: User) -> None:
    org = analyst.org
    admin = _admin(org)
    doc = alert_export_document(_alert(org))
    doc["alert"]["sourceRef"] = "ref-unknown"
    doc["customFields"] = [{"name": "NoSuchField", "value": 1}]
    doc["observables"] = [{"dataType": "no-such-type", "data": "x"}]

    assert _login(admin).post(reverse("ui-alert-import"), {"file": _upload(doc)}).status_code == 302
    imported = Alert.objects.get(source_ref="ref-unknown")
    assert imported.custom_field_values.count() == 0
    assert imported.alert_observables.count() == 0
    warnings = imported.ingestion_warnings["imported"]["warnings"]
    assert any("unknown field" in w for w in warnings)
    assert any("unknown type" in w for w in warnings)


def test_import_page_get(analyst: User) -> None:
    response = _login(_admin(analyst.org)).get(reverse("ui-alert-import"))
    assert response.status_code == 200
    assert b"multipart/form-data" in response.content


def test_export_round_trips_status_by_value(analyst: User) -> None:
    org = analyst.org
    admin = _admin(org)
    status = _status("InProgress")
    alert = _alert(org, status=status)
    doc = alert_export_document(alert)
    doc["alert"]["sourceRef"] = "ref-status"
    doc["alert"]["date"] = datetime(2026, 1, 2, 3, 4, tzinfo=UTC).isoformat()

    assert _login(admin).post(reverse("ui-alert-import"), {"file": _upload(doc)}).status_code == 302
    imported = Alert.objects.get(source_ref="ref-status")
    assert imported.status.value == "InProgress"
    assert imported.date.year == 2026
