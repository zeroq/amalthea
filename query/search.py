"""Shared search service for Amalthea global search.

The UI (`ui/search.py`) and the API (`query/search_views.py`) both call this module; neither
implementation owns its own matching rules. The service is deliberately HTTP-agnostic: it accepts
a raw query string and a user and returns DTOs.

Approved semantics (`docs/planning/PLAN-2026-11-01-global-search.md` §5):

* case-insensitive substring matching (`__icontains`), never regex or raw SQL;
* all terms are ANDed; fields within one term are ORed;
* a digit-only term (with optional leading ``#``) matches ``Case.number`` exactly;
* cases are scoped with ``case_visible_q`` (own org or a share); alerts/observables with ``org_q``;
* blank queries issue no result queries;
* max 200 characters / 8 terms;
* cases: number/title/description/summary; alerts: title/description/summary; observables: data.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Any, Literal

from django.db.models import Case as DjangoCase
from django.db.models import IntegerField, Q, QuerySet, Value, When

from identity.scoping import case_visible_q, org_q

MAX_QUERY_LENGTH = 200
MAX_TERMS = 8
SECTION_LIMIT = 10
SEARCH_PAGE_SIZE = 50
SEARCH_MAX_PAGE_SIZE = 200

SearchKind = Literal["case", "alert", "observable"]
KINDS: tuple[SearchKind, SearchKind, SearchKind] = ("case", "alert", "observable")


@dataclass(frozen=True)
class SearchQuery:
    """A normalised query plus the original, human-readable form."""

    raw: str
    terms: tuple[str, ...]
    case_number: int | None

    @property
    def is_empty(self) -> bool:
        return not self.terms


@dataclass(frozen=True)
class SearchHit:
    """One result row, rendered by the UI or serialised by the API."""

    kind: SearchKind
    id: str
    number: int | None
    title: str
    subtitle: str
    severity: int | None
    status: str | None
    timestamp: datetime | None
    org_id: str | None = None
    org_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """API shape. Raw text only; no HTML and no UI highlighting."""
        return {
            "type": self.kind,
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "subtitle": self.subtitle,
            "severity": self.severity,
            "status": self.status,
            "timestamp": self.timestamp.isoformat() if self.timestamp is not None else None,
            "organisation": self.org_name,
        }


@dataclass(frozen=True)
class SearchSection:
    """One entity type's result page/slice."""

    kind: SearchKind
    total: int
    hits: list[SearchHit]
    page: int
    per_page: int
    has_next: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "total": self.total,
            "page": self.page,
            "per_page": self.per_page,
            "has_next": self.has_next,
            "results": [hit.to_dict() for hit in self.hits],
        }


# --- Query parsing -------------------------------------------------------------------------


def parse_query(raw: str | None) -> SearchQuery:
    """Trim, collapse, clip, and split a raw query into terms.

    The returned ``raw`` preserves the caller's case for display/highlighting. Matching is
    case-insensitive either way.
    """
    q = re.sub(r"\s+", " ", str(raw or "").strip())
    if not q:
        return SearchQuery(raw="", terms=(), case_number=None)

    q = q[:MAX_QUERY_LENGTH].strip()
    terms = tuple(term for term in q.split(" ")[:MAX_TERMS] if term)
    if not terms:
        return SearchQuery(raw="", terms=(), case_number=None)

    case_number: int | None = None
    for term in terms:
        candidate = term.lstrip("#")
        if candidate.isdigit():
            try:
                case_number = int(candidate)
            except ValueError:
                continue
            break

    return SearchQuery(raw=" ".join(terms), terms=terms, case_number=case_number)


def _term_case_q(term: str) -> Q:
    candidate = term.lstrip("#")
    if candidate.isdigit():
        return Q(number=int(candidate))
    return Q(title__icontains=term) | Q(description__icontains=term) | Q(summary__icontains=term)


def _term_alert_q(term: str) -> Q:
    return Q(title__icontains=term) | Q(description__icontains=term) | Q(summary__icontains=term)


def _term_observable_q(term: str) -> Q:
    return Q(data__icontains=term)


def _all_terms(builder: Callable[[str], Q], terms: tuple[str, ...]) -> Q:
    """AND every term; each term may match any field of the entity."""
    combined = Q()
    for term in terms:
        combined &= builder(term)
    return combined


# --- Querysets -----------------------------------------------------------------------------


