"""SuperAdmin-only alert-status mutation views."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from alerts.models import AlertStatus
from core.enums import ALERT_STAGES
from core.taxonomy import (
    TaxonomyError,
    create_alert_status,
    delete_alert_status,
    update_alert_status,
)

from ..taxonomy_forms import service_error, status_form
from ..views.common import _require_superadmin_ui, not_viewer
from ._common import _audit, _delete_error, _status_error, _success

# --- Alert statuses ----------------------------------------------------------


@not_viewer
@login_required
@require_POST
def alert_status_create(request: HttpRequest) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    modal_id = "alert-status-create-modal"
    form_id = "alert-status-create-form"
    form = status_form(request.POST, stages=ALERT_STAGES, is_edit=False)
    if not form:
        return _status_error(
            request,
            target_type="AlertStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            is_edit=False,
        )

    try:
        with transaction.atomic():
            obj, result = create_alert_status(**form.values)
            _audit(request, result, namespace="alert_status", identifier_key="value")
    except TaxonomyError as exc:
        form.errors.update(service_error(exc))
        return _status_error(
            request,
            target_type="AlertStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            is_edit=False,
        )

    return _success(request, f"Alert status '{obj.value}' created.")


@not_viewer
@login_required
@require_POST
def alert_status_edit(request: HttpRequest, status_id: UUID) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    obj = get_object_or_404(AlertStatus, pk=status_id)
    modal_id = f"alert-status-edit-{obj.id}"
    form_id = f"alert-status-edit-form-{obj.id}"
    form = status_form(request.POST, stages=ALERT_STAGES, is_edit=True, existing_order=obj.order)
    if not form:
        return _status_error(
            request,
            target_type="AlertStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            obj=obj,
            is_edit=True,
        )

    try:
        with transaction.atomic():
            result = update_alert_status(
                obj,
                value=form.values["value"],
                order=form.values["order"],
                description=form.values["description"],
                hidden=form.values["hidden"],
            )
            _audit(request, result, namespace="alert_status", identifier_key="value")
    except TaxonomyError as exc:
        form.errors.update(service_error(exc))
        return _status_error(
            request,
            target_type="AlertStatus",
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            obj=obj,
            is_edit=True,
        )

    return _success(request, f"Alert status '{obj.value}' updated.")


@not_viewer
@login_required
@require_POST
def alert_status_delete(request: HttpRequest, status_id: UUID) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    obj = get_object_or_404(AlertStatus, pk=status_id)
    modal_id = f"alert-status-delete-{obj.id}"
    usage_count = obj.alerts.count()
    identifier = obj.value
    try:
        with transaction.atomic():
            result = delete_alert_status(obj)
            _audit(request, result, namespace="alert_status", identifier_key="value")
    except TaxonomyError as exc:
        return _delete_error(
            request,
            modal_id=modal_id,
            entity_label="alert status",
            identifier=identifier,
            message=str(exc),
            usage_count=usage_count,
        )
    return _success(request, f"Alert status '{identifier}' deleted.")
