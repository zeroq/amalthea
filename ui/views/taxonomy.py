"""Taxonomy page and context."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from alerts.models import AlertStatus
from cases.models import (
    Case,
    CaseStatus,
    CaseTemplate,
    Tag,
)
from core import taxonomy_read
from core.enums import (
    ALERT_STAGES,
    CASE_PHASE_CHOICES,
    CASE_SCOPE_CHOICES,
    CASE_STAGES,
    CASE_TYPE_CHOICES,
    DATA_CLASSIFICATION_CHOICES,
)
from observables.models import ObservableType

from .common import (
    _base_context,
)

logger = logging.getLogger(__name__)


def _taxonomy_context(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    """Context for `/taxonomy`, shared by the page and its mutation error re-renders.

    Hidden rows stay on this page (they are excluded from pickers, not from the editor), and
    the usage counts feed the delete dialogs so an in-use row never offers a doomed button.
    """

    from core.models import TaxonomyTerm

    # Usage counts for terms
    terms = list(TaxonomyTerm.objects.all())
    # annotate usage
    for t in terms:
        if t.category == "case_scope":
            t.case_count = Case.objects.filter(scope=t.code).count()
            t.template_count = CaseTemplate.objects.filter(scope=t.code).count()
            t.in_use_count = t.case_count + t.template_count
        elif t.category == "case_phase":
            t.case_count = Case.objects.filter(phase=t.code).count()
            t.template_count = CaseTemplate.objects.filter(phase=t.code).count()
            t.in_use_count = t.case_count + t.template_count
        elif t.category == "case_type":
            t.case_count = Case.objects.filter(case_type=t.code).count()
            t.template_count = CaseTemplate.objects.filter(case_type=t.code).count()
            t.in_use_count = t.case_count + t.template_count
        elif t.category == "data_classification":
            t.case_count = Case.objects.filter(data_classification=t.code).count()
            t.in_use_count = t.case_count
        elif t.category == "case_verdict":
            t.case_count = Case.objects.filter(verdict=t.code).count()
            t.in_use_count = t.case_count
        else:
            t.in_use_count = 0

    return _base_context(
        request,
        case_statuses=CaseStatus.objects.annotate(
            case_count=Count("cases", distinct=True)
        ).order_by("order", "value"),
        alert_statuses=AlertStatus.objects.annotate(
            alert_count=Count("alerts", distinct=True)
        ).order_by("order", "value"),
        observable_types=ObservableType.objects.annotate(
            observable_count=Count("observables", distinct=True)
        ).order_by("name"),
        tags=Tag.objects.all().order_by("name"),
        taxonomy_terms=terms,
        taxonomy_terms_by_category={
            "case_scope": [t for t in terms if t.category == "case_scope"],
            "case_phase": [t for t in terms if t.category == "case_phase"],
            "case_type": [t for t in terms if t.category == "case_type"],
            "data_classification": [t for t in terms if t.category == "data_classification"],
            "case_verdict": [t for t in terms if t.category == "case_verdict"],
        },
        scope_choices=CASE_SCOPE_CHOICES,
        phase_choices=CASE_PHASE_CHOICES,
        case_type_choices=CASE_TYPE_CHOICES,
        data_classification_choices=DATA_CLASSIFICATION_CHOICES,
        verdict_choices=taxonomy_read.choices(taxonomy_read.CATEGORY_CASE_VERDICT),
        case_stage_choices=CASE_STAGES,
        alert_stage_choices=ALERT_STAGES,
        can_manage_taxonomy=getattr(request.user, "role", "") == "superAdmin",
        **extra,
    )


@login_required
@require_GET
def taxonomy(request: HttpRequest) -> HttpResponse:
    """Aggregate taxonomy page for UI pickers and the vocabulary editor.

    ``Tag`` and ``CaseTemplate`` are imported at module level from ``cases.models`` — there
    is no ``tags`` app. (Commit 3255f1f added a local ``from tags.models import Tag`` here,
    which made ``/taxonomy`` raise ``ModuleNotFoundError`` → HTTP 500.)
    """
    return render(request, "ui/taxonomy.html", _taxonomy_context(request))
