"""SuperAdmin-only mutation views for the three database-backed taxonomy vocabularies.

Every write goes through `core.taxonomy`, the same service the API uses, so validation,
rename semantics, in-use deletion protection, and audit payloads cannot drift between the
two surfaces. These views add only HTTP concerns: session auth, the superAdmin gate,
form parsing, flash messages, and the HTMX/no-HTMX response split.

Progressive enhancement: the modal footer is a real POST form. Without HTMX the view redirects
to `/taxonomy` on success and re-renders the page with the offending dialog open on error.
With HTMX, success is `204 + HX-Redirect`; validation is `422` with a body fragment, which
`modal.js` opts into swapping (HTMX otherwise treats 4xx as non-swaps).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from alerts.models import AlertStatus
from cases.models import CaseStatus
from core.enums import ALERT_STAGES, CASE_STAGES
from core.taxonomy import (
    TaxonomyError,
    TaxonomyResult,
    create_alert_status,
    create_case_status,
    create_observable_type,
    create_taxonomy_term,
    delete_alert_status,
    delete_case_status,
    delete_observable_type,
    delete_taxonomy_term,
    update_alert_status,
    update_case_status,
    update_observable_type,
    update_taxonomy_term,
)
from identity.audit import record_taxonomy_change
from observables.models import ObservableType

from .taxonomy_forms import (
    FormResult,
    observable_type_form,
    service_error,
    status_form,
    taxonomy_term_create_form,
    taxonomy_term_edit_form,
)
from .views import (
    _htmx_redirect,
    _require_superadmin_ui,
    _taxonomy_context,
    not_viewer,
)

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


# --- Taxonomy terms (compiled enums) -----------------------------------------


def _term_create_context(request: HttpRequest, category: str, form: FormResult) -> dict[str, Any]:
    modal_id = f"taxonomy-term-create-{category}-modal"
    form_id = f"taxonomy-term-create-{category}-form"
    return {
        "modal_id": modal_id,
        "form_id": form_id,
        "category": category,
        "is_edit": False,
        "body_template": "ui/_taxonomy_term_form.html",
        "title": f"New {category.replace('_', ' ')} term",
        "confirm_text": "Create",
        "form_action": reverse("ui-taxonomy-term-create"),
        "form_hx_post": reverse("ui-taxonomy-term-create"),
        "form_hx_target": f"#{modal_id}-body",
        "form_values": form.values,
        "errors": form.errors,
        "size": "md",
    }


def _term_edit_context(
    request: HttpRequest, obj: Any, category: str, form: FormResult
) -> dict[str, Any]:
    modal_id = f"taxonomy-term-edit-{obj.id}"
    form_id = f"taxonomy-term-edit-form-{obj.id}"
    return {
        "modal_id": modal_id,
        "form_id": form_id,
        "obj": obj,
        "category": category,
        "is_edit": True,
        "body_template": "ui/_taxonomy_term_form.html",
        "title": "Edit taxonomy term",
        "confirm_text": "Save",
        "form_action": reverse("ui-taxonomy-term-edit", kwargs={"term_id": obj.id}),
        "form_hx_post": reverse("ui-taxonomy-term-edit", kwargs={"term_id": obj.id}),
        "form_hx_target": f"#{modal_id}-body",
        "form_values": form.values,
        "errors": form.errors,
        "size": "md",
    }


@not_viewer
@login_required
@require_POST
def taxonomy_term_create(request: HttpRequest) -> HttpResponse:

    if getattr(request.user, "role", "") != "superAdmin":
        return render(request, "ui/404.html", status=404)

    form = taxonomy_term_create_form(request.POST)
    category = form.values.get("category") or ""
    if form.errors:
        if request.headers.get("HX-Request"):
            return render(
                request,
                "ui/_modal_body.html",
                _term_create_context(request, category, form),
                status=422,
            )
        ctx = _taxonomy_context(
            request,
            open_modal_id=f"taxonomy-term-create-{category}-modal",
            form_state={
                "modal_id": f"taxonomy-term-create-{category}-modal",
                "values": form.values,
                "errors": form.errors,
            },
        )
        return render(request, "ui/taxonomy.html", ctx, status=200)

    try:
        with transaction.atomic():
            _term, result = create_taxonomy_term(
                category=form.values.get("category") or "",
                code=form.values.get("code") or "",
                label=form.values.get("label") or "",
                order=int(form.values.get("order") or 0),
                hidden=bool(form.values.get("hidden")),
                protected=bool(form.values.get("protected")),
                is_closing=bool(form.values.get("is_closing")),
            )
            details: dict[str, object] = {"changed": result.changed}
            if result.old_identifier is not None:
                details["old"] = {"identifier": result.old_identifier}
            if result.new_identifier is not None:
                details["new"] = {"identifier": result.new_identifier}
            record_taxonomy_change(
                request,
                actor=request.user,
                action=f"taxonomy.term.{result.action}",
                target_type=result.target_type,
                target_id=result.target_id,
                details=details,
            )
    except TaxonomyError as exc:
        form = FormResult(
            values=form.values,
            errors=service_error(exc),
        )
        if request.headers.get("HX-Request"):
            return render(
                request,
                "ui/_modal_body.html",
                _term_create_context(request, category, form),
                status=422,
            )
        ctx = _taxonomy_context(
            request,
            open_modal_id=f"taxonomy-term-create-{category}-modal",
            form_state={
                "modal_id": f"taxonomy-term-create-{category}-modal",
                "values": form.values,
                "errors": form.errors,
            },
        )
        return render(request, "ui/taxonomy.html", ctx, status=200)

    if request.headers.get("HX-Request"):
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = "/taxonomy"
        return resp
    messages.success(request, "Taxonomy term created.")
    return redirect("ui-taxonomy")


@not_viewer
@login_required
@require_POST
def taxonomy_term_edit(request: HttpRequest, term_id: str) -> HttpResponse:
    from core.models import TaxonomyTerm

    if getattr(request.user, "role", "") != "superAdmin":
        return render(request, "ui/404.html", status=404)

    term = get_object_or_404(TaxonomyTerm, pk=term_id)
    form = taxonomy_term_edit_form(request.POST)
    if form.errors:
        if request.headers.get("HX-Request"):
            return render(
                request,
                "ui/_modal_body.html",
                _term_edit_context(request, term, term.category, form),
                status=422,
            )
        ctx = _taxonomy_context(
            request,
            open_modal_id=f"taxonomy-term-edit-{term.id}",
            form_state={
                "modal_id": f"taxonomy-term-edit-{term.id}",
                "values": form.values,
                "errors": form.errors,
            },
        )
        return render(request, "ui/taxonomy.html", ctx, status=200)

    try:
        with transaction.atomic():
            result = update_taxonomy_term(
                term,
                label=form.values.get("label") or None,
                order=int(form.values.get("order") or term.order),
                hidden=bool(form.values.get("hidden")),
                is_closing=bool(form.values.get("is_closing"))
                if term.category == "case_verdict"
                else None,
            )
            details: dict[str, object] = {"changed": result.changed}
            if result.old_identifier is not None:
                details["old"] = {"identifier": result.old_identifier}
            if result.new_identifier is not None:
                details["new"] = {"identifier": result.new_identifier}
            record_taxonomy_change(
                request,
                actor=request.user,
                action=f"taxonomy.term.{result.action}",
                target_type=result.target_type,
                target_id=result.target_id,
                details=details,
            )
    except TaxonomyError as exc:
        form = FormResult(values=form.values, errors=service_error(exc))
        if request.headers.get("HX-Request"):
            return render(
                request,
                "ui/_modal_body.html",
                _term_edit_context(request, term, term.category, form),
                status=422,
            )
        ctx = _taxonomy_context(
            request,
            open_modal_id=f"taxonomy-term-edit-{term.id}",
            form_state={
                "modal_id": f"taxonomy-term-edit-{term.id}",
                "values": form.values,
                "errors": form.errors,
            },
        )
        return render(request, "ui/taxonomy.html", ctx, status=200)

    if request.headers.get("HX-Request"):
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = "/taxonomy"
        return resp
    messages.success(request, "Taxonomy term updated.")
    return redirect("ui-taxonomy")


@not_viewer
@login_required
@require_POST
def taxonomy_term_delete(request: HttpRequest, term_id: str) -> HttpResponse:
    from core.models import TaxonomyTerm

    if getattr(request.user, "role", "") != "superAdmin":
        return render(request, "ui/404.html", status=404)

    term = get_object_or_404(TaxonomyTerm, pk=term_id)
    try:
        with transaction.atomic():
            result = delete_taxonomy_term(term)
            details: dict[str, object] = {"changed": result.changed}
            if result.old_identifier is not None:
                details["old"] = {"identifier": result.old_identifier}
            record_taxonomy_change(
                request,
                actor=request.user,
                action=f"taxonomy.term.{result.action}",
                target_type=result.target_type,
                target_id=result.target_id,
                details=details,
            )
    except TaxonomyError as exc:
        messages.error(request, exc.message)
        if request.headers.get("HX-Request"):
            resp = HttpResponse(status=204)
            resp["HX-Redirect"] = "/taxonomy"
            return resp
        return redirect("ui-taxonomy")

    if request.headers.get("HX-Request"):
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = "/taxonomy"
        return resp
    messages.success(request, "Taxonomy term deleted.")
    return redirect("ui-taxonomy")
