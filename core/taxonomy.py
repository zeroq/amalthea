"""Shared taxonomy mutation services for CaseStatus, AlertStatus, and ObservableType.

Used by both API and UI views so validation and audit payload construction cannot drift.
The functions are HTTP-agnostic; callers own authorization and audit persistence.

Renaming note: `Case.status`, `Alert.status`, and `Observable.data_type` are foreign keys to
these rows, so changing `value`/`name` updates every reference without touching child rows.
`stage` is deliberately not accepted by the update helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from django.db import IntegrityError, transaction

from core.enums import ALERT_STAGES, CASE_STAGES

if TYPE_CHECKING:
    from alerts.models import AlertStatus
    from cases.models import CaseStatus
    from core.models import TaxonomyTerm
    from observables.models import ObservableType


class _HasId(Protocol):
    @property
    def id(self) -> object: ...


@dataclass(slots=True)
class TaxonomyResult:
    """Everything a caller needs to write an audit row."""

    target_type: str
    target_id: str
    action: str  # created | updated | renamed | deleted
    changed: list[str]
    old_identifier: str | None
    new_identifier: str | None


class TaxonomyError(Exception):
    """A field-level validation or in-use deletion error."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


def _clean_identifier(raw: str, *, field: str, max_length: int) -> str:
    value = (raw or "").strip()
    if not value:
        raise TaxonomyError(f"{field} is required", field)
    if len(value) > max_length:
        raise TaxonomyError(f"{field} must be {max_length} characters or fewer", field)
    return value


def _created_result(
    *, target_type: str, obj: _HasId, identifier: str, changed: list[str]
) -> TaxonomyResult:
    return TaxonomyResult(
        target_type=target_type,
        target_id=str(obj.id),
        action="created",
        changed=changed,
        old_identifier=None,
        new_identifier=identifier,
    )


def _updated_result(
    *,
    target_type: str,
    obj: _HasId,
    changed: list[str],
    renamed: bool,
    old_identifier: str | None,
    new_identifier: str | None,
) -> TaxonomyResult:
    return TaxonomyResult(
        target_type=target_type,
        target_id=str(obj.id),
        action="renamed" if renamed else "updated",
        changed=changed,
        old_identifier=old_identifier,
        new_identifier=new_identifier,
    )


def _deleted_result(*, target_type: str, target_id: str, identifier: str) -> TaxonomyResult:
    return TaxonomyResult(
        target_type=target_type,
        target_id=target_id,
        action="deleted",
        changed=[],
        old_identifier=identifier,
        new_identifier=None,
    )


# --- Case statuses -----------------------------------------------------------


def create_case_status(
    *,
    value: str,
    stage: str,
    order: int,
    description: str = "",
    hidden: bool = False,
) -> tuple[CaseStatus, TaxonomyResult]:
    from cases.models import CaseStatus

    value = _clean_identifier(value, field="value", max_length=64)
    if stage not in CASE_STAGES:
        raise TaxonomyError(f"unknown stage {stage!r}", "stage")
    if CaseStatus.objects.filter(value=value).exists():
        raise TaxonomyError("value already exists", "value")

    try:
        with transaction.atomic():
            status_obj = CaseStatus.objects.create(
                value=value,
                stage=stage,
                order=order,
                description=description or "",
                hidden=hidden,
            )
    except IntegrityError as exc:
        raise TaxonomyError("value already exists", "value") from exc

    result = _created_result(
        target_type="CaseStatus",
        obj=status_obj,
        identifier=status_obj.value,
        changed=["value", "stage", "order", "description", "hidden"],
    )
    return status_obj, result


