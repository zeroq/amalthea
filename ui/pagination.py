"""Server-side pagination primitive for Amalthea UI lists.

This module provides a single reusable `paginate()` helper that wraps
`django.core.paginator.Paginator` and enforces the following security
and correctness policies:

**Untrusted parameter handling (CWE-20):**
- The `page` query parameter is treated as untrusted input. Non-integer values
  (including empty strings, whitespace, floats like "1e9"), zero, negative
  integers, and absurdly large integers are all handled gracefully — never a
  500. Invalid/junk values fall back to page 1.
- The `per_page` query parameter is similarly untrusted. Only the explicit
  allow-list {"25", "50", "100", "200"} is honoured; any other value falls
  back to the caller's `size` argument. `MAX_PAGE_SIZE` (200) is a hard
  ceiling that can never be exceeded.

**Out-of-range clamping (decision Q5):**
- When `page` exceeds the number of pages, it is clamped to the last page
  (no redirect, HTTP 200). This applies to both valid large integers and
  values that would be out of range for the current queryset.

**Empty queryset handling:**
- An empty queryset still returns a valid `Page` object (page 1 of 1), so
  templates can rely on `page_obj.paginator.count == 0` and
  `page_obj.has_other_pages() == False` without special cases.

**Org-scoping preservation (AC-P1.3, AC-P1.4):**
- `paginate()` never applies or loosens any filtering. The caller is
  responsible for passing an already org-filtered queryset (via `org_q`,
  `case_visible_q`, etc.). Pagination only slices what the caller provides.

**Performance:**
- Exactly one `COUNT` query is executed per request (via `Paginator.count`),
  matching the cost of the previous hard-coded `[:PAGE_SIZE]` slice.
- The `filtered_from` count (unfiltered org-scoped total) is computed by the
  *view* only when a filter is active, not by this helper.

**Template contract:**
- Returns a `django.core.paginator.Page` object. Templates receive this as
  `page_obj` and use `page_obj.object_list`, `page_obj.has_other_pages()`,
  `page_obj.start_index()`, `page_obj.end_index()`,
  `page_obj.paginator.count`, `page_obj.paginator.num_pages`.
- The view also passes `filtered_from` (int | None) — the unfiltered,
  org-scoped count when a filter is active; `None` otherwise.
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, Page, PageNotAnInteger, Paginator
from django.db import models
from django.db.models.query import QuerySet
from django.http import HttpRequest

# Frozen interface constants (PLAN-2026-10-18 §11a)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# Maximum rows any non-paged rail/detail list may render. A cap must always be
# shown with an honest count line (D1).
#
# Two distinct cap semantics exist, and the caller must choose correctly:
# - Vocabulary / picker (e.g. TTPs, observable types): cap **head-first** in
#   display order (`qs[:RAIL_CAP]`). The list is searched/scanned alphabetically,
#   so the first N items must be visible.
# - Log / working set (e.g. alerts, tasks, comments, timeline, pages, runs,
#   procedures, audit logs): cap to **newest N** and re-order back to display
#   order. The analyst needs to see recent activity, not the oldest rows.
RAIL_CAP = 200
# Page size for picker dropdowns (assignee pickers, merge candidate lists, etc.)
# — NOT for paginated lists. Paginated lists use DEFAULT_PAGE_SIZE / MAX_PAGE_SIZE.
PICKER_PAGE_SIZE = 200

# Allow-list for ?per_page= — only these exact strings are accepted.
ALLOWED_PER_PAGE = {"25", "50", "100", "200"}


def paginate[T: models.Model](
    request: HttpRequest,
    queryset: QuerySet[T],
    *,
    size: int = DEFAULT_PAGE_SIZE,
    param: str = "page",
    per_page_param: str = "per_page",
) -> Page[T]:
    """Paginate an already-filtered queryset for a UI list.

    Args:
        request: The incoming HTTP request (for `page` and `per_page` params).
        queryset: An **already org-filtered** QuerySet. This function does not
            apply any additional filtering — it only slices what is given.
        size: Default page size (rows per page). Defaults to `DEFAULT_PAGE_SIZE`
            (50). Clamped to `MAX_PAGE_SIZE` (200) internally.
        param: Query parameter name for the page number. Defaults to "page".
        per_page_param: Query parameter name for the page size override.
            Defaults to "per_page".

    Returns:
        A `django.core.paginator.Page` object for the requested (or clamped)
        page. Never raises; invalid input yields page 1, out-of-range yields
        the last page.

    Security notes:
        - `page` and `per_page` are untrusted (CWE-20). All parsing is bounded
          and clamped; no value can cause a 500 or escape the caller's queryset.
        - `per_page` is restricted to the allow-list `ALLOWED_PER_PAGE` and
          hard-clamped to `MAX_PAGE_SIZE`. A malicious `per_page=999999` is
          ignored, not honoured.
        - The returned `Page` reflects only the caller's queryset — org
          isolation is the caller's responsibility and is preserved by
          construction.
    """
    # Resolve effective page size: ?per_page= is honoured only within the
    # allow-list; anything else falls back to `size`. MAX_PAGE_SIZE is a
    # hard ceiling that can never be exceeded.
    per_page_raw = request.GET.get(per_page_param)
    if per_page_raw in ALLOWED_PER_PAGE:
        effective_size = min(int(per_page_raw), MAX_PAGE_SIZE)
    else:
        effective_size = min(size, MAX_PAGE_SIZE)

    paginator = Paginator(queryset, effective_size)

    # Resolve page number: untrusted input, never a 500.
    page_raw = request.GET.get(param)
    try:
        page_number = int(page_raw) if page_raw is not None else 1
    except (TypeError, ValueError):
        # Non-integer (abc, "", "1e9", " 2 ", etc.) -> page 1
        page_number = 1

    if page_number <= 0:
        # Zero or negative -> page 1
        page_number = 1

    try:
        page_obj = paginator.page(page_number)
    except EmptyPage:
        # Out of range (page > num_pages) -> clamp to last page (decision Q5)
        if paginator.num_pages > 0:
            page_obj = paginator.page(paginator.num_pages)
        else:
            # Empty queryset: Paginator.page(1) returns a valid empty Page
            page_obj = paginator.page(1)
    except PageNotAnInteger:
        # Should not reach here due to the try/except above, but defensive
        page_obj = paginator.page(1)

    return page_obj
