"""Tag CRUD and case-tag toggle views."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from cases.models import (
    Tag,
)
from identity.scoping import (
    resolve_case,
)
from ui.pagination import paginate

from .common import (
    _base_context,
    not_viewer,
)

logger = logging.getLogger(__name__)


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
    if Tag.objects.filter(name=name[:200]).exists():
        messages.error(request, "Tag with that name already exists.")
        return redirect("ui-tag-create")
    Tag.objects.create(name=name[:200])
    messages.success(request, f"Tag '{name}' created.")
    return redirect("ui-tag-list")


@not_viewer
@login_required
def tag_detail(request: HttpRequest, tag_id: str) -> HttpResponse:
    """View or edit a tag."""
    try:
        import uuid

        uuid.UUID(tag_id)
        tag = get_object_or_404(Tag, pk=tag_id)
    except ValueError:
        tag = get_object_or_404(Tag, name=tag_id)
    if request.method == "GET":
        # Fetch case_links ordered by case start_date descending
        related_tags = tag.case_links.select_related("case").order_by("-case__start_date")[:20]
        return render(
            request,
            "ui/tag_detail.html",
            _base_context(request, tag=tag, related_tags=related_tags),
        )
    # Update
    tag.name = (request.POST.get("name") or tag.name)[:200]
    tag.save()
    messages.success(request, f"Tag '{tag.name}' updated.")
    return redirect("ui-tag-detail", tag_id=tag.id)


@not_viewer
@login_required
@require_POST
def tag_delete(request: HttpRequest, tag_id: str) -> HttpResponse:
    """Delete a tag."""
    try:
        import uuid

        uuid.UUID(tag_id)
        tag = get_object_or_404(Tag, pk=tag_id)
    except ValueError:
        tag = get_object_or_404(Tag, name=tag_id)
    name = tag.name
    tag.delete()
    messages.success(request, f"Tag '{name}' deleted.")
    return redirect("ui-tag-list")
