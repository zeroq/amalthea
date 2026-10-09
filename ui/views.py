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
from django.contrib.auth.views import LogoutView
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from alerts.escalation import (
    import_alert_to_case,
    merge_alert_into_case,
    resolve_case_status,
)
from alerts.models import Alert
from automation.models import AutomationRun, Playbook
from cases.ledger import append_timeline_event
from cases.models import Case, CaseStatus, CaseTemplate, Comment, Tag, Task
from core.enums import SEVERITY_CHOICES
from identity.models import User
from identity.ratelimit import check_login_rate_limit, record_failed_login, record_successful_login
from ingest.models import IngestionSource
from observables.extractor import extract_into_case
from observables.models import Observable, ObservableType

SEVERITY_LABELS = dict(SEVERITY_CHOICES)
#: Severity as a CSS class suffix, so a template says `sev-{{ case.severity }}` and the palette lives
#: in one stylesheet. Mapping the number to a class name in the template would be a fourth spelling.
SEVERITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "critical"}
CASE_STAGE_VALUES = ("New", "InProgress", "Closed")
PAGE_SIZE = 200


def _resolve_case(case_id: str) -> Case:
    """A case by UUID *or* by human number.

    An analyst reads a case number off a ticket and a UUID off a log line; making them pick one would
    mean a "case not found" that is really a lookup mistake. `isdigit` is the discriminator because a
    UUID is never all-digits.
    """
    return get_object_or_404(
        Case, **({"number": int(case_id)} if case_id.isdigit() else {"pk": case_id})
    )


def _base_context(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    """Everything every page needs, in one place.

    The nav badges live here rather than per-view because two views counting them differently is how
    a navigation bar starts lying about what is waiting.
    """
    return {
        "nav_alert_count": Alert.objects.filter(case__isnull=True).count(),
        "nav_case_count": Case.objects.filter(closed_date__isnull=True).count(),
        "nav_task_count": Task.objects.exclude(status="Completed").count(),
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
        "alerts_total": Alert.objects.count(),
        "alerts_unlinked": Alert.objects.filter(case__isnull=True).count(),
        "cases_total": Case.objects.count(),
        "cases_open": Case.objects.filter(closed_date__isnull=True).count(),
        "runs_total": AutomationRun.objects.count(),
        "runs_failed": AutomationRun.objects.filter(status="Failed").count(),
    }
    by_severity = {
        row["severity"]: row["n"]
        for row in Case.objects.filter(closed_date__isnull=True)
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
                Alert.objects.select_related("status")
                .filter(case__isnull=True)
                .order_by("-date")[:10]
            ),
            open_cases=list(
                Case.objects.select_related("status", "assignee")
                .filter(closed_date__isnull=True)
                .order_by("-severity", "-start_date")[:10]
            ),
            runs=list(
                AutomationRun.objects.select_related("case", "playbook").order_by("-created_at")[
                    :10
                ]
            ),
        ),
    )


