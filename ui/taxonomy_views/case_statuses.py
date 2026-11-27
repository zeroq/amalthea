"""SuperAdmin-only case-status mutation views."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from cases.models import CaseStatus
from core.enums import CASE_STAGES
from core.taxonomy import (
    TaxonomyError,
    create_case_status,
    delete_case_status,
    update_case_status,
)

from ..taxonomy_forms import service_error, status_form
from ..views.common import _require_superadmin_ui, not_viewer
from ._common import _audit, _delete_error, _status_error, _success

# --- Case statuses -----------------------------------------------------------


@not_viewer
@login_required
@require_POST
def case_status_create(request: HttpRequest) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    modal_id = "case-status-create-modal"
    form_id = "case-status-create-form"
    form = status_form(request.POST, stages=CASE_STAGES, is_edit=False)
    if not form:
        return _status_error(
            request,
            target_type="CaseStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            is_edit=False,
        )

    try:
        with transaction.atomic():
            obj, result = create_case_status(**form.values)
            _audit(request, result, namespace="case_status", identifier_key="value")
    except TaxonomyError as exc:
        form.errors.update(service_error(exc))
        return _status_error(
            request,
            target_type="CaseStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            is_edit=False,
        )

    return _success(request, f"Case status '{obj.value}' created.")


@not_viewer
@login_required
@require_POST
def case_status_edit(request: HttpRequest, status_id: UUID) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    obj = get_object_or_404(CaseStatus, pk=status_id)
    modal_id = f"case-status-edit-{obj.id}"
    form_id = f"case-status-edit-form-{obj.id}"
    form = status_form(request.POST, stages=CASE_STAGES, is_edit=True, existing_order=obj.order)
    if not form:
        return _status_error(
            request,
            target_type="CaseStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            obj=obj,
            is_edit=True,
        )

    try:
        with transaction.atomic():
            result = update_case_status(
                obj,
                value=form.values["value"],
                order=form.values["order"],
                description=form.values["description"],
                hidden=form.values["hidden"],
            )
            _audit(request, result, namespace="case_status", identifier_key="value")
    except TaxonomyError as exc:
        form.errors.update(service_error(exc))
        return _status_error(
            request,
            target_type="CaseStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            obj=obj,
            is_edit=True,
        )

    return _success(request, f"Case status '{obj.value}' updated.")


@not_viewer
@login_required
@require_POST
def case_status_delete(request: HttpRequest, status_id: UUID) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    obj = get_object_or_404(CaseStatus, pk=status_id)
    modal_id = f"case-status-delete-{obj.id}"
    usage_count = obj.cases.count()
    identifier = obj.value
    try:
        with transaction.atomic():
            result = delete_case_status(obj)
            _audit(request, result, namespace="case_status", identifier_key="value")
    except TaxonomyError as exc:
        return _delete_error(
            request,
            modal_id=modal_id,
            entity_label="case status",
            identifier=identifier,
            message=str(exc),
            usage_count=usage_count,
        )
    return _success(request, f"Case status '{identifier}' deleted.")
