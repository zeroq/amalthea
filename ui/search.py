"""UI view for global search at ``/search``.

Parsing and data access live in :mod:`query.search`; this module only owns the request/response
shape and the template context.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from query.search import (
    KINDS,
    SEARCH_PAGE_SIZE,
    SECTION_LIMIT,
    parse_query,
    queryset_for,
    search_sections,
)
from ui.pagination import paginate

_VALID_TYPES = {"all", *KINDS}


@login_required
@require_GET
def global_search(request):
    """Render grouped search results for the caller's visible scope.

    Context contract used by ``ui/search.html``:
        query: trimmed query text (empty for the blank state)
        type: ``all`` or one of ``case``/``alert``/``observable``
        counts: all three counts, for tabs
        sections: list of SearchSection (empty for blank; three for all; one typed)
        page_obj: Page for typed views, ``None`` otherwise
        is_superadmin: show the owning-organisation column
    """
    raw_type = request.GET.get("type", "all")
    search_type = raw_type if raw_type in _VALID_TYPES else "all"
    query = parse_query(request.GET.get("q", ""))
    is_superadmin = getattr(request.user, "role", "") == "superAdmin"

    page_obj = None
    if query.is_empty:
        sections = []
        counts = dict.fromkeys(KINDS, 0)
    elif search_type == "all":
        result = search_sections(query, request.user, None, page=1, per_page=SECTION_LIMIT)
        sections = result["sections"]
        counts = result["counts"]
    else:
        page_obj = paginate(
            request,
            queryset_for(search_type, query, request.user),
            size=SEARCH_PAGE_SIZE,
        )
        result = search_sections(
            query,
            request.user,
            search_type,
            page=page_obj.number,
            per_page=page_obj.paginator.per_page,
        )
        sections = result["sections"]
        counts = result["counts"]

    return render(
        request,
        "ui/search.html",
        {
            "query": query.raw,
            "type": search_type,
            "counts": counts,
            "sections": sections,
            "page_obj": page_obj,
            "is_superadmin": is_superadmin,
        },
    )
