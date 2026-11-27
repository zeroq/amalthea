"""Ledger write path (§13.4)."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from django.db import transaction
from django.utils import timezone

from cases.models import Case, TimelineEvent
from core.serializers import timeline_event_json
from identity.models import User
from realtime.publisher import publish_case_event, publish_org_event


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
    # The case room is the authoritative live ledger (PLAN AC7.1); dropping this publish
    # silently breaks every case tab while the org-room refresh below would still look healthy.
    transaction.on_commit(
        partial(
            publish_case_event,
            str(case.id),
            kind,
            {"event": timeline_event_json(event)},
        )
    )
    # Map the timeline kind to a refresh reason for org broadcasts.
    # Only the canonical kinds emit org events; custom events do not carry bodies.
    reason_map = {
        "comment": "case-updated",
        "status-changed": "case-updated",
        "status-change": "case-updated",
        "verdict-changed": "case-updated",
        "automation-run": "case-updated",
        "alert-imported": "case-updated",
        "alert-merged": "case-updated",
    }
    reason = reason_map.get(kind)
    if reason is not None:
        transaction.on_commit(
            partial(
                publish_org_event,
                str(case.owner_org_id),
                "refresh",
                {
                    "surface": "cases",
                    "reason": reason,
                    "object_id": str(case.id),
                    "occurred_at": timezone.now().isoformat(),
                },
            )
        )
    return event
