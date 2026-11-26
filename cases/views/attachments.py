from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from cases.models import (
    Attachment,
    Case,
)
from core.serializers import (
    attachment_json,
)

from ._common import _actor, _as_uuid, _bad, _case_access, _not_found

# --- Attachments (T2 P3) ---------------------------------------------------
#
# The highest-risk surface of the T2 wave: client-controlled bytes and a client-controlled
# filename. Two rules hold throughout — the client's name is *display data only* and never a path
# component, and the size is checked before a byte reaches permanent storage.

# Concrete types only. A wildcard like `application/octet-stream` is deliberately absent: it would
# declare nothing and defeat the content sniff below.
_ATTACHMENT_ALLOWED_TYPES = frozenset(
    {
        "text/plain",
        "text/csv",
        "text/markdown",
        "text/x-python",
        "application/json",
        "application/x-ndjson",
        "application/pdf",
        "application/zip",
        "application/xml",
        "text/xml",
        "image/png",
        "image/jpeg",
        "image/gif",
    }
)

# Magic-byte signatures for the binary types above. A declared type that has a signature here must
# match the bytes; a text/JSON/XML type must instead decode as UTF-8 (the fallback in `_sniff`).
_ATTACHMENT_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
)


def _unsupported(message: str) -> Response:
    return Response(
        {"type": "UnsupportedMediaType", "message": message},
        status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def _too_large(message: str) -> Response:
    return Response(
        {"type": "TooLarge", "message": message},
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


def _safe_attachment_name(raw: str) -> str:
    """A display filename reduced to one harmless segment.

    The stored blob never uses this (see `_attachment_storage_path`), but it is stored and echoed in
    `Content-Disposition`, so `../../etc/passwd` or an embedded NUL must not survive into a header
    or a log line.
    """
    name = Path((raw or "").replace("\x00", "")).name.strip()
    if not name or name in {".", ".."}:
        return "attachment"
    return name[:255]


def _attachment_storage_path(name: str) -> str:
    """An opaque, collision-free storage key under the configured prefix.

    The extension comes from the client's name but is re-validated to `[a-z0-9]` and length-capped,
    so a crafted suffix cannot reintroduce a path separator. The stem is a server-side uuid4, so two
    uploads of the same name never share a blob and there is nothing for a traversal to climb.
    """
    prefix = getattr(settings, "ATTACHMENT_STORAGE_PREFIX", "attachments")
    suffix = Path(name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ""
    return f"{prefix}/{uuid4().hex}{suffix}"


def _sniff_attachment(content_type: str, data: bytes) -> bool:
    """True when `data` is consistent with the declared `content_type`.

    A file whose bytes start with a known signature must declare that signature's type (so PNG
    bytes sent as `text/plain`, or a PDF sent as `image/png`, is a 415). Anything the signature
    table does not know must at least decode as UTF-8 when it claims to be text-ish, which rejects
    a binary smuggled in under a text type. This is a sanity check, not a full MIME detector.
    """
    for signature, declared in _ATTACHMENT_SIGNATURES:
        if data.startswith(signature):
            return declared == content_type
    if content_type.startswith("text/") or content_type in {
        "application/json",
        "application/x-ndjson",
        "application/xml",
    }:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def _find_attachment(case: Case, attachment_id: str) -> Attachment | None:
    """An attachment reached *through* its case, so a foreign case id cannot address it."""
    pk = _as_uuid(attachment_id)
    if pk is None:
        return None
    return Attachment.objects.filter(pk=pk, case=case).first()


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_attachment_list(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/attachments` — multipart upload (TheHive 5.8).

    Accepts one or more files under the repeated `attachments` field and answers with TheHive's
    wrapper `{"attachments": [...]}`, not a bare list. Every file is size-checked and
    content-checked **before** anything is written to storage, so a rejected upload leaves neither a
    blob nor a row (AC6.1-P3-a). The client's filename is stored as `name` only; the blob name is
    generated server-side.
    """
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    uploads = request.FILES.getlist("attachments")
    if not uploads:
        return _bad("attachments is required", {"attachments": ["required"]})
    max_bytes = int(getattr(settings, "ATTACHMENT_MAX_BYTES", 25 * 1024 * 1024))

    # Phase 1 — validate all of them before a single byte hits permanent storage.
    prepared: list[tuple[str, str, bytes]] = []
    for upload in uploads:
        content_type = (upload.content_type or "").split(";")[0].strip().lower()
        if content_type not in _ATTACHMENT_ALLOWED_TYPES:
            return _unsupported(f"content type {content_type or 'unknown'} is not allowed")
        if upload.size is None or upload.size > max_bytes:
            return _too_large(f"attachment exceeds {max_bytes} bytes")
        data = upload.read()
        if len(data) > max_bytes:
            return _too_large(f"attachment exceeds {max_bytes} bytes")
        if not data:
            return _bad("an empty file is not an attachment", {"attachments": ["empty"]})
        if not _sniff_attachment(content_type, data):
            return _unsupported(f"content does not match declared type {content_type}")
        prepared.append((_safe_attachment_name(upload.name or ""), content_type, data))

    # Phase 2 — write the blobs, then the rows. A failure mid-way deletes every blob written, so a
    # rejected request cannot strand an unreferenced file in the attachment volume.
    stored: list[str] = []
    attachments: list[Attachment] = []
    try:
        with transaction.atomic():
            for name, content_type, data in prepared:
                stored.append(
                    default_storage.save(_attachment_storage_path(name), ContentFile(data))
                )
                attachments.append(
                    Attachment.objects.create(
                        case=case,
                        name=name,
                        content_type=content_type,
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        path=stored[-1],
                        created_by=_actor(request),
                    )
                )
    except Exception:
        for path in stored:
            default_storage.delete(path)
        raise
    return Response(
        {"attachments": [attachment_json(item) for item in attachments]},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_attachment_download(request: Request, case_id: str, attachment_id: str) -> Response:
    """`GET .../attachment/{id}/download` — stream the blob under its original name (TheHive 5.8).

    Read access is enough. The response carries the stored `name` and `content_type`; the recorded
    `sha256` is what a caller (or a test) compares the body against to prove the same bytes came
    back (AC6.1-P3-b).
    """
    case = _case_access(request, case_id, write=False)
    if case is None:
        return _not_found("Case")
    attachment = _find_attachment(case, attachment_id)
    if attachment is None or not default_storage.exists(attachment.path):
        return _not_found("Attachment")
    return FileResponse(
        default_storage.open(attachment.path, "rb"),
        as_attachment=True,
        filename=attachment.name,
        content_type=attachment.content_type,
    )


@api_view(["DELETE"])
@renderer_classes([JSONRenderer])
def case_attachment_detail(request: Request, case_id: str, attachment_id: str) -> Response:
    """`DELETE /api/v1/case/{idOrNumber}/attachment/{id}` — remove a case attachment (TheHive 5.8).

    The row goes first: a missing blob must not turn a delete into a 500, and a row without a blob
    is a broken download while a blob without a row is only wasted bytes.
    """
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    attachment = _find_attachment(case, attachment_id)
    if attachment is None:
        return _not_found("Attachment")
    path = attachment.path
    attachment.delete()
    default_storage.delete(path)
    return Response(status=status.HTTP_204_NO_CONTENT)
