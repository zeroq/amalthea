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
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
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
from cases.ledger import append_timeline_event
from cases.models import Attachment, Case, CaseStatus, CaseTemplate, Comment, Tag, Task
from cases.update import CaseUpdateError, apply_case_update
from cases.views import (
    _as_uuid,
    _observable_affected_cases,
    _observable_cross_case_guard,
    _set_observable_fields,
)
from core.enums import ALERT_STAGES, PAP_CHOICES, SEVERITY_CHOICES, TLP_CHOICES
from identity.models import AuditLog, Organisation, User
from identity.ratelimit import check_login_rate_limit, record_failed_login, record_successful_login
from identity.scoping import (
    case_visible_q,
    is_case_owner,
    org_filtered,
    org_q,
    resolve_alert,
    resolve_case,
    resolve_observable,
)
from identity.services import (
    resolve_organisation,
    serializes_superadmin_changes,
)
from ingest.models import IngestionSource
from observables.extractor import extract_into_case
from observables.models import Observable, ObservableType

SEVERITY_LABELS = dict(SEVERITY_CHOICES)
#: Severity as a CSS class suffix, so a template says `sev-{{ case.severity }}` and the palette lives
#: in one stylesheet. Mapping the number to a class name in the template would be a fourth spelling.
SEVERITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "critical"}
CASE_STAGE_VALUES = ("New", "InProgress", "Closed")
PAGE_SIZE = 200
#: Upper bound on outbound HTTP headers per playbook. `header_count` is form-controlled and drives a
#: CPU-bound `range()`, so it must be capped (see `_parse_playbook_config`) — far above real usage.
MAX_HEADERS = 64


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
        ),
    )


