"""Server-rendered analyst UI: the navigation layer over the Phase 4-6 API.

Deliberately small and deliberately server-rendered. TheHive is a React SPA; reproducing that is
out of scope for an MVP (BRIEF §0.3 forbids the TheHive UI endpoints) and would consume the whole
budget for a shell around endpoints that already work. What an analyst needs to *see* the Module D
loop working is a handful of pages and a keyboard, and that is what this is:

* **dashboard** — counts and the newest alerts/cases, so there is somewhere to land;
* **alerts / alert detail** — the triage queue and the escalate/merge actions;
* **cases / case detail** — the case list and the ledger: timeline, observables, tasks, automation runs;
* **automation** — the Module D run ledger across every case.

Three choices worth stating, because each is a deviation from "just render the API":

* **Views call the same services the API does**, not the API over HTTP. `alerts.escalation`,
  `observables.extractor` and `automation.dispatcher` are reused directly, so a UI action and an API
  call cannot diverge in behaviour — and no view has to authenticate itself over loopback.
* **Mutations are POST-only and CSRF protected.** There is no GET that changes state. The API is
  DRF-authenticated, this is session-authenticated, and both land on identical logic.
* **Severity is rendered from `core.enums`, never re-spelled here.** The API's 1-4 is the domain's
  1-4; a fourth spelling in a template is a fourth place to get it wrong.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from collections.abc import Callable
from functools import partial, wraps
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import (  # noqa: F401 (re-exported for ui/urls.py)
    LogoutView,
    PasswordChangeView,
)
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Max
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy  # noqa: F401 (re-exported for ui/urls.py)
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from rest_framework.response import Response

from alerts.escalation import (
    import_alert_to_case,
    merge_alert_into_case,
    resolve_case_status,
)
from alerts.models import Alert, AlertStatus
from automation.dispatcher import TRIGGER_EVENTS
from automation.executor import registered_actions
from automation.models import AutomationRun, Playbook
from automation.playbooks import ACTIONS, HTTP_METHODS
from cases.create import DEFAULT_CASE_STATUS, create_case
from cases.ledger import append_timeline_event
from cases.models import (
    TTP,
    Attachment,
    Case,
    CaseObservable,
    CaseStatus,
    CaseTemplate,
    Comment,
    Page,
    Tag,
    Task,
    TaskNote,
)
from cases.update import CaseUpdateError, apply_case_update
from cases.views import (
    _as_uuid,
    _observable_affected_cases,
    _observable_cross_case_guard,
    _resolve_login,
    _resolve_ttp_value,
    _set_observable_fields,
)
from core.enums import (
    ALERT_STAGES,
    CASE_PHASE_CHOICES,
    CASE_SCOPE_CHOICES,
    CASE_TYPE_CHOICES,
    DATA_CLASSIFICATION_CHOICES,
    PAP_CHOICES,
    SEVERITY_CHOICES,
    TASK_STATUS_CHOICES,
    TLP_CHOICES,
)
from core.serializers import page_json, task_json
from identity.models import AuditLog, Organisation, User
from identity.ratelimit import check_login_rate_limit, record_failed_login, record_successful_login
from identity.scoping import (
    case_visible_q,
    is_admin,
    is_case_owner,
    is_viewer,
    org_filtered,
    org_q,
    resolve_alert,
    resolve_case,
    resolve_observable,
    resolve_task,
)
from identity.services import (
    resolve_organisation,
    serializes_superadmin_changes,
)
from ingest.models import IngestionSource
from observables.extractor import add_observable_with_status, extract_into_case
from observables.models import Observable, ObservableType
from realtime.publisher import publish_case_event

logger = logging.getLogger(__name__)

SEVERITY_LABELS = dict(SEVERITY_CHOICES)
#: Severity as a CSS class suffix, so a template says `sev-{{ case.severity }}` and the palette lives
#: in one stylesheet. Mapping the number to a class name in the template would be a fourth spelling.
SEVERITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "critical"}
CASE_STAGE_VALUES = ("New", "InProgress", "Closed")
PAGE_SIZE = 200
#: Upper bound on outbound HTTP headers per playbook. `header_count` is form-controlled and drives a
#: CPU-bound `range()`, so it must be capped (see `_parse_playbook_config`) — far above real usage.
MAX_HEADERS = 64
#: Upper bound on procedures accepted in one bulk POST. The item index set is form-controlled, so it
#: is capped here rather than trusted to `DATA_UPLOAD_MAX_NUMBER_FIELDS` (1000) — each item costs a
#: row insert plus a `ttpId` lookup, and no analyst attaches procedures a hundred at a time.
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


def _parse_playbook_config(post: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate playbook action config from POST data.

    Returns (config_dict, error_message). If error_message is not None, config_dict is None.
    Mirrors the validation logic from playbook_create/playbook_detail so a GET→POST round-trip
    produces an identical config (including header order).
    """
    from automation.playbooks import MAX_TIMEOUT_SECONDS

    action = (post.get("action") or "").lower()
    if action not in ("http", "python"):
        return None, "Action must be 'http' or 'python'."

    config: dict[str, Any] = {"action": action}

    if action == "http":
        url = (post.get("url") or "").strip()
        if not url:
            return None, "URL is required for HTTP actions."
        config["url"] = url

        method = (post.get("http_method") or "GET").upper()
        if method not in HTTP_METHODS:
            return None, "Invalid HTTP method."
        config["method"] = method

        timeout = post.get("timeout_seconds")
        if timeout:
            try:
                timeout_val = float(timeout)
                if timeout_val <= 0 or timeout_val > MAX_TIMEOUT_SECONDS:
                    raise ValueError
                config["timeoutSeconds"] = timeout_val
            except ValueError:
                return None, f"Timeout must be a positive number <= {MAX_TIMEOUT_SECONDS}."

        # Headers: preserve order by iterating header_key_<i> in index order
        headers = {}
        header_count = post.get("header_count")
        try:
            max_idx = int(header_count) if header_count else 4
        except (TypeError, ValueError):
            max_idx = 4
        # Bound the loop: header_count is attacker-controlled and drives a CPU-bound range(),
        # so an unbounded value is an authenticated DoS. Reject rather than silently clamp.
        if not 0 <= max_idx <= MAX_HEADERS:
            return None, f"Too many headers (max {MAX_HEADERS})."
        for i in range(max_idx):
            key_field = f"header_key_{i}"
            val_field = f"header_val_{i}"
            if post.get(key_field):
                headers[post[key_field]] = post.get(val_field, "")
        if headers:
            config["headers"] = headers

        body = post.get("http_body")
        if body:
            config["body"] = body

    else:  # python
        action_path = (post.get("action_path") or "").strip()
        if not action_path:
            return None, "Action path is required for Python actions."
        if action_path not in registered_actions():
            return (
                None,
                f"Unknown action path. Registered: {', '.join(sorted(registered_actions().keys()))}",
            )
        config["action_path"] = action_path

    return config, None


