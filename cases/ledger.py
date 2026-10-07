"""The case ledger's single write path, and the read path that resyncs a dropped client.

Two rules that look like plumbing are the actual product here:

* **A ledger entry that never committed is not a ledger entry.** The publish is therefore a
  `transaction.on_commit` hook, never a bare call — the same rule the automation dispatch follows
  for `execute_run.delay` (AC6.6). A rolled-back case edit emits nothing, so no client can render
  an event the database does not have.
* **A committed entry the team cannot see live is half an entry.** Every `TimelineEvent` row in the
  product is created by `append_timeline_event`, so the publish cannot be forgotten at a call site
  — the choke-point scan in `tests/conformance/test_realtime.py` fails the build if a site goes
  back to `TimelineEvent.objects.create`.

No signals: this repo's culture is explicit calls, and a guard can enforce explicit calls where it
cannot enforce a signal's ordering.

`events_after` is the read half of the same contract. The server's only ordering promise is
`(date, id)` — the keyset pagination tuple the timeline index is built for — so the ordering is
defined once, next to the write, instead of once per consumer.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from cases.models import Case, TimelineEvent
from core.serializers import timeline_event_json
from identity.models import User
from realtime.publisher import publish_case_event


def append_timeline_event(
    case: Case,
    *,
    title: str,
    kind: str,
    description: str = "",
    actor: User | None = None,
    metadata: dict[str, Any] | None = None,
    date: datetime | None = None,
    end_date: datetime | None = None,
) -> TimelineEvent:
    """Write one ledger entry and publish it to the case room once it is durable.

    The row and the hook are written together on purpose: splitting them would let a future caller
    create a row that is invisible to every live client, which is the failure this module exists to
    make unrepresentable.

    `date` defaults to *now* for the events the system itself emits (a status change happened
    now), but a `POST .../customEvent` carries the caller's own timestamp — an entry back-dated to
    when the incident occurred is the entire reason that endpoint exists. Both are passed to
    `objects.create` rather than assigned afterwards so the published payload and the stored row
    are the same instant: mutating `date` after the fact would broadcast one timestamp and
    persist another.

    `kind` doubles as the publish `event_type` — one vocabulary for "what happened" in the
    database, on the wire and in the timeline's CSS class, so a new event type needs no second
    mapping table.
    """
    event = TimelineEvent.objects.create(
        case=case,
        date=date if date is not None else timezone.now(),
        end_date=end_date,
        title=title,
        description=description,
        kind=kind,
        actor=actor,
        metadata=metadata or {},
    )
    transaction.on_commit(
        partial(
            publish_case_event,
            str(case.id),
            kind,
            {"event": timeline_event_json(event)},
        )
    )
    return event


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
