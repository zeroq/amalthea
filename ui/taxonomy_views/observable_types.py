"""SuperAdmin-only observable-type mutation views."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from core.taxonomy import (
    TaxonomyError,
    create_observable_type,
    delete_observable_type,
    update_observable_type,
)
from observables.models import ObservableType

from ..taxonomy_forms import observable_type_form, service_error
from ..views.common import _require_superadmin_ui, not_viewer
from ._common import _audit, _delete_error, _success, _type_error

# --- Observable types --------------------------------------------------------


@not_viewer
@login_required
@require_POST
def observable_type_create(request: HttpRequest) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    modal_id = "observable-type-create-modal"
    form_id = "observable-type-create-form"
    form = observable_type_form(request.POST)
    if not form:
        return _type_error(request, modal_id=modal_id, form_id=form_id, form=form, is_edit=False)

    try:
        with transaction.atomic():
            obj, result = create_observable_type(**form.values)
            _audit(request, result, namespace="observable_type", identifier_key="name")
    except TaxonomyError as exc:
        form.errors.update(service_error(exc))
        return _type_error(request, modal_id=modal_id, form_id=form_id, form=form, is_edit=False)

    return _success(request, f"Observable type '{obj.name}' created.")


@not_viewer
@login_required
@require_POST
def observable_type_edit(request: HttpRequest, type_id: UUID) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    obj = get_object_or_404(ObservableType, pk=type_id)
    modal_id = f"observable-type-edit-{obj.id}"
    form_id = f"observable-type-edit-form-{obj.id}"
    form = observable_type_form(request.POST)
    if not form:
        return _type_error(
            request,
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            obj=obj,
            is_edit=True,
        )

    try:
        with transaction.atomic():
            result = update_observable_type(
                obj,
                name=form.values["name"],
                is_attachment=form.values["is_attachment"],
                is_case_sensitive=form.values["is_case_sensitive"],
            )
            _audit(request, result, namespace="observable_type", identifier_key="name")
    except TaxonomyError as exc:
        form.errors.update(service_error(exc))
        return _type_error(
            request,
            modal_id=modal_id,
            form_id=form_id,
            form=form,
            obj=obj,
            is_edit=True,
        )

    return _success(request, f"Observable type '{obj.name}' updated.")


@not_viewer
@login_required
@require_POST
def observable_type_delete(request: HttpRequest, type_id: UUID) -> HttpResponse:
    denied = _require_superadmin_ui(request)
    if denied:
        return denied

    obj = get_object_or_404(ObservableType, pk=type_id)
    modal_id = f"observable-type-delete-{obj.id}"
    usage_count = obj.observables.count()
    identifier = obj.name
    try:
        with transaction.atomic():
            result = delete_observable_type(obj)
            _audit(request, result, namespace="observable_type", identifier_key="name")
    except TaxonomyError as exc:
        return _delete_error(
            request,
            modal_id=modal_id,
            entity_label="observable type",
            identifier=identifier,
            message=str(exc),
            usage_count=usage_count,
        )
    return _success(request, f"Observable type '{identifier}' deleted.")