@login_required
@require_POST
def alert_escalate(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Promote an alert into a new case (Module B).

    `transaction.atomic` because `import_alert_to_case` writes the case, links the alert, extracts
    observables and appends a timeline entry; a rollback halfway through would leave the alert marked
    `Imported` with no case, which is the one state the caller cannot recover from.
    """
    alert = resolve_alert(request.user, alert_id)
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
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_POST
def alert_merge(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Fold this alert into an existing case."""
    alert = resolve_alert(request.user, alert_id)
    if alert is None:
        return render(request, "ui/404.html", status=404)
    case = resolve_case(request.user, request.POST.get("case_id") or "", write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    with transaction.atomic():
        merge_alert_into_case(alert, case, actor=request.user)
    messages.success(request, f"Alert merged into case {case.number}.")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_GET
def case_list(request: HttpRequest) -> HttpResponse:
    """Open cases by default; closed ones on request."""
    from identity.scoping import case_visible_q

    show_closed = request.GET.get("closed") == "1"
    queryset = Case.objects.select_related("status", "assignee", "owner_org").filter(
        case_visible_q(request.user)
    )
    if not show_closed:
        queryset = queryset.filter(closed_date__isnull=True)
    return render(
        request,
        "ui/case_list.html",
        _base_context(
            request,
            cases=queryset.order_by("-severity", "-start_date")[:PAGE_SIZE],
            show_closed=show_closed,
        ),
    )


@login_required
@require_GET
def case_detail(request: HttpRequest, case_id: str) -> HttpResponse:
    """The ledger: timeline, observables, tasks and automation runs for one case."""
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        return render(request, "ui/404.html", status=404)
    is_owner = is_case_owner(request.user, case)
    user_org_id = getattr(request.user, "org_id", None)
    is_cross_org = case.owner_org_id != user_org_id
    return render(
        request,
        "ui/case_detail.html",
        _base_context(
            request,
            case=case,
            timeline=case.timeline_events.select_related("actor").order_by("date", "id"),
            observables=case.case_observables.select_related("observable__data_type").order_by(
                "-created_at"
            ),
            tasks=case.tasks.select_related("assignee").order_by("order", "created_at"),
            pages=case.pages.select_related("created_by").order_by("order", "-created_at"),
            runs=AutomationRun.objects.filter(case=case)
            .select_related("playbook", "triggered_by_observable")
            .order_by("-created_at"),
            status_choices=CaseStatus.objects.filter(hidden=False).order_by("order", "value"),
            assignees=User.objects.filter(org_q("org", request.user), is_active=True).order_by(
                "login"
            )[:PAGE_SIZE],
            is_owner=is_owner,
            is_cross_org=is_cross_org,
            attachments=case.attachments.select_related("created_by").order_by("-created_at"),
        ),
    )


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
        return render(request, "ui/404.html", status=404)
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
    return redirect("ui-case-detail", case_id=case.number)


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
        return render(request, "ui/404.html", status=404)
    body = (request.POST.get("body") or "").strip()
    if not body:
        # A 400, not a redirect: HTMX only swaps 2xx, so the flash error below would have nowhere
        # to land and the analyst would see a silent no-op.
        if request.headers.get("HX-Request"):
            return HttpResponse("A note needs a body.", status=400)
        messages.error(request, "A note needs a body.")
        return redirect("ui-case-detail", case_id=case.number)
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
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_POST
def case_task_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Add a task. An unrecognised assignee is left unassigned rather than auto-created (ADR-002 §D11)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "A task needs a title.")
        return redirect("ui-case-detail", case_id=case.number)
    login = (request.POST.get("assignee") or "").strip()
    Task.objects.create(
        case=case,
        title=title[:500],
        status=request.POST.get("status") or "Waiting",
        assignee=User.objects.filter(org_q("org", request.user), login=login).first()
        if login
        else None,
        description=(request.POST.get("description") or "").strip(),
    )
    messages.success(request, "Task added.")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_POST
def case_task_toggle(request: HttpRequest, case_id: str, task_id: str) -> HttpResponse:
    """Flip a task between `Waiting` and `Completed`."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    task = get_object_or_404(Task, pk=task_id, case=case)
    task.status = "Waiting" if task.status == "Completed" else "Completed"
    task.save(update_fields=["status", "updated_at"])
    return HttpResponseRedirect(reverse("ui-case-detail", kwargs={"case_id": case.number}))


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
        return render(request, "ui/404.html", status=404)
    with transaction.atomic():
        found = extract_into_case(case, case.title, case.description)
    messages.success(
        request,
        f"Extracted {len(found)} new artifact(s); automation dispatched for each new link.",
    )
    return redirect("ui-case-detail", case_id=case.number)


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
    from django.db.models import Q

    user_org_id = getattr(request.user, "org_id", None)
    if user_org_id is None:
        alert_filter = Q(pk__in=[])
    else:
        alert_filter = Q(alerts__owner_org_id=user_org_id)

    return render(
        request,
        "ui/sources_list.html",
        _base_context(
            request,
            sources=IngestionSource.objects.annotate(
                alert_count=Count("alerts", filter=alert_filter)
            ).order_by("slug"),
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
        return render(request, "ui/404.html", status=404)
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
        return render(request, "ui/404.html", status=404)
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


@login_required
@require_POST
def case_tag_toggle(request: HttpRequest, case_id: str, tag_id: str) -> HttpResponse:
    """Add or remove a tag from a case."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    tag = get_object_or_404(Tag, pk=tag_id)
    if tag in case.tags.all():
        case.tags.remove(tag)
        messages.success(request, f"Removed tag '{tag.name}'.")
    else:
        case.tags.add(tag)
        messages.success(request, f"Added tag '{tag.name}'.")
    return redirect("ui-case-tags", case_id=case.number)


@login_required
def case_apply_template(request: HttpRequest, case_id: str) -> HttpResponse:
    """Apply a case template to a case."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
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
    return redirect("ui-case-detail", case_id=case.number)


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
        _base_context(request, template=template),
    )


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
    """Global observables list with cross-case links (org-scoped)."""
    queryset = (
        Observable.objects.select_related("data_type", "owner_org")
        .prefetch_related("case_observables__case")
        .filter(org_q("owner_org", request.user))
        .order_by("-created_at")[:PAGE_SIZE]
    )
    return render(
        request,
        "ui/observable_list.html",
        _base_context(request, observables=queryset),
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
        ),
    )


# =============================================================================
# P2 — Triage and edit flows (C1, C2, C3)
# =============================================================================


@login_required
@require_POST
def alert_status(request: HttpRequest, alert_id: str) -> HttpResponse:
    """P2/C1 — Change an alert's status from the UI.

    Form field: `status` = an `AlertStatus.value`.
    Validates against the existing stage vocabulary (excludes "Imported", which is
    system-assigned by escalation) so a garbage value cannot auto-create a junk
    status (ADR-002 §D11 tolerance in the API path).
    """
    alert = resolve_alert(request.user, alert_id)
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
        return render(request, "ui/404.html", status=404)

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
        }
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
    return redirect("ui-case-detail", case_id=case.number)


def _render_case_edit_form(
    request: HttpRequest,
    case: Case,
    status_options,
    assignees,
    post_data,
) -> HttpResponse:
    """Re-render the case edit form with submitted values and error message."""
    # `post_data` is a QueryDict; `dict(...)` yields lists (`{"title": ["x"]}`), which render as
    # `['x']`. Read scalars with `.get()` so the form echoes what was typed (SEC-2026-10-11 N1).
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
    )
    form_values = {key: post_data.get(key, "") for key in fields}
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
            case_status_options=status_options,
            assignees=assignees,
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def observable_edit(request: HttpRequest, observable_id: str) -> HttpResponse:
    """P2/C3 — Edit an observable's editable fields (type, message, TLP/PAP, flags, enrichment).

    The value (`data`) is the per-org dedupe key and stays immutable on both surfaces; the
    editable set is applied through `_set_observable_fields` so normalisation, validation and
    the wire shape match the API exactly (P2 C3).
    """
    observable = resolve_observable(request.user, observable_id)
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


@login_required
@require_POST
def observable_delete(request: HttpRequest, observable_id: str) -> HttpResponse:
    """P2/C3 — Delete an observable, honouring the cross-case blast-radius guard.

    Reads `force` from POST data. If the observable is linked to multiple cases
    and `force` is not set, refuses with a message and redirects back.
    """
    observable = resolve_observable(request.user, observable_id)
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


# --- Case share management (P7) ---


@login_required
def case_share_list(request: HttpRequest, case_id: str) -> HttpResponse:
    """List and manage case shares (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
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


@login_required
@require_POST
def case_share_revoke(request: HttpRequest, case_id: str, share_id: str) -> HttpResponse:
    """Revoke a case share (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
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
    """Aggregate taxonomy page for UI pickers."""
    from alerts.models import AlertStatus
    from cases.models import TTP, CaseStatus

    return render(
        request,
        "ui/taxonomy.html",
        _base_context(
            request,
            case_statuses=CaseStatus.objects.filter(hidden=False).order_by("order", "value"),
            alert_statuses=AlertStatus.objects.filter(hidden=False).order_by("order", "value"),
            observable_types=ObservableType.objects.all().order_by("name"),
            case_templates=CaseTemplate.objects.all().order_by("name"),
            tags=Tag.objects.all().order_by("name"),
            ttps=TTP.objects.all().order_by("name"),
        ),
    )


@login_required
def case_merge(request: HttpRequest, case_id: str) -> HttpResponse:
    """Merge cases via UI."""
    from cases.merge import merge_cases

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
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
        return redirect("ui-case-detail", case_id=merged.number)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-merge", case_id=case.number)


@login_required
@require_GET
def case_attachment_list(request: HttpRequest, case_id: str) -> HttpResponse:
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        return render(request, "ui/404.html", status=404)
    return render(
        request,
        "ui/case_attachments.html",
        _base_context(request, case=case, attachments=case.attachments.all()),
    )


@login_required
@require_POST
def case_attachment_upload(request: HttpRequest, case_id: str) -> HttpResponse:
    from cases.views import case_attachment_list as api_view

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    # The API view expects multipart/form-data with 'attachments' field
    # We just pass through
    response = api_view(request, case_id=case.id)
    if response.status_code == 201:
        messages.success(request, "Attachment uploaded.")
    else:
        messages.error(request, f"Upload failed: {response.content.decode()[:200]}")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_GET
def case_attachment_download(
    request: HttpRequest, case_id: str, attachment_id: str
) -> HttpResponse:
    from cases.views import case_attachment_download as api_view

    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        return render(request, "ui/404.html", status=404)
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


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
        return render(request, "ui/404.html", status=404)
    attachment = get_object_or_404(Attachment, pk=attachment_id, case=case)
    name = attachment.name
    path = attachment.path
    attachment.delete()
    default_storage.delete(path)
    messages.success(request, f"Deleted attachment '{name}'.")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_GET
def case_attachment_detail(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    """Attachment detail (download or delete)."""
    from cases.views import case_attachment_detail as api_view

    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        return render(request, "ui/404.html", status=404)
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


@login_required
@require_GET
def case_procedure_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Create a procedure on a case."""
    from cases.views import case_procedure_create as api_view

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    return api_view(request, case_id=case_id)


@login_required
@require_POST
def case_procedures_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk create procedures on a case."""
    from cases.views import case_procedures_create as api_view

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)
    return api_view(request, case_id=case_id)


# =============================================================================
# O4 — Settings UI (superAdmin only)
# =============================================================================


def _require_superadmin_ui(request: HttpRequest) -> HttpResponse | None:
    """Return a 404 response if the user is not a superAdmin, else None."""
    if getattr(request.user, "role", "") != "superAdmin":
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
    ):
        if count:
            parts.append(f"{count} {singular}{'' if count == 1 else 's'}")
    return ", ".join(parts)


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

        errors = {}
        if role and role not in USER_ROLE_VALUES:
            errors["role"] = f"must be one of {', '.join(USER_ROLE_VALUES)}"

        org = resolve_organisation(org_id) if org_id else None
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

    orgs = Organisation.objects.order_by("name")
    return render(
        request,
        "ui/settings_user_form.html",
        _base_context(
            request, user_obj=user, orgs=orgs, role_choices=USER_ROLE_VALUES, mode="edit"
        ),
    )


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
