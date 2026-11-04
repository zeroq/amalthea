"""Case pages, attachments, and procedures."""

from __future__ import annotations

import logging
from functools import partial

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from cases.models import (
    Attachment,
    Page,
)
from cases.views import (
    _resolve_ttp_value,
)
from core.serializers import page_json
from identity.scoping import (
    resolve_case,
)
from realtime.publisher import publish_case_event

from .common import (
    MAX_BULK_PROCEDURES,
    _api_error_detail,
    _base_context,
    _case_detail_redirect,
    not_viewer,
)

logger = logging.getLogger(__name__)


@not_viewer
@login_required
@require_POST
def case_page_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Add a TheHive-style page to the case from the UI.

    `POST /case/{id}/page` is JSON-only, so a browser form cannot drive it; this writes the same row
    through the same model instead — the same reason `case_task_create` writes a `Task` directly.

    `order` is a required API field that the UI never shows, so a new page is *appended* (after the
    highest existing `order`) rather than left at the model default: at `order=0` every UI-created
    page would tie with the API-created ones and the list would silently re-order itself.

    The insert publishes the same `page` frame the API publishes, so a colleague with the case open
    sees it without a reload; the author already sees it through the POST→redirect.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "A page needs a title.")
        return _case_detail_redirect(request, case.number)
    # Do not trust the form with `order`: it is not rendered, and a hidden field would let a crafted
    # POST re-order the analyst's pages. The highest existing `order` wins.
    last = case.pages.aggregate(highest=Max("order"))["highest"]
    page = Page.objects.create(
        case=case,
        title=title[:500],
        content=(request.POST.get("content") or "").strip(),
        category=(request.POST.get("category") or "").strip()[:100],
        order=0 if last is None else last + 1,
        created_by=request.user,
    )
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "page", {"event": page_json(page)})
    )
    messages.success(request, f"Page '{page.title}' added.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_page_update(request: HttpRequest, case_id: str, page_id: str) -> HttpResponse:
    """Save an edited page (title, Markdown body, category).

    One form owns all three fields, so they are written unconditionally instead of field-by-field the
    way the API's `PATCH` does: an empty *submitted* textarea means the analyst cleared the body,
    which is not the same thing as "field absent".

    `order` is excluded from `update_fields` on purpose (plan N1): editing a body must not undo an
    arrangement the analyst made through the API.

    `updated_by` is stamped so the entry can show "edited by …" — the model carries the field and the
    API's `PATCH` sets it, but nothing has ever rendered it.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    page = get_object_or_404(Page, pk=page_id, case=case)
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "A page needs a title.")
        return _case_detail_redirect(request, case.number)
    page.title = title[:500]
    page.content = (request.POST.get("content") or "").strip()
    page.category = (request.POST.get("category") or "").strip()[:100]
    page.updated_by = request.user
    page.save(update_fields=["title", "content", "category", "updated_by", "updated_at"])
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "page", {"event": page_json(page)})
    )
    messages.success(request, f"Page '{page.title}' updated.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_page_delete(request: HttpRequest, case_id: str, page_id: str) -> HttpResponse:
    """Delete a page.

    Reached *through* its case (`pk` **and** `case=case`), so a page id belonging to another case — or
    another tenant — is a 404 rather than a deletion.

    Unlike create and update this publishes nothing, matching the API's `DELETE`, which has never
    emitted a frame. A page deleted here therefore leaves a colleague's open list on their next load
    (plan R2) rather than mid-edit.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    page = get_object_or_404(Page, pk=page_id, case=case)
    title = page.title
    page.delete()
    messages.success(request, f"Deleted page '{title}'.")
    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def case_attachment_list(request: HttpRequest, case_id: str) -> HttpResponse:
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    return render(
        request,
        "ui/case_attachments.html",
        _base_context(request, case=case, attachments=case.attachments.all()),
    )


@not_viewer
@login_required
@require_POST
def case_attachment_upload(request: HttpRequest, case_id: str) -> HttpResponse:
    from cases.views import case_attachment_list as api_view

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    # The API view expects multipart/form-data with 'attachments' field
    # We just pass through
    response = api_view(request, case_id=case.id)
    if response.status_code == 201:
        messages.success(request, "Attachment uploaded.")
    else:
        messages.error(request, f"Upload failed: {_api_error_detail(response)}")
    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def case_attachment_download(
    request: HttpRequest, case_id: str, attachment_id: str
) -> HttpResponse:
    from cases.views import case_attachment_download as api_view

    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


@not_viewer
@login_required
@require_POST
def case_attachment_delete(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    """Delete an attachment from the UI.

    The API endpoint is `DELETE`-only and a browser form can only `POST`, so the UI performs the
    deletion itself instead of delegating (which used to answer this POST with a 405 and silently
    leave the row and blob in place). The attachment is reached *through* its case, so a foreign
    case id cannot address it — the same scoping `cases.views.case_attachment_detail` applies, and
    only an authenticated analyst (`@login_required`) may act.

    Ordering mirrors the API: the row goes first, so a missing blob cannot turn a delete into a 500.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    attachment = get_object_or_404(Attachment, pk=attachment_id, case=case)
    name = attachment.name
    path = attachment.path
    attachment.delete()
    default_storage.delete(path)
    messages.success(request, f"Deleted attachment '{name}'.")
    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def case_attachment_detail(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    """Attachment detail (download or delete)."""
    from cases.views import case_attachment_detail as api_view

    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


@not_viewer
@login_required
@require_POST
def case_procedure_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Create a procedure on a case from the UI (plain POST fallback).

    The API endpoint is POST-only and returns JSON; the UI performs the creation directly
    so a browser form gets a proper redirect + messages instead of a JSON blob.
    """
    from cases.procedures import create_procedure
    from compat.procedures import parse_procedure

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    # Build payload from form fields (matching TheHive wire keys for parse_procedure)
    pattern_id = (request.POST.get("patternId") or "").strip()
    if not pattern_id:
        messages.error(request, "Invalid patternId: ATT&CK ID is required")
        return _case_detail_redirect(request, case.number)

    payload = {
        "occurDate": request.POST.get("occurDate") or None,
        "patternId": pattern_id,
        "patternName": request.POST.get("patternName") or "",
        "tactic": request.POST.get("tactic") or "",
        "description": request.POST.get("description") or "",
        "ttpId": request.POST.get("ttpId") or None,
    }

    kwargs, error = parse_procedure(payload, resolve_ttp=_resolve_ttp_value)
    if error is not None:
        field, message = error
        messages.error(request, f"Invalid {field}: {message}")
        return _case_detail_redirect(request, case.number)

    try:
        with transaction.atomic():
            create_procedure(case=case, **kwargs)
    except Exception:  # pragma: no cover - defensive
        # The exception text can carry schema/SQL detail; keep it in the log, not in the flash.
        logger.exception("procedure create failed", extra={"case_id": str(case.id)})
        messages.error(request, "Could not attach the procedure.")
        return _case_detail_redirect(request, case.number)

    messages.success(request, "Procedure attached to case.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_procedures_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk create procedures on a case from the UI (plain POST fallback).

    Accepts repeated form fields (e.g. patternId, patternName, tactic, description lists
    or indexed pattern_id_<i>) and reuses parse_procedure/create_procedure in a transaction.
    Reports a per-item count via messages; never returns JSON, never 500s on bad input.
    """
    from cases.procedures import create_procedure
    from compat.procedures import parse_procedure

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    # Collect repeated fields — the simplest correct option: indexed fields like patternId_0, patternId_1...
    # Also support list-style field names for compatibility.
    post = request.POST

    # Determine number of items by finding the highest index for patternId
    item_indices = set()
    for key in post.keys():
        if key.startswith("patternId_"):
            try:
                idx = int(key.split("_", 1)[1])
                item_indices.add(idx)
            except (ValueError, IndexError):
                pass

    # Fallback: if no indexed fields, check for list-style (multiple values for same key)
    if not item_indices:
        # Check how many patternId values were submitted
        pattern_ids = post.getlist("patternId")
        if pattern_ids:
            item_indices = set(range(len(pattern_ids)))

    if not item_indices:
        messages.error(request, "No procedures provided.")
        return _case_detail_redirect(request, case.number)

    if len(item_indices) > MAX_BULK_PROCEDURES:
        messages.error(request, f"Too many procedures in one request (max {MAX_BULK_PROCEDURES}).")
        return _case_detail_redirect(request, case.number)

    # Hoist the repeated-field reads out of the loop: `post.getlist` re-walks the QueryDict on every
    # call, which made the item loop quadratic in the number of submitted fields.
    repeated = {
        field: post.getlist(field)
        for field in ("occurDate", "patternId", "patternName", "tactic", "description", "ttpId")
    }

    created_count = 0
    error_count = 0

    try:
        with transaction.atomic():
            for idx in sorted(item_indices):
                # Support both indexed and list-style; bind idx as default to avoid late-binding closure
                def get_field(field_name: str, *, _idx: int = idx) -> str:
                    indexed_key = f"{field_name}_{_idx}"
                    if indexed_key in post:
                        return post.get(indexed_key, "")
                    lst = repeated[field_name]
                    if _idx < len(lst):
                        return lst[_idx]
                    return ""

                payload = {
                    "occurDate": get_field("occurDate") or None,
                    "patternId": get_field("patternId"),
                    "patternName": get_field("patternName"),
                    "tactic": get_field("tactic"),
                    "description": get_field("description"),
                    "ttpId": get_field("ttpId") or None,
                }

                # Skip empty procedures
                if (
                    not payload["patternId"]
                    and not payload["patternName"]
                    and not payload["description"]
                ):
                    continue

                # patternId is required
                if not payload["patternId"].strip():
                    messages.error(
                        request, f"Procedure {idx + 1}: invalid patternId: ATT&CK ID is required"
                    )
                    error_count += 1
                    continue

                kwargs, error = parse_procedure(payload, resolve_ttp=_resolve_ttp_value)
                if error is not None:
                    field, message = error
                    messages.error(request, f"Procedure {idx + 1}: invalid {field}: {message}")
                    error_count += 1
                    continue

                # One savepoint per item: a DB-level failure (a constraint, a race on the parent)
                # would otherwise poison the surrounding transaction and make every later item fail
                # with `TransactionManagementError` — one bad row would silently become N.
                try:
                    with transaction.atomic():
                        create_procedure(case=case, **kwargs)
                except Exception:  # pragma: no cover - defensive
                    logger.exception(
                        "procedure create failed",
                        extra={"case_id": str(case.id), "index": idx},
                    )
                    messages.error(request, f"Procedure {idx + 1}: could not be attached.")
                    error_count += 1
                    continue
                created_count += 1

    except Exception:  # pragma: no cover - defensive
        # Same rule as the single-item path: the exception text stays out of the flash message.
        logger.exception("bulk procedure create aborted", extra={"case_id": str(case.id)})
        messages.error(request, "Could not attach the procedures.")
        return _case_detail_redirect(request, case.number)

    if created_count:
        messages.success(request, f"Attached {created_count} procedure(s) to case.")
    if error_count:
        messages.warning(request, f"{error_count} procedure(s) had errors and were skipped.")

    return _case_detail_redirect(request, case.number)