def update_case_status(
    status: CaseStatus,
    *,
    value: str | None = None,
    order: int | None = None,
    description: str | None = None,
    hidden: bool | None = None,
) -> TaxonomyResult:
    from cases.models import CaseStatus

    changed: list[str] = []
    old_identifier = status.value
    renamed = False

    if value is not None:
        value = _clean_identifier(value, field="value", max_length=64)
        if value != status.value:
            if CaseStatus.objects.exclude(pk=status.pk).filter(value=value).exists():
                raise TaxonomyError("value already exists", "value")
            status.value = value
            changed.append("value")
            renamed = True

    if order is not None:
        status.order = order
        changed.append("order")

    if description is not None:
        status.description = description or ""
        changed.append("description")

    if hidden is not None:
        status.hidden = hidden
        changed.append("hidden")

    if not changed:
        raise TaxonomyError("No updatable field supplied", "")

    try:
        with transaction.atomic():
            status.save(update_fields=[*changed, "updated_at"])
    except IntegrityError as exc:
        raise TaxonomyError("value already exists", "value") from exc

    return _updated_result(
        target_type="CaseStatus",
        obj=status,
        changed=changed,
        renamed=renamed,
        old_identifier=old_identifier if renamed else None,
        new_identifier=status.value if renamed else None,
    )


def delete_case_status(status: CaseStatus) -> TaxonomyResult:
    if status.cases.exists():
        raise TaxonomyError("Case status is in use", "non_field")

    old_identifier = status.value
    target_id = str(status.id)
    with transaction.atomic():
        status.delete()
    return _deleted_result(target_type="CaseStatus", target_id=target_id, identifier=old_identifier)


# --- Alert statuses ----------------------------------------------------------


def create_alert_status(
    *,
    value: str,
    stage: str,
    order: int,
    description: str = "",
    hidden: bool = False,
) -> tuple[AlertStatus, TaxonomyResult]:
    from alerts.models import AlertStatus

    value = _clean_identifier(value, field="value", max_length=64)
    if stage not in ALERT_STAGES:
        raise TaxonomyError(f"unknown stage {stage!r}", "stage")
    if AlertStatus.objects.filter(value=value).exists():
        raise TaxonomyError("value already exists", "value")

    try:
        with transaction.atomic():
            status_obj = AlertStatus.objects.create(
                value=value,
                stage=stage,
                order=order,
                description=description or "",
                hidden=hidden,
            )
    except IntegrityError as exc:
        raise TaxonomyError("value already exists", "value") from exc

    result = _created_result(
        target_type="AlertStatus",
        obj=status_obj,
        identifier=status_obj.value,
        changed=["value", "stage", "order", "description", "hidden"],
    )
    return status_obj, result


def update_alert_status(
    status: AlertStatus,
    *,
    value: str | None = None,
    order: int | None = None,
    description: str | None = None,
    hidden: bool | None = None,
) -> TaxonomyResult:
    from alerts.models import AlertStatus

    changed: list[str] = []
    old_identifier = status.value
    renamed = False

    if value is not None:
        value = _clean_identifier(value, field="value", max_length=64)
        if value != status.value:
            if AlertStatus.objects.exclude(pk=status.pk).filter(value=value).exists():
                raise TaxonomyError("value already exists", "value")
            status.value = value
            changed.append("value")
            renamed = True

    if order is not None:
        status.order = order
        changed.append("order")

    if description is not None:
        status.description = description or ""
        changed.append("description")

    if hidden is not None:
        status.hidden = hidden
        changed.append("hidden")

    if not changed:
        raise TaxonomyError("No updatable field supplied", "")

    try:
        with transaction.atomic():
            status.save(update_fields=[*changed, "updated_at"])
    except IntegrityError as exc:
        raise TaxonomyError("value already exists", "value") from exc

    return _updated_result(
        target_type="AlertStatus",
        obj=status,
        changed=changed,
        renamed=renamed,
        old_identifier=old_identifier if renamed else None,
        new_identifier=status.value if renamed else None,
    )


def delete_alert_status(status: AlertStatus) -> TaxonomyResult:
    if status.alerts.exists():
        raise TaxonomyError("Alert status is in use", "non_field")

    old_identifier = status.value
    target_id = str(status.id)
    with transaction.atomic():
        status.delete()
    return _deleted_result(
        target_type="AlertStatus", target_id=target_id, identifier=old_identifier
    )


