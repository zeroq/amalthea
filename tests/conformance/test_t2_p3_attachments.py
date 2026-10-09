"""T2 Phase P3 surface — case attachments (plan §6-P3).

The highest-risk surface of the wave: client-controlled bytes and a client-controlled filename.
The three acceptance criteria are the behaviours that fail *dangerously* if the controls drift.

=====  =====================================================================
AC-a   oversize => 413, wrong content type => 415, and a traversal filename is
       stored sanitised — no blob ever leaves the attachment volume.
AC-b   download returns the original filename + content type and the bytes hash
       to the recorded sha256.
AC-c   a foreign organisation cannot upload, download or delete (404); no
       credentials is 401.
=====  =====================================================================

`MEDIA_ROOT` is redirected to a per-test tmp dir so the suite never writes into the repository's
`media/` tree, and every "nothing was written" assertion can look at a directory that starts empty.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from cases.models import Attachment, Case, CaseStatus
from identity.models import Organisation, User

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"rest of a fake png"


@pytest.fixture(autouse=True)
def _media_root(tmp_path: Path, settings: Any) -> None:
    """Point storage at a scratch dir; `setting_changed` resets the `default_storage` lazy object."""
    settings.MEDIA_ROOT = tmp_path / "media"


def _owned_case(org: Organisation, title: str = "Owned") -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _foreign_api(org: Organisation) -> APIClient:
    user = User.objects.create(
        login=f"foreign-{org.id.hex[:6]}",
        username=f"foreign-{org.id.hex[:6]}",
        email="f@example.net",
        org=org,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _upload(client: APIClient, case: Case, name: str, content: bytes, content_type: str) -> Any:
    return client.post(
        f"/api/v1/case/{case.id}/attachments",
        {"attachments": SimpleUploadedFile(name, content, content_type=content_type)},
        format="multipart",
    )


def _body(response: Any) -> bytes:
    return b"".join(response.streaming_content)


# --- AC-a — size, content type, traversal ---------------------------------


def test_an_oversize_upload_is_413_and_writes_nothing(
    api: APIClient, analyst: User, settings: Any
) -> None:
    settings.ATTACHMENT_MAX_BYTES = 8
    case = _owned_case(analyst.org)

    response = _upload(api, case, "big.txt", b"0123456789", "text/plain")

    assert response.status_code == 413, response.content
    assert not Attachment.objects.filter(case=case).exists(), "a rejected upload landed a row"
    assert not (Path(settings.MEDIA_ROOT) / "attachments").exists(), (
        "a rejected upload wrote a blob"
    )


def test_a_disallowed_content_type_is_415(api: APIClient, analyst: User) -> None:
    case = _owned_case(analyst.org)

    response = _upload(api, case, "payload.bin", b"MZ\x90\x00", "application/x-msdownload")

    assert response.status_code == 415, response.content
    assert not Attachment.objects.filter(case=case).exists()


def test_content_that_does_not_match_its_declared_type_is_415(
    api: APIClient, analyst: User
) -> None:
    """PNG bytes declared `text/plain`: the signature table must reject the lie."""
    case = _owned_case(analyst.org)

    response = _upload(api, case, "sneaky.txt", PNG_MAGIC, "text/plain")

    assert response.status_code == 415, response.content
    assert not Attachment.objects.filter(case=case).exists()


def test_a_traversal_filename_is_stored_sanitised(
    api: APIClient, analyst: User, settings: Any
) -> None:
    case = _owned_case(analyst.org)

    response = _upload(api, case, "../../etc/passwd.txt", b"not really", "text/plain")

    assert response.status_code == 201, response.content
    attachment = Attachment.objects.get(case=case)
    # The client's name survives only as display data, never as a path component.
    assert attachment.name == "passwd.txt"
    assert attachment.path.startswith("attachments/")
    assert ".." not in attachment.path
    # And the blob is genuinely inside MEDIA_ROOT, not beside it.
    blob = (Path(settings.MEDIA_ROOT) / attachment.path).resolve()
    assert blob.is_file()
    assert blob.is_relative_to(Path(settings.MEDIA_ROOT).resolve())


# --- AC-b — download fidelity and delete ----------------------------------


def test_download_returns_the_original_name_type_and_bytes(api: APIClient, analyst: User) -> None:
    case = _owned_case(analyst.org)
    content = b"user: analyst\nhost: WS-14\n"

    created = _upload(api, case, "evidence.log", content, "text/plain")
    assert created.status_code == 201, created.content
    body = created.json()
    assert set(body) == {"attachments"}
    entry = body["attachments"][0]
    assert entry["_type"] == "attachment"
    assert entry["name"] == "evidence.log"
    assert entry["size"] == len(content)
    assert entry["hashes"] == [hashlib.sha256(content).hexdigest()]

    downloaded = api.get(f"/api/v1/case/{case.id}/attachment/{entry['_id']}/download")
    assert downloaded.status_code == 200, downloaded.content
    assert downloaded["Content-Type"].startswith("text/plain")
    assert "evidence.log" in downloaded["Content-Disposition"]
    received = _body(downloaded)
    assert received == content
    assert (
        hashlib.sha256(received).hexdigest()
        == Attachment.objects.get(pk=entry["_id"]).sha256
        == entry["hashes"][0]
    )


def test_multiple_files_upload_returns_the_wrapper_and_delete_removes_the_blob(
    api: APIClient, analyst: User, settings: Any
) -> None:
    case = _owned_case(analyst.org)
    response = api.post(
        f"/api/v1/case/{case.id}/attachments",
        {
            "attachments": [
                SimpleUploadedFile("a.txt", b"one", content_type="text/plain"),
                SimpleUploadedFile("b.json", b'{"two": true}', content_type="application/json"),
            ]
        },
        format="multipart",
    )
    assert response.status_code == 201, response.content
    assert len(response.json()["attachments"]) == 2
    assert Attachment.objects.filter(case=case).count() == 2

    attachment = Attachment.objects.filter(case=case).first()
    assert attachment is not None
    path = attachment.path
    deleted = api.delete(f"/api/v1/case/{case.id}/attachment/{attachment.id}")
    assert deleted.status_code == 204, deleted.content
    assert not Attachment.objects.filter(pk=attachment.id).exists()
    assert not default_storage.exists(path), "the blob outlived its row"


# --- AC-c — cross-org and anonymous ---------------------------------------


def test_a_foreign_org_cannot_upload_download_or_delete(api: APIClient, analyst: User) -> None:
    case = _owned_case(analyst.org)
    created = _upload(api, case, "secret.txt", b"owner only", "text/plain")
    attachment_id = created.json()["attachments"][0]["_id"]

    other = Organisation.objects.create(name="Other Org")
    foreign = _foreign_api(other)

    assert _upload(foreign, case, "nope.txt", b"nope", "text/plain").status_code == 404
    assert (
        foreign.get(f"/api/v1/case/{case.id}/attachment/{attachment_id}/download").status_code
        == 404
    )
    assert foreign.delete(f"/api/v1/case/{case.id}/attachment/{attachment_id}").status_code == 404
    # The owner's row is untouched and no foreign row was written.
    assert Attachment.objects.filter(case=case).count() == 1


def test_the_surface_is_401_without_credentials(anonymous_api: APIClient) -> None:
    case_id = "11111111-1111-1111-1111-111111111111"
    attachment_id = "22222222-2222-2222-2222-222222222222"
    for path, method in (
        (f"/api/v1/case/{case_id}/attachments", "post"),
        (f"/api/v1/case/{case_id}/attachment/{attachment_id}/download", "get"),
        (f"/api/v1/case/{case_id}/attachment/{attachment_id}", "delete"),
    ):
        response = getattr(anonymous_api, method)(path, {}, format="json")
        assert response.status_code == 401, f"{method.upper()} {path} -> {response.status_code}"


def test_an_attachment_reached_through_the_wrong_case_is_404(api: APIClient, analyst: User) -> None:
    case = _owned_case(analyst.org)
    other = _owned_case(analyst.org, title="Other")
    created = _upload(api, case, "a.txt", b"data", "text/plain")
    attachment_id = created.json()["attachments"][0]["_id"]

    assert (
        api.get(f"/api/v1/case/{other.id}/attachment/{attachment_id}/download").status_code == 404
    )
    assert api.delete(f"/api/v1/case/{other.id}/attachment/{attachment_id}").status_code == 404
    assert Attachment.objects.filter(pk=attachment_id).exists()
