"""Case detail page and similar-case fragments."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from automation.models import AutomationRun
from cases.correlation import (
    alerts_fuzzy_similar_to_case,
    cases_sharing_findings,
)
from cases.models import (
    TTP,
    Case,
    CaseObservable,
    CaseStatus,
    Tag,
)
from core import taxonomy_read
from core.enums import (
    PAP_CHOICES,
    SEVERITY_CHOICES,
    TASK_STATUS_CHOICES,
    TLP_CHOICES,
)
from identity.models import AuditLog, User
from identity.scoping import (
    case_visible_q,
    is_admin,
    is_case_owner,
    is_viewer,
    org_q,
    resolve_case,
)
from observables.models import ObservableType
from ui.case_custom_fields import case_custom_field_rows
from ui.pagination import PICKER_PAGE_SIZE

from .common import (
    CASE_TABS,
    _base_context,
)

logger = logging.getLogger(__name__)


def _similar_for_case(case: Case, user) -> tuple[list, int, list, int]:
    """Return (similar_cases, similar_total, similar_alerts, similar_alerts_total) for a case.

    Builds findings from the case's CaseObservables (excluding ignore_similarity),
    then calls cases_sharing_findings and alerts_fuzzy_similar_to_case.
    """
    from observables.extractor import Finding

    # Build findings from this case's CaseObservables, excluding ignore_similarity
    case_observables = (
        CaseObservable.objects.filter(case=case)
        .select_related("observable__data_type")
        .exclude(observable__ignore_similarity=True)
    )
    findings = [
        Finding(
            type_name=co.observable.data_type.name,
            raw=co.observable.data,
            normalized=co.observable.normalized_data,
        )
        for co in case_observables
    ]
    similar_cases, similar_total = cases_sharing_findings(findings, user=user, exclude_case=case)
    # Similar alerts: unlinked alerts fuzzy-similar to this case's attached alerts
    similar_alerts, similar_alerts_total = alerts_fuzzy_similar_to_case(case, user=user)
    return similar_cases, similar_total, similar_alerts, similar_alerts_total


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

    # Fetch custom field rows before tab resolution (UI custom tab). The tab only exists when
    # the case carries at least one value; empty cases keep the old panel list.
    custom_field_rows = case_custom_field_rows(case)
    tabs = [{"slug": slug, "label": label, "count": None} for slug, label in CASE_TABS]
    if not custom_field_rows:
        tabs = [tab for tab in tabs if tab["slug"] != "custom"]

    # Resolve active tab against the *visible* allowlist: unknown, missing, or a now-hidden
    # `custom` tab falls back to General.
    active_tab = request.GET.get("tab")
    if active_tab not in {tab["slug"] for tab in tabs}:
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

    # Inject counts for tabs that have them
    counts = {
        "alerts": alerts_qs.count(),
        "tasks": tasks_qs.count(),
        "observables": observables_qs.count(),
        "attachments": attachments_qs.count(),
        "ttp": procedures_count,
        # Counts exactly what the Comments panel lists: analyst notes.
        "comments": comments_qs.count(),
        "custom": len(custom_field_rows),
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
    # The General-tab status control lists concrete taxonomy statuses, not stages. Closed-stage
    # statuses stay out: closing needs the verdict/comment dialog.
    case_status_control_options = [
        status for status in case_status_options if status.stage != "Closed"
    ]

    # Task tab context: uncapped assignee list for the person picker, and status vocabulary
    task_assignees = User.objects.filter(org_q("org", request.user), is_active=True).order_by(
        "login"
    )
    task_status_options = TASK_STATUS_CHOICES

    can_classify = is_admin(request.user)

    # Rails (R surfaces) — cap with honest totals (Q3/Q4, D1/D2/D3).
    # No filtered main-list route exists for these, so we provide `<name>_total` counts.
    # For chronological rails rendered oldest-first, we take the newest RAIL_CAP and
    # re-order back to display order so the rendered subset is the newest N.
    from ui.pagination import RAIL_CAP

    # Alerts: order by -date (newest first), so simple slice is correct
    alerts_total = alerts_qs.count()
    alerts = list(alerts_qs[:RAIL_CAP])

    # Tasks: order by (order, created_at) ascending — take newest by -order, -created_at then reverse
    tasks_total = tasks_qs.count()
    tasks = list(tasks_qs.order_by("-order", "-created_at")[:RAIL_CAP])[::-1]

    # Observables: order by -created_at (newest first), so simple slice is correct
    observables_total = observables_qs.count()
    observables = list(observables_qs[:RAIL_CAP])

    # Suggestions: fetch for this case, grouped by status
    suggestions_qs = case.suggestions.select_related(
        "data_type", "source_alert", "observable__data_type"
    ).order_by("-created_at")
    suggestions_total = suggestions_qs.count()
    suggestions_proposed = list(suggestions_qs.filter(status="proposed", filtered_out=False))
    suggestions_accepted = list(suggestions_qs.filter(status="accepted"))
    suggestions_rejected = list(suggestions_qs.filter(status="rejected"))
    suggestions_ignored = list(suggestions_qs.filter(status="ignored"))
    # Disjoint from the status sections below: a filtered suggestion that is later accepted
    # belongs under "Accepted", not also under "Filtered out".
    suggestions_filtered = list(suggestions_qs.filter(status="proposed", filtered_out=True))
    suggestions_filtered_total = len(suggestions_filtered)

    # "Seen in" modal payload (plan 2026-10-20): the cases each rendered artifact has been seen in.
    # Scoped through case_visible_q so a share/cross-org link can never leak a case number/title.
    # `case_visible_q` describes *Case* fields (`owner_org_id`/`shares__organisation_id`), so it must
    # be applied via `case__in=Case.objects.filter(...)` — not directly to the CaseObservable queryset
    # (that raises FieldError). This mirrors the established pattern in this module.
    # Bounded by RAIL_CAP observables; replaces the per-row case_observables.count() N+1.
    # Keyed by the observable's UUID (not str()) so the template looks it up directly with
    # `{{ linked_cases_by_observable|get_item:link.observable.id }}` — no string formatting, and no
    # filter-chain precedence trap (`|get_item:…|stringformat:"s"` stringifies the *result*, not the key).
    observable_ids = [link.observable_id for link in observables]
    seen_links = (
        CaseObservable.objects.filter(observable_id__in=observable_ids)
        .filter(case__in=Case.objects.filter(case_visible_q(request.user)))
        .select_related("case__status")
        .order_by("-case__start_date")
    )
    linked_cases_by_observable: dict[uuid.UUID, dict] = {}
    for cl in seen_links:
        bucket = linked_cases_by_observable.setdefault(cl.observable_id, {"cases": [], "total": 0})
        bucket["total"] += 1
        if len(bucket["cases"]) < RAIL_CAP:
            bucket["cases"].append(
                {
                    "number": cl.case.number,
                    "title": cl.case.title,
                    # `Case.status` is an FK to `CaseStatus` with no `choices`, so there is no
                    # auto-generated `get_status_display`; the label lives on the related row.
                    "status": cl.case.status.value,
                    "severity": cl.case.severity,
                }
            )

    # Alert provenance for observables: map observable_id -> {latest_alert, other_count, alerts[]}
    # Fetch AlertObservable links for the rendered observables, ordered by alert created_at desc
    from alerts.models import AlertObservable

    alert_obs_links = (
        AlertObservable.objects.filter(observable_id__in=observable_ids)
        .select_related("alert")
        .order_by("observable_id", "-alert__created_at")
    )
    alert_provenance_by_observable: dict[uuid.UUID, dict] = {}
    for ao in alert_obs_links:
        bucket = alert_provenance_by_observable.setdefault(ao.observable_id, {"alerts": []})
        bucket["alerts"].append(ao.alert)
    # Post-process to extract latest_alert and other_count
    for _obs_id, bucket in alert_provenance_by_observable.items():
        alerts_list = bucket["alerts"]
        bucket["latest_alert"] = alerts_list[0] if alerts_list else None
        bucket["other_count"] = len(alerts_list) - 1 if len(alerts_list) > 1 else 0

    # Attachments: order by -created_at (newest first), so simple slice is correct
    attachments_total = attachments_qs.count()
    attachments = list(attachments_qs[:RAIL_CAP])

    # Comments: order by (date, id) ascending (chronological) — take newest then reverse
    comments_total = comments_qs.count()
    comments = list(comments_qs.order_by("-date", "-id")[:RAIL_CAP])[::-1]

    # Timeline: order by (date, id) ascending (chronological, contractual for live.js) — take newest then reverse
    timeline_total = timeline_qs.count()
    timeline = list(timeline_qs.order_by("-date", "-id")[:RAIL_CAP])[::-1]

    # Audit logs: order by -created_at (newest first), so simple slice is correct
    audit_logs_total = audit_logs_qs.count()
    audit_logs = list(audit_logs_qs[:RAIL_CAP])

    # Pages: newest-first by created_at, order as stable tie-breaker; cap at RAIL_CAP
    pages_qs = case.pages.select_related("created_by", "updated_by").order_by(
        "-created_at", "-order"
    )
    pages_total = pages_qs.count()
    pages = list(pages_qs[:RAIL_CAP])

    # Runs: order by -created_at (newest first), so simple slice is correct
    runs_qs = (
        AutomationRun.objects.filter(case=case)
        .select_related("playbook", "triggered_by_observable")
        .order_by("-created_at")
    )
    runs_total = runs_qs.count()
    runs = list(runs_qs[:RAIL_CAP])

    # Procedures: order by (occur_date, id) ascending — take newest then reverse
    procedures_qs = case.procedures.select_related("ttp").order_by("occur_date", "id")
    procedures_total = procedures_qs.count()
    procedures = list(procedures_qs.order_by("-occur_date", "-id")[:RAIL_CAP])[::-1]

    # TTPs: global vocabulary (not a log) — cap **head-first** in display order (D1/D5).
    # A vocabulary is searched/scanned alphabetically, so the first RAIL_CAP items
    # must be visible. This is distinct from logs/working sets (tasks, comments,
    # timeline, pages, procedures, etc.) which are capped to their newest N.
    ttps_qs = TTP.objects.order_by("name")
    ttps_total = ttps_qs.count()
    ttps = list(ttps_qs[:RAIL_CAP])

    # Similar cases/alerts — compute unconditionally for the badge (D1) and for the tab.
    # The helper is called once per page render (G3, R3).
    similar_cases, similar_total, similar_alerts, similar_alerts_total = _similar_for_case(
        case, request.user
    )

    # Badge for Similar tab: combined count, only when > 0 (the template's count-is-not-None guard
    # hides it at 0). Set directly on `tabs` — the `counts`→`tabs` loop above (line ~980)
    # already ran, so mutating `counts` here would never reach the badge.
    combined = similar_total + similar_alerts_total
    if combined > 0:
        for tab in tabs:
            if tab["slug"] == "similar":
                tab["count"] = combined
                break

    # Add-tag dialog (§12.8): `Tag` is global and grows without bound, so the picker is capped at
    # `PICKER_PAGE_SIZE`. Fetching one row past the cap detects truncation without a second `COUNT`
    # on the hot case-detail path, and without evaluating the queryset twice.
    available_tag_choices = list(
        Tag.objects.exclude(id__in=case.tags.values_list("id", flat=True)).order_by("name")[
            : PICKER_PAGE_SIZE + 1
        ]
    )
    available_tags = available_tag_choices[:PICKER_PAGE_SIZE]
    available_tags_truncated = len(available_tag_choices) > PICKER_PAGE_SIZE

    return render(
        request,
        "ui/case_detail.html",
        _base_context(
            request,
            case=case,
            timeline=timeline,
            timeline_total=timeline_total,
            comments=comments,
            comments_total=comments_total,
            observables=observables,
            observables_total=observables_total,
            linked_cases_by_observable=linked_cases_by_observable,
            alert_provenance_by_observable=alert_provenance_by_observable,
            tasks=tasks,
            tasks_total=tasks_total,
            pages=pages,
            pages_total=pages_total,
            runs=runs,
            runs_total=runs_total,
            procedures=procedures,
            procedures_total=procedures_total,
            ttps=ttps,
            ttps_total=ttps_total,
            status_choices=case_status_options,
            case_status_control_options=case_status_control_options,
            assignees=User.objects.filter(org_q("org", request.user), is_active=True).order_by(
                "login"
            )[:PICKER_PAGE_SIZE],
            is_owner=is_owner,
            is_cross_org=is_cross_org,
            attachments=attachments,
            attachments_total=attachments_total,
            alerts=alerts,
            alerts_total=alerts_total,
            active_tab=active_tab,
            tabs=tabs,
            audit_logs=audit_logs,
            audit_logs_total=audit_logs_total,
            case_procedure_create_url=reverse(
                "ui-case-procedure-create", kwargs={"case_id": case.number}
            ),
            procedure_create_extra_fields={"tab": "ttp"},
            # Similar cases (Module C)
            similar_cases=similar_cases,
            similar_total=similar_total,
            # Similar alerts (Module C)
            similar_alerts=similar_alerts,
            similar_alerts_total=similar_alerts_total,
            # Edit-case modal (case_detail.html §"Edit case"): mirror the edit page's context.
            form_values=case_edit_form_values,
            severity_choices=SEVERITY_CHOICES,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
            scope_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False
            ),
            case_type_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False
            ),
            data_classification_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_DATA_CLASSIFICATION, include_hidden=False
            ),
            phase_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False
            ),
            can_classify=can_classify,
            case_status_options=case_status_options,
            case_edit_extra_fields={"tab": active_tab},
            # General-tab inline edits (PLAN-2026-10-17 §12.8): one descriptor per field drives both
            # the edit icon and its `case-field-<slug>` dialog; the close dialog's verdict select
            # offers only the closing verdicts; the Add-tag dialog lists tags not yet on the case.
            inline_edit_fields=_inline_edit_fields(case),
            containment_action_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CONTAINMENT_ACTIONS
            ),
            containment_selected_codes=list(
                case.containment_actions.order_by("order", "code").values_list("code", flat=True)
            ),
            containment_other_text=(
                case.containment_action_links.filter(
                    term__code=taxonomy_read.CONTAINMENT_ACTION_OTHER_CODE
                )
                .values_list("other_text", flat=True)
                .first()
                or ""
            ),
            verdict_choices=taxonomy_read.choices(taxonomy_read.CATEGORY_CASE_VERDICT),
            closing_verdict_choices=taxonomy_read.closing_verdict_choices(),
            available_tags=available_tags,
            available_tags_truncated=available_tags_truncated,
            case_field_edit_url=reverse("ui-case-field-edit", kwargs={"case_id": case.number}),
            case_close_url=reverse("ui-case-close", kwargs={"case_id": case.number}),
            case_tag_add_url=reverse("ui-case-tag-add", kwargs={"case_id": case.number}),
            # Custom field rows + edit URL for the Custom tab
            custom_field_rows=custom_field_rows,
            case_custom_field_edit_url=reverse(
                "ui-case-custom-field-edit", kwargs={"case_id": case.number}
            ),
            # Observables tab create/edit modals. The per-row Edit gate uses `is_owner` above
            # (`is_case_owner`, so a superAdmin qualifies), matching this view's owner-org check.
            data_type_options=ObservableType.objects.order_by("name"),
            observable_tlp_choices=[c for c in TLP_CHOICES if c[0] <= 3],
            # Task tab create/edit modal
            task_assignees=task_assignees,
            task_status_options=task_status_options,
            # Suggestions (P6)
            suggestions_total=suggestions_total,
            suggestions_proposed=suggestions_proposed,
            suggestions_accepted=suggestions_accepted,
            suggestions_rejected=suggestions_rejected,
            suggestions_ignored=suggestions_ignored,
            suggestions_filtered=suggestions_filtered,
            suggestions_filtered_total=suggestions_filtered_total,
        ),
    )


@login_required
@require_GET
def case_similar_fragment(request: HttpRequest, case_id: str) -> HttpResponse:
    """Fragment endpoint for the Similar tab (lazy-load).
    Returns the two card partials with data attributes for the combined count.
    Viewer-allowed (read-only); cross-org/unknown -> 404.
    """
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()

    similar_cases, similar_total, similar_alerts, similar_alerts_total = _similar_for_case(
        case, request.user
    )
    can_mutate = not is_viewer(request.user)

    return render(
        request,
        "ui/_similar_tab_fragment.html",
        {
            "case": case,
            "can_mutate": can_mutate,
            "similar_cases": similar_cases,
            "similar_total": similar_total,
            "similar_alerts": similar_alerts,
            "similar_alerts_total": similar_alerts_total,
            "alerts": case.alerts.all(),  # for empty-state check in alerts card
        },
    )


def _inline_edit_fields(case: Case) -> list[dict[str, Any]]:
    """Descriptors for the General tab's inline edit icons and their `case-field-<slug>` dialogs.

    Each entry carries the field name, the label shown on the icon and the dialog title, the
    option list, and the case's current value — so the template renders the same control set for
    every field with one loop and no per-field branching. `admin_only` marks the one field the
    applier gates on `is_admin` (data_classification), so the icon can be hidden rather than
    rendered and then refused.
    """
    return [
        {
            "slug": "severity",
            "label": "Severity",
            "value": str(case.severity),
            "choices": [(str(val), label) for val, label in SEVERITY_CHOICES],
            "admin_only": False,
        },
        {
            "slug": "scope",
            "label": "Scope",
            "value": case.scope,
            "choices": list(
                taxonomy_read.choices(taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False)
            ),
            "admin_only": False,
        },
        {
            "slug": "case_type",
            "label": "Type",
            "value": case.case_type,
            "choices": list(
                taxonomy_read.choices(taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False)
            ),
            "admin_only": False,
        },
        {
            "slug": "data_classification",
            "label": "Classification",
            "value": case.data_classification,
            "choices": list(
                taxonomy_read.choices(
                    taxonomy_read.CATEGORY_DATA_CLASSIFICATION, include_hidden=False
                )
            ),
            "admin_only": True,
        },
        {
            "slug": "phase",
            "label": "Phase",
            "value": case.phase,
            "choices": list(
                taxonomy_read.choices(taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False)
            ),
            "admin_only": False,
        },
        {
            "slug": "verdict",
            "label": "Verdict",
            "value": case.verdict,
            "choices": list(taxonomy_read.choices(taxonomy_read.CATEGORY_CASE_VERDICT)),
            "admin_only": False,
        },
    ]
