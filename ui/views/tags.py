"""Tag CRUD and case-tag toggle views."""

from __future__ import annotations

import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from cases.models import (
    Tag,
)
from identity.scoping import (
    is_admin,
    resolve_case,
)
from ui.pagination import paginate

from .common import (
    _base_context,
    _require_admin_ui,
    not_viewer,
)

logger = logging.getLogger(__name__)

MAX_TAG_NAME_LENGTH = 100
MAX_TAG_COLOUR_LENGTH = 20


def _resolve_tag_or_404(identifier: str) -> Tag:
    """Resolve a tag by UUID first, then by its unique name (the UI accepts both)."""
    try:
        uuid.UUID(identifier)
    except (TypeError, ValueError, AttributeError):
        return get_object_or_404(Tag, name=identifier)
    return get_object_or_404(Tag, pk=identifier)


def _tag_edit_redirect(request: HttpRequest, tag: Tag) -> HttpResponse:
    """Return to the detail page when the modal was opened there, else the list."""
    if request.POST.get("return_to") == "detail":
        return redirect("ui-tag-detail", tag_id=tag.id)
    return redirect("ui-tag-list")


@login_required
@require_GET
def case_tags(request: HttpRequest, case_id: str) -> HttpResponse:
    """Manage tags on a case."""
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    tags = Tag.objects.all().order_by("name")
    case_tag_ids = set(case.tags.values_list("id", flat=True))
    return render(
        request,
        "ui/case_tags.html",
        _base_context(
            request,
            case=case,
            all_tags=tags,
            case_tag_ids=case_tag_ids,
        ),
    )


@not_viewer
@login_required
@require_POST
def case_tag_toggle(request: HttpRequest, case_id: str, tag_id: str) -> HttpResponse:
    """Add or remove a tag from a case."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    tag = get_object_or_404(Tag, pk=tag_id)
    if tag in case.tags.all():
        case.tags.remove(tag)
        messages.success(request, f"Removed tag '{tag.name}'.")
    else:
        case.tags.add(tag)
        messages.success(request, f"Added tag '{tag.name}'.")
    return redirect("ui-case-tags", case_id=case.number)


@login_required
@require_GET
def tag_list(request: HttpRequest) -> HttpResponse:
    """List all tags."""
    queryset = Tag.objects.all().order_by("name")
    page_obj = paginate(request, queryset)
    filtered_from = None  # No filters on this list
    return render(
        request,
        "ui/tag_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
            can_edit_tags=is_admin(request.user),
        ),
    )


@not_viewer
@login_required
def tag_create(request: HttpRequest) -> HttpResponse:
    """Create a tag."""
    if request.method == "GET":
        return render(request, "ui/tag_form.html", _base_context(request))
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-tag-create")
    if Tag.objects.filter(name=name[:MAX_TAG_NAME_LENGTH]).exists():
        messages.error(request, "Tag with that name already exists.")
        return redirect("ui-tag-create")
    colour = (request.POST.get("colour") or "").strip()[:MAX_TAG_COLOUR_LENGTH]
    description = (request.POST.get("description") or "").strip()
    tag = Tag.objects.create(
        name=name[:MAX_TAG_NAME_LENGTH],
        colour=colour,
        description=description,
    )
    messages.success(request, f"Tag '{tag.name}' created.")
    return redirect("ui-tag-list")


@not_viewer
@login_required
@require_GET
def tag_detail(request: HttpRequest, tag_id: str) -> HttpResponse:
    """Display a tag and its links. Editing goes through `tag_edit` (admin-only)."""
    tag = _resolve_tag_or_404(tag_id)
    # Fetch case_links ordered by case start_date descending
    related_tags = tag.case_links.select_related("case", "case__status").order_by(
        "-case__start_date"
    )[:20]
    related_alerts = tag.alerts.select_related("status", "case").order_by("-date")
    related_observables = tag.observables.select_related("data_type").order_by("-created_at")
    return render(
        request,
        "ui/tag_detail.html",
        _base_context(
            request,
            tag=tag,
            related_tags=related_tags,
            related_alerts=related_alerts,
            related_observables=related_observables,
            can_edit_tags=is_admin(request.user),
        ),
    )


@not_viewer
@login_required
@require_POST
def tag_edit(request: HttpRequest, tag_id: str) -> HttpResponse:
    """Admin-only edit of a tag's name, colour and description."""
    denied = _require_admin_ui(request)
    if denied is not None:
        return denied
    tag = _resolve_tag_or_404(tag_id)
    name = (request.POST.get("name") or "").strip()[:MAX_TAG_NAME_LENGTH]
    if not name:
        messages.error(request, "Name is required.")
        return _tag_edit_redirect(request, tag)
    if Tag.objects.exclude(pk=tag.pk).filter(name=name).exists():
        messages.error(request, "Tag with that name already exists.")
        return _tag_edit_redirect(request, tag)
    tag.name = name
    tag.colour = (request.POST.get("colour") or "").strip()[:MAX_TAG_COLOUR_LENGTH]
    tag.description = (request.POST.get("description") or "").strip()
    tag.save(update_fields=["name", "colour", "description", "updated_at"])
    messages.success(request, f"Tag '{tag.name}' updated.")
    return _tag_edit_redirect(request, tag)


@not_viewer
@login_required
@require_POST
def tag_delete(request: HttpRequest, tag_id: str) -> HttpResponse:
    """Delete a tag."""
    tag = _resolve_tag_or_404(tag_id)
    name = tag.name
    tag.delete()
    messages.success(request, f"Tag '{name}' deleted.")
    return redirect("ui-tag-list")
