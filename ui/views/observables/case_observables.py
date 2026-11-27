"""Case-scoped observable link actions."""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.http import require_POST
from rest_framework.response import Response

from cases.ledger import append_timeline_event
from cases.models import CaseObservable
from cases.views import _observable_cross_case_guard, _set_observable_fields
from identity.scoping import is_case_owner, resolve_case
from observables.extractor import add_observable_with_status

from ..common import _case_detail_redirect, not_viewer


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
