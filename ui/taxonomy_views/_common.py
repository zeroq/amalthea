"""Shared helpers for taxonomy mutation views."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from alerts.models import AlertStatus
from cases.models import CaseStatus
from core.enums import ALERT_STAGES, CASE_STAGES
from core.taxonomy import TaxonomyResult
from identity.audit import record_taxonomy_change
from observables.models import ObservableType

from ..taxonomy_forms import FormResult
from ..views.common import _htmx_redirect
from ..views.taxonomy import _taxonomy_context

_STATUS_PARTIALS = {
    "CaseStatus": "ui/_taxonomy_case_status_form.html",
    "AlertStatus": "ui/_taxonomy_alert_status_form.html",
}
_TYPE_PARTIAL = "ui/_taxonomy_observable_type_form.html"
_DELETE_PARTIAL = "ui/_taxonomy_delete_body.html"


def _audit_details(result: TaxonomyResult, *, identifier_key: str) -> dict[str, Any]:
    details: dict[str, Any] = {"changed": result.changed}
    if result.old_identifier is not None:
        details["old"] = {identifier_key: result.old_identifier}
    if result.new_identifier is not None:
        details["new"] = {identifier_key: result.new_identifier}
    return details


def _audit(
    request: HttpRequest,
    result: TaxonomyResult,
    *,
    namespace: str,
    identifier_key: str,
) -> None:
    record_taxonomy_change(
        request,
        actor=request.user,
        action=f"taxonomy.{namespace}.{result.action}",
        target_type=result.target_type,
        target_id=result.target_id,
        details=_audit_details(result, identifier_key=identifier_key),
    )


def _success(request: HttpRequest, message: str) -> HttpResponse:
    if request.headers.get("HX-Request"):
        return _htmx_redirect(reverse("ui-taxonomy"))
    messages.success(request, message)
    return redirect("ui-taxonomy")


def _status_error(
    request: HttpRequest,
    *,
    target_type: str,
    modal_id: str,
    form_id: str,
    form: FormResult,
    obj: CaseStatus | AlertStatus | None = None,
    is_edit: bool,
) -> HttpResponse:
    """Re-render either the HTMX modal body or the whole taxonomy page."""
    if request.headers.get("HX-Request"):
        return render(
            request,
            _STATUS_PARTIALS[target_type],
            {
                "modal_id": modal_id,
                "form_id": form_id,
                "is_edit": is_edit,
                "obj": obj,
                "form_values": form.values,
                "errors": form.errors,
                "stage_choices": CASE_STAGES if target_type == "CaseStatus" else ALERT_STAGES,
            },
            status=422,
        )

    messages.error(request, "Please correct the highlighted field.")
    return render(
        request,
        "ui/taxonomy.html",
        _taxonomy_context(
            request,
            open_modal_id=modal_id,
            form_state={"modal_id": modal_id, "values": form.values, "errors": form.errors},
        ),
    )


def _type_error(
    request: HttpRequest,
    *,
    modal_id: str,
    form_id: str,
    form: FormResult,
    obj: ObservableType | None = None,
    is_edit: bool,
) -> HttpResponse:
    if request.headers.get("HX-Request"):
        return render(
            request,
            _TYPE_PARTIAL,
            {
                "modal_id": modal_id,
                "form_id": form_id,
                "is_edit": is_edit,
                "obj": obj,
                "form_values": form.values,
                "errors": form.errors,
            },
            status=422,
        )

    messages.error(request, "Please correct the highlighted field.")
    return render(
        request,
        "ui/taxonomy.html",
        _taxonomy_context(
            request,
            open_modal_id=modal_id,
            form_state={"modal_id": modal_id, "values": form.values, "errors": form.errors},
        ),
    )


def _delete_error(
    request: HttpRequest,
    *,
    modal_id: str,
    entity_label: str,
    identifier: str,
    message: str,
    usage_count: int,
) -> HttpResponse:
    context = {
        "entity_label": entity_label,
        "identifier": identifier,
        "usage_count": usage_count,
        "error": message,
    }
    if request.headers.get("HX-Request"):
        return render(request, _DELETE_PARTIAL, context, status=422)
    messages.error(request, message)
    return render(
        request,
        "ui/taxonomy.html",
        _taxonomy_context(request, open_modal_id=modal_id),
    )