# --- Observable types --------------------------------------------------------


def create_observable_type(
    *,
    name: str,
    is_attachment: bool = False,
    is_case_sensitive: bool = False,
) -> tuple[ObservableType, TaxonomyResult]:
    from observables.models import ObservableType

    name = _clean_identifier(name, field="name", max_length=100)
    if ObservableType.objects.filter(name=name).exists():
        raise TaxonomyError("name already exists", "name")

    try:
        with transaction.atomic():
            otype = ObservableType.objects.create(
                name=name,
                is_attachment=is_attachment,
                is_case_sensitive=is_case_sensitive,
            )
    except IntegrityError as exc:
        raise TaxonomyError("name already exists", "name") from exc

    result = _created_result(
        target_type="ObservableType",
        obj=otype,
        identifier=otype.name,
        changed=["name", "is_attachment", "is_case_sensitive"],
    )
    return otype, result


def update_observable_type(
    otype: ObservableType,
    *,
    name: str | None = None,
    is_attachment: bool | None = None,
    is_case_sensitive: bool | None = None,
) -> TaxonomyResult:
    from observables.models import ObservableType

    changed: list[str] = []
    old_identifier = otype.name
    renamed = False

    if name is not None:
        name = _clean_identifier(name, field="name", max_length=100)
        if name != otype.name:
            if ObservableType.objects.exclude(pk=otype.pk).filter(name=name).exists():
                raise TaxonomyError("name already exists", "name")
            otype.name = name
            changed.append("name")
            renamed = True

    if is_attachment is not None:
        otype.is_attachment = is_attachment
        changed.append("is_attachment")

    if is_case_sensitive is not None:
        otype.is_case_sensitive = is_case_sensitive
        changed.append("is_case_sensitive")

    if not changed:
        raise TaxonomyError("No updatable field supplied", "")

    # save(), not update(), so ObservableType.save() can re-hash on a case-rule flip.
    try:
        with transaction.atomic():
            otype.save(update_fields=[*changed, "updated_at"])
    except IntegrityError as exc:
        raise TaxonomyError("name already exists", "name") from exc

    return _updated_result(
        target_type="ObservableType",
        obj=otype,
        changed=changed,
        renamed=renamed,
        old_identifier=old_identifier if renamed else None,
        new_identifier=otype.name if renamed else None,
    )


def delete_observable_type(otype: ObservableType) -> TaxonomyResult:
    if otype.observables.exists():
        raise TaxonomyError("Observable type is in use", "non_field")

    old_identifier = otype.name
    target_id = str(otype.id)
    with transaction.atomic():
        otype.delete()
    return _deleted_result(
        target_type="ObservableType", target_id=target_id, identifier=old_identifier
    )


# --- Taxonomy terms (compiled enums) ----------------------------------------


def _clean_term_code(raw: str) -> str:
    code = (raw or "").strip().lower().replace(" ", "_")
    if not code:
        raise TaxonomyError("code is required", "code")
    if len(code) > 64:
        raise TaxonomyError("code must be 64 characters or fewer", "code")
    # basic validation
    import re

    if not re.match(r"^[a-z][a-z0-9_]*$", code):
        raise TaxonomyError(
            "code must start with a letter and contain only lowercase letters, digits, and underscores",
            "code",
        )
    return code


def _clean_label(raw: str) -> str:
    label = (raw or "").strip()
    if not label:
        raise TaxonomyError("label is required", "label")
    if len(label) > 128:
        raise TaxonomyError("label must be 128 characters or fewer", "label")
    return label


