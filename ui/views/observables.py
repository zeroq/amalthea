"""Observable views and case-scoped observable CRUD."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from rest_framework.response import Response

from cases.ledger import append_timeline_event
from cases.models import (
    CaseObservable,
)
from cases.views import (
    _observable_affected_cases,
    _observable_cross_case_guard,
    _set_observable_fields,
)
from core.enums import (
    PAP_CHOICES,
    TLP_CHOICES,
)
from identity.models import AuditLog
from identity.scoping import (
    is_case_owner,
    org_q,
    resolve_case,
    resolve_observable,
)
from observables.extractor import add_observable_with_status
from observables.models import Observable, ObservableType
from observables.suggestions import create_suggestions_for_case
from ui.pagination import paginate

from .common import (
    _base_context,
    _case_detail_redirect,
    not_viewer,
)

logger = logging.getLogger(__name__)


@not_viewer
@login_required
@require_POST
def case_extract_observables(request: HttpRequest, case_id: str) -> HttpResponse:
    """Re-run extraction over the case text, creating suggestions for review.

    The hand-pressable version of the Module D loop. Creates/updates `ObservableSuggestion`
    rows idempotently. Analysts review and accept suggestions to link observables and fire automation.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    with transaction.atomic():
        # Re-extract must reproduce the same unique keys as the original ingestion, otherwise it
        # creates a second set of suggestions (the unique constraint includes `source_alert`).
        # Mirror escalation: one pass per linked alert with that alert's payload, plus the case
        # text alone when the case has no linked alert.
        alerts = list(case.alerts.all())
        if alerts:
            suggestions = []
            for alert in alerts:
                suggestions.extend(
                    create_suggestions_for_case(
                        case,
                        case.title,
                        case.description,
                        alert.title,
                        source_alert=alert,
                        raw_payload=alert.raw_payload,
                    )
                )
        else:
            suggestions = create_suggestions_for_case(case, case.title, case.description)
    proposed_count = sum(1 for s in suggestions if s.status == "proposed")
    messages.success(
        request,
        f"Extraction complete: {proposed_count} new suggestion(s) created for review.",
    )
    return _case_detail_redirect(request, case.number)


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

    base_queryset = (
        Observable.objects.select_related("data_type", "owner_org")
        .prefetch_related("case_observables__case")
        .filter(org_q("owner_org", request.user))
    )
    unfiltered_count = base_queryset.count()

    queryset = base_queryset
    if query:
        queryset = queryset.filter(data__icontains=query)
    if type_filter:
        queryset = queryset.filter(data_type_id=type_filter)

    # Pagination for the observables list (primary list P)
    has_filter = bool(query or type_filter)
    page_obj = paginate(request, queryset.order_by("-created_at"))
    filtered_from = (
        unfiltered_count if has_filter and unfiltered_count > page_obj.paginator.count else None
    )

    return render(
        request,
        "ui/observable_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
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

    # Linked cases rail (R surface) — cap with honest total (Q3/Q4, D1)
    from ui.pagination import RAIL_CAP

    cases_qs = observable.case_observables.select_related("case__owner_org").order_by(
        "-case__start_date"
    )
    cases_total = cases_qs.count()
    cases = list(cases_qs[:RAIL_CAP])

    return render(
        request,
        "ui/observable_detail.html",
        _base_context(
            request,
            observable=observable,
            cases=cases,
            cases_total=cases_total,
            affected_cases=affected_cases,
            is_owner=is_cross_org is False,  # owner org can manage
            is_cross_org=is_cross_org,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
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
