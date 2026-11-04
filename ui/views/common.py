"""Shared helpers, constants, and decorators for the UI view package."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert
from cases.models import (
    Case,
    Task,
)
from core.enums import (
    SEVERITY_CHOICES,
)
from identity.scoping import (
    case_visible_q,
    is_admin,
    is_viewer,
    org_filtered,
    org_q,
)

logger = logging.getLogger(__name__)

SEVERITY_LABELS = dict(SEVERITY_CHOICES)

SEVERITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "critical"}

CASE_STAGE_VALUES = ("New", "InProgress", "Closed")

CASE_INLINE_EDIT_FIELDS: frozenset[str] = frozenset(
    {"severity", "scope", "case_type", "data_classification", "phase", "verdict"}
)

MAX_HEADERS = 64

MAX_BULK_PROCEDURES = 100


def _parse_due_date(raw: str | None) -> datetime.datetime | None:
    """Parse a `YYYY-MM-DD` date string into an aware datetime at 23:59:59 in `settings.TIME_ZONE`.

    The HTML `<input type="date">` submits a day-granularity value. Storing it at end-of-day
    ensures a task due *today* is not considered "overdue" at 00:00 UTC. Returns `None` for
    empty/invalid input; callers should treat a parse failure as a field error.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        naive = datetime.datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    # Make it end-of-day in the project's timezone, then convert to aware UTC for storage.
    # `timezone.make_aware` uses `settings.TIME_ZONE` when `is_dst` is not provided.
    end_of_day = timezone.make_aware(
        datetime.datetime(naive.year, naive.month, naive.day, 23, 59, 59)
    )
    return end_of_day


def not_viewer(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Deny the viewer (read-only) role every mutating UI view (ADR-006).

    A viewer may read; they may not write anything. The denial is a uniform 404 —
    the same shape as the case-scoping rule and the playbook gate — so a viewer
    cannot use the response to learn whether an object exists.

    Applied as the **outermost** decorator, so it runs before method checks and
    before any parsing: a viewer can never reach validation, a form, or a write
    path. Declared per view (rather than as middleware) so the gate sits next to
    the surface it protects; `tests/conformance/test_viewer_readonly_ui.py`
    enumerates the URLconf and fails if any mutating route is missing it.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if is_viewer(request.user):
            # Log the *route name*, never `request.path`: `<str:case_id>` accepts `%0A`, which
            # PATH_INFO decodes, so a viewer could forge extra log lines with a crafted URL
            # (CWE-117; REVIEW-2026-10-12 L1). Both fields below are server-controlled.
            match = getattr(request, "resolver_match", None)
            logger.warning(
                "viewer denied: %s %s",
                request.method,
                match.url_name if match is not None else "?",
                extra={"user_id": str(request.user.pk)},
            )
            return render(request, "ui/404.html", status=404)
        return view(request, *args, **kwargs)

    return wrapper


def _base_context(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    """Everything every page needs, in one place.

    The nav badges live here rather than per-view because two views counting them differently is how
    a navigation bar starts lying about what is waiting.
    """
    return {
        "nav_alert_count": Alert.objects.filter(
            org_q("owner_org", request.user), case__isnull=True, status__stage="New"
        ).count(),
        "nav_case_count": Case.objects.filter(
            case_visible_q(request.user), closed_date__isnull=True
        ).count(),
        "nav_task_count": org_filtered(Task.objects.all(), request.user, path="case__owner_org")
        .exclude(status="Completed")
        .count(),
        "severity_labels": SEVERITY_LABELS,
        "severity_names": SEVERITY_NAMES,
        "case_stages": CASE_STAGE_VALUES,
        # Layer 3 (ADR-006 §4.4): the shift from "a viewer's writes fail" to "a viewer is never
        # offered one". Nothing is gated on this — the server answers 404 first (Layer 1/2); a
        # template that forgets it shows a button that 404s, never a hole.
        "can_mutate": not is_viewer(request.user),
        **extra,
    }


CASE_TABS: tuple[tuple[str, str], ...] = (
    ("general", "General"),
    ("custom", "Custom"),
    ("alerts", "Alerts"),
    ("tasks", "Tasks"),
    ("observables", "Observables"),
    ("ttp", "TTPs"),
    ("attachments", "Attachments"),
    ("similar", "Similar"),
    ("comments", "Comments"),
    ("audit", "Audit"),
)

CASE_TAB_SLUGS: frozenset[str] = frozenset(slug for slug, _ in CASE_TABS)


def _case_detail_redirect(request: HttpRequest, case_id: str) -> HttpResponseRedirect:
    """Redirect back to the case page, staying on the tab the form was submitted from.

    Each panel form carries a hidden `tab` input. It is validated against `CASE_TAB_SLUGS` and
    dropped when absent or unknown, so the redirect target is always a known panel — a hostile
    value can never reach the URL or a template path.
    """
    url = reverse("ui-case-detail", kwargs={"case_id": case_id})
    tab = request.POST.get("tab")
    if tab in CASE_TAB_SLUGS:
        url = f"{url}?tab={tab}"
    return HttpResponseRedirect(url)


def _htmx_redirect(url: str) -> HttpResponse:
    """A `204` that tells HTMX to navigate instead of swapping.

    Used when a modal's POST succeeds: HTMX treats `HX-Redirect` as a full navigation, so the
    dialog closes, the target page reloads with its flash, and no swap target is needed. A bare
    `204` would leave the modal open and the list stale — the trap this avoids.
    """
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _api_error_detail(response: HttpResponse) -> str:
    """A short human-readable reason from a DRF view invoked as a plain function.

    A DRF `Response` handed straight back from a view function has not been rendered, so reading
    `.content` raises `ContentNotRenderedError` — turning a rejected upload into a 500 instead of
    an error message. `.data` is already populated, so prefer the API's own `message` field.
    """
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message:
            return message[:200]
    return f"HTTP {response.status_code}"


def _require_superadmin_ui(request: HttpRequest) -> HttpResponse | None:
    """Return a 404 response if the user is not a superAdmin, else None."""
    if getattr(request.user, "role", "") != "superAdmin":
        return render(request, "ui/404.html", status=404)
    return None


def _require_admin_ui(request: HttpRequest) -> HttpResponse | None:
    """Return a 404 response if the user is not an admin or superAdmin, else None.

    Used for Settings sub-pages accessible to both admin and superAdmin roles.
    """

    if not is_admin(request.user):
        return render(request, "ui/404.html", status=404)
    return None


USERS_PER_ORG_CAP = 200
