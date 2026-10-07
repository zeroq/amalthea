"""The `events_after` keyset's boundary semantics (perf fix Wave D-F1).

The filter's shape changed — `date > a OR (date = a AND id > a)` became the logically
identical `date >= a AND NOT (date = a AND id <= a)` — because only the second form Postgres
can fold into an `Index Cond` on `timeline_case_date_idx` instead of scanning every row of
the case. "Logically identical" is a claim that needs the boundary pinned against the
*implemented* form: the anchor row itself, an earlier date, and a same-date sibling with a
lower-or-equal id must all stay out; a same-date sibling with a higher id and any later date
must stay in.

`test_realtime.py` exercises the same predicate end-to-end through the sync protocol; this
module pins the tuple-ordering contract directly, where a failure names the boundary rather
than a socket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cases.ledger import append_timeline_event, events_after
from cases.models import TimelineEvent

from ._t1_seed import _case


@pytest.mark.django_db
def test_events_after_returns_the_strictly_after_tail_in_date_id_order() -> None:
    case = _case("Keyset case")
    instant = datetime(2026, 5, 6, 7, 8, 9, 123456, tzinfo=UTC)
    earlier = append_timeline_event(
        case, title="Earlier", kind="custom", date=instant - timedelta(seconds=1)
    )
    anchor = append_timeline_event(case, title="Anchor", kind="custom", date=instant)
    sibling_a = append_timeline_event(case, title="Sibling A", kind="custom", date=instant)
    sibling_b = append_timeline_event(case, title="Sibling B", kind="custom", date=instant)
    later = append_timeline_event(
        case, title="Later", kind="custom", date=instant + timedelta(seconds=1)
    )
    # Three rows share `instant`; the DB orders them by id, and only the ids *relative to
    # the anchor's* decide which side of the boundary each sibling falls on.
    siblings = [sibling_a, sibling_b]
    below = [event for event in siblings if event.id < anchor.id]
    above = [event for event in siblings if event.id > anchor.id]

    returned = list(events_after(case.id, anchor.id))
    returned_ids = {event.id for event in returned}

    assert anchor.id not in returned_ids, "the anchor itself came back — not strictly after"
    assert earlier.id not in returned_ids, "an event before the anchor's date came back"
    assert later.id in returned_ids, "an event after the anchor's date was dropped"
    assert all(event.id not in returned_ids for event in below), (
        "same date, lower id: before the anchor in (date, id) but came back"
    )
    assert all(event.id in returned_ids for event in above), (
        "same date, higher id: after the anchor in (date, id) but was dropped"
    )

    # The whole result is exactly the strictly-after tail — Python's tuple comparison is the
    # independent oracle for the SQL predicate — delivered in the ordering promise's order.
    all_events: list[TimelineEvent] = [earlier, anchor, sibling_a, sibling_b, later]
    expected = {
        event.id for event in all_events if (event.date, event.id) > (anchor.date, anchor.id)
    }
    assert returned_ids == expected
    assert returned == sorted(returned, key=lambda event: (event.date, event.id))


@pytest.mark.django_db
def test_events_after_without_a_resolvable_anchor_replays_the_whole_ledger() -> None:
    """The documented fallbacks: no anchor, or an anchor the case does not contain.

    A full replay is recoverable (the client dedupes by `data-event-id`); a dropped event
    would not be, which is why an unresolvable anchor must never resolve to "empty tail".
    """
    case = _case("Replay case")
    seeded = [append_timeline_event(case, title=f"Entry {i}", kind="custom") for i in range(3)]
    ordered = sorted(seeded, key=lambda event: (event.date, event.id))

    assert list(events_after(case.id)) == ordered
    assert list(events_after(case.id, "00000000-0000-4000-8000-0000000000ff")) == ordered
