"""Read-only taxonomy resolver for compiled vocabularies.

TaxonomyTerm is the runtime source of truth for the five compiled categories.
This module must never query the database at import time and must not import
cases/ui to avoid import cycles.
"""

from __future__ import annotations

CATEGORY_CASE_SCOPE = "case_scope"
CATEGORY_CASE_PHASE = "case_phase"
CATEGORY_CASE_TYPE = "case_type"
CATEGORY_DATA_CLASSIFICATION = "data_classification"
CATEGORY_CASE_VERDICT = "case_verdict"

ALL_CATEGORIES = (
    CATEGORY_CASE_SCOPE,
    CATEGORY_CASE_PHASE,
    CATEGORY_CASE_TYPE,
    CATEGORY_DATA_CLASSIFICATION,
    CATEGORY_CASE_VERDICT,
)


def _get_term_model():
    from core.models import TaxonomyTerm

    return TaxonomyTerm


def choices(category: str, *, include_hidden: bool = False) -> tuple[tuple[str, str], ...]:
    term_model = _get_term_model()
    qs = term_model.objects.filter(category=category)
    if not include_hidden:
        qs = qs.filter(hidden=False)
    return tuple((t.code, t.label) for t in qs.order_by("order", "code"))


def choices_by_category(
    categories: tuple[str, ...] = ALL_CATEGORIES,
    *,
    include_hidden: bool = False,
) -> dict[str, tuple[tuple[str, str], ...]]:
    term_model = _get_term_model()
    qs = term_model.objects.filter(category__in=categories)
    if not include_hidden:
        qs = qs.filter(hidden=False)
    terms = list(qs.order_by("category", "order", "code"))
    result: dict[str, list[tuple[str, str]]] = {c: [] for c in categories}
    for t in terms:
        if t.category in result:
            result[t.category].append((t.code, t.label))
    return {k: tuple(v) for k, v in result.items()}


def codes(category: str, *, include_hidden: bool = True) -> tuple[str, ...]:
    term_model = _get_term_model()
    qs = term_model.objects.filter(category=category)
    if not include_hidden:
        qs = qs.filter(hidden=False)
    return tuple(qs.order_by("order", "code").values_list("code", flat=True))


def exists(category: str, code: str) -> bool:
    if code is None or code == "":
        return False
    term_model = _get_term_model()
    return term_model.objects.filter(category=category, code=str(code)).exists()


def get_term(category: str, code: str):
    if code is None or code == "":
        return None
    term_model = _get_term_model()
    try:
        return term_model.objects.get(category=category, code=str(code))
    except term_model.DoesNotExist:
        return None


def is_closing_verdict(code: str) -> bool:
    if code is None or code == "":
        return False
    term = get_term(CATEGORY_CASE_VERDICT, str(code))
    if not term:
        return False
    if term.code == "undefined":
        return False
    return bool(term.is_closing and not term.hidden)


def closing_verdict_choices(*, include_hidden: bool = False) -> tuple[tuple[str, str], ...]:
    term_model = _get_term_model()
    qs = term_model.objects.filter(category=CATEGORY_CASE_VERDICT, is_closing=True)
    if not include_hidden:
        qs = qs.filter(hidden=False)
    return tuple((t.code, t.label) for t in qs.order_by("order", "code"))


def label(category: str, code: str) -> str:
    if code is None or code == "":
        return ""
    term = get_term(category, str(code))
    if term and term.label:
        return term.label
    return str(code)
