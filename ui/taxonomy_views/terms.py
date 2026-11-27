"""SuperAdmin-only compiled taxonomy-term mutation views."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.taxonomy import (
    TaxonomyError,
    create_taxonomy_term,
    delete_taxonomy_term,
    update_taxonomy_term,
)
from identity.audit import record_taxonomy_change

from ..taxonomy_forms import (
    FormResult,
    service_error,
    taxonomy_term_create_form,
    taxonomy_term_edit_form,
)
from ..views.common import not_viewer
from ..views.taxonomy import _taxonomy_context

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