def case_queryset(query: SearchQuery, user: Any) -> QuerySet[Any]:
    """Case results in display order, org/share-scoped."""
    from cases.models import Case

    if query.is_empty:
        return Case.objects.none()

    qs: QuerySet[Any] = Case.objects.filter(
        case_visible_q(user) & _all_terms(_term_case_q, query.terms)
    )
    qs = qs.select_related("owner_org", "status", "assignee")
    if query.case_number is not None:
        qs = qs.annotate(
            _search_exact=DjangoCase(
                When(number=query.case_number, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by("-_search_exact", "-severity", "-start_date", "-id")
    else:
        qs = qs.order_by("-severity", "-start_date", "-id")
    return qs


def alert_queryset(query: SearchQuery, user: Any) -> QuerySet[Any]:
    """Alert results in display order, org-scoped."""
    from alerts.models import Alert

    if query.is_empty:
        return Alert.objects.none()

    return (
        Alert.objects.filter(org_q("owner_org", user) & _all_terms(_term_alert_q, query.terms))
        .select_related("owner_org", "status")
        .order_by("-severity", "-date", "-id")
    )


def observable_queryset(query: SearchQuery, user: Any) -> QuerySet[Any]:
    """Observable results in display order, org-scoped."""
    from observables.models import Observable

    if query.is_empty:
        return Observable.objects.none()

    return (
        Observable.objects.filter(
            org_q("owner_org", user) & _all_terms(_term_observable_q, query.terms)
        )
        .select_related("owner_org", "data_type")
        .order_by("-created_at", "-id")
    )


def queryset_for(kind: SearchKind, query: SearchQuery, user: Any) -> QuerySet[Any]:
    if kind == "case":
        return case_queryset(query, user)
    if kind == "alert":
        return alert_queryset(query, user)
    if kind == "observable":
        return observable_queryset(query, user)
    raise ValueError(f"Unknown search kind: {kind}")


# --- DTO builders --------------------------------------------------------------------------


def _org_parts(obj: Any) -> tuple[str | None, str | None]:
    org = getattr(obj, "owner_org", None)
    if org is None:
        return None, None
    return str(org.id), org.name


def _case_hit(case: Any) -> SearchHit:
    status = case.status.value if case.status else None
    assignee = case.assignee.login if case.assignee else None
    subtitle = " · ".join(part for part in (status, assignee) if part)
    org_id, org_name = _org_parts(case)
    return SearchHit(
        kind="case",
        id=str(case.id),
        number=case.number,
        title=case.title,
        subtitle=subtitle,
        severity=case.severity,
        status=status,
        timestamp=case.start_date,
        org_id=org_id,
        org_name=org_name,
    )


def _alert_hit(alert: Any) -> SearchHit:
    status = alert.status.value if alert.status else None
    subtitle = " / ".join(part for part in (alert.type, alert.source, status) if part)
    org_id, org_name = _org_parts(alert)
    return SearchHit(
        kind="alert",
        id=str(alert.id),
        number=None,
        title=alert.title,
        subtitle=subtitle,
        severity=alert.severity,
        status=status,
        timestamp=alert.date,
        org_id=org_id,
        org_name=org_name,
    )


def _observable_hit(observable: Any) -> SearchHit:
    subtitle = observable.data_type.name if observable.data_type else ""
    org_id, org_name = _org_parts(observable)
    return SearchHit(
        kind="observable",
        id=str(observable.id),
        number=None,
        title=observable.data,
        subtitle=subtitle,
        severity=None,
        status=None,
        timestamp=observable.created_at,
        org_id=org_id,
        org_name=org_name,
    )


def _hit(kind: SearchKind, obj: Any) -> SearchHit:
    if kind == "case":
        return _case_hit(obj)
    if kind == "alert":
        return _alert_hit(obj)
    if kind == "observable":
        return _observable_hit(obj)
    raise ValueError(f"Unknown search kind: {kind}")


# --- Section shaping -----------------------------------------------------------------------


def _section(
    kind: SearchKind,
    query: SearchQuery,
    user: Any,
    *,
    page: int,
    per_page: int,
    total: int,
    has_next: bool | None = None,
) -> SearchSection:
    if total == 0:
        return SearchSection(kind=kind, total=0, hits=[], page=1, per_page=per_page, has_next=False)

    pages = max(1, ceil(total / per_page))
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    qs = queryset_for(kind, query, user)
    hits = [_hit(kind, obj) for obj in qs[offset : offset + per_page]]
    return SearchSection(
        kind=kind,
        total=total,
        hits=hits,
        page=page,
        per_page=per_page,
        has_next=page < pages if has_next is None else has_next,
    )


def search_counts(query: SearchQuery, user: Any) -> dict[str, int]:
    """Counts for all three types; zero queries for a blank query."""
    if query.is_empty:
        return dict.fromkeys(KINDS, 0)
    return {kind: queryset_for(kind, query, user).count() for kind in KINDS}


def search_sections(
    query: SearchQuery,
    user: Any,
    kind: SearchKind | None = None,
    *,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Build one section (typed) or three top-N sections (``kind is None``).

    Counts for all three kinds are always returned so the UI tabs are honest even on a typed
    request. A typed request still runs three COUNTs; that is the cost of accurate tab counts.
    """
    counts = search_counts(query, user)
    if query.is_empty:
        return {"sections": [], "total": 0, "counts": counts}

    selected: tuple[SearchKind, ...]
    if kind is None:
        selected = KINDS
        effective_page = 1
        effective_per_page = SECTION_LIMIT
    else:
        if kind not in KINDS:
            raise ValueError(f"Unknown search kind: {kind}")
        selected = (kind,)
        effective_page = page
        effective_per_page = per_page

    sections = [
        _section(
            k,
            query,
            user,
            page=effective_page,
            per_page=effective_per_page,
            total=counts[k],
            has_next=False if kind is None else None,
        )
        for k in selected
    ]
    return {
        "sections": sections,
        "total": sum(counts.values()),
        "counts": counts,
    }