def _base_context(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    """Everything every page needs, in one place.

    The nav badges live here rather than per-view because two views counting them differently is how
    a navigation bar starts lying about what is waiting.
    """
    return {
        "nav_alert_count": Alert.objects.filter(
            org_q("owner_org", request.user), case__isnull=True
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


class SignInView(DjangoLoginView):
    """Django's `LoginView`, restricted to the UI's own template and redirect.

    `redirect_authenticated_user=False` is the default; the visible consequence is that an
    already-signed-in analyst who opens `/login/` lands on the dashboard via `get_success_url`.
    """

    template_name = "ui/login.html"
    redirect_authenticated_user = True

    def get_success_url(self) -> str:
        return reverse("ui-dashboard")

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Rate limiting check
        username = (request.POST.get("username") or "").strip()
        allowed, retry_after = check_login_rate_limit(request, username)
        if not allowed:
            messages.error(
                request,
                f"Too many failed login attempts. Please try again in {retry_after} seconds.",
            )
            return self.form_invalid(self.get_form())

        # Process the login
        response = super().post(request, *args, **kwargs)

        # Record login attempt result
        username = (request.POST.get("username") or "").strip()
        if response.status_code == 302:  # Success redirect
            record_successful_login(request, username)
        else:  # Form invalid (failed login)
            record_failed_login(request, username)

        return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        return _base_context(self.request, **context)


sign_out = LogoutView.as_view()


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    """Landing page: what arrived, what is open, what automation has done."""

    counts = {
        "alerts_total": Alert.objects.filter(org_q("owner_org", request.user)).count(),
        "alerts_unlinked": Alert.objects.filter(
            org_q("owner_org", request.user), case__isnull=True
        ).count(),
        "cases_total": Case.objects.filter(case_visible_q(request.user)).count(),
        "cases_open": Case.objects.filter(
            case_visible_q(request.user), closed_date__isnull=True
        ).count(),
        "runs_total": AutomationRun.objects.filter(
            case__in=Case.objects.filter(case_visible_q(request.user))
        ).count(),
        "runs_failed": AutomationRun.objects.filter(
            case__in=Case.objects.filter(case_visible_q(request.user)), status="Failed"
        ).count(),
    }
    by_severity = {
        row["severity"]: row["n"]
        for row in Case.objects.filter(case_visible_q(request.user), closed_date__isnull=True)
        .values("severity")
        .annotate(n=Count("id"))
    }
    return render(
        request,
        "ui/dashboard.html",
        _base_context(
            request,
            counts=counts,
            by_severity=by_severity,
            recent_alerts=list(
                Alert.objects.select_related("status", "owner_org")
                .filter(org_q("owner_org", request.user), case__isnull=True)
                .order_by("-date")[:10]
            ),
            open_cases=list(
                Case.objects.select_related("status", "assignee", "owner_org")
                .filter(case_visible_q(request.user), closed_date__isnull=True)
                .order_by("-severity", "-start_date")[:10]
            ),
            runs=list(
                AutomationRun.objects.select_related("case", "playbook")
                .filter(case__in=Case.objects.filter(case_visible_q(request.user)))
                .order_by("-created_at")[:10]
            ),
        ),
    )


@login_required
@require_GET
def alert_list(request: HttpRequest) -> HttpResponse:
    """The triage queue: unlinked alerts by default, because those are the decisions waiting."""
    scope = request.GET.get("scope", "unlinked")
    queryset = Alert.objects.select_related(
        "status", "assignee", "ingestion_source", "case", "owner_org"
    ).filter(org_q("owner_org", request.user))
    if scope == "linked":
        queryset = queryset.filter(case__isnull=False)
    elif scope == "unlinked":
        queryset = queryset.filter(case__isnull=True)
    return render(
        request,
        "ui/alert_list.html",
        _base_context(
            request,
            alerts=queryset.order_by("-date")[:PAGE_SIZE],
            scope=scope,
            scopes=("unlinked", "linked", "all"),
        ),
    )


@login_required
@require_GET
def alert_detail(request: HttpRequest, alert_id: str) -> HttpResponse:
    alert = resolve_alert(request.user, alert_id)
    if alert is None:
        return render(request, "ui/404.html", status=404)
    user_org_id = getattr(request.user, "org_id", None)
    is_cross_org = alert.owner_org_id != user_org_id
    # P2/C1: status options for the triage control — exclude "Imported" (system-assigned)
    alert_status_options = (
        AlertStatus.objects.filter(stage__in=ALERT_STAGES)
        .exclude(stage="Imported")
        .order_by("order", "value")
    )
    return render(
        request,
        "ui/alert_detail.html",
        _base_context(
            request,
            alert=alert,
            merge_candidates=Case.objects.filter(case_visible_q(request.user))
            .filter(closed_date__isnull=True)
            .order_by("-start_date")[:PAGE_SIZE],
            is_cross_org=is_cross_org,
            alert_status_options=alert_status_options,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
        ),
    )


@not_viewer
@login_required
@require_POST
def alert_escalate(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Promote an alert into a new case (Module B).

    `transaction.atomic` because `import_alert_to_case` writes the case, links the alert, extracts
    observables and appends a timeline entry; a rollback halfway through would leave the alert marked
    `Imported` with no case, which is the one state the caller cannot recover from.
    """
    alert = resolve_alert(request.user, alert_id, write=True)
    if alert is None:
        return render(request, "ui/404.html", status=404)
    try:
        with transaction.atomic():
            case, warnings = import_alert_to_case(
                alert,
                title=(request.POST.get("title") or "").strip() or None,
                description=(request.POST.get("description") or "").strip() or None,
                assignee=request.user if request.POST.get("assign_me") else None,
                actor=request.user,
            )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-alert-detail", alert_id=alert_id)
    if warnings:
        messages.warning(request, "Escalated, with warnings: " + "; ".join(warnings))
    else:
        messages.success(request, f"Alert escalated into case {case.number}.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def alert_merge(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Fold this alert into an existing case."""
    alert = resolve_alert(request.user, alert_id, write=True)
    if alert is None:
        return render(request, "ui/404.html", status=404)
    case = resolve_case(request.user, request.POST.get("case_id") or "", write=True)
    if case is None:
        raise Http404()
    with transaction.atomic():
        merge_alert_into_case(alert, case, actor=request.user)
    messages.success(request, f"Alert merged into case {case.number}.")
    return _case_detail_redirect(request, case.number)


def _case_create_form_context(
    post_data: Any,
    status_options: Any,
    assignees: Any,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    """Build the context extras for the case create form (page or modal).

    `post_data` is a `QueryDict` (`None` on a GET): `dict(...)` would yield lists and render as
    `['x']`, so scalars are read with `.get()` (SEC-2026-10-11 N1). The numeric selects fall back
    to the model defaults, which is what the service applies when the wire omits them.
    """
    can_classify = is_admin(request.user) if request is not None else False
    form_values = {
        "title": "",
        "description": "",
        "summary": "",
        "severity": "2",
        "tlp": "2",
        "pap": "2",
        "status": DEFAULT_CASE_STATUS,
        "assignee": "",
        "tags": "",
        "flag": "",
        "scope": "unset",
        "case_type": "unset",
        "data_classification": "unset",
        "phase": "initial_triage",
    }
    if post_data is not None:
        for key in (
            "title",
            "description",
            "summary",
            "severity",
            "tlp",
            "pap",
            "status",
            "assignee",
            "tags",
            "scope",
            "case_type",
            "phase",
        ):
            form_values[key] = post_data.get(key, "")
        # data_classification is admin-only (Q-D5); only echo back if the actor may classify
        if can_classify:
            form_values["data_classification"] = post_data.get("data_classification", "")
        form_values["flag"] = "on" if post_data.get("flag") == "on" else ""
    return {
        "form_values": form_values,
        "case_status_options": status_options,
        "assignees": assignees,
        "severity_choices": SEVERITY_CHOICES,
        "tlp_choices": TLP_CHOICES,
        "pap_choices": PAP_CHOICES,
        "scope_choices": CASE_SCOPE_CHOICES,
        "case_type_choices": CASE_TYPE_CHOICES,
        "data_classification_choices": DATA_CLASSIFICATION_CHOICES,
        "phase_choices": CASE_PHASE_CHOICES,
        "can_classify": can_classify,
    }


def _render_case_create_form(
    request: HttpRequest,
    post_data: Any,
    status_options: Any,
    assignees: Any,
) -> HttpResponse:
    """Render the standalone create form page, pre-filled from the submission on an error re-render."""
    return render(
        request,
        "ui/case_create.html",
        _base_context(
            request,
            **_case_create_form_context(post_data, status_options, assignees, request=request),
        ),
    )


def _render_case_list(
    request: HttpRequest,
    *,
    open_new_case: bool = False,
    post_data: Any = None,
    status_options: Any = None,
    assignees: Any = None,
) -> HttpResponse:
    """Render the case list, optionally with the New-case modal open and pre-filled.

    Only when the user can mutate (not a viewer) do we include the form context;
    viewers must not trigger those user/status queries at all.
    """
    from identity.scoping import case_visible_q, is_viewer

    show_closed = request.GET.get("closed") == "1"
    queryset = Case.objects.select_related("status", "assignee", "owner_org").filter(
        case_visible_q(request.user)
    )
    if not show_closed:
        queryset = queryset.filter(closed_date__isnull=True)

    context = {
        "cases": queryset.order_by("-severity", "-start_date")[:PAGE_SIZE],
        "show_closed": show_closed,
        "open_new_case": open_new_case,
    }

    # Only include form context for users who can actually create cases.
    if not is_viewer(request.user):
        if status_options is None:
            status_options = CaseStatus.objects.all().order_by("order", "value")
        if assignees is None:
            assignees = (
                User.objects.filter(org=request.user.org, is_active=True)
                .select_related("org")
                .order_by("login")[:PAGE_SIZE]
            )
        context.update(
            _case_create_form_context(post_data, status_options, assignees, request=request)
        )

    return render(request, "ui/case_list.html", _base_context(request, **context))


@login_required
@require_GET
def case_list(request: HttpRequest) -> HttpResponse:
    """Open cases by default; closed ones on request.

    The `new=1` query parameter opens the New-case modal (for the `n` shortcut
    and for deep-linking into the modal state).
    """
    return _render_case_list(request, open_new_case=request.GET.get("new") == "1")


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def case_create(request: HttpRequest) -> HttpResponse:
    """§10.2 — Open a case from the UI with no alert behind it.

    The analyst's "phoned in by the helpdesk" path. GET renders the form; POST delegates to
    `cases.create.create_case`, the same service `POST /api/v1/case` uses, so both surfaces share
    the field set, the enum bounds, the org-scoped assignee lookup, extraction and the
    "Case created" ledger entry.

    Uniform 404 for a viewer via `@not_viewer` (ADR-006), before any parsing.
    """
    org = request.user.org

    status_options = CaseStatus.objects.all().order_by("order", "value")
    assignees = (
        User.objects.filter(org=org, is_active=True)
        .select_related("org")
        .order_by("login")[:PAGE_SIZE]
    )

    if request.method == "GET":
        return _render_case_create_form(request, None, status_options, assignees)

    payload: dict[str, Any] = {
        "title": (request.POST.get("title") or "").strip()[:500],
        "description": (request.POST.get("description") or "").strip(),
        "summary": (request.POST.get("summary") or "").strip(),
    }
    for key in ("severity", "tlp", "pap"):
        raw = request.POST.get(key)
        if raw not in (None, ""):
            payload[key] = raw
    # The form always renders the checkbox, so an absent key means "unchecked".
    payload["flag"] = request.POST.get("flag") == "on"

    # Classification fields (PLAN-2026-10-14) — explicit allow-list, never splat request.POST
    for key in ("scope", "case_type", "phase"):
        raw = request.POST.get(key)
        if raw not in (None, ""):
            payload[key] = raw
    # data_classification is admin-only (Q-D5); only include if the actor can classify
    if is_admin(request.user):
        raw = request.POST.get("data_classification")
        if raw not in (None, ""):
            payload["data_classification"] = raw

    # Origin-aware error re-render: if the POST came from the cases list modal
    # (origin=cases), re-render the list with the modal open and values kept.
    # Otherwise (standalone page), re-render the standalone form page.
    # SEC/UX: a modal error must not dump the user onto a different page,
    # or silently close and lose their input.
    def _reject() -> HttpResponse:
        if request.POST.get("origin") == "cases":
            return _render_case_list(
                request,
                open_new_case=True,
                post_data=request.POST,
                status_options=status_options,
                assignees=assignees,
            )
        return _render_case_create_form(request, request.POST, status_options, assignees)

    # Status is bounded to the existing vocabulary so a crafted POST cannot mint one (ADR-002
    # §D11 keeps the shared service tolerant; the form must not be the thing that mints).
    status_val = (request.POST.get("status") or "").strip()
    if status_val:
        if not CaseStatus.objects.filter(value=status_val).exists():
            messages.error(request, f"Unknown case status: {status_val}")
            return _reject()
        payload["status"] = status_val

    assignee_val = (request.POST.get("assignee") or "").strip()
    if assignee_val:
        person = _resolve_login(assignee_val, org=org)
        if person is None:
            messages.error(request, "That assignee is not an active user in your organisation.")
            return _reject()
        payload["assignee"] = str(person.pk)

    tags_val = (request.POST.get("tags") or "").strip()
    if tags_val:
        payload["tags"] = [t.strip() for t in tags_val.split(",") if t.strip()]

    try:
        # The audit row shares the mutation's transaction (SEC-2026-10-11 L2): a failed audit
        # insert must roll the create back rather than leave an unlogged case behind.
        with transaction.atomic():
            case, warnings = create_case(payload, actor=request.user, org=org)
            AuditLog.objects.create(
                actor=request.user,
                actor_org=org,
                target_org=case.owner_org,
                action="case.created",
                target_type="Case",
                target_id=str(case.id),
                ip=request.META.get("REMOTE_ADDR"),
            )
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _reject()

    if warnings:
        messages.warning(request, "Created, with warnings: " + "; ".join(warnings))
    else:
        messages.success(request, f"Case {case.number} created.")
    return _case_detail_redirect(request, case.number)


# Tab allowlist for case_detail — binding is server-side; never interpolated into a template path.
CASE_TABS: tuple[tuple[str, str], ...] = (
    ("general", "General"),
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
    dropped when absent or unknown, so the redirect target is always one of the eight known
    panels — a hostile value can never reach the URL or a template path.
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


@login_required
@require_GET
def case_detail(request: HttpRequest, case_id: str) -> HttpResponse:
    """The ledger: timeline, observables, tasks and automation runs for one case."""
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    is_owner = is_case_owner(request.user, case)
    user_org_id = getattr(request.user, "org_id", None)
    is_cross_org = case.owner_org_id != user_org_id

    # Resolve active tab from query string against the allowlist (unknown/missing -> "general").
    active_tab = request.GET.get("tab")
    if active_tab not in CASE_TAB_SLUGS:
        active_tab = "general"

    # Build tab list with counts where applicable
    alerts_qs = case.alerts.select_related("status", "ingestion_source").order_by("-date")
    tasks_qs = case.tasks.select_related("assignee").order_by("order", "created_at")
    observables_qs = case.case_observables.select_related("observable__data_type").order_by(
        "-created_at"
    )
    attachments_qs = case.attachments.select_related("created_by").order_by("-created_at")
    # Comments tab = the analyst view: notes only, rendered once and not streamed.
    # Audit tab = the full ledger, and the list `live.js` owns the socket for.
    comments_qs = (
        case.timeline_events.filter(kind="comment").select_related("actor").order_by("date", "id")
    )
    timeline_qs = case.timeline_events.select_related("actor").order_by("date", "id")
    audit_logs_qs = AuditLog.objects.filter(target_type="Case", target_id=str(case.id)).order_by(
        "-created_at"
    )
    procedures_count = case.procedures.count()

    tabs = [{"slug": slug, "label": label, "count": None} for slug, label in CASE_TABS]
    # Inject counts for tabs that have them
    counts = {
        "alerts": alerts_qs.count(),
        "tasks": tasks_qs.count(),
        "observables": observables_qs.count(),
        "attachments": attachments_qs.count(),
        "ttp": procedures_count,
        # Counts exactly what the Comments panel lists: analyst notes.
        "comments": comments_qs.count(),
    }
    for tab in tabs:
        if tab["slug"] in counts:
            tab["count"] = counts[tab["slug"]]

    # The edit-case modal (_case_edit_fields.html) renders the same control set as the
    # standalone edit page, so it needs the same context. Derived here from `case` because
    # this is a read view: there is no submission to re-render.
    case_edit_form_values = {
        "title": case.title,
        "description": case.description,
        "summary": case.summary,
        "severity": str(case.severity),
        "tlp": str(case.tlp),
        "pap": str(case.pap),
        "flag": "on" if case.flag else "",
        "status": case.status.value if case.status_id else "",
        "assignee": case.assignee.login if case.assignee_id else "",
        "tags": ", ".join(case.tags.values_list("name", flat=True)),
        "scope": case.scope,
        "case_type": case.case_type,
        "data_classification": case.data_classification,
        "phase": case.phase,
    }

    case_status_options = list(CaseStatus.objects.filter(hidden=False).order_by("order", "value"))

    # Task tab context: uncapped assignee list for the person picker, and status vocabulary
    task_assignees = User.objects.filter(org_q("org", request.user), is_active=True).order_by(
        "login"
    )
    task_status_options = TASK_STATUS_CHOICES

    can_classify = is_admin(request.user)

    return render(
        request,
        "ui/case_detail.html",
        _base_context(
            request,
            case=case,
            timeline=timeline_qs,
            comments=comments_qs,
            observables=observables_qs,
            tasks=tasks_qs,
            pages=case.pages.select_related("created_by", "updated_by").order_by(
                "order", "created_at"
            ),
            runs=AutomationRun.objects.filter(case=case)
            .select_related("playbook", "triggered_by_observable")
            .order_by("-created_at"),
            procedures=case.procedures.select_related("ttp").order_by("occur_date", "id"),
            ttps=TTP.objects.order_by("name"),
            status_choices=case_status_options,
            assignees=User.objects.filter(org_q("org", request.user), is_active=True).order_by(
                "login"
            )[:PAGE_SIZE],
            is_owner=is_owner,
            is_cross_org=is_cross_org,
            attachments=attachments_qs,
            alerts=alerts_qs,
            active_tab=active_tab,
            tabs=tabs,
            audit_logs=audit_logs_qs,
            case_procedure_create_url=reverse(
                "ui-case-procedure-create", kwargs={"case_id": case.number}
            ),
            procedure_create_extra_fields={"tab": "ttp"},
            # Edit-case modal (case_detail.html §"Edit case"): mirror the edit page's context.
            form_values=case_edit_form_values,
            severity_choices=SEVERITY_CHOICES,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
            scope_choices=CASE_SCOPE_CHOICES,
            case_type_choices=CASE_TYPE_CHOICES,
            data_classification_choices=DATA_CLASSIFICATION_CHOICES,
            phase_choices=CASE_PHASE_CHOICES,
            can_classify=can_classify,
            case_status_options=case_status_options,
            case_edit_extra_fields={"tab": active_tab},
            # Observables tab create/edit modals. The per-row Edit gate uses `is_owner` above
            # (`is_case_owner`, so a superAdmin qualifies), matching this view's owner-org check.
            data_type_options=ObservableType.objects.order_by("name"),
            observable_tlp_choices=[c for c in TLP_CHOICES if c[0] <= 3],
            # Task tab create/edit modal
            task_assignees=task_assignees,
            task_status_options=task_status_options,
        ),
    )


@not_viewer
@login_required
@require_POST
def case_set_status(request: HttpRequest, case_id: str) -> HttpResponse:
    """Move a case between stages, stamping `closed_date` on close.

    `closed_date` is derived rather than left to the analyst: every open-case list filters on
    `closed_date IS NULL`, so a case marked `Closed` without one would vanish from the "open" list and
    still appear in the dashboard's open count.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    warnings: list[str] = []
    stage = resolve_case_status(request.POST.get("stage") or "InProgress", warnings=warnings)
    case.status = stage
    if stage.stage == "Closed":
        case.closed_date = case.closed_date or timezone.now()
        case.end_date = case.end_date or case.closed_date
    else:
        case.closed_date = None
    case.save(update_fields=["status", "closed_date", "end_date", "updated_at"])
    append_timeline_event(
        case,
        title=f"Status changed to {stage.value}",
        description="; ".join(warnings),
        kind="status-change",
        actor=request.user,
        metadata={"stage": stage.stage},
    )
    messages.success(request, f"Case {case.number} is now {stage.value}.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_comment(request: HttpRequest, case_id: str) -> HttpResponse:
    """Append a note to the case ledger and record it as a first-class `Comment`.

    Two response shapes, one write. With `HX-Request` the browser already has the timeline on
    screen, so the rendered `<li>` comes back for HTMX to append — no reload, and the WebSocket
    clients see the same entry through the publish the write performs. Without HTMX the POST
    redirects exactly as it did before: a non-JS client has no swap to feed, and a bare 200 would
    strand it on a blank page (brief 6).

    Since T2 P2 the note is *also* a `Comment` row: the ledger `TimelineEvent` is what the timeline
    and the WebSocket render, while the `Comment` is the TheHive-parity entity the API's
    `GET /case/{id}/comment` returns. Both land in one transaction so a note can never be visible in
    one view of the case but not the other.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    body = (request.POST.get("body") or "").strip()
    if not body:
        # A 400, not a redirect: HTMX only swaps 2xx, so the flash error below would have nowhere
        # to land and the analyst would see a silent no-op.
        if request.headers.get("HX-Request"):
            return HttpResponse("A note needs a body.", status=400)
        messages.error(request, "A note needs a body.")
        return _case_detail_redirect(request, case.number)
    with transaction.atomic():
        Comment.objects.create(case=case, message=body, created_by=request.user)
        event = append_timeline_event(
            case, title="Note added", description=body[:10000], kind="comment", actor=request.user
        )
    if request.headers.get("HX-Request"):
        # No flash on this path: it would render on the *next* full page load, announcing a note
        # that is already on screen.
        return render(request, "ui/_timeline_entry.html", {"event": event})
    messages.success(request, "Note added to the case timeline.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_task_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Add a task. An unrecognised assignee is left unassigned rather than auto-created (ADR-002 §D11).

    Extended to accept `description`, `due_date`, `flag`, and a validated `status` (D-1 fix:
    reject any status not in `TASK_STATUS_CHOICES`). On HTMX error, re-renders the modal body
    with inline errors; otherwise flashes and redirects.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    title = (request.POST.get("title") or "").strip()
    if not title:
        errors = {"title": "A task needs a title."}
        return _render_task_form_error(request, case, errors, request.POST)

    # Validate status against the enum (D-1 fix)
    status_val = (request.POST.get("status") or "Waiting").strip()
    valid_statuses = {s for s, _ in TASK_STATUS_CHOICES}
    if status_val not in valid_statuses:
        errors = {"status": f"Invalid status: {status_val}"}
        return _render_task_form_error(request, case, errors, request.POST)

    # Parse due_date (YYYY-MM-DD -> end-of-day aware datetime)
    due_date_raw = (request.POST.get("due_date") or "").strip()
    due_date = _parse_due_date(due_date_raw) if due_date_raw else None
    if due_date_raw and due_date is None:
        errors = {"due_date": "Invalid date format. Use YYYY-MM-DD."}
        return _render_task_form_error(request, case, errors, request.POST)

    # Resolve assignee by login (org-scoped; unknown -> unassigned)
    login = (request.POST.get("assignee") or "").strip()
    assignee = (
        User.objects.filter(org_q("org", request.user), login=login).first() if login else None
    )

    # Flag checkbox: "on" when checked, absent when unchecked
    flag = request.POST.get("flag") == "on"

    description = (request.POST.get("description") or "").strip()

    task = Task.objects.create(
        case=case,
        title=title[:500],
        description=description,
        status=status_val,
        assignee=assignee,
        due_date=due_date,
        flag=flag,
    )

    # Publish `task` frame for live list refresh (mirrors `case_page_create`)
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "task", {"event": task_json(task)})
    )

    if request.headers.get("HX-Request"):
        # A successful modal POST: navigate back to the case (tab preserved) so the dialog closes
        # and the flash shows. A bare `204` would strand the open modal on screen.
        return _htmx_redirect(_case_detail_redirect(request, case.number).url)

    messages.success(request, "Task added.")
    return _case_detail_redirect(request, case.number)


def _render_task_form_error(
    request: HttpRequest,
    case: Case,
    errors: dict[str, str],
    post_data: Any,
    *,
    task: Task | None = None,
) -> HttpResponse:
    """Re-render the task form modal body with errors and submitted values (for HTMX).

    On non-HTMX POST, falls back to flash + redirect (preserves legacy behaviour).
    If `task` is provided, the template is in edit mode and receives the task for prefill.
    """
    if request.headers.get("HX-Request"):
        # Build form_values dict mirroring what the template expects
        form_values = {
            "title": post_data.get("title", ""),
            "description": post_data.get("description", ""),
            "due_date": post_data.get("due_date", ""),
            "assignee": post_data.get("assignee", ""),
            "status": post_data.get("status", "Waiting"),
            "flag": "on" if post_data.get("flag") == "on" else "",
        }
        # The error body replaces the *existing* modal body (`hx-target="#<modal_id>-body"`), so it
        # must reproduce the ids the full-page render used: every control references the footer form
        # via `form="{{ form_id }}"`, and the labels key off `modal_id`. Both are derived from the
        # path (create vs. edit) and never from the request, so a hostile POST cannot inject an id.
        if task is not None:
            modal_id = f"task-edit-{task.id}"
            form_id = f"task-edit-form-{task.id}"
        else:
            modal_id = "task-create-modal"
            form_id = "task-create-form"
        return render(
            request,
            "ui/_task_form_modal_body.html",
            {
                "errors": errors,
                "form_values": form_values,
                "task_status_options": TASK_STATUS_CHOICES,
                # Same uncapped, org-scoped list the tab and detail page pass; without it the
                # re-rendered assignee <select> would drop every option but "Unassigned".
                "task_assignees": User.objects.filter(
                    org_q("org", request.user), is_active=True
                ).order_by("login"),
                "case": case,
                "is_edit": task is not None,
                "task": task,
                "modal_id": modal_id,
                "form_id": form_id,
            },
            status=200,
        )
    # Non-HTMX fallback
    for msg in errors.values():
        messages.error(request, msg)
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_task_edit(request: HttpRequest, task_id: str) -> HttpResponse:
    """Edit a task (title, description, status, assignee, due_date, flag).

    Resolves the task through its case (org-scoped via `resolve_task`). Same validation
    and error handling as `case_task_create`. Publishes a `task` frame on success.
    """
    task = resolve_task(request.user, task_id, write=True)
    if task is None:
        raise Http404()
    case = task.case

    title = (request.POST.get("title") or "").strip()
    if not title:
        errors = {"title": "A task needs a title."}
        return _render_task_form_error(request, case, errors, request.POST, task=task)

    status_val = (request.POST.get("status") or "Waiting").strip()
    valid_statuses = {s for s, _ in TASK_STATUS_CHOICES}
    if status_val not in valid_statuses:
        errors = {"status": f"Invalid status: {status_val}"}
        return _render_task_form_error(request, case, errors, request.POST, task=task)

    due_date_raw = (request.POST.get("due_date") or "").strip()
    due_date = _parse_due_date(due_date_raw) if due_date_raw else None
    if due_date_raw and due_date is None:
        errors = {"due_date": "Invalid date format. Use YYYY-MM-DD."}
        return _render_task_form_error(request, case, errors, request.POST, task=task)

    login = (request.POST.get("assignee") or "").strip()
    assignee = (
        User.objects.filter(org_q("org", request.user), login=login).first() if login else None
    )

    flag = request.POST.get("flag") == "on"
    description = (request.POST.get("description") or "").strip()

    task.title = title[:500]
    task.description = description
    task.status = status_val
    task.assignee = assignee
    task.due_date = due_date
    task.flag = flag
    task.save(
        update_fields=[
            "title",
            "description",
            "status",
            "assignee",
            "due_date",
            "flag",
            "updated_at",
        ]
    )

    transaction.on_commit(
        partial(publish_case_event, str(case.id), "task", {"event": task_json(task)})
    )

    if request.headers.get("HX-Request"):
        return _htmx_redirect(_case_detail_redirect(request, case.number).url)

    messages.success(request, "Task updated.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_task_delete(request: HttpRequest, task_id: str) -> HttpResponse:
    """Delete a task (scoped to the user's org via `resolve_task`).

    Resolves and records the case ID *before* deletion (since `task.case` becomes
    inaccessible after). Publishes a `task` frame, then redirects to the case detail
    preserving `?tab=tasks`.
    """
    task = resolve_task(request.user, task_id, write=True)
    if task is None:
        raise Http404()
    case = task.case
    case_number = case.number
    case_id_str = str(case.id)

    task.delete()

    transaction.on_commit(
        partial(
            publish_case_event,
            case_id_str,
            "task",
            {"event": {"id": task_id, "deleted": True}},
        )
    )

    messages.success(request, "Task deleted.")
    return _case_detail_redirect(request, case_number)


@not_viewer
@login_required
@require_POST
def case_task_toggle(request: HttpRequest, case_id: str, task_id: str) -> HttpResponse:
    """Flip a task between `Waiting` and `Completed`."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    task = get_object_or_404(Task, pk=task_id, case=case)
    task.status = "Waiting" if task.status == "Completed" else "Completed"
    task.save(update_fields=["status", "updated_at"])

    # Publish `task` frame for live list refresh
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "task", {"event": task_json(task)})
    )

    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def task_detail(request: HttpRequest, task_id: str) -> HttpResponse:
    """Task detail page: all fields + notes log + add-note composer.

    Viewer-readable (no `@not_viewer`). Resolves the task via `resolve_task` (read
    access through case visibility). Renders `ui/task_detail.html`.
    """
    task = resolve_task(request.user, task_id, write=False)
    if task is None:
        raise Http404()
    case = task.case
    can_mutate = not is_viewer(request.user)

    notes = task.notes.select_related("created_by").order_by("created_at")

    return render(
        request,
        "ui/task_detail.html",
        _base_context(
            request,
            task=task,
            case=case,
            notes=notes,
            can_mutate=can_mutate,
            task_status_options=TASK_STATUS_CHOICES,
            # The edit modal's assignee picker needs the same uncapped, org-scoped list the tab
            # passes; without it the <select> would render empty and a save would clear the
            # assignee. Same query as `case_detail`'s `task_assignees`.
            task_assignees=User.objects.filter(org_q("org", request.user), is_active=True).order_by(
                "login"
            ),
        ),
    )


@not_viewer
@login_required
@require_POST
def task_note(request: HttpRequest, task_id: str) -> HttpResponse:
    """Append a note to a task.

    Validates non-empty body. In one transaction: creates `TaskNote` (+ `created_by`)
    and mirrors into the case ledger via `append_timeline_event(kind="task-note")`.
    On HTMX: returns 204 with `HX-Redirect` to the task detail URL (avoids depending
    on a template the frontend owns). On non-HTMX: redirects with flash.
    """
    task = resolve_task(request.user, task_id, write=True)
    if task is None:
        raise Http404()
    case = task.case

    body = (request.POST.get("body") or "").strip()
    if not body:
        if request.headers.get("HX-Request"):
            return HttpResponse("A note needs a body.", status=400)
        messages.error(request, "A note needs a body.")
        return redirect("ui-task-detail", task_id=task.id)

    with transaction.atomic():
        note = TaskNote.objects.create(task=task, message=body, created_by=request.user)
        append_timeline_event(
            case,
            title="Task note",
            description=body[:10000],
            kind="task-note",
            actor=request.user,
            metadata={"task_id": str(task.id), "note_id": str(note.id)},
        )

    task_detail_url = reverse("ui-task-detail", kwargs={"task_id": task.id})
    if request.headers.get("HX-Request"):
        # Reuse the shared 204+HX-Redirect helper so the note composer behaves like the task modals.
        return _htmx_redirect(task_detail_url)

    messages.success(request, "Note added.")
    return redirect("ui-task-detail", task_id=task.id)


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


@not_viewer
@login_required
@require_POST
def case_extract_observables(request: HttpRequest, case_id: str) -> HttpResponse:
    """Re-run extraction over the case text, firing automation for anything newly linked.

    The hand-pressable version of the Module D loop. Extraction dispatches `observable.created` only
    for a *new* `CaseObservable` link, so pressing this repeatedly is safe and a press after editing
    the description picks up what was missed.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    with transaction.atomic():
        found = extract_into_case(case, case.title, case.description)
    messages.success(
        request,
        f"Extracted {len(found)} new artifact(s); automation dispatched for each new link.",
    )
    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def automation_list(request: HttpRequest) -> HttpResponse:
    """Every automation run, newest first: the Module D ledger across all cases (org-scoped)."""
    from identity.scoping import case_visible_q

    visible_cases = Case.objects.filter(case_visible_q(request.user))
    return render(
        request,
        "ui/automation_list.html",
        _base_context(
            request,
            runs=AutomationRun.objects.select_related("case", "playbook", "triggered_by_observable")
            .filter(case__in=visible_cases)
            .order_by("-created_at")[:PAGE_SIZE],
            playbooks=AutomationRun.objects.filter(case__in=visible_cases)
            .values("playbook_name")
            .annotate(n=Count("id"))
            .order_by("playbook_name"),
        ),
    )


@login_required
@require_GET
def sources_list(request: HttpRequest) -> HttpResponse:
    """Configured feeds and how many alerts each has produced (org-scoped)."""
    from django.db.models import Exists, OuterRef, Q

    from identity.models import IngestionToken
    from identity.scoping import org_q

    user_org_id = getattr(request.user, "org_id", None)
    if user_org_id is None:
        alert_filter = Q(pk__in=[])
    else:
        alert_filter = Q(alerts__owner_org_id=user_org_id)

    # A source is "protected" if it has at least one non-revoked IngestionToken.
    # This replaces the legacy webhook_secret_hash check, since the receiver
    # now authenticates via IngestionToken rows.
    token_subquery = IngestionToken.objects.filter(
        source=OuterRef("pk"),
        revoked_at__isnull=True,
    )

    from ingest.mapping import read_rules

    sources = list(
        # ADR-009: org-owned, so the list is scoped like every other tenant row (superAdmin: all).
        IngestionSource.objects.filter(org_q("organisation", request.user))
        .annotate(
            alert_count=Count("alerts", filter=alert_filter),
            has_token=Exists(token_subquery),
        )
        .order_by("slug")
    )
    for source in sources:
        # Display-only attributes: "7 rules · severity←severity_name" (§7). The count is the honest
        # "how much is configured", and the severity rule is the one an analyst checks first.
        rules = read_rules(source.mapping_config)
        source.rule_count = len(rules)  # type: ignore[attr-defined]
        severity_rule = rules.get("severity") or {}
        source.severity_rule = (
            severity_rule.get("transform")
            or (  # type: ignore[attr-defined]
                (severity_rule.get("paths") or [None])[0]
            )
        )

    from identity.scoping import is_admin

    return render(
        request,
        "ui/sources_list.html",
        _base_context(
            request,
            sources=sources,
            # The detail page is admin-only, so a viewer/analyst gets plain text rather than a link
            # that would 404 (§7: "admin for own-org sources, everyone else never").
            can_open_source_page=is_admin(request.user),
        ),
    )


@login_required
@require_GET
def playbook_list(request: HttpRequest) -> HttpResponse:
    """List all playbooks with their trigger, action, and last run status."""
    return render(
        request,
        "ui/playbook_list.html",
        _base_context(
            request,
            playbooks=Playbook.objects.prefetch_related("runs").order_by("name"),
        ),
    )


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def playbook_create(request: HttpRequest) -> HttpResponse:
    """Create a new playbook. GET shows the form, POST creates it."""
    # GET: any authenticated org member; POST: requires analyst or above
    if request.method == "POST":
        if request.user.role not in ("analyst", "admin", "superAdmin"):
            return render(request, "ui/404.html", status=404)

    from automation.dispatcher import TRIGGER_EVENTS
    from automation.executor import registered_actions
    from automation.playbooks import ACTIONS, HTTP_METHODS

    if request.method == "GET":
        return render(
            request,
            "ui/playbook_form.html",
            _base_context(
                request,
                trigger_events=TRIGGER_EVENTS,
                actions=ACTIONS,
                http_methods=HTTP_METHODS,
                registered_paths=sorted(registered_actions().keys()),
            ),
        )

    # POST - create the playbook
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-playbook-create")

    if Playbook.objects.filter(name=name[:200]).exists():
        messages.error(request, "A playbook with that name already exists.")
        return redirect("ui-playbook-create")

    trigger_event = (request.POST.get("trigger_event") or "").strip()
    if trigger_event not in TRIGGER_EVENTS:
        messages.error(
            request, f"Invalid trigger event. Must be one of: {', '.join(TRIGGER_EVENTS)}"
        )
        return redirect("ui-playbook-create")

    # Parse and validate config using shared helper
    config, error = _parse_playbook_config(request.POST)
    if error:
        messages.error(request, error)
        return redirect("ui-playbook-create")

    description = (request.POST.get("description") or "").strip()
    is_active = request.POST.get("is_active") == "on"

    Playbook.objects.create(
        name=name[:200],
        description=description,
        trigger_event=trigger_event,
        is_active=is_active,
        config=config,
    )
    messages.success(request, f"Playbook '{name}' created.")
    return redirect("ui-playbook-list")


@not_viewer
@login_required
def playbook_detail(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """View or edit a playbook.

    GET: any authenticated org member.
    POST (update): requires analyst role or above.
    """
    from automation.dispatcher import TRIGGER_EVENTS
    from automation.executor import registered_actions
    from automation.playbooks import ACTIONS, HTTP_METHODS
    from identity.scoping import case_visible_q

    # Resolve by UUID or name
    playbook = None
    try:
        import uuid

        uuid.UUID(playbook_id)
        playbook = get_object_or_404(Playbook, pk=playbook_id)
    except ValueError:
        playbook = get_object_or_404(Playbook, name=playbook_id)

    if request.method == "GET":
        visible_cases = Case.objects.filter(case_visible_q(request.user))
        runs = (
            AutomationRun.objects.filter(playbook=playbook, case__in=visible_cases)
            .select_related("case", "triggered_by_observable")
            .order_by("-created_at")[:50]
        )
        return render(
            request,
            "ui/playbook_detail.html",
            _base_context(
                request,
                playbook=playbook,
                runs=runs,
                trigger_events=sorted(TRIGGER_EVENTS),
                actions=ACTIONS,
                http_methods=HTTP_METHODS,
                registered_paths=sorted(registered_actions().keys()),
            ),
        )

    # POST - update the playbook: requires analyst or above
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    description = (request.POST.get("description") or "").strip()
    trigger_event = (request.POST.get("trigger_event") or "").strip()
    if trigger_event and trigger_event not in TRIGGER_EVENTS:
        messages.error(request, "Invalid trigger event.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    is_active = request.POST.get("is_active") == "on"

    action = (request.POST.get("action") or "").lower()
    if action and action not in ("http", "python"):
        messages.error(request, "Action must be 'http' or 'python'.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    config = dict(playbook.config) if playbook.config else {}
    if action:
        config["action"] = action
    elif "action" in config:
        action = config["action"]

    if action == "http":
        url = request.POST.get("url")
        if url is not None:
            url = url.strip()
            if not url:
                messages.error(request, "URL is required for HTTP actions.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            config["url"] = url

        method = request.POST.get("http_method")
        if method:
            method = method.upper()
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                messages.error(request, "Invalid HTTP method.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            config["method"] = method

        timeout = request.POST.get("timeout_seconds")
        if timeout is not None:
            timeout = timeout.strip()
            if timeout:
                try:
                    timeout_val = float(timeout)
                    if timeout_val <= 0 or timeout_val > 30:
                        raise ValueError
                    config["timeoutSeconds"] = timeout_val
                except ValueError:
                    messages.error(request, "Timeout must be a positive number <= 30.")
                    return redirect("ui-playbook-detail", playbook_id=playbook_id)
            else:
                config.pop("timeoutSeconds", None)

        # Handle headers - this is a simplified version, full form would be more complex
        # For now, we just allow clearing
        if "clear_headers" in request.POST:
            config.pop("headers", None)

        body = request.POST.get("http_body")
        if body is not None:
            config["body"] = body.strip() if body else None

    elif action == "python":
        action_path = request.POST.get("action_path")
        if action_path is not None:
            action_path = action_path.strip()
            if not action_path:
                messages.error(request, "Action path is required for Python actions.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            from automation.executor import registered_actions

            if action_path not in registered_actions():
                messages.error(request, "Unknown action path.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            config["action_path"] = action_path

    playbook.description = description
    if trigger_event:
        playbook.trigger_event = trigger_event
    playbook.is_active = is_active
    playbook.config = config
    playbook.save(
        update_fields=["description", "trigger_event", "is_active", "config", "updated_at"]
    )

    messages.success(request, f"Playbook '{playbook.name}' updated.")
    return redirect("ui-playbook-detail", playbook_id=playbook_id)


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def playbook_edit(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Edit a playbook.

    GET: any authenticated org member.
    POST: requires analyst role or above.
    """
    # Resolve by UUID or name
    playbook = None
    try:
        import uuid

        uuid.UUID(playbook_id)
        playbook = get_object_or_404(Playbook, pk=playbook_id)
    except ValueError:
        playbook = get_object_or_404(Playbook, name=playbook_id)

    if request.method == "GET":
        # GET: any authenticated org member
        config = playbook.config or {}
        return render(
            request,
            "ui/playbook_form.html",
            _base_context(
                request,
                playbook=playbook,
                config=config,
                trigger_events=sorted(TRIGGER_EVENTS),
                actions=ACTIONS,
                http_methods=HTTP_METHODS,
                registered_paths=sorted(registered_actions().keys()),
            ),
        )

    # POST - update the playbook: requires analyst or above
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    # Parse and validate config using shared helper
    config, error = _parse_playbook_config(request.POST)
    if error:
        messages.error(request, error)
        return redirect("ui-playbook-edit", playbook_id=playbook_id)

    description = (request.POST.get("description") or "").strip()
    trigger_event = (request.POST.get("trigger_event") or "").strip()
    if trigger_event and trigger_event not in TRIGGER_EVENTS:
        messages.error(request, "Invalid trigger event.")
        return redirect("ui-playbook-edit", playbook_id=playbook_id)

    is_active = request.POST.get("is_active") == "on"

    playbook.description = description
    if trigger_event:
        playbook.trigger_event = trigger_event
    playbook.is_active = is_active
    playbook.config = config
    playbook.save(
        update_fields=["description", "trigger_event", "is_active", "config", "updated_at"]
    )

    messages.success(request, f"Playbook '{playbook.name}' updated.")
    return redirect("ui-playbook-detail", playbook_id=playbook_id)


@not_viewer
@login_required
@require_POST
def playbook_delete(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Delete a playbook if it has no runs. Requires analyst role or above."""
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    try:
        import uuid

        uuid.UUID(playbook_id)
        playbook = get_object_or_404(Playbook, pk=playbook_id)
    except ValueError:
        playbook = get_object_or_404(Playbook, name=playbook_id)

    if playbook.runs.exists():
        messages.error(request, "Cannot delete playbook: it has associated runs.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    name = playbook.name
    playbook.delete()
    messages.success(request, f"Playbook '{name}' deleted.")
    return redirect("ui-playbook-list")


@not_viewer
@login_required
@require_POST
def playbook_run(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Manually trigger a playbook run. Requires analyst role or above."""
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    from automation.dispatcher import run_now

    try:
        import uuid

        uuid.UUID(playbook_id)
        playbook = get_object_or_404(Playbook, pk=playbook_id)
    except ValueError:
        playbook = get_object_or_404(Playbook, name=playbook_id)

    case_id = (request.POST.get("case") or "").strip()
    observable_id = (request.POST.get("observable") or "").strip()

    if not case_id and not observable_id:
        messages.error(request, "Either a case or an observable is required.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    case = None
    if case_id:
        case = resolve_case(request.user, case_id, write=True)
        if case is None:
            return render(request, "ui/404.html", status=404)

    observable = None
    if observable_id:
        observable = resolve_observable(request.user, observable_id)
        if observable is None:
            return render(request, "ui/404.html", status=404)

    # Ensure observable is linked to case if case not provided
    if case is None and observable is not None:
        link = observable.case_observables.select_related("case").first()
        if link is None:
            messages.error(request, "Observable is not linked to any case.")
            return redirect("ui-playbook-detail", playbook_id=playbook_id)
        case = link.case
        # Verify the resolved case is also accessible (write)
        case = resolve_case(request.user, str(case.id), write=True)
        if case is None:
            return render(request, "ui/404.html", status=404)

    try:
        run = run_now(playbook, case=case, observable=observable)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    messages.success(request, f"Playbook triggered (run {run.id}).")
    return redirect("ui-playbook-detail", playbook_id=playbook_id)


# =============================================================================
# 6.9 — T2 UI catch-up views
# =============================================================================


@login_required
@require_GET
def case_export(request: HttpRequest, case_id: str) -> HttpResponse:
    """Download case export (JSON)."""
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    from cases.views import _case_export_document

    doc = _case_export_document(case)
    import json

    response = HttpResponse(
        json.dumps(doc, indent=2, default=str),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="case-{case.number}-export.json"'
    return response


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


@not_viewer
@login_required
def case_apply_template(request: HttpRequest, case_id: str) -> HttpResponse:
    """Apply a case template to a case."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if request.method == "GET":
        templates = CaseTemplate.objects.all().order_by("name")
        return render(
            request,
            "ui/case_template_apply.html",
            _base_context(request, case=case, templates=templates),
        )
    # POST
    template_id = request.POST.get("template_id")
    if not template_id:
        messages.error(request, "Template is required.")
        return redirect("ui-case-apply-template", case_id=case.number)
    template = get_object_or_404(CaseTemplate, pk=template_id)
    from cases.views import _apply_template

    _apply_template(case, template, actor=request.user)
    messages.success(request, f"Applied template '{template.name}' to case {case.number}.")
    return _case_detail_redirect(request, case.number)


# --- Case Templates ---


@login_required
@require_GET
def case_template_list(request: HttpRequest) -> HttpResponse:
    """List case templates."""
    return render(
        request,
        "ui/case_template_list.html",
        _base_context(
            request,
            templates=CaseTemplate.objects.all().order_by("name"),
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
        ),
    )


def _parse_template_form(request: HttpRequest) -> dict:
    """Parse and validate case template form data. Returns dict of cleaned values or raises ValueError."""
    import json

    name = (request.POST.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if len(name) > 200:
        name = name[:200]

    # Parse tasks JSON
    tasks_raw = (request.POST.get("tasks") or "").strip()
    tasks = []
    if tasks_raw:
        try:
            tasks = json.loads(tasks_raw)
            if not isinstance(tasks, list):
                raise ValueError("Tasks must be a JSON array.")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid tasks JSON: {e}") from e

    # Parse custom_fields JSON
    custom_fields_raw = (request.POST.get("custom_fields") or "").strip()
    custom_fields = []
    if custom_fields_raw:
        try:
            custom_fields = json.loads(custom_fields_raw)
            if not isinstance(custom_fields, list):
                raise ValueError("Custom fields must be a JSON array.")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid custom_fields JSON: {e}") from e

    # flag is a boolean field - checkbox sends "on" when checked
    flag_raw = request.POST.get("flag")
    flag = flag_raw in ("on", "true", "1", True)

    return {
        "name": name,
        "display_name": (request.POST.get("display_name") or "").strip(),
        "title_prefix": (request.POST.get("title_prefix") or "").strip(),
        "description": (request.POST.get("description") or "").strip(),
        "summary": (request.POST.get("summary") or "").strip(),
        "flag": flag,
        "severity": int(request.POST.get("severity") or 2),
        "tlp": int(request.POST.get("tlp") or 2),
        "pap": int(request.POST.get("pap") or 2),
        "tags": [t.strip() for t in (request.POST.get("tags") or "").split(",") if t.strip()],
        "tasks": tasks,
        "custom_fields": custom_fields,
    }


@not_viewer
@login_required
def case_template_create(request: HttpRequest) -> HttpResponse:
    """Create a case template.

    GET: any authenticated user.
    POST: requires analyst role or above (excludes read-only).
    """
    if request.method == "GET":
        return render(request, "ui/case_template_form.html", _base_context(request))

    # POST - requires analyst role or above (mirrors playbook_create gate)
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    try:
        data = _parse_template_form(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-template-create")

    if CaseTemplate.objects.filter(name=data["name"]).exists():
        messages.error(request, "Template with that name already exists.")
        return redirect("ui-case-template-create")

    CaseTemplate.objects.create(**data)
    messages.success(request, f"Template '{data['name']}' created.")
    return redirect("ui-case-template-list")


@not_viewer
@login_required
def case_template_detail(request: HttpRequest, template_id: str) -> HttpResponse:
    """View a case template."""
    try:
        import uuid

        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)

    return render(
        request,
        "ui/case_template_detail.html",
        _base_context(
            request,
            template=template,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
        ),
    )


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def case_template_edit(request: HttpRequest, template_id: str) -> HttpResponse:
    """Edit a case template.

    GET: any authenticated user.
    POST: requires analyst role or above (excludes read-only).
    """
    try:
        import uuid

        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)

    if request.method == "GET":
        return render(
            request,
            "ui/case_template_form.html",
            _base_context(request, template=template, mode="edit"),
        )

    # POST - requires analyst role or above (mirrors playbook_detail update gate)
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    # POST - update
    try:
        data = _parse_template_form(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-template-edit", template_id=template.id)

    # Check name uniqueness (excluding self)
    if CaseTemplate.objects.exclude(pk=template.pk).filter(name=data["name"]).exists():
        messages.error(request, "Template with that name already exists.")
        return redirect("ui-case-template-edit", template_id=template.id)

    for field, value in data.items():
        setattr(template, field, value)
    template.save()

    # Audit the mutation (mirror API audit pattern)
    from identity.models import AuditLog

    AuditLog.objects.create(
        actor=request.user,
        actor_org=getattr(request.user, "org", None),
        target_org=None,  # templates are global
        action="template.updated",
        target_type="CaseTemplate",
        target_id=str(template.id),
        ip=request.META.get("REMOTE_ADDR"),
    )

    messages.success(request, f"Template '{template.name}' updated.")
    return redirect("ui-case-template-detail", template_id=template.id)


@not_viewer
@login_required
@require_POST
def case_template_delete(request: HttpRequest, template_id: str) -> HttpResponse:
    """Delete a case template. Requires analyst role or above (excludes read-only)."""
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    try:
        import uuid

        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)
    name = template.name
    template.delete()
    messages.success(request, f"Template '{name}' deleted.")
    return redirect("ui-case-template-list")


# --- Tags ---


@login_required
@require_GET
def tag_list(request: HttpRequest) -> HttpResponse:
    """List all tags."""
    return render(
        request,
        "ui/tag_list.html",
        _base_context(
            request,
            tags=Tag.objects.all().order_by("name"),
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
        return render(request, "ui/tag_detail.html", _base_context(request, tag=tag))
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


# --- Observables ---


@login_required
@require_GET
def observable_list(request: HttpRequest) -> HttpResponse:
    """Global observables list with cross-case links (org-scoped).

    The filter bar is a plain `GET` form, so its state lives in the query string: `q`
    matches the artifact value (case-insensitive substring) and `type` narrows to one
    `ObservableType` id. Both are optional — an absent or blank filter leaves the list
    whole. A `type` that is not a UUID is dropped rather than handed to the ORM: a
    malformed id would otherwise raise a 500 out of the UUID column, and form-supplied
    identifiers are never trusted anywhere else in the UI.

    `observable_types` feeds the dropdown. It is the full vocabulary (types are global
    taxonomy, not tenant data — ADR-002 §D4), ordered so the options do not reshuffle
    between loads.
    """
    query = (request.GET.get("q") or "").strip()
    type_filter = (request.GET.get("type") or "").strip()
    if type_filter:
        try:
            uuid.UUID(type_filter)
        except ValueError:
            # Malformed id: ignore it and keep the raw value out of the context, so the
            # dropdown falls back to "All types" instead of echoing junk back into the form.
            type_filter = ""

    queryset = (
        Observable.objects.select_related("data_type", "owner_org")
        .prefetch_related("case_observables__case")
        .filter(org_q("owner_org", request.user))
    )
    if query:
        queryset = queryset.filter(data__icontains=query)
    if type_filter:
        queryset = queryset.filter(data_type_id=type_filter)

    return render(
        request,
        "ui/observable_list.html",
        _base_context(
            request,
            observables=queryset.order_by("-created_at")[:PAGE_SIZE],
            observable_types=ObservableType.objects.order_by("name"),
            query=query,
            type_filter=type_filter,
        ),
    )


@login_required
@require_GET
def observable_detail(request: HttpRequest, observable_id: str) -> HttpResponse:
    """Observable detail with cross-case fan-out."""
    observable = resolve_observable(request.user, observable_id)
    if observable is None:
        return render(request, "ui/404.html", status=404)
    user_org_id = getattr(request.user, "org_id", None)
    is_cross_org = observable.owner_org_id != user_org_id
    # P2/C3: blast-radius for delete dialog
    affected_cases = _observable_affected_cases(observable)
    return render(
        request,
        "ui/observable_detail.html",
        _base_context(
            request,
            observable=observable,
            cases=observable.case_observables.select_related("case__owner_org").order_by(
                "-case__start_date"
            ),
            affected_cases=affected_cases,
            is_owner=is_cross_org is False,  # owner org can manage
            is_cross_org=is_cross_org,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
        ),
    )


# =============================================================================
# P2 — Triage and edit flows (C1, C2, C3)
# =============================================================================


@not_viewer
@login_required
@require_POST
def alert_status(request: HttpRequest, alert_id: str) -> HttpResponse:
    """P2/C1 — Change an alert's status from the UI.

    Form field: `status` = an `AlertStatus.value`.
    Validates against the existing stage vocabulary (excludes "Imported", which is
    system-assigned by escalation) so a garbage value cannot auto-create a junk
    status (ADR-002 §D11 tolerance in the API path).
    """
    alert = resolve_alert(request.user, alert_id, write=True)
    if alert is None:
        return render(request, "ui/404.html", status=404)

    value = (request.POST.get("status") or "").strip()
    # Validate against existing vocabulary — never auto-create from form input
    valid_status = (
        AlertStatus.objects.filter(stage__in=ALERT_STAGES)
        .exclude(stage="Imported")
        .filter(value=value)
        .first()
    )
    if not valid_status:
        messages.error(request, f"Invalid status: {value}")
        return redirect("ui-alert-detail", alert_id=alert.id)

    # The mutation, its audit row and the case timeline event share one transaction
    # (SEC-2026-10-11 NEW-2): a failed audit must roll the status change back rather than
    # leave the change persisted but unlogged.
    with transaction.atomic():
        alert.status = valid_status
        alert.save(update_fields=["status", "updated_at"])

        AuditLog.objects.create(
            actor=request.user,
            actor_org=getattr(request.user, "org", None),
            target_org=alert.owner_org,
            action="alert.status_changed",
            target_type="Alert",
            target_id=str(alert.id),
            ip=request.META.get("REMOTE_ADDR"),
        )

        # Timeline event on the case if alert is attached
        if alert.case_id:
            append_timeline_event(
                alert.case,
                title=f"Alert status changed to {value}",
                kind="status-changed",
                actor=request.user,
                metadata={"alert": str(alert.id), "to": value},
            )

    messages.success(request, f"Alert status changed to {value}.")
    return redirect("ui-alert-detail", alert_id=alert.id)


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def case_edit(request: HttpRequest, case_id: str) -> HttpResponse:
    """P2/C2 — Edit a case (title, description, severity, TLP/PAP, flag, summary, status, assignee, tags).

    GET: renders the edit form.
    POST: applies the update via `apply_case_update` (shared with API).
    Write share required (resolve_case with write=True); uniform 404 otherwise.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    # Context values for the form (shared by GET and error re-render)
    status_options = CaseStatus.objects.all().order_by("order", "value")
    assignees = (
        User.objects.filter(org=case.owner_org, is_active=True)
        .select_related("org")
        .order_by("login")[:PAGE_SIZE]
    )

    if request.method == "GET":
        # Derive form_values from the case for initial render
        form_values = {
            "title": case.title,
            "description": case.description,
            "summary": case.summary,
            "severity": str(case.severity),
            "tlp": str(case.tlp),
            "pap": str(case.pap),
            "flag": "on" if case.flag else "",
            "status": case.status.value if case.status_id else "",
            # The select's option values are logins, so prefill the *login* — a prefilled UUID
            # never matches and the browser would submit the first option ("Unassigned"),
            # silently unassigning the case on a save that did not touch the field (SEC-2026-10-11
            # NEW-1).
            "assignee": case.assignee.login if case.assignee_id else "",
            "tags": ", ".join(case.tags.values_list("name", flat=True)),
            "scope": case.scope,
            "case_type": case.case_type,
            "data_classification": case.data_classification,
            "phase": case.phase,
        }
        can_classify = is_admin(request.user)
        return render(
            request,
            "ui/case_edit.html",
            _base_context(
                request,
                case=case,
                form_values=form_values,
                is_cross_org=case.owner_org_id != getattr(request.user, "org_id", None),
                severity_choices=SEVERITY_CHOICES,
                tlp_choices=TLP_CHOICES,
                pap_choices=PAP_CHOICES,
                scope_choices=CASE_SCOPE_CHOICES,
                case_type_choices=CASE_TYPE_CHOICES,
                data_classification_choices=DATA_CLASSIFICATION_CHOICES,
                phase_choices=CASE_PHASE_CHOICES,
                can_classify=can_classify,
                case_status_options=status_options,
                assignees=assignees,
            ),
        )

    # POST — build payload from form
    payload: dict[str, Any] = {
        "title": (request.POST.get("title") or "").strip()[:500],
        "description": (request.POST.get("description") or "").strip(),
        "summary": (request.POST.get("summary") or "").strip(),
    }

    # Integer fields
    for key in ("severity", "tlp", "pap"):
        raw = request.POST.get(key)
        if raw is not None and raw != "":
            try:
                payload[key] = int(raw)
            except ValueError:
                messages.error(request, f"Invalid value for {key}")
                return _render_case_edit_form(
                    request, case, status_options, assignees, request.POST
                )

    # The form always renders the flag checkbox, so an absent key means "unchecked".
    payload["flag"] = request.POST.get("flag") == "on"

    # Classification fields (PLAN-2026-10-14) — explicit allow-list, never splat request.POST
    for key in ("scope", "case_type", "phase"):
        raw = request.POST.get(key)
        if raw not in (None, ""):
            payload[key] = raw
    # data_classification is admin-only (Q-D5); only include if the actor can classify
    if is_admin(request.user):
        raw = request.POST.get("data_classification")
        if raw not in (None, ""):
            payload["data_classification"] = raw

    # Status (CaseStatus.value) — bounded to the existing vocabulary so a crafted POST cannot
    # mint a new status (the shared applier is tolerant by design; ADR-002 §D11).
    status_val = (request.POST.get("status") or "").strip()
    if status_val:
        if not CaseStatus.objects.filter(value=status_val).exists():
            messages.error(request, f"Unknown case status: {status_val}")
            return _render_case_edit_form(request, case, status_options, assignees, request.POST)
        payload["status"] = status_val

    # Assignee (User UUID or login, or "" to unassign). Resolved against the case's owner org
    # (SEC-2026-10-11 M1): a cross-org login/UUID is rejected here with a clear message rather
    # than reaching the applier, which would silently unassign it. Defence in depth — the shared
    # applier is itself org-scoped, so the API cannot be used as a cross-tenant oracle either.
    assignee_val = (request.POST.get("assignee") or "").strip()
    if assignee_val:
        person = User.objects.filter(org=case.owner_org, login=assignee_val).first()
        if person is None:
            pk = _as_uuid(assignee_val)
            person = User.objects.filter(org=case.owner_org, pk=pk).first() if pk else None
        if person is None:
            messages.error(request, "Assignee is not a member of this case's organisation.")
            return _render_case_edit_form(request, case, status_options, assignees, request.POST)
        payload["assignee"] = str(person.pk)
    elif "assignee" in request.POST:
        payload["assignee"] = None

    # Tags (comma-separated string)
    tags_val = (request.POST.get("tags") or "").strip()
    if tags_val:
        payload["tags"] = [t.strip() for t in tags_val.split(",") if t.strip()]

    try:
        # The audit row shares the mutation's transaction (SEC-2026-10-11 L2): a failed audit
        # insert must roll the change back rather than leave an unlogged mutation.
        with transaction.atomic():
            apply_case_update(case, payload, actor=request.user)
            AuditLog.objects.create(
                actor=request.user,
                actor_org=getattr(request.user, "org", None),
                target_org=case.owner_org,
                action="case.updated",
                target_type="Case",
                target_id=str(case.id),
                ip=request.META.get("REMOTE_ADDR"),
            )
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _render_case_edit_form(request, case, status_options, assignees, request.POST)

    messages.success(request, f"Case {case.number} updated.")
    return _case_detail_redirect(request, case.number)


def _render_case_edit_form(
    request: HttpRequest,
    case: Case,
    status_options,
    assignees,
    post_data,
) -> HttpResponse:
    """Re-render the case edit form with submitted values and error message."""
    # `post_data` is a QueryDict; `dict(...)` would yield lists (`{"title": ["x"]}`), which render as
    # `['x']`. Read scalars with `.get()` so the form echoes what was typed (SEC-2026-10-11 N1).
    can_classify = is_admin(request.user)
    fields = (
        "title",
        "description",
        "summary",
        "severity",
        "tlp",
        "pap",
        "status",
        "assignee",
        "tags",
        "scope",
        "case_type",
        "phase",
    )
    form_values = {key: post_data.get(key, "") for key in fields}
    # data_classification is admin-only (Q-D5); only echo back if the actor may classify
    if can_classify:
        form_values["data_classification"] = post_data.get("data_classification", "")
    # For classification fields, if POST doesn't carry them (e.g. non-admin form), fall back to persisted value
    for key in ("scope", "case_type", "data_classification", "phase"):
        if form_values.get(key, "") == "":
            form_values[key] = getattr(case, key)
    # Ensure checkbox renders correctly
    form_values["flag"] = "on" if post_data.get("flag") == "on" else ""
    return render(
        request,
        "ui/case_edit.html",
        _base_context(
            request,
            case=case,
            form_values=form_values,
            is_cross_org=case.owner_org_id != getattr(request.user, "org_id", None),
            severity_choices=SEVERITY_CHOICES,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
            scope_choices=CASE_SCOPE_CHOICES,
            case_type_choices=CASE_TYPE_CHOICES,
            data_classification_choices=DATA_CLASSIFICATION_CHOICES,
            phase_choices=CASE_PHASE_CHOICES,
            can_classify=can_classify,
            case_status_options=status_options,
            assignees=assignees,
        ),
    )


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def observable_edit(request: HttpRequest, observable_id: str) -> HttpResponse:
    """P2/C3 — Edit an observable's editable fields (type, message, TLP/PAP, flags, enrichment).

    The value (`data`) is the per-org dedupe key and stays immutable on both surfaces; the
    editable set is applied through `_set_observable_fields` so normalisation, validation and
    the wire shape match the API exactly (P2 C3).
    """
    observable = resolve_observable(request.user, observable_id, write=True)
    if observable is None:
        return render(request, "ui/404.html", status=404)

    common = {
        "observable": observable,
        "cases": observable.case_observables.select_related("case__owner_org").order_by(
            "-case__start_date"
        ),
        "affected_cases": _observable_affected_cases(observable),
        "is_owner": observable.owner_org_id == getattr(request.user, "org_id", None),
        "is_cross_org": observable.owner_org_id != getattr(request.user, "org_id", None),
        "data_type_options": ObservableType.objects.order_by("name"),
        # `cases.views._set_observable_fields` bounds observable TLP to 0..3 while
        # `TLP_CHOICES` also carries 4/Unknown (valid on Case/Alert) — offering it here would
        # only produce a validation error, so the observable form lists the accepted range.
        "tlp_choices": [choice for choice in TLP_CHOICES if choice[0] <= 3],
        "pap_choices": PAP_CHOICES,
        "edit_mode": True,
    }

    if request.method == "GET":
        import json

        form_values = {
            "data": observable.data,
            "dataType": observable.data_type.name,
            "message": observable.message,
            "tlp": str(observable.tlp),
            "pap": str(observable.pap),
            "ioc": "on" if observable.ioc else "",
            "sighted": "on" if observable.sighted else "",
            "ignoreSimilarity": "on" if observable.ignore_similarity else "",
            "enrichment_data": json.dumps(observable.enrichment_data or {}, indent=2),
        }
        return render(
            request,
            "ui/observable_detail.html",
            _base_context(request, form_values=form_values, **common),
        )

    # POST — build the update payload in the applier's wire shape (`_set_observable_fields`
    # accepts camelCase keys and deliberately excludes `data`: the value is the per-org
    # dedupe key, so it is immutable from both surfaces — see P2/C3 in the wave plan).
    # A shared observable needs the same acknowledgement the API's `?force=true` demands.
    force = (request.POST.get("force") or "").lower() in ("on", "1", "true", "yes")
    guard = _observable_cross_case_guard(observable, force)
    if guard is not None:
        messages.error(request, guard.data.get("message", "This observable is shared."))
        return _render_observable_edit_form(request, observable, request.POST)

    payload: dict[str, Any] = {}
    if "message" in request.POST:
        payload["message"] = (request.POST.get("message") or "").strip()
    if "dataType" in request.POST and (request.POST.get("dataType") or "").strip():
        payload["dataType"] = request.POST["dataType"].strip()

    for key in ("tlp", "pap"):
        raw = request.POST.get(key)
        if raw is not None and raw != "":
            try:
                payload[key] = int(raw)
            except ValueError:
                messages.error(request, f"Invalid value for {key}")
                return _render_observable_edit_form(request, observable, request.POST)

    for key in ("ioc", "sighted", "ignoreSimilarity"):
        if key in request.POST:
            payload[key] = request.POST.get(key) == "on"

    # `enrichment_data` is a plain JSON column (not part of the dedupe key), so the UI applies
    # it directly rather than widening the shared applier's contract.
    enrichment_touched = "enrichment_data" in request.POST
    if enrichment_touched:
        raw_json = (request.POST.get("enrichment_data") or "").strip()
        if raw_json:
            import json

            try:
                parsed_enrichment = json.loads(raw_json)
            except json.JSONDecodeError:
                messages.error(request, "Enrichment data is not valid JSON.")
                return _render_observable_edit_form(request, observable, request.POST)
        else:
            parsed_enrichment = {}
        if not isinstance(parsed_enrichment, dict):
            messages.error(request, "Enrichment data must be a JSON object.")
            return _render_observable_edit_form(request, observable, request.POST)

    fields = _set_observable_fields(observable, payload)
    if isinstance(fields, Response):
        messages.error(request, fields.data.get("message", "Validation failed"))
        return _render_observable_edit_form(request, observable, request.POST)
    if enrichment_touched:
        observable.enrichment_data = parsed_enrichment
        fields = [*fields, "enrichment_data"]
    if not fields:
        messages.error(request, "No updatable field supplied.")
        return _render_observable_edit_form(request, observable, request.POST)

    # The audit row shares the mutation's transaction (SEC-2026-10-11 L2): a failed audit
    # insert rolls the change back rather than leaving an unlogged mutation.
    with transaction.atomic():
        observable.save(update_fields=[*fields, "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            actor_org=getattr(request.user, "org", None),
            target_org=observable.owner_org,
            action="observable.updated",
            target_type="Observable",
            target_id=str(observable.id),
            ip=request.META.get("REMOTE_ADDR"),
        )

    messages.success(request, "Observable updated.")
    return redirect("ui-observable-detail", observable_id=observable.id)


def _render_observable_edit_form(
    request: HttpRequest, observable: Observable, post_data: Any
) -> HttpResponse:
    """Re-render the observable edit form with the submitted values and a message."""
    # `post_data` is a QueryDict; `dict(...)` yields lists — read scalars with `.get()` so the
    # form echoes what was typed (SEC-2026-10-11 N1).
    fields = ("data", "dataType", "message", "tlp", "pap", "enrichment_data")
    form_values = {key: post_data.get(key, "") for key in fields}
    for key in ("ioc", "sighted", "ignoreSimilarity"):
        form_values[key] = "on" if post_data.get(key) == "on" else ""
    return render(
        request,
        "ui/observable_detail.html",
        _base_context(
            request,
            observable=observable,
            cases=observable.case_observables.select_related("case__owner_org").order_by(
                "-case__start_date"
            ),
            affected_cases=_observable_affected_cases(observable),
            is_owner=observable.owner_org_id == getattr(request.user, "org_id", None),
            is_cross_org=observable.owner_org_id != getattr(request.user, "org_id", None),
            data_type_options=ObservableType.objects.order_by("name"),
            form_values=form_values,
            tlp_choices=[choice for choice in TLP_CHOICES if choice[0] <= 3],
            pap_choices=PAP_CHOICES,
            edit_mode=True,
        ),
    )


@not_viewer
@login_required
@require_POST
def observable_delete(request: HttpRequest, observable_id: str) -> HttpResponse:
    """P2/C3 — Delete an observable, honouring the cross-case blast-radius guard.

    Reads `force` from POST data. If the observable is linked to multiple cases
    and `force` is not set, refuses with a message and redirects back.
    """
    observable = resolve_observable(request.user, observable_id, write=True)
    if observable is None:
        return render(request, "ui/404.html", status=404)

    force = (request.POST.get("force") or "").lower() in ("on", "1", "true", "yes")
    guard = _observable_cross_case_guard(observable, force, action="deletion")
    if guard is not None:
        # Translate the API 409 into a UI message + redirect
        messages.error(request, guard.data.get("message", "This observable is shared."))
        return redirect("ui-observable-detail", observable_id=observable.id)

    # Capture before delete(): Django nulls the pk on the instance, which would otherwise
    # log target_id="None" and re-query a detached FK.
    observable_pk = str(observable.id)
    target_org = observable.owner_org
    with transaction.atomic():
        observable.delete()
        AuditLog.objects.create(
            actor=request.user,
            actor_org=getattr(request.user, "org", None),
            target_org=target_org,
            action="observable.deleted",
            target_type="Observable",
            target_id=observable_pk,
            ip=request.META.get("REMOTE_ADDR"),
        )

    messages.success(request, "Observable deleted.")
    return redirect("ui-observable-list")


# --- Case-scoped observable CRUD (tab affordances) ---


@not_viewer
@login_required
@require_POST
def case_observable_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Add a manually supplied artifact to a case from the Observables tab.

    Mirrors the API's `POST /case/{id}/observable` but accepts a plain form POST so the
    browser gets a redirect + message. Delegates to `add_observable_with_status` (the same
    service the API's `add_observable` wraps) so normalization, dedupe, linking and automation
    dispatch are identical.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    type_name = (request.POST.get("dataType") or "").strip()
    value = (request.POST.get("data") or "").strip()
    if not value:
        messages.error(request, "An artifact needs a value.")
        return _case_detail_redirect(request, case.number)

    with transaction.atomic():
        resolved = add_observable_with_status(case, type_name, value)
    if resolved is None:
        messages.error(request, f"Unknown artifact type: {type_name!r}")
        return _case_detail_redirect(request, case.number)
    observable, created = resolved

    # The optional fields are applied ONLY when the artifact row is brand new. A known value is
    # deduped per org (`resolve_observable`) and may already be shared by other cases, so re-adding
    # it here must link it and stop — applying the form's defaults would reset the message/TLP/PAP/
    # enrichment the other cases rely on, and would do so without the edit route's force guard.
    if not created:
        messages.success(request, "Artifact already known — linked to this case.")
        return _case_detail_redirect(request, case.number)

    # Optional fields: message, tlp, pap, ioc, sighted, ignoreSimilarity, enrichment_data
    payload: dict[str, Any] = {}
    if "message" in request.POST:
        payload["message"] = (request.POST.get("message") or "").strip()
    if "tlp" in request.POST and request.POST.get("tlp") != "":
        try:
            payload["tlp"] = int(request.POST.get("tlp"))
        except ValueError:
            messages.error(request, "Invalid value for tlp")
            return _case_detail_redirect(request, case.number)
    if "pap" in request.POST and request.POST.get("pap") != "":
        try:
            payload["pap"] = int(request.POST.get("pap"))
        except ValueError:
            messages.error(request, "Invalid value for pap")
            return _case_detail_redirect(request, case.number)
    for key in ("ioc", "sighted", "ignoreSimilarity"):
        if key in request.POST:
            payload[key] = request.POST.get(key) == "on"

    enrichment_touched = "enrichment_data" in request.POST
    parsed_enrichment = None
    if enrichment_touched:
        raw_json = (request.POST.get("enrichment_data") or "").strip()
        if raw_json:
            import json

            try:
                parsed_enrichment = json.loads(raw_json)
            except json.JSONDecodeError:
                messages.error(request, "Enrichment data is not valid JSON.")
                return _case_detail_redirect(request, case.number)
        else:
            parsed_enrichment = {}
        if not isinstance(parsed_enrichment, dict):
            messages.error(request, "Enrichment data must be a JSON object.")
            return _case_detail_redirect(request, case.number)

    fields = _set_observable_fields(observable, payload)
    if isinstance(fields, Response):
        messages.error(request, fields.data.get("message", "Validation failed"))
        return _case_detail_redirect(request, case.number)
    if enrichment_touched:
        observable.enrichment_data = parsed_enrichment
        fields = [*fields, "enrichment_data"]
    if fields:
        observable.save(update_fields=[*fields, "updated_at"])

    messages.success(request, "Artifact added.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_observable_update(
    request: HttpRequest, case_id: str, observable_id: uuid.UUID
) -> HttpResponse:
    """Edit an artifact's editable fields from the Observables tab.

    The observable is reached *through* the case (CaseObservable link), so a foreign id 404s.
    Edit is owner-org-only (parity with the standalone detail page): a share-org writer may add/
    remove but not edit another org's artifact.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    link = (
        CaseObservable.objects.filter(case=case, observable_id=observable_id)
        .select_related("observable")
        .first()
    )
    if link is None:
        raise Http404()
    observable = link.observable

    # Owner-org-only edit (Q5): share-org writers can add/remove but not edit. `is_case_owner`
    # is the app's own predicate (case owner org, or a `superAdmin` role) — not Django's
    # `is_superuser` flag, which this product never sets, so using it would 404 every superAdmin.
    # An observable is always created with its case's `owner_org` (`add_observable`), so the case
    # owner and the observable owner are the same org here.
    if not is_case_owner(request.user, case):
        raise Http404()

    force = (request.POST.get("force") or "").lower() in ("on", "1", "true", "yes")
    guard = _observable_cross_case_guard(observable, force)
    if guard is not None:
        messages.error(request, guard.data.get("message", "This observable is shared."))
        return _case_detail_redirect(request, case.number)

    # Build payload exactly like observable_edit (data is immutable)
    payload: dict[str, Any] = {}
    if "message" in request.POST:
        payload["message"] = (request.POST.get("message") or "").strip()
    if "dataType" in request.POST and (request.POST.get("dataType") or "").strip():
        payload["dataType"] = request.POST["dataType"].strip()

    for key in ("tlp", "pap"):
        raw = request.POST.get(key)
        if raw is not None and raw != "":
            try:
                payload[key] = int(raw)
            except ValueError:
                messages.error(request, f"Invalid value for {key}")
                return _case_detail_redirect(request, case.number)

    for key in ("ioc", "sighted", "ignoreSimilarity"):
        if key in request.POST:
            payload[key] = request.POST.get(key) == "on"

    enrichment_touched = "enrichment_data" in request.POST
    parsed_enrichment = None
    if enrichment_touched:
        raw_json = (request.POST.get("enrichment_data") or "").strip()
        if raw_json:
            import json

            try:
                parsed_enrichment = json.loads(raw_json)
            except json.JSONDecodeError:
                messages.error(request, "Enrichment data is not valid JSON.")
                return _case_detail_redirect(request, case.number)
        else:
            parsed_enrichment = {}
        if not isinstance(parsed_enrichment, dict):
            messages.error(request, "Enrichment data must be a JSON object.")
            return _case_detail_redirect(request, case.number)

    fields = _set_observable_fields(observable, payload)
    if isinstance(fields, Response):
        messages.error(request, fields.data.get("message", "Validation failed"))
        return _case_detail_redirect(request, case.number)
    if enrichment_touched:
        observable.enrichment_data = parsed_enrichment
        fields = [*fields, "enrichment_data"]
    if not fields:
        messages.error(request, "No updatable field supplied.")
        return _case_detail_redirect(request, case.number)

    with transaction.atomic():
        observable.save(update_fields=[*fields, "updated_at"])

    messages.success(request, "Observable updated.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_observable_remove(
    request: HttpRequest, case_id: str, observable_id: uuid.UUID
) -> HttpResponse:
    """Remove an artifact from a case (non-destructive unlink, Q1).

    Deletes the CaseObservable link only; the Observable survives on its other cases.
    Any case writer may unlink (Q5); no force guard (unlinking is a case-membership op).
    Emits a ledger event (R3).
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    link = (
        CaseObservable.objects.filter(case=case, observable_id=observable_id)
        .select_related("observable")
        .first()
    )
    if link is None:
        raise Http404()

    observable = link.observable

    with transaction.atomic():
        link.delete()
        append_timeline_event(
            case,
            title=f"Artifact removed: {observable.data_type.name} {observable.data}",
            kind="artifact-removed",
            actor=request.user,
            metadata={"observable_id": str(observable.id)},
        )

    messages.success(request, "Artifact removed from case.")
    return _case_detail_redirect(request, case.number)


# --- Case share management (P7) ---


@not_viewer
@login_required
def case_share_list(request: HttpRequest, case_id: str) -> HttpResponse:
    """List and manage case shares (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if not is_case_owner(request.user, case):
        return render(request, "ui/404.html", status=404)

    from cases.models import Share
    from identity.models import Organisation

    shares_qs = case.shares.select_related("organisation").order_by("created_at")
    available_orgs = Organisation.objects.exclude(
        id__in=shares_qs.values_list("organisation_id", flat=True)
    ).exclude(id=case.owner_org_id)

    if request.method == "POST":
        org_id = request.POST.get("organisation")
        permissions = request.POST.get("permissions", "read")
        if org_id:
            # A tampered/blank id must not raise out of the query compiler (F7 class).
            org = resolve_organisation(org_id)
            if org is None:
                messages.error(request, "Select a valid organisation.")
                return redirect("ui-case-share-list", case_id=case.number)
            share, created = Share.objects.update_or_create(
                case=case,
                organisation=org,
                defaults={"permissions": {"read": True, "write": permissions == "readwrite"}},
            )
            messages.success(
                request, f"Share {'created' if created else 'updated'} for {org.name}."
            )
        return redirect("ui-case-share-list", case_id=case.number)

    # Build share data with revoke URLs
    shares = []
    for share in shares_qs:
        shares.append(
            {
                "share": share,
                "revoke_url": reverse(
                    "ui-case-share-revoke", kwargs={"case_id": case.number, "share_id": share.id}
                ),
            }
        )

    return render(
        request,
        "ui/case_share_list.html",
        _base_context(request, case=case, shares=shares, available_orgs=available_orgs),
    )


@not_viewer
@login_required
@require_POST
def case_share_revoke(request: HttpRequest, case_id: str, share_id: str) -> HttpResponse:
    """Revoke a case share (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if not is_case_owner(request.user, case):
        return render(request, "ui/404.html", status=404)

    from cases.models import Share

    share = get_object_or_404(Share, pk=share_id, case=case)
    org_name = share.organisation.name
    share.delete()
    messages.success(request, f"Revoked share for {org_name}.")
    return redirect("ui-case-share-list", case_id=case.number)


# --- Taxonomy ---


@login_required
@require_GET
def taxonomy(request: HttpRequest) -> HttpResponse:
    """Aggregate taxonomy page for UI pickers.

    ``Tag`` and ``CaseTemplate`` are imported at module level from ``cases.models`` — there
    is no ``tags`` app. (Commit 3255f1f added a local ``from tags.models import Tag`` here,
    which made ``/taxonomy`` raise ``ModuleNotFoundError`` → HTTP 500.)
    """
    from alerts.models import AlertStatus
    from cases.models import CaseStatus
    from core.enums import (
        CASE_PHASE_CHOICES,
        CASE_SCOPE_CHOICES,
        CASE_TYPE_CHOICES,
        DATA_CLASSIFICATION_CHOICES,
    )
    from observables.models import ObservableType

    return render(
        request,
        "ui/taxonomy.html",
        _base_context(
            request,
            case_statuses=CaseStatus.objects.filter(hidden=False).order_by("order", "value"),
            alert_statuses=AlertStatus.objects.filter(hidden=False).order_by("order", "value"),
            observable_types=ObservableType.objects.all().order_by("name"),
            tags=Tag.objects.all().order_by("name"),
            scope_choices=CASE_SCOPE_CHOICES,
            phase_choices=CASE_PHASE_CHOICES,
            case_type_choices=CASE_TYPE_CHOICES,
            data_classification_choices=DATA_CLASSIFICATION_CHOICES,
        ),
    )


@not_viewer
@login_required
def case_merge(request: HttpRequest, case_id: str) -> HttpResponse:
    """Merge cases via UI."""
    from cases.merge import merge_cases

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if request.method == "GET":
        # Show merge form with candidate cases
        candidates = (
            Case.objects.filter(case_visible_q(request.user))
            .filter(closed_date__isnull=True)
            .exclude(pk=case.pk)
            .order_by("-start_date")
        )
        return render(
            request, "ui/case_merge.html", _base_context(request, case=case, candidates=candidates)
        )
    # POST
    target_id = request.POST.get("target_case")
    if not target_id:
        messages.error(request, "Target case is required.")
        return redirect("ui-case-merge", case_id=case.number)
    target = resolve_case(request.user, target_id, write=True)
    if target is None:
        return render(request, "ui/404.html", status=404)
    try:
        with transaction.atomic():
            merged = merge_cases([target, case], actor=request.user)
        messages.success(request, f"Merged case {case.number} into {merged.number}.")
        return _case_detail_redirect(request, merged.number)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-merge", case_id=case.number)


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


# =============================================================================
# O4 — Settings UI (superAdmin only)
# =============================================================================


def _require_superadmin_ui(request: HttpRequest) -> HttpResponse | None:
    """Return a 404 response if the user is not a superAdmin, else None."""
    if getattr(request.user, "role", "") != "superAdmin":
        return render(request, "ui/404.html", status=404)
    return None


def _require_admin_ui(request: HttpRequest) -> HttpResponse | None:
    """Return a 404 response if the user is not an admin or superAdmin, else None.

    Used for Settings sub-pages accessible to both admin and superAdmin roles.
    """
    from identity.scoping import is_admin

    if not is_admin(request.user):
        return render(request, "ui/404.html", status=404)
    return None


def _org_blockers_text(org: object) -> str:
    """Human summary of the tenant data that PROTECTs an org from deletion.

    Rendered server-side into the delete modal so the warning never depends on JS
    building HTML from server data (F3/F10).
    """
    parts: list[str] = []
    for count, singular in (
        (getattr(org, "cases_count", 0), "case"),
        (getattr(org, "alerts_count", 0), "alert"),
        (getattr(org, "observables_count", 0), "observable"),
        (getattr(org, "tokens_count", 0), "ingestion token"),
        (getattr(org, "sources_count", 0), "ingestion source"),
    ):
        if count:
            parts.append(f"{count} {singular}{'' if count == 1 else 's'}")
    return ", ".join(parts)


@not_viewer
@login_required
def settings_organisations(request: HttpRequest) -> HttpResponse:
    """Organisations list + create (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:200]
        description = (request.POST.get("description") or "").strip()
        if not name:
            messages.error(request, "Name is required.")
        elif Organisation.objects.filter(name=name).exists():
            messages.error(request, "An organisation with that name already exists.")
        else:
            from django.db import IntegrityError

            try:
                org = Organisation.objects.create(name=name, description=description)
            except IntegrityError:
                # Truncation can make two input names collide after the pre-check (§F7).
                messages.error(request, "An organisation with that name already exists.")
            else:
                from identity.audit import record_organisation_change

                record_organisation_change(
                    request,
                    actor=request.user,
                    action="organisation.created",
                    target_org=org,
                    target_id=str(org.id),
                )
                messages.success(request, f"Organisation '{org.name}' created.")
                return redirect("ui-settings-organisations")

    from django.db.models import Count

    orgs = list(
        Organisation.objects.annotate(
            members_count=Count("users", distinct=True),
            cases_count=Count("cases", distinct=True),
            alerts_count=Count("alerts", distinct=True),
            observables_count=Count("observables", distinct=True),
            tokens_count=Count("ingestion_tokens", distinct=True),
            # ADR-009: an owned IngestionSource PROTECTs its org.
            sources_count=Count("ingestion_sources", distinct=True),
        ).order_by("name")
    )
    for org in orgs:
        # Dynamic, template-only display attributes (not model fields).
        org.blockers_text = _org_blockers_text(org)  # type: ignore[attr-defined]
        org.is_blocked = bool(org.blockers_text)  # type: ignore[attr-defined]

    return render(
        request,
        "ui/settings_organisations.html",
        _base_context(request, organisations=orgs),
    )


@not_viewer
@login_required
def settings_organisations_edit(request: HttpRequest, org_id: str) -> HttpResponse:
    """Edit an organisation (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    org = get_object_or_404(Organisation, pk=org_id)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:200]
        description = (request.POST.get("description") or "").strip()
        if not name:
            messages.error(request, "Name is required.")
        elif Organisation.objects.exclude(pk=org.pk).filter(name=name).exists():
            messages.error(request, "An organisation with that name already exists.")
        else:
            from django.db import IntegrityError

            org.name = name
            org.description = description
            try:
                org.save(update_fields=["name", "description", "updated_at"])
            except IntegrityError:
                messages.error(request, "An organisation with that name already exists.")
            else:
                from identity.audit import record_organisation_change

                record_organisation_change(
                    request,
                    actor=request.user,
                    action="organisation.updated",
                    target_org=org,
                    target_id=str(org.id),
                )
                messages.success(request, f"Organisation '{org.name}' updated.")
                return redirect("ui-settings-organisations")

    return render(
        request,
        "ui/settings_organisation_form.html",
        _base_context(request, org=org, mode="edit"),
    )


@not_viewer
@login_required
@require_POST
def settings_organisations_delete(request: HttpRequest, org_id: str) -> HttpResponse:
    """Delete an organisation (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    org = get_object_or_404(Organisation, pk=org_id)
    members_action = request.POST.get("members")
    target_org_id = request.POST.get("target_org")
    # Malformed ids resolve to None instead of raising out of the query compiler (F7).
    target_org = resolve_organisation(target_org_id)

    # Mirror the API's up-front validation: a reassign must name a real, different
    # organisation, otherwise members would be orphaned (or reassigned to itself).
    if members_action == "reassign" and (target_org is None or target_org.pk == org.pk):
        messages.error(request, "Select a valid target organisation to reassign members to.")
        return redirect("ui-settings-organisations")

    from identity.services import OrganisationDeleteBlockedError, delete_organisation

    try:
        delete_organisation(
            org,
            members=members_action,
            target=target_org,
            actor=request.user,
            request=request,
        )
        messages.success(request, "Organisation deleted.")
    except OrganisationDeleteBlockedError as e:
        if e.reason == "self_lockout":
            messages.error(
                request,
                "Cannot delete your own account by deleting an organisation. "
                "Reassign members instead.",
            )
        elif e.reason == "last_superadmin":
            messages.error(
                request,
                "Cannot delete the last active superAdmin. Reassign members instead.",
            )
        elif e.reason == "invalid_target":
            messages.error(request, "Select a valid target organisation to reassign members to.")
        elif e.members is not None and not e.blockers.get("race"):
            messages.error(request, "Organisation has members. Choose reassign or delete.")
        else:
            blockers_text = ", ".join(f"{k}: {v}" for k, v in e.blockers.items() if v)
            messages.error(
                request, f"Cannot delete: organisation owns tenant data ({blockers_text})."
            )
        return redirect("ui-settings-organisations")

    return redirect("ui-settings-organisations")


@not_viewer
@login_required
def settings_users(request: HttpRequest) -> HttpResponse:
    """Users list + create (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    from core.enums import USER_ROLE_VALUES

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        role = request.POST.get("role") or "analyst"
        org_id = request.POST.get("org")
        is_active = request.POST.get("is_active") == "on"

        errors = {}
        if not username:
            errors["username"] = "required"
        if not email:
            errors["email"] = "required"
        if not password:
            errors["password"] = "required"  # noqa: S105
        if role not in USER_ROLE_VALUES:
            errors["role"] = f"must be one of {', '.join(USER_ROLE_VALUES)}"
        if not org_id:
            errors["org"] = "required"

        org = resolve_organisation(org_id) if org_id else None
        if org_id and org is None:
            errors["org"] = "organisation not found"

        if User.objects.filter(username=username).exists():
            errors.setdefault("username", "already exists")
        if User.objects.filter(login=username).exists():
            errors.setdefault("username", "already exists")

        if errors:
            for field, msg in errors.items():
                messages.error(request, f"{field}: {msg}")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError

            user = User(username=username, email=email, role=role, org=org, is_active=is_active)
            try:
                validate_password(password, user=user)
            except ValidationError as e:
                for msg in e.messages:
                    messages.error(request, f"password: {msg}")
            else:
                user.set_password(password)
                user.save()
                from identity.audit import record_user_change

                record_user_change(
                    request,
                    actor=request.user,
                    action="user.created",
                    target_id=str(user.id),
                    target_org=org,
                )
                messages.success(request, f"User '{user.login}' created.")
                return redirect("ui-settings-users")

    users = User.objects.select_related("org").order_by("username")
    orgs = Organisation.objects.order_by("name")

    return render(
        request,
        "ui/settings_users.html",
        _base_context(request, users=users, orgs=orgs, role_choices=USER_ROLE_VALUES),
    )


@not_viewer
@login_required
@serializes_superadmin_changes
def settings_users_edit(request: HttpRequest, user_id: str) -> HttpResponse:
    """Edit a user (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    user = get_object_or_404(User, pk=user_id)

    from core.enums import USER_ROLE_VALUES

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        role = request.POST.get("role")
        org_id = request.POST.get("org")
        is_active = request.POST.get("is_active") == "on"
        password = request.POST.get("password") or None

        org = resolve_organisation(org_id) if org_id else None

        errors = {}
        if role and role not in USER_ROLE_VALUES:
            errors["role"] = f"must be one of {', '.join(USER_ROLE_VALUES)}"
        # An org can be reassigned but never cleared. Absent ⇒ leave unchanged
        # (partial posts such as a password reset must keep working); present-but-empty
        # is an explicit attempt to clear it and is rejected.
        if "org" in request.POST and not org_id:
            errors["org"] = "required"
        if org_id and org is None:
            errors["org"] = "organisation not found"

        if errors:
            for field, msg in errors.items():
                messages.error(request, f"{field}: {msg}")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError

            changed = False
            if user.email != email:
                user.email = email
                changed = True
            if role and user.role != role:
                # Guard: cannot change your own role away from superAdmin
                if (
                    user.pk == request.user.pk
                    and request.user.role == "superAdmin"
                    and role != "superAdmin"
                ):
                    messages.error(request, "Cannot change your own role away from superAdmin.")
                    return redirect("ui-settings-users-edit", user_id=user_id)
                # Guard: cannot demote the last active superAdmin
                if user.role == "superAdmin" and role != "superAdmin":
                    active_superadmins = User.objects.filter(
                        role="superAdmin", is_active=True
                    ).count()
                    if active_superadmins == 1:
                        messages.error(request, "Cannot demote the last active superAdmin.")
                        return redirect("ui-settings-users-edit", user_id=user_id)
                user.role = role
                changed = True
            if org and user.org_id != org.id:
                user.org = org
                changed = True
            if user.is_active != is_active:
                # Guard: cannot deactivate yourself as superAdmin
                if (
                    user.pk == request.user.pk
                    and request.user.role == "superAdmin"
                    and not is_active
                ):
                    messages.error(request, "Cannot deactivate yourself as superAdmin.")
                    return redirect("ui-settings-users-edit", user_id=user_id)
                # Guard: cannot deactivate the last active superAdmin
                if user.role == "superAdmin" and user.is_active and not is_active:
                    active_superadmins = User.objects.filter(
                        role="superAdmin", is_active=True
                    ).count()
                    if active_superadmins == 1:
                        messages.error(request, "Cannot deactivate the last active superAdmin.")
                        return redirect("ui-settings-users-edit", user_id=user_id)
                user.is_active = is_active
                changed = True
            if password:
                try:
                    validate_password(password, user=user)
                except ValidationError as e:
                    for msg in e.messages:
                        messages.error(request, f"password: {msg}")
                    return redirect("ui-settings-users-edit", user_id=user_id)
                user.set_password(password)
                changed = True

            if changed:
                user.save()
                from identity.audit import record_user_change

                record_user_change(
                    request,
                    actor=request.user,
                    action="user.updated",
                    target_id=str(user.id),
                    target_org=user.org,
                )
                messages.success(request, f"User '{user.login}' updated.")
            else:
                messages.info(request, "No changes made.")
            return redirect("ui-settings-users")

    # GET: render the edit form
    orgs = Organisation.objects.order_by("name")
    return render(
        request,
        "ui/settings_user_form.html",
        _base_context(
            request, user_obj=user, orgs=orgs, role_choices=USER_ROLE_VALUES, mode="edit"
        ),
    )


@not_viewer
@login_required
@require_POST
@serializes_superadmin_changes
def settings_users_delete(request: HttpRequest, user_id: str) -> HttpResponse:
    """Delete a user (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    user = get_object_or_404(User, pk=user_id)

    # Guard: cannot delete yourself
    if user.pk == request.user.pk:
        messages.error(request, "Cannot delete yourself.")
        return redirect("ui-settings-users")

    # Guard: cannot delete the last active superAdmin
    if user.role == "superAdmin" and user.is_active:
        active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
        if active_superadmins == 1:
            messages.error(request, "Cannot delete the last active superAdmin.")
            return redirect("ui-settings-users")

    from identity.services import delete_user

    delete_user(user, actor=request.user, request=request)
    messages.success(request, f"User '{user.login}' deleted.")
    return redirect("ui-settings-users")


# =============================================================================
# Settings — Sources (admin + superAdmin)
# =============================================================================


@not_viewer
@login_required
def settings_home(request: HttpRequest) -> HttpResponse:
    """Settings entry: redirect to first submenu item."""
    from django.shortcuts import redirect

    guard = _require_admin_ui(request)
    if guard:
        return guard

    if getattr(request.user, "role", "") == "superAdmin":
        return redirect("ui-settings-organisations")
    return redirect("ui-settings-sources")


def _settings_sources_context(request: HttpRequest) -> dict:
    """Page context for Sources: the token list plus the create-modal lookups.

    Shared by the GET list and by `settings_token_create`'s show-once re-render, so the modal the
    operator just submitted still finds its datalist (existing feed names) and — for a superAdmin —
    its organisation options when the page comes back.
    """
    is_superadmin = request.user.role == "superAdmin"
    from identity.models import IngestionToken

    qs = (
        IngestionToken.objects.select_related("source", "organisation", "created_by")
        .defer("secret_hash")
        .order_by("-created_at")
    )
    if not is_superadmin:
        qs = qs.filter(organisation_id=request.user.org_id)

    from identity.scoping import org_q
    from ingest.models import IngestionSource

    return {
        "tokens": list(qs),
        "show_org_column": is_superadmin,
        # ADR-009: only the org's own feeds are nameable here (a superAdmin sees all and picks the
        # org, which is then the org the source is provisioned in).
        "sources": IngestionSource.objects.filter(org_q("organisation", request.user)).order_by(
            "name"
        ),
        "organisations": Organisation.objects.order_by("name") if is_superadmin else None,
        "is_superadmin": is_superadmin,
        # The endpoint is source-less: every feed posts to the same URL and the token does the routing.
        "webhook_path": "/api/v1/alerts/webhook/",
    }


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def settings_source_detail(request: HttpRequest, source_id) -> HttpResponse:
    """One feed: identity, its editable rules, and the stored config (§7, P2 shell + P3 editor).

    P2 landed the read-only half — identity, rule summary, tokens, stored JSON — so the tenancy gate
    (role **and** org) was tested before a write path existed. P3 adds the two writers over the same
    object: the table (one row per target) and the advanced JSON box, which is the only place
    `value_maps` is edited (plan Q3). Neither writes anything else: every other top-level key survives
    a save verbatim (`ingest/editor.py` rule 1).

    Uniform 404 for "no such source" and "another org's source" (`resolve_ingestion_source`), and the
    cross-org *read* by a superAdmin is audited (ADR-004 Q5) — a mapping is a tenant's data, so
    peeking at another tenant's rules is a break-glass read, not a routine one. A superAdmin's
    cross-org *write* is not a separate action: the `ingestion_source.updated` row already carries both
    `actor_org` and `target_org`, so the crossover is visible where the change is recorded.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    from identity.scoping import resolve_ingestion_source

    # `write=True` on POST layers the viewer deny over the role gate above (ADR-006 Layer 1/2); the
    # page itself is already unreachable for a viewer, so this is depth, not the boundary.
    source = resolve_ingestion_source(request.user, source_id, write=request.method == "POST")
    if source is None:
        return render(request, "ui/404.html", status=404)

    if request.method == "POST":
        return _source_mapping_post(request, source)

    from identity.audit import maybe_record_cross_org_read

    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=source,
        target_type="IngestionSource",
        target_id=source.id,
        target_org_field="organisation",
    )

    return render(
        request,
        "ui/source_detail.html",
        _base_context(request, **_source_detail_context(request, source)),
    )


def _source_detail_context(
    request: HttpRequest,
    source,
    *,
    submitted: dict[str, dict[str, str]] | None = None,
    errors: dict[str, tuple[str, ...]] | None = None,
    failed_form: str = "",
    json_text: str | None = None,
    apply_pending: object | None = None,
    pending_config_json: str | None = None,
) -> dict:
    """Everything the source page renders, for both the GET and a rejected POST.

    One builder for both, so a form that re-renders after an error cannot drift from the page the
    operator was looking at: `submitted` replaces the stored config in the table's rows and `errors`
    lands on the row that caused it (`ingest/editor.py`).

    `apply_pending` (a `RemapSummary`) is the confirmation dialog's half of the same page: it is set by
    the `intent=apply` POST, which is the only render in which the dialog exists at all — so a page
    reached by GET can never show a confirm control for a write nobody asked for.
    """
    from ingest.editor import TARGET_LABELS, editor_rows, transforms_for_editor
    from ingest.mapping import config_shape, read_rules
    from ingest.remap import MAX_REMAP_ALERTS, REMAP_FIELDS

    errors = errors or {}
    return {
        "source": source,
        # `legacy` is the one shape the page must explain rather than silently upgrade (§7 rule 4).
        "config_shape": config_shape(source.mapping_config),
        "editor_rows": editor_rows(source.mapping_config, submitted=submitted, errors=errors),
        "editor_errors": errors,
        # The whole-config messages of whichever form was submitted: `validate_config` has no row to
        # attach `value_maps` (or a JSON parse failure) to, so they are shown above it.
        "config_errors": errors.get("", ()),
        "failed_form": failed_form,
        "mapping_json_text": (
            json_text
            if json_text is not None
            else json.dumps(source.mapping_config or {}, indent=2, sort_keys=True)
        ),
        "transforms": transforms_for_editor(),
        "rule_count": len(read_rules(source.mapping_config)),
        "tokens": source.tokens.select_related("organisation", "created_by")
        .defer("secret_hash")
        .order_by("-created_at"),
        "alert_count": Alert.objects.filter(ingestion_source=source).count(),
        "is_superadmin": request.user.role == "superAdmin",
        # --- P7: the "apply to existing alerts" confirmation dialog -------------------------------
        "apply_pending": apply_pending,
        "apply_confirm_fields": (
            {"intent": "apply_confirmed", "pending_config": pending_config_json or "{}"}
            if apply_pending is not None
            else None
        ),
        # Over the cap the dialog still opens — but its confirm control is disabled (server-side) and
        # nothing is written. A "save & apply" that quietly drops the apply half is the worse failure.
        "apply_confirm_disabled": bool(apply_pending is not None and apply_pending.over_cap),
        "apply_fields": [(target, TARGET_LABELS.get(target, target)) for target in REMAP_FIELDS],
        # Rules for targets a re-map never rewrites (`type`, `source_ref` — MAP-9). The dialog names them,
        # because "I added a ref mapping and nothing changed" is otherwise a puzzle: the rule is real, and
        # it only ever applies to alerts ingested *after* it was saved.
        #
        # Read from the **candidate** config, not the stored one: the dialog exists precisely for rules
        # that have not been saved yet, so a disclosure computed from the stored config would never fire
        # on the one render where it is needed.
        "apply_ignored_rules": [
            (target, TARGET_LABELS.get(target, target), tuple(rule.get("paths") or ()))
            for target, rule in read_rules(
                json.loads(pending_config_json) if pending_config_json else source.mapping_config
            ).items()
            if target not in REMAP_FIELDS
        ],
        "remap_cap": MAX_REMAP_ALERTS,
    }


def _source_mapping_rows(request: HttpRequest) -> dict[str, dict[str, str]]:
    """The table's submission as `{target: {paths, transform, default}}` (`ingest/editor.py`)."""
    from ingest.mapping import TARGETS

    return {
        target: {
            "paths": request.POST.get(f"paths__{target}", ""),
            "transform": request.POST.get(f"transform__{target}", ""),
            "default": request.POST.get(f"default__{target}", ""),
        }
        for target in TARGETS
    }


def _source_mapping_post(request: HttpRequest, source) -> HttpResponse:
    """Route one POST to the mapping page to the right half of it (P3 save, P7 apply).

    `intent` is the discriminator, and its default is the *old* behaviour: the Save buttons carry no
    `intent` at all, so every existing submission lands on the save path unchanged.
    """
    intent = request.POST.get("intent") or "save"
    if intent == "apply":
        # Validates and *counts*, then opens the confirmation dialog. Writes nothing at all.
        return _confirm_mapping_apply(request, source)
    if intent == "apply_confirmed":
        return _apply_mapping_to_alerts(request, source)
    return _save_source_mapping(request, source)


def _submitted_mapping(request: HttpRequest, before: dict) -> tuple[str, dict | None, object]:
    """Read whichever form was posted into a validated `EditorResult`.

    `form=table|json` says which writer spoke; the table is rebuilt *from* the stored config so an
    unsubmitted row keeps its rule, and the JSON box is parsed as-is (§7.4).
    """
    from ingest.editor import build_table_config, parse_json_config

    which = "json" if request.POST.get("form") == "json" else "table"
    submitted = None if which == "json" else _source_mapping_rows(request)
    if which == "json":
        result = parse_json_config(request.POST.get("mapping_json"))
    else:
        result = build_table_config(before, submitted)
    return which, submitted, result


def _reject_mapping(
    request: HttpRequest,
    source,
    *,
    which: str,
    submitted: dict | None,
    result: object,
    json_text: str | None = None,
) -> HttpResponse:
    """The only rejection response: a 200 that re-renders the page *with the operator's input*.

    A redirect would discard what they typed, which is the entire value of per-row errors (AC11/AC20).
    """
    return render(
        request,
        "ui/source_detail.html",
        _base_context(
            request,
            **_source_detail_context(
                request,
                source,
                submitted=submitted,
                errors=result.errors,
                failed_form=which,
                json_text=json_text,
            ),
        ),
    )


def _persist_mapping(request: HttpRequest, source, before: dict, config: dict) -> None:
    """Write the config and record the edit. The **caller** owns the transaction (P7 shares it)."""
    from identity.audit import record_ingestion_source_updated
    from ingest.editor import changed_keys

    source.mapping_config = config
    # `updated_at` is `auto_now`: its value is set on save, but an `update_fields` UPDATE writes only the
    # columns it names, so the timestamp has to be named (AC12 reads it).
    source.save(update_fields=["mapping_config", "updated_at"])
    record_ingestion_source_updated(
        request,
        actor=request.user,
        source=source,
        changed_keys=changed_keys(before, config),
    )


def _mapping_saved_message(request: HttpRequest, config: dict) -> None:
    rules = len(config.get("fields") or {}) if isinstance(config, dict) else 0
    if rules:
        messages.success(request, f"Mapping saved — {rules} rule{'' if rules == 1 else 's'}.")
    else:
        messages.success(request, "Mapping saved — no rules; payload literal keys are used.")


def _save_source_mapping(request: HttpRequest, source) -> HttpResponse:
    """Persist one edit of a feed's `mapping_config` (P3, AC11-AC14).

    Two writers, one object: the table and the advanced JSON box are separate `<form>`s, so a request
    carries exactly one of them (`form=table|json`) and the other half of the page keeps rendering the
    stored config — "one storage object, two views of it, never two writers" (plan §7.4).

    A rejected save is a **200 that echoes the input**; success is PRG; and an unchanged submission is a
    no-op: no row write, no `updated_at` bump, no audit (AC12).
    """
    from django.db import transaction

    from ingest.editor import is_noop_save

    before = source.mapping_config or {}
    which, submitted, result = _submitted_mapping(request, before)
    if not result.ok:
        return _reject_mapping(
            request,
            source,
            which=which,
            submitted=submitted,
            result=result,
            json_text=request.POST.get("mapping_json") if which == "json" else None,
        )

    if is_noop_save(before, result.config):
        messages.info(request, "No changes to save.")
        return redirect("ui-settings-source-detail", source_id=source.id)

    with transaction.atomic():
        _persist_mapping(request, source, before, result.config)

    _mapping_saved_message(request, result.config)
    return redirect("ui-settings-source-detail", source_id=source.id)


def _confirm_mapping_apply(request: HttpRequest, source) -> HttpResponse:
    """`intent=apply` — validate the submitted mapping, count the candidates, open the dialog (P7, AC20).

    The whole point is what this does **not** do: no config write, no alert write, no audit row. The
    operator asked to apply a mapping; they have not yet agreed to rewrite anything, so the page comes
    back with the exact numbers and the exact field list and the *confirm* control carries the validated
    config (a hidden `pending_config`) — not the table, which cannot be re-read across two requests.

    The numbers come from a **dry pass over the real alerts** (`preview_alerts`), not from the rule table:
    "19 alerts, these fields" is true and useless when a rule cannot read the payload, so each target is
    reported with what it resolves and what it would change (P7.1, AC24). Reading is the whole cost; the
    candidates are capped (`MAX_REMAP_ALERTS`), so this cannot walk an unbounded set.
    """
    from ingest.remap import preview_alerts

    before = source.mapping_config or {}
    which, submitted, result = _submitted_mapping(request, before)
    if not result.ok:
        return _reject_mapping(
            request,
            source,
            which=which,
            submitted=submitted,
            result=result,
            json_text=request.POST.get("mapping_json") if which == "json" else None,
        )

    return render(
        request,
        "ui/source_detail.html",
        _base_context(
            request,
            **_source_detail_context(
                request,
                source,
                # The page keeps rendering what is *stored* — this POST saved nothing — while the dialog
                # carries the candidate config. Two different objects, deliberately not conflated.
                apply_pending=preview_alerts(source, result.config),
                pending_config_json=json.dumps(result.config, sort_keys=True),
            ),
        ),
    )


def _apply_mapping_to_alerts(request: HttpRequest, source) -> HttpResponse:
    """`intent=apply_confirmed` — save the mapping and re-map the feed's unlinked open alerts (P7).

    One transaction for both halves (AC22): the rules that produced the new values and the values
    themselves commit together, so a feed can never end up stored one way and applied the other. The
    config arrives back from the client and is therefore **re-validated** here — the same check the save
    path runs, so the client cannot smuggle a config past the editor.

    The result is reported in full, because "unchanged" is ambiguous: a row can keep its values and still
    have needed a write (its `mapping` record was stale), and a *rule* can have produced nothing at all —
    which is the only honest explanation for an operator whose new severity rule changed no severity
    (P7.1, AC25).
    """
    from django.db import transaction

    from identity.audit import record_ingestion_source_mapping_applied
    from ingest.editor import changed_keys, is_noop_save, parse_json_config
    from ingest.remap import MAX_REMAP_ALERTS, remap_alerts, summarise_candidates

    pending = request.POST.get("pending_config")
    result = parse_json_config(pending)
    if not result.ok:
        # Tampered or hand-edited between the two steps: same treatment as any rejected submission.
        return _reject_mapping(
            request, source, which="json", submitted=None, result=result, json_text=pending
        )

    summary = summarise_candidates(source)
    if summary.over_cap:
        # Only reachable when the candidate count grew between rendering the dialog and confirming it
        # (the dialog disables its own confirm control over the cap). Refuse rather than write a partial
        # pass, and let the operator see the new count by pressing Apply again.
        messages.error(
            request,
            f"Not applied: this feed now has {summary.candidates} unlinked open alerts, over the "
            f"{MAX_REMAP_ALERTS}-per-request limit. Narrow the feed or use the API.",
        )
        return redirect("ui-settings-source-detail", source_id=source.id)

    before = source.mapping_config or {}
    with transaction.atomic():
        if not is_noop_save(before, result.config):
            _persist_mapping(request, source, before, result.config)
        counts = remap_alerts(source, result.config)
        record_ingestion_source_mapping_applied(
            request,
            actor=request.user,
            source=source,
            changed_keys=changed_keys(before, result.config),
            candidates=counts.candidates,
            updated=counts.updated,
            unchanged=counts.unchanged,
            refreshed=counts.refreshed,
        )

    _mapping_saved_message(request, result.config)
    if counts.candidates:
        messages.success(
            request,
            f"Re-applied to {counts.candidates} unlinked open alert"
            f"{'' if counts.candidates == 1 else 's'}: {counts.updated} updated, "
            f"{counts.unchanged} unchanged.",
        )
        if counts.touched:
            messages.info(request, f"Fields written: {', '.join(counts.touched)}.")
        if counts.refreshed:
            messages.info(
                request,
                f"Rule state refreshed on {counts.refreshed} alert"
                f"{'' if counts.refreshed == 1 else 's'} with no field values changed.",
            )
        for line in _unresolved_lines(counts.unresolved, counts.candidates):
            messages.warning(request, line)
    else:
        messages.info(request, "No unlinked open alerts to re-map.")
    return redirect("ui-settings-source-detail", source_id=source.id)


def _unresolved_lines(unresolved: dict[str, dict[str, int]], candidates: int) -> list[str]:
    """One line per target whose rule produced no value, for the flash message (P7.1, AC25).

    The reason is named rather than counted, because it is the fix: `bad_type` on a 0-100 vendor score
    means "you need the `severity_score` transform", and that is not something a bare "0 updated" can
    ever say. Targets are listed in rule order and reasons alphabetically, so the message is stable
    enough to assert on.
    """
    lines = []
    for target in sorted(unresolved):
        reasons = unresolved[target]
        count = max(reasons.values())
        named = ", ".join(f"{reason}" for reason in sorted(reasons))
        lines.append(
            f"{target}: the mapping produced no value on {count} of {candidates} "
            f"alert{'' if candidates == 1 else 's'} ({named}) — those alerts keep what they have."
        )
    return lines


@not_viewer
@login_required
def settings_sources(request: HttpRequest) -> HttpResponse:
    """Token list for ingestion sources (admin + superAdmin)."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    return render(
        request,
        "ui/settings_sources.html",
        _base_context(request, **_settings_sources_context(request)),
    )


@not_viewer
@login_required
@require_POST
def settings_token_create(request: HttpRequest) -> HttpResponse:
    """Mint a new ingestion token (show-once).

    Success is a 200 that re-renders the Sources list with the show-once dialog open (never a
    redirect: the plaintext must not survive in a URL, a session or a cache). Validation failures
    redirect back with a message.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    is_superadmin = request.user.role == "superAdmin"

    # Resolve organisation first: the purely-request fields are validated before anything is
    # provisioned, so a rejected submission cannot leave a stray global `IngestionSource` behind.
    if is_superadmin:
        org_id = request.POST.get("organisation")
        if not org_id:
            messages.error(request, "Organisation is required.")
            return redirect("ui-settings-sources")
        try:
            organisation = Organisation.objects.get(pk=org_id)
        except Organisation.DoesNotExist:
            messages.error(request, "Organisation not found.")
            return redirect("ui-settings-sources")
    else:
        organisation = request.user.org

    # Validate name
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-settings-sources")
    if len(name) > 200:
        messages.error(request, "Name must be 200 characters or fewer.")
        return redirect("ui-settings-sources")

    # Resolve the source by *name*, within the token's organisation. The webhook URL is source-less,
    # so the source is not a routing key — it is the org-owned mapping profile this token points at
    # (ADR-009, which reverses ADR-008 §4's fleet-global profile). A name is therefore all the
    # operator supplies, and the row is provisioned on demand in **their** org: `slugify` gives it a
    # stable key, so an existing name in the same org reuses that profile, while the same name in
    # another org is that org's own feed with its own rules (AC10). Scoping the lookup by org is also
    # what keeps the pair legal: an unscoped lookup would hand back another tenant's source and the
    # token would then be refused by the ADR-009 invariant.
    from django.db import IntegrityError
    from django.utils.text import slugify

    from ingest.models import IngestionSource

    source_name = (request.POST.get("source_name") or "").strip()
    if not source_name:
        messages.error(request, "Source name is required.")
        return redirect("ui-settings-sources")
    if len(source_name) > 200:
        messages.error(request, "Source name must be 200 characters or fewer.")
        return redirect("ui-settings-sources")

    source_slug = slugify(source_name)[:100]
    if not source_slug:
        messages.error(request, "Source name must contain at least one letter or digit.")
        return redirect("ui-settings-sources")

    try:
        source, source_created = IngestionSource.objects.get_or_create(
            organisation=organisation,
            slug=source_slug,
            defaults={"name": source_name},
        )
    except IntegrityError:
        # Lost a race with a concurrent create — the row exists now, so just bind to it.
        source, source_created = (
            IngestionSource.objects.get(organisation=organisation, slug=source_slug),
            False,
        )

    if source.organisation_id != organisation.id:
        # Unreachable while the lookup above is org-scoped (the ADR-009 invariant would raise first);
        # kept as the explicit refusal so a future refactor fails with a message, not a 500.
        messages.error(
            request,
            f"'{source.slug}' belongs to another organisation, so it cannot be used for this token.",
        )
        return redirect("ui-settings-sources")

    if source_created:
        from identity.audit import record_ingestion_source_created

        record_ingestion_source_created(request, actor=request.user, source=source)

    # Create token with bounded retry on prefix collision
    from django.db import transaction

    from identity.models import IngestionToken

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                token, plaintext = IngestionToken.create_token(
                    source=source,
                    organisation=organisation,
                    name=name,
                    created_by=request.user,
                )
                # Write audit row in the same transaction
                from identity.audit import record_ingestion_token_change

                record_ingestion_token_change(
                    request,
                    actor=request.user,
                    action="ingestion_token.created",
                    token=token,
                )
        except IntegrityError:
            # The only unique constraint on IngestionToken is `prefix` (PK is UUID).
            # A collision here can only mean a prefix collision — retry with a new random prefix.
            if attempt < max_retries - 1:
                continue
            messages.error(request, "Could not generate a unique token. Please try again.")
            return redirect("ui-settings-sources")
        else:
            break

    # Show-once render. The secret is rendered into the `token-created-modal` dialog on the Sources
    # page (an `{% if token_created %}` include in `settings_sources.html`) instead of into a bare
    # partial: a partial with no `{% extends %}` arrived as a document with no shell and no
    # stylesheets, so the one secret we can never show again landed on an unstyled page. Re-rendering
    # the list also means its new row is already behind the modal when the modal is dismissed.
    #
    # `token_plaintext` is set on THIS path only — never a redirect, a session, the URL or a log — and
    # the body partial reads it by that exact name (`test_show_once_displays_the_plaintext`).
    context = _settings_sources_context(request)
    context.update(token_created=True, token_plaintext=plaintext)
    return render(request, "ui/settings_sources.html", _base_context(request, **context))


@not_viewer
@login_required
@require_POST
def settings_token_revoke(request: HttpRequest, token_id: str) -> HttpResponse:
    """Revoke an ingestion token."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    is_superadmin = request.user.role == "superAdmin"
    from identity.models import IngestionToken

    qs = IngestionToken.objects.select_related("organisation")
    if not is_superadmin:
        qs = qs.filter(organisation_id=request.user.org_id)

    try:
        token = qs.get(pk=token_id)
    except IngestionToken.DoesNotExist:
        # Uniform 404 for cross-org access (no existence oracle)
        return render(request, "ui/404.html", status=404)

    if token.revoked_at is None:
        from django.utils import timezone

        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at", "updated_at"])

        from identity.audit import record_ingestion_token_change

        record_ingestion_token_change(
            request,
            actor=request.user,
            action="ingestion_token.revoked",
            token=token,
        )
        messages.success(request, f"Token '{token.name}' revoked.")
    else:
        messages.info(request, f"Token '{token.name}' was already revoked.")

    return redirect("ui-settings-sources")


# =============================================================================
# Error handlers (D2)
# =============================================================================


def handler404(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """Custom 404 handler — renders themed 404 template."""
    return render(request, "ui/404.html", status=404)


def handler500(request: HttpRequest) -> HttpResponse:
    """Custom 500 handler — renders themed 500 template without DB access.

    Must not query the database because a 500 often means the DB is unavailable
    or the connection is broken. Renders the template WITHOUT request context
    processors (no `request=`) so that `auth` and `messages` processors don't
    touch the session/user tables.
    """
    from django.http import HttpResponseServerError
    from django.template import loader

    return HttpResponseServerError(loader.render_to_string("ui/500.html"))


def handler403(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """Custom 403 handler — renders themed 403 template."""
    return render(request, "ui/403.html", status=403)
