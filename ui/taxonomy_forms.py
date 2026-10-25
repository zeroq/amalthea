"""Parsing/validation for the three editable taxonomy vocabularies.

The domain service (`core.taxonomy`) owns uniqueness, in-use checks, and audit payload
construction. This module only turns an HTML form body into service kwargs and field errors;
keeping it separate lets the create and edit modals share one error surface.

Checkbox semantics are intentionally HTML's: an absent checkbox means ``False``. The API uses
JSON booleans and does not go through this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.taxonomy import TaxonomyError


@dataclass(slots=True)
class FormResult:
    values: dict[str, Any]
    errors: dict[str, str]

    def __bool__(self) -> bool:
        return not self.errors


def _bad(errors: dict[str, str], field: str, message: str) -> None:
    errors.setdefault(field, message)


def _clean_text(post: Mapping[str, Any], field: str) -> str:
    return str(post.get(field) or "").strip()


def _parse_order(post: Mapping[str, Any], *, default: int) -> tuple[int, str | None]:
    raw = str(post.get("order") or "").strip()
    if not raw:
        return default, None
    try:
        return int(raw), None
    except ValueError:
        return default, "Order must be a whole number."


def status_form(
    post: Mapping[str, Any],
    *,
    stages: tuple[str, ...],
    is_edit: bool,
    existing_order: int = 0,
) -> FormResult:
    """Parse the shared case/alert status form body."""
    errors: dict[str, str] = {}
    value = _clean_text(post, "value")
    if not value:
        _bad(errors, "value", "Value is required.")

    stage = str(post.get("stage") or "").strip()
    if not is_edit and stage not in stages:
        _bad(errors, "stage", "Choose a valid stage.")

    order, order_error = _parse_order(post, default=existing_order)
    if order_error:
        _bad(errors, "order", order_error)

    return FormResult(
        values={
            "value": value,
            "stage": stage,
            "order": order,
            "description": str(post.get("description") or "").strip(),
            "hidden": post.get("hidden") == "on",
        },
        errors=errors,
    )


def observable_type_form(post: Mapping[str, Any]) -> FormResult:
    """Parse the observable-type form body."""
    errors: dict[str, str] = {}
    name = _clean_text(post, "name")
    if not name:
        _bad(errors, "name", "Name is required.")

    return FormResult(
        values={
            "name": name,
            "is_attachment": post.get("is_attachment") == "on",
            "is_case_sensitive": post.get("is_case_sensitive") == "on",
        },
        errors=errors,
    )


def service_error(exc: TaxonomyError) -> dict[str, str]:
    """Map a domain error onto the form field the modal renders."""
    field = exc.field or ""
    return {field or "non_field": exc.message}


# --- Taxonomy terms (compiled enums) -----------------------------------------


def taxonomy_term_create_form(post: Mapping[str, Any]) -> FormResult:
    """Parse the taxonomy term create form body."""
    errors: dict[str, str] = {}
    category = (post.get("category") or "").strip()
    code = (post.get("code") or "").strip()
    label = (post.get("label") or "").strip()
    order_raw = post.get("order")
    hidden = post.get("hidden") == "on"
    is_closing = post.get("is_closing") == "on"
    protected = post.get("protected") == "on"

    if not category:
        _bad(errors, "category", "Category is required.")
    if not code:
        _bad(errors, "code", "Code is required.")
    if not label:
        _bad(errors, "label", "Label is required.")

    if order_raw not in (None, ""):
        try:
            int(order_raw)
        except (TypeError, ValueError):
            _bad(errors, "order", "Order must be an integer.")

    return FormResult(
        values={
            "category": category,
            "code": code,
            "label": label,
            "order": order_raw if order_raw not in (None, "") else "",
            "hidden": hidden,
            "is_closing": is_closing,
            "protected": protected,
        },
        errors=errors,
    )


def taxonomy_term_edit_form(post: Mapping[str, Any]) -> FormResult:
    """Parse the taxonomy term edit form body."""
    errors: dict[str, str] = {}
    label = (post.get("label") or "").strip()
    order_raw = post.get("order")
    hidden = post.get("hidden") == "on"
    is_closing = post.get("is_closing") == "on"

    if not label:
        _bad(errors, "label", "Label is required.")

    if order_raw not in (None, ""):
        try:
            int(order_raw)
        except (TypeError, ValueError):
            _bad(errors, "order", "Order must be an integer.")

    return FormResult(
        values={
            "label": label,
            "order": order_raw if order_raw not in (None, "") else "",
            "hidden": hidden,
            "is_closing": is_closing,
        },
        errors=errors,
    )