@login_required
@require_GET
def alert_list(request: HttpRequest) -> HttpResponse:
    """The triage queue: unlinked alerts by default, because those are the decisions waiting."""
    scope = request.GET.get("scope", "unlinked")
    queryset = Alert.objects.select_related("status", "assignee", "ingestion_source", "case")
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
    alert = get_object_or_404(
        Alert.objects.select_related("status", "ingestion_source", "case"), pk=alert_id
    )
    return render(
        request,
        "ui/alert_detail.html",
        _base_context(
            request,
            alert=alert,
            merge_candidates=Case.objects.filter(closed_date__isnull=True).order_by("-start_date")[
                :PAGE_SIZE
            ],
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
    alert = get_object_or_404(Alert, pk=alert_id)
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
    alert = get_object_or_404(Alert, pk=alert_id)
    case = _resolve_case(request.POST.get("case_id") or "")
    with transaction.atomic():
        merge_alert_into_case(alert, case, actor=request.user)
    messages.success(request, f"Alert merged into case {case.number}.")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_GET
def case_list(request: HttpRequest) -> HttpResponse:
    """Open cases by default; closed ones on request."""
    show_closed = request.GET.get("closed") == "1"
    queryset = Case.objects.select_related("status", "assignee")
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
    case = _resolve_case(case_id)
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
            assignees=User.objects.filter(is_active=True).order_by("login")[:PAGE_SIZE],
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
    case = _resolve_case(case_id)
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
    case = _resolve_case(case_id)
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
    case = _resolve_case(case_id)
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "A task needs a title.")
        return redirect("ui-case-detail", case_id=case.number)
    login = (request.POST.get("assignee") or "").strip()
    Task.objects.create(
        case=case,
        title=title[:500],
        status=request.POST.get("status") or "Waiting",
        assignee=User.objects.filter(login=login).first() if login else None,
        description=(request.POST.get("description") or "").strip(),
    )
    messages.success(request, "Task added.")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_POST
def case_task_toggle(request: HttpRequest, case_id: str, task_id: str) -> HttpResponse:
    """Flip a task between `Waiting` and `Completed`."""
    case = _resolve_case(case_id)
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
    case = _resolve_case(case_id)
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
    """Every automation run, newest first: the Module D ledger across all cases."""
    return render(
        request,
        "ui/automation_list.html",
        _base_context(
            request,
            runs=AutomationRun.objects.select_related(
                "case", "playbook", "triggered_by_observable"
            ).order_by("-created_at")[:PAGE_SIZE],
            playbooks=AutomationRun.objects.values("playbook_name")
            .annotate(n=Count("id"))
            .order_by("playbook_name"),
        ),
    )


@login_required
@require_GET
def sources_list(request: HttpRequest) -> HttpResponse:
    """Configured feeds and how many alerts each has produced."""
    return render(
        request,
        "ui/sources_list.html",
        _base_context(
            request,
            sources=IngestionSource.objects.annotate(alert_count=Count("alerts")).order_by("slug"),
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
def playbook_create(request: HttpRequest) -> HttpResponse:
    """Create a new playbook. GET shows the form, POST creates it."""
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

    action = (request.POST.get("action") or "").lower()
    if action not in ("http", "python"):
        messages.error(request, "Action must be 'http' or 'python'.")
        return redirect("ui-playbook-create")

    config: dict[str, Any] = {"action": action}
    if action == "http":
        url = (request.POST.get("url") or "").strip()
        if not url:
            messages.error(request, "URL is required for HTTP actions.")
            return redirect("ui-playbook-create")
        config["url"] = url

        method = (request.POST.get("http_method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            messages.error(request, "Invalid HTTP method.")
            return redirect("ui-playbook-create")
        config["method"] = method

        timeout = request.POST.get("timeout_seconds")
        if timeout:
            try:
                timeout_val = float(timeout)
                if timeout_val <= 0 or timeout_val > 30:
                    raise ValueError
                config["timeoutSeconds"] = timeout_val
            except ValueError:
                messages.error(request, "Timeout must be a positive number <= 30.")
                return redirect("ui-playbook-create")

        headers = {}
        for key in request.POST:
            if key.startswith("header_key_") and key[11:]:
                idx = key[11:]
                val_key = f"header_val_{idx}"
                if val_key in request.POST:
                    headers[request.POST[key]] = request.POST[val_key]
        if headers:
            config["headers"] = headers

        body = request.POST.get("http_body")
        if body:
            config["body"] = body

    else:  # python
        action_path = (request.POST.get("action_path") or "").strip()
        if not action_path:
            messages.error(request, "Action path is required for Python actions.")
            return redirect("ui-playbook-create")
        from automation.executor import registered_actions

        if action_path not in registered_actions():
            messages.error(
                request,
                f"Unknown action path. Registered: {', '.join(sorted(registered_actions().keys()))}",
            )
            return redirect("ui-playbook-create")
        config["action_path"] = action_path

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
    """View or edit a playbook."""
    from automation.dispatcher import TRIGGER_EVENTS
    from automation.executor import registered_actions
    from automation.playbooks import ACTIONS, HTTP_METHODS

    # Resolve by UUID or name
    playbook = None
    try:
        import uuid

        uuid.UUID(playbook_id)
        playbook = get_object_or_404(Playbook, pk=playbook_id)
    except ValueError:
        playbook = get_object_or_404(Playbook, name=playbook_id)

    if request.method == "GET":
        runs = AutomationRun.objects.filter(playbook=playbook).order_by("-created_at")[:50]
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

    # POST - update the playbook
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
@require_POST
def playbook_delete(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Delete a playbook if it has no runs."""
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
    """Manually trigger a playbook run."""
    from automation.dispatcher import run_now
    from cases.models import Case

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
        try:
            case = Case.objects.get(number=int(case_id))
        except (ValueError, Case.DoesNotExist):
            try:
                case = Case.objects.get(pk=case_id)
            except Case.DoesNotExist:
                messages.error(request, "Case not found.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)

    observable = None
    if observable_id:
        try:
            import uuid

            uuid.UUID(observable_id)
            observable = get_object_or_404(Observable, pk=observable_id)
        except ValueError:
            messages.error(request, "Invalid observable ID.")
            return redirect("ui-playbook-detail", playbook_id=playbook_id)

    # Ensure observable is linked to case if case not provided
    if case is None and observable is not None:
        link = observable.case_observables.select_related("case").first()
        if link is None:
            messages.error(request, "Observable is not linked to any case.")
            return redirect("ui-playbook-detail", playbook_id=playbook_id)
        case = link.case

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
    case = _resolve_case(case_id)
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
    case = _resolve_case(case_id)
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
    case = _resolve_case(case_id)
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
    case = _resolve_case(case_id)
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
    from cases.views import _apply_template_to_case
    _apply_template_to_case(case, template)
    messages.success(request, f"Applied template '{template.name}' to case {case.number}.")
    return redirect("ui-case-detail", case_id=case.number)


@login_required
@require_POST
def case_bulk(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk update cases (stub for UI integration)."""
    messages.info(request, "Bulk operations available via API.")
    return redirect("ui-case-list")


@login_required
@require_GET
def case_attachment_list(request: HttpRequest, case_id: str) -> HttpResponse:
    """List and upload attachments for a case."""
    case = _resolve_case(case_id)
    return render(
        request,
        "ui/case_attachments.html",
        _base_context(
            request,
            case=case,
        ),
    )


@login_required
@require_POST
def case_attachment_upload(request: HttpRequest, case_id: str) -> HttpResponse:
    """Upload an attachment to a case."""
    case = _resolve_case(case_id)
    # Reuse the API logic but adapt for UI
    from django.http import QueryDict
    request.POST = QueryDict(mutable=True)
    for key, value in request.POST.items():
        request.POST[key] = value
    for key, value in request.FILES.items():
        request.FILES[key] = value
    # Call the API view logic directly
    from cases.views import case_attachment_list as api_view
    response = api_view(request, case_id=case.id)
    if response.status_code == 201:
        messages.success(request, "Attachment uploaded.")
    else:
        messages.error(request, f"Upload failed: {response.content.decode()[:200]}")
    return redirect("ui-case-attachment-list", case_id=case.number)


@login_required
@require_GET
def case_attachment_download(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    """Download an attachment."""

    from cases.views import case_attachment_download as api_view
    # Reuse API logic
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


@login_required
@require_POST
def case_attachment_delete(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    """Delete an attachment."""
    from cases.views import case_attachment_detail as api_view
    response = api_view(request, case_id=case_id, attachment_id=attachment_id)
    if response.status_code == 204:
        messages.success(request, "Attachment deleted.")
    else:
        messages.error(request, "Delete failed.")
    return redirect("ui-case-attachment-list", case_id=case_id)


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


@login_required
def case_template_create(request: HttpRequest) -> HttpResponse:
    """Create a case template."""
    if request.method == "GET":
        return render(request, "ui/case_template_form.html", _base_context(request))
    # POST
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-case-template-create")
    if CaseTemplate.objects.filter(name=name[:200]).exists():
        messages.error(request, "Template with that name already exists.")
        return redirect("ui-case-template-create")
    CaseTemplate.objects.create(
        name=name[:200],
        display_name=(request.POST.get("display_name") or "").strip(),
        title_prefix=(request.POST.get("title_prefix") or "").strip(),
        description=(request.POST.get("description") or "").strip(),
        summary=(request.POST.get("summary") or "").strip(),
        flag=(request.POST.get("flag") or "").strip(),
        severity=int(request.POST.get("severity") or 2),
        tlp=int(request.POST.get("tlp") or 2),
        pap=int(request.POST.get("pap") or 2),
        tags=[t.strip() for t in (request.POST.get("tags") or "").split(",") if t.strip()],
        tasks=[],
        custom_fields=[],
    )
    messages.success(request, f"Template '{name}' created.")
    return redirect("ui-case-template-list")


@login_required
def case_template_detail(request: HttpRequest, template_id: str) -> HttpResponse:
    """View or edit a case template."""
    try:
        import uuid
        uuid.UUID(template_id)
        template = get_object_or_404(CaseTemplate, pk=template_id)
    except ValueError:
        template = get_object_or_404(CaseTemplate, name=template_id)

    if request.method == "GET":
        return render(
            request,
            "ui/case_template_detail.html",
            _base_context(request, template=template),
        )
    # PATCH/POST
    # Update logic similar to playbook_detail
    # Simplified for brevity
    template.name = (request.POST.get("name") or template.name)[:200]
    template.display_name = (request.POST.get("display_name") or template.display_name)
    template.title_prefix = (request.POST.get("title_prefix") or template.title_prefix)
    template.description = (request.POST.get("description") or template.description)
    template.summary = (request.POST.get("summary") or template.summary)
    template.flag = (request.POST.get("flag") or template.flag)
    template.severity = int(request.POST.get("severity") or template.severity)
    template.tlp = int(request.POST.get("tlp") or template.tlp)
    template.pap = int(request.POST.get("pap") or template.pap)
    template.tags = [t.strip() for t in (request.POST.get("tags") or "").split(",") if t.strip()]
    template.save()
    messages.success(request, f"Template '{template.name}' updated.")
    return redirect("ui-case-template-detail", template_id=template.id)


@login_required
@require_POST
def case_template_delete(request: HttpRequest, template_id: str) -> HttpResponse:
    """Delete a case template."""
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
    """Global observables list with cross-case links."""
    return render(
        request,
        "ui/observable_list.html",
        _base_context(
            request,
            observables=Observable.objects.select_related("data_type")
            .prefetch_related("case_observables__case")
            .order_by("-created_at")[:PAGE_SIZE],
        ),
    )


@login_required
@require_GET
def observable_detail(request: HttpRequest, observable_id: str) -> HttpResponse:
    """Observable detail with cross-case fan-out."""
    try:
        import uuid
        uuid.UUID(observable_id)
        observable = get_object_or_404(Observable, pk=observable_id)
    except ValueError:
        observable = get_object_or_404(Observable, pk=observable_id)
    return render(
        request,
        "ui/observable_detail.html",
        _base_context(
            request,
            observable=observable,
            cases=observable.case_observables.select_related("case").order_by("-case__start_date"),
        ),
    )


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
@require_GET
def case_merge(request: HttpRequest, case_id: str) -> HttpResponse:
    """Merge cases via UI."""
    from alerts.escalation import merge_cases as merge_cases_fn
    case = _resolve_case(case_id)
    if request.method == "GET":
        # Show merge form with candidate cases
        candidates = Case.objects.filter(closed_date__isnull=True).exclude(pk=case.pk).order_by("-start_date")
        return render(request, "ui/case_merge.html", _base_context(request, case=case, candidates=candidates))
    # POST
    target_id = request.POST.get("target_case")
    if not target_id:
        messages.error(request, "Target case is required.")
        return redirect("ui-case-merge", case_id=case.number)
    target = _resolve_case(target_id)
    try:
        with transaction.atomic():
            merged = merge_cases_fn(target, [case])
        messages.success(request, f"Merged case {case.number} into {merged.number}.")
        return redirect("ui-case-detail", case_id=merged.number)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-merge", case_id=case.number)


@login_required
@require_POST
def case_bulk(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk update cases (stub)."""
    messages.info(request, "Bulk operations available via API.")
    return redirect("ui-case-list")


@login_required
@require_GET
def case_attachment_list(request: HttpRequest, case_id: str) -> HttpResponse:
    case = _resolve_case(case_id)
    return render(request, "ui/case_attachments.html", _base_context(request, case=case, attachments=case.attachments.all()))


@login_required
@require_POST
def case_attachment_upload(request: HttpRequest, case_id: str) -> HttpResponse:
    from cases.views import case_attachment_list as api_view
    case = _resolve_case(case_id)
    # The API view expects multipart/form-data with 'attachments' field
    # We just pass through
    response = api_view(request, case_id=case.id)
    if response.status_code == 201:
        messages.success(request, "Attachment uploaded.")
    else:
        messages.error(request, f"Upload failed: {response.content.decode()[:200]}")
    return redirect("ui-case-attachment-list", case_id=case.number)


@login_required
@require_GET
def case_attachment_download(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    from cases.views import case_attachment_download as api_view
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


@login_required
@require_POST
def case_attachment_delete(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    from cases.views import case_attachment_detail as api_view
    response = api_view(request, case_id=case_id, attachment_id=attachment_id)
    if response.status_code == 204:
        messages.success(request, "Attachment deleted.")
    else:
        messages.error(request, "Delete failed.")
    return redirect("ui-case-attachment-list", case_id=case_id)


@login_required
@require_GET
def case_attachment_detail(request: HttpRequest, case_id: str, attachment_id: str) -> HttpResponse:
    """Attachment detail (download or delete)."""
    from cases.views import case_attachment_detail as api_view
    return api_view(request, case_id=case_id, attachment_id=attachment_id)


@login_required
@require_GET
def case_procedure_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Create a procedure on a case."""
    from cases.views import case_procedure_create as api_view
    return api_view(request, case_id=case_id)


@login_required
@require_POST
def case_procedures_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Bulk create procedures on a case."""
    from cases.views import case_procedures_create as api_view
    return api_view(request, case_id=case_id)
