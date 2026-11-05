"""Alert triage and alert lifecycle views."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from alerts.escalation import (
    import_alert_to_case,
    merge_alert_into_case,
)
from alerts.models import Alert, AlertStatus, AlertTagLink
from cases.correlation import (
    candidate_cases_for_alert,
    record_alert_bundled,
)
from cases.ledger import append_timeline_event
from cases.models import (
    Case,
    CaseObservable,
    Tag,
)
from cases.tagging import MAX_TAG_NAME
from core import taxonomy_read
from core.enums import (
    ALERT_STAGES,
    PAP_CHOICES,
    TLP_CHOICES,
)
from identity.models import AuditLog
from identity.scoping import (
    case_visible_q,
    org_q,
    resolve_alert,
    resolve_case,
)
from ui.pagination import paginate

from .common import (
    _base_context,
    _case_detail_redirect,
    not_viewer,
)

logger = logging.getLogger(__name__)

ALERT_SCOPE_OPTIONS = (
    ("unlinked_open", "Unlinked (open)"),
    ("unlinked_triaged", "Unlinked (triaged)"),
    ("unlinked_closed", "Unlinked (closed)"),
    ("linked_open", "Linked (open)"),
    ("linked_closed", "Linked (closed)"),
)

DEFAULT_ALERT_SCOPE = "unlinked_open"

ALERT_SCOPE_PREDICATES = {
    "unlinked_open": Q(case__isnull=True, status__stage="New"),
    "unlinked_triaged": Q(case__isnull=True, status__stage="InProgress"),
    "unlinked_closed": Q(case__isnull=True, status__stage="Closed"),
    "linked_open": Q(case__isnull=False, case__closed_date__isnull=True),
    "linked_closed": Q(case__isnull=False, case__closed_date__isnull=False),
}


@login_required
@require_GET
def alert_list(request: HttpRequest) -> HttpResponse:
    """The triage queue: unlinked alerts by default, because those are the decisions waiting."""
    scope = request.GET.get("scope", DEFAULT_ALERT_SCOPE)
    if scope not in ALERT_SCOPE_PREDICATES:
        scope = DEFAULT_ALERT_SCOPE
    mine = request.GET.get("mine") == "1"

    base_queryset = Alert.objects.select_related(
        "status", "assignee", "ingestion_source", "case", "owner_org"
    ).filter(org_q("owner_org", request.user))

    # Compute unfiltered org-scoped count for filtered_from (honest empty state support)
    unfiltered_count = base_queryset.count()

    if mine:
        base_queryset = base_queryset.filter(assignee=request.user)

    queryset = base_queryset.filter(ALERT_SCOPE_PREDICATES[scope])

    page_obj = paginate(request, queryset.order_by("-date"))
    filtered_from = unfiltered_count if unfiltered_count > page_obj.paginator.count else None

    return render(
        request,
        "ui/alert_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
            scope=scope,
            mine=mine,
            scopes=ALERT_SCOPE_OPTIONS,
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
        .exclude(stage="Closed")
        .order_by("order", "value")
    )
    # Correlation-ranked merge candidates (P2) with plain open-case fallback
    merge_candidates = candidate_cases_for_alert(alert, user=request.user)

    # Observables linked to this alert via AlertObservable, with their cases
    from alerts.models import AlertObservable

    alert_observables = (
        AlertObservable.objects.filter(alert=alert)
        .select_related("observable__data_type")
        .order_by("-created_at")
    )

    # For each alert observable, find the cases this observable appears in (via CaseObservable).
    # One query scoped to the cases the caller may see, rather than a visibility check per row.
    observable_ids = [ao.observable_id for ao in alert_observables]
    visible_case_ids = Case.objects.filter(case_visible_q(request.user)).values("pk")
    case_observable_links = (
        CaseObservable.objects.filter(
            observable_id__in=observable_ids,
            case_id__in=visible_case_ids,
        )
        .select_related("observable", "case")
        .order_by("-case__start_date")
    )

    # Group cases by observable
    from collections import defaultdict

    cases_by_observable = defaultdict(list)
    for co in case_observable_links:
        if co.case_id:
            cases_by_observable[co.observable_id].append(co.case)

    # Build the alert_observables list with cases for the template
    alert_observables_with_cases = []
    for ao in alert_observables:
        alert_observables_with_cases.append(
            {
                "observable": ao.observable,
                "cases": cases_by_observable.get(ao.observable_id, []),
                "created_at": ao.created_at,
            }
        )

    # Resolve existing tags on this alert (via AlertTagLink)
    existing_tag_ids = list(
        AlertTagLink.objects.filter(alert=alert).values_list("tag_id", flat=True)
    )

    # Available tags: all tags in the system, excluding those already on the alert.
    # Note: Tags are organization-scoped at the link level (AlertTagLink), so we filter by
    # the alert's organization to ensure cross-org tags aren't shown here.
    from ui.pagination import PICKER_PAGE_SIZE

    available_tags_qs = (
        Tag.objects.annotate(
            count=Count("alert_links__alert__owner_org"),
            case_count=Count("case_links__case__owner_org"),
            observable_count=Count("observable_links__observable__owner_org"),
        )
        .filter(
            Q(alert_links__alert__owner_org=user_org_id)
            | Q(case_links__case__owner_org=user_org_id)
            | Q(observable_links__observable__owner_org=user_org_id)
        )
        .exclude(id__in=existing_tag_ids)
        .order_by("name")[: PICKER_PAGE_SIZE + 1]
    )
    available_tags = list(available_tags_qs)[:PICKER_PAGE_SIZE]
    available_tags_total = available_tags_qs.count()
    available_tags_truncated = available_tags_total > PICKER_PAGE_SIZE

    tabs = [
        {"slug": "general", "label": "General"},
        {"slug": "raw-data", "label": "Raw data"},
    ]
    active_tab = request.GET.get("tab", "general")
    if active_tab not in {t["slug"] for t in tabs}:
        active_tab = "general"

    return render(
        request,
        "ui/alert_detail.html",
        _base_context(
            request,
            alert=alert,
            merge_candidates=merge_candidates,
            is_cross_org=is_cross_org,
            alert_status_options=alert_status_options,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
            alert_observables=alert_observables_with_cases,
            alert_verdict=alert.verdict,
            closing_verdict_choices=taxonomy_read.closing_verdict_choices(),
            alert_close_url=reverse("ui-alert-close", kwargs={"alert_id": alert.id}),
            alert_tag_add_url=reverse("ui-alert-tag-add", kwargs={"alert_id": str(alert.id)}),
            tabs=tabs,
            active_tab=active_tab,
            alert_tag_ids=existing_tag_ids,
            alert_tags_total=available_tags_total,
            available_tags=available_tags,
            available_tags_truncated=available_tags_truncated,
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
            # Record the bundle in ledger + audit (P2 — closes ADR-002 §9 gap on this path)
            record_alert_bundled(
                case,
                alert,
                actor=request.user,
                reason="Alert escalated into new case",
                source="escalate",
            )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-alert-detail", alert_id=alert_id)
    except PermissionDenied as exc:
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
    try:
        with transaction.atomic():
            merge_alert_into_case(alert, case, actor=request.user)
            # Record the bundle in ledger + audit (P2 — closes ADR-002 §9 gap on this path)
            record_alert_bundled(
                case,
                alert,
                actor=request.user,
                reason="Alert merged into existing case",
                source="merge",
            )
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return redirect("ui-alert-detail", alert_id=alert_id)
    messages.success(request, f"Alert merged into case {case.number}.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def alert_delete_ui(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Delete an alert — superAdmin only (UI endpoint)."""
    from core.enums import SUPER_ADMIN_ROLE

    if request.user.role != SUPER_ADMIN_ROLE:
        return render(request, "ui/404.html", status=404)

    alert = resolve_alert(request.user, alert_id, write=True)
    if alert is None:
        return render(request, "ui/404.html", status=404)

    alert.delete()
    messages.success(request, "Alert deleted.")
    return redirect("ui-alert-list")


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

    if valid_status.stage == "Closed":
        messages.error(request, "Closing an alert requires a verdict and closing comment.")
        return redirect("ui-alert-detail", alert_id=alert.id)

    # The mutation, its audit row and the case timeline event share one transaction
    # (SEC-2026-10-11 NEW-2): a failed audit must roll the status change back rather than
    # leave the change persisted but unlogged.
    with transaction.atomic():
        alert.status = valid_status
        update_fields = ["status", "updated_at"]
        if valid_status.value == "Triaged" or valid_status.stage == "InProgress":
            alert.assignee = request.user
            update_fields.append("assignee")
        alert.save(update_fields=update_fields)

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
@require_POST
def alert_close(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Close an alert via the close modal (verdict + closing comment required)."""
    alert = resolve_alert(request.user, alert_id, write=True)
    if alert is None:
        return render(request, "ui/404.html", status=404)

    verdict = (request.POST.get("verdict") or "").strip()
    comment = (request.POST.get("comment") or "").strip()

    if not taxonomy_read.is_closing_verdict(verdict):
        allowed = (
            ", ".join([c[0] for c in taxonomy_read.closing_verdict_choices()])
            or "true_positive, false_positive"
        )
        messages.error(
            request,
            f"A closing verdict ({allowed}) is required to close an alert.",
        )
        return redirect("ui-alert-detail", alert_id=alert.id)
    if not comment:
        messages.error(request, "A closing comment is required to close an alert.")
        return redirect("ui-alert-detail", alert_id=alert.id)

    closed_status = AlertStatus.objects.filter(stage="Closed").order_by("order", "value").first()
    if closed_status is None:
        messages.error(request, "No alert status is defined for the Closed stage.")
        return redirect("ui-alert-detail", alert_id=alert.id)

    with transaction.atomic():
        alert.status = closed_status
        alert.verdict = verdict
        alert.assignee = request.user
        alert.save(update_fields=["status", "verdict", "assignee", "updated_at"])

        AuditLog.objects.create(
            actor=request.user,
            actor_org=getattr(request.user, "org", None),
            target_org=alert.owner_org,
            action="alert.status_changed",
            target_type="Alert",
            target_id=str(alert.id),
            ip=request.META.get("REMOTE_ADDR"),
        )

        if alert.case_id:
            append_timeline_event(
                alert.case,
                title=f"Alert closed as {verdict}",
                kind="status-changed",
                actor=request.user,
                metadata={
                    "alert": str(alert.id),
                    "to": closed_status.value,
                    "verdict": verdict,
                    "comment": comment,
                },
            )
        else:
            # Even if not attached to a case, record the closing action via audit/timeline-like behavior
            pass

    messages.success(request, f"Alert closed as {verdict}.")
    return redirect("ui-alert-detail", alert_id=alert.id)


@not_viewer
@login_required
@require_POST
def alert_tag_add(request: HttpRequest, alert_id: str) -> HttpResponse:
    """Add tags to an alert via the modal form."""

    alert = resolve_alert(request.user, alert_id, write=True)
    if alert is None:
        messages.error(request, "Alert not found.")
        return redirect("ui-alert-list")

    # Collect tag IDs from checkboxes and new tag names from text input. The modal advertises a
    # comma-separated list, so split it the same way the case-tag form does; clamp each name to the
    # `Tag.name` column limit before anything is written (Postgres rejects a longer value).
    existing_tag_ids = [tag_id for tag_id in request.POST.getlist("existing_tag_ids", []) if tag_id]
    new_tag_names = list(
        dict.fromkeys(
            name.strip()[:MAX_TAG_NAME]
            for name in (request.POST.get("new_tag_names") or "").split(",")
            if name.strip()
        )
    )

    # Validate: at least one tag provided
    if not existing_tag_ids and not new_tag_names:
        messages.error(request, "Please select or type at least one tag.")
        return redirect("ui-alert-detail", alert_id=alert.id)

    with transaction.atomic():
        # Collect all tag IDs that will be on the alert (existing + new)
        existing_tag_ids_on_alert = set(
            AlertTagLink.objects.filter(alert=alert).values_list("tag_id", flat=True)
        )
        all_tag_ids = set(existing_tag_ids_on_alert)

        # Add existing tag IDs (if not already on alert)
        all_tag_ids.update(existing_tag_ids)

        # Create and add new tags
        for tag_name in new_tag_names:
            tag, _created = Tag.objects.get_or_create(
                name=tag_name, defaults={"colour": "", "description": ""}
            )
            all_tag_ids.add(tag.id)

        # Link all tags to the alert in one operation
        tags_to_link = list(
            Tag.objects.filter(id__in=all_tag_ids).exclude(id__in=existing_tag_ids_on_alert)
        )
        AlertTagLink.objects.bulk_create(
            [AlertTagLink(alert=alert, tag=tag) for tag in tags_to_link]
        )

    messages.success(request, f"Added {len(tags_to_link)} new tag(s) to alert.")
    return redirect("ui-alert-detail", alert_id=alert_id)
