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
from automation.models import AutomationRun
from cases.ledger import append_timeline_event
from cases.models import Case, CaseStatus, Comment, Task
from core.enums import SEVERITY_CHOICES
from identity.models import User
from ingest.models import IngestionSource
from observables.extractor import extract_into_case

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