def create_taxonomy_term(
    *,
    category: str,
    code: str,
    label: str,
    order: int = 0,
    hidden: bool = False,
    protected: bool = False,
    is_closing: bool = False,
) -> tuple[TaxonomyTerm, TaxonomyResult]:
    from core.models import TaxonomyTerm

    code = _clean_term_code(code)
    label = _clean_label(label)
    if category not in dict(TaxonomyTerm.CATEGORY_CHOICES):
        raise TaxonomyError("invalid category", "category")
    if TaxonomyTerm.objects.filter(category=category, code=code).exists():
        raise TaxonomyError("code already exists", "code")
    if category != "case_verdict" and is_closing:
        raise TaxonomyError("is_closing only applies to case_verdict", "is_closing")

    try:
        with transaction.atomic():
            term = TaxonomyTerm.objects.create(
                category=category,
                code=code,
                label=label,
                order=order or 0,
                hidden=hidden,
                protected=protected,
                is_closing=is_closing if category == "case_verdict" else False,
            )
    except IntegrityError as exc:
        raise TaxonomyError("code already exists", "code") from exc

    result = _created_result(
        target_type="TaxonomyTerm",
        obj=term,
        identifier=f"{category}:{code}",
        changed=["category", "code", "label", "order", "hidden", "protected", "is_closing"],
    )
    return term, result


def update_taxonomy_term(
    term: TaxonomyTerm,
    *,
    label: str | None = None,
    order: int | None = None,
    hidden: bool | None = None,
    is_closing: bool | None = None,
) -> TaxonomyResult:

    changed: list[str] = []
    old_identifier = f"{term.category}:{term.code}"
    renamed = False

    if label is not None:
        label = _clean_label(label)
        if label != term.label:
            term.label = label
            changed.append("label")
            renamed = True  # treat label change as update action but track

    if order is not None:
        term.order = order
        changed.append("order")

    if hidden is not None:
        term.hidden = hidden
        changed.append("hidden")

    if is_closing is not None and term.category == "case_verdict":
        term.is_closing = bool(is_closing)
        changed.append("is_closing")

    if not changed:
        raise TaxonomyError("No updatable field supplied", "")

    try:
        with transaction.atomic():
            term.save(update_fields=[*changed, "updated_at"])
    except IntegrityError as exc:
        raise TaxonomyError("invalid update", "") from exc

    return _updated_result(
        target_type="TaxonomyTerm",
        obj=term,
        changed=changed,
        renamed=False,
        old_identifier=old_identifier if renamed and "label" in changed else None,
        new_identifier=term.label if renamed and "label" in changed else None,
    )


def delete_taxonomy_term(term: TaxonomyTerm) -> TaxonomyResult:
    from cases.models import Case, CaseTemplate

    if term.protected:
        raise TaxonomyError("Protected term cannot be deleted", "non_field")

    # check in use
    if term.category == "case_scope":
        if (
            Case.objects.filter(scope=term.code).exists()
            or CaseTemplate.objects.filter(scope=term.code).exists()
        ):
            raise TaxonomyError("Term is in use", "non_field")
    elif term.category == "case_phase":
        if (
            Case.objects.filter(phase=term.code).exists()
            or CaseTemplate.objects.filter(phase=term.code).exists()
        ):
            raise TaxonomyError("Term is in use", "non_field")
    elif term.category == "case_type":
        if (
            Case.objects.filter(case_type=term.code).exists()
            or CaseTemplate.objects.filter(case_type=term.code).exists()
        ):
            raise TaxonomyError("Term is in use", "non_field")
    elif term.category == "data_classification":
        if Case.objects.filter(data_classification=term.code).exists():
            raise TaxonomyError("Term is in use", "non_field")
    elif term.category == "case_verdict":
        if Case.objects.filter(verdict=term.code).exists():
            raise TaxonomyError("Term is in use", "non_field")

    old_identifier = f"{term.category}:{term.code}"
    target_id = str(term.id)
    with transaction.atomic():
        term.delete()
    return _deleted_result(
        target_type="TaxonomyTerm", target_id=target_id, identifier=old_identifier
    )
