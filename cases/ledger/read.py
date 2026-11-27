"""Ledger keyset read path (§13.4)."""

from __future__ import annotations

from uuid import UUID

from django.db.models import Q, QuerySet

from cases.models import TimelineEvent


def events_after(
    case_id: UUID | str,
    after_event_id: UUID | str | None = None,
) -> QuerySet[TimelineEvent]:
    """The case's events strictly after `after_event_id`, ordered `("date", "id")`.

    An anchor the case does not contain — an id from another case, an id since deleted, or no id
    at all because the client is joining for the first time — returns the whole ledger. The client
    dedupes by `data-event-id`, so a full replay is recoverable while a gap is not: a sync that
    dropped an event because its anchor was stale would leave a hole in the timeline permanently.

    The anchor is looked up inside the case's own queryset rather than against the table, so an id
    belonging to another case resolves to "not found" instead of leaking a position in someone
    else's ledger.
    """
    events = TimelineEvent.objects.filter(case_id=case_id).select_related("actor")
    if after_event_id is None:
        return events.order_by("date", "id")

    anchor = events.filter(pk=after_event_id).first()
    if anchor is None:
        return events.order_by("date", "id")

    # Keyset, not OFFSET: two events sharing a microsecond (automation writes several at once) make
    # row-number pagination skip or repeat, and "no duplicates" is AC7.4. `id` breaks the tie and
    # UUIDv4 ordering is consistent between SQLite's text storage and PostgreSQL's uuid type.
    #
    # Written as a range plus a negated tie-break rather than `date > a | (date = a & id > a)`:
    # the two forms are logically identical, but only this one lets Postgres fold `date__gte`
    # into an `Index Cond` on `timeline_case_date_idx` instead of filtering every row of the
    # case (perf review Wave D-F1: 12,501 filtered rows → 1 on a 25k ledger). The anchor row
    # itself and every same-date sibling with a lower or equal id are excluded — the return
    # stays *strictly after* the anchor, exactly as before.
    keyset = Q(date__gte=anchor.date) & ~Q(date=anchor.date, id__lte=anchor.id)
    return events.filter(keyset).order_by("date", "id")
